"""One writer of a governed contract's source at a time, and no projection of a
source that is being written.

A contract ingest (upload, OCR retry, reindex) rewrites the document's accepted
page text and lexical rows. The projection builder reads that text, so it must
never build from a source mid-write. ``documents.status = processing`` was the
only signal, and it is not a lock:

* two consumers can run ingests of one document at once - the queue redelivers
  a job whose heartbeat went stale, every API process and the worker consume,
  and the enqueue dedupe is a read-then-write - and the first to finish writes
  ``completed`` over the other's ``processing`` while it is still writing;
* a legacy Document whose string ``_id`` looks like an ObjectId never received
  ``processing`` at all, because the status write looked it up as an ObjectId.

So the source has a durable per-document lease (``contract_source_leases``): one
mutating owner at a time, reclaimable when it expires, a stale owner cannot
finalize, and the builder publishes only a generation of the source that was
settled when it read it and is still the settled one when it commits.

The ingest itself (OCR, parsing) is replaced by a controllable writer of the page
store, because the race is in the orchestration around it: the code under test
is ``ContractService.process_ingest_job``, the function every queue consumer runs.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import pytest

pytestmark = pytest.mark.integration

if not os.environ.get("CONTRACT_REPROJECTION_RUNTIME_MONGODB_URI"):  # pragma: no cover
    pytest.skip(
        "CONTRACT_REPROJECTION_RUNTIME_MONGODB_URI is not set; this suite needs a "
        "disposable replica-set Mongo",
        allow_module_level=True,
    )

from rbac_backend.tests.integration import (  # noqa: E402
    test_contract_reprojection_runtime_mongo as runtime,
)
from rbac_backend.tests.integration.test_contract_reprojection_runtime_mongo import (  # noqa: E402
    EXPECTED_CLAUSE,
    ORG,
    PROJECT,
    _contract_worker_pass,
    _Database,
    _instrument,
    _promote,
    _results_text,
    _search,
)

NEW_CLAUSE = "The Contractor shall give notice within 28 days of becoming aware"
LEASES = "contract_source_leases"


@pytest.fixture
def database():
    handle = _Database()
    try:
        yield handle
    finally:
        handle.drop()


class _Ingest:
    """Stands in for OCR + parsing: rewrites the page store in two halves.

    ``hold`` pauses it between the halves, so a test can act while the source
    is genuinely half-written. ``active`` counts ingests inside the write.
    """

    active = 0
    peak = 0

    def __init__(self, db, *, hold: Optional[asyncio.Event] = None, text: str = NEW_CLAUSE) -> None:
        self.db = db
        self.hold = hold
        self.text = text
        self.entered = asyncio.Event()

    async def __call__(self, path, organization_id, project_id, filename, tags, upload_id, document_id, **_kw):
        type(self).active += 1
        type(self).peak = max(type(self).peak, type(self).active)
        try:
            await self.db.contract_ocr_pages.update_one(
                {"document_id": document_id, "page_number": 1},
                {"$set": {"cleaned_text": "1. DEFINITIONS\n1.1 (rewriting)\n", "raw_text": "(rewriting)"}},
            )
            self.entered.set()
            if self.hold is not None:
                await self.hold.wait()
            text = f"20. VARIATIONS\n20.1 {self.text} of the event.\n"
            await self.db.contract_ocr_pages.update_one(
                {"document_id": document_id, "page_number": 2},
                {"$set": {"cleaned_text": text, "raw_text": text}},
            )
            return {"ok": True, "categories": []}
        finally:
            type(self).active -= 1


@pytest.fixture(autouse=True)
def _reset_counters():
    _Ingest.active = 0
    _Ingest.peak = 0
    yield


def _service(db, ingest: _Ingest):
    from rbac_backend.services.contract_service import ContractService

    service = ContractService()
    service._db = db
    service.ingest_contract = ingest  # type: ignore[method-assign]

    async def _no_clause_index(document_id):
        return None

    service._run_clause_indexing_safe = _no_clause_index  # type: ignore[method-assign]
    return service


def _payload(tmp_path, name: str) -> Dict[str, Any]:
    path = tmp_path / f"{name}.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    return {
        "upload_id": "upload-reproj",
        "document_id": runtime.DOC,
        "organization_id": ORG,
        "project_id": PROJECT,
        "filename": "Particular Conditions.pdf",
        "processing_path": str(path),
    }


def _current(database) -> str:
    instrument_id = _promote(database)
    report = database.run(lambda db, client: _contract_worker_pass(db))
    assert report.completed == [instrument_id]
    return instrument_id


# --------------------------------------------------------------------------- #
# reachability and serialisation
# --------------------------------------------------------------------------- #


def test_two_ingests_of_one_document_never_write_the_source_at_once(database, tmp_path):
    """Two queue consumers run the same document (a redelivered job, or two
    uploads' jobs). Only one may be inside the source write."""
    _current(database)

    async def race(db, client):
        hold = asyncio.Event()
        first = _Ingest(db, hold=hold)
        task_a = asyncio.create_task(_service(db, first).process_ingest_job(_payload(tmp_path, "a")))
        await asyncio.wait_for(first.entered.wait(), 10)

        second = _Ingest(db)
        outcome_b: Any = None
        try:
            await _service(db, second).process_ingest_job(_payload(tmp_path, "b"))
        except Exception as exc:  # noqa: BLE001 - the outcome is what is asserted
            outcome_b = exc
        document_mid = await db["documents"].find_one({"_id": runtime.DOC})
        hold.set()
        await task_a
        return outcome_b, document_mid, _Ingest.peak

    outcome_b, document_mid, peak = database.run(race)
    assert peak == 1, "two ingests were writing the same source at once"
    from rbac_backend.services.contract_source_lease import ContractSourceBusy

    assert isinstance(outcome_b, ContractSourceBusy), outcome_b
    # The refused ingest wrote nothing over the running one's state.
    assert document_mid["status"] == "processing"


def test_a_projection_is_never_built_from_a_source_mid_write(database, tmp_path):
    """Whatever documents.status says: here another writer has already written
    ``completed`` over it, which is what a concurrent ingest's finish did."""
    instrument_id = _current(database)

    async def race(db, client):
        hold = asyncio.Event()
        ingest = _Ingest(db, hold=hold)
        task = asyncio.create_task(_service(db, ingest).process_ingest_job(_payload(tmp_path, "a")))
        await asyncio.wait_for(ingest.entered.wait(), 10)
        await db["documents"].update_one({"_id": runtime.DOC}, {"$set": {"status": "completed"}})
        report = await _contract_worker_pass(db, worker_id="W-mid-write")
        mid = await _instrument(db, instrument_id)
        hold.set()
        await task
        return report, mid

    report, mid = database.run(race)
    assert report.completed == [], "a projection was published from a half-written source"
    assert mid["projection_status"] != "CURRENT"

    # Once the ingest settles, the projection is rebuilt from the new source.
    report = database.run(lambda db, client: _contract_worker_pass(db, worker_id="W-after"))
    assert report.completed == [instrument_id]
    body = _search(database.name, query="Contractor shall give notice within 28 days")
    assert NEW_CLAUSE in _results_text(body)


def test_a_build_whose_source_changed_before_commit_does_not_publish(database, tmp_path):
    """The generation read at build start must still be the settled one at commit."""
    from rbac_backend.services.contract_projection_builder import ContractProjectionBuilder
    from rbac_backend.services.contract_reprojection_worker import ContractReprojectionWorker

    instrument_id = _promote(database)

    async def race(db, client):
        worker = ContractReprojectionWorker(db)
        claim = await worker.claim(instrument_id, revision=1, worker_id="W-old-source")
        builder = ContractProjectionBuilder(
            db, embedder=runtime.StrictEmbedder(), vector_store=runtime.RecordingVectorStore()
        )
        built = await builder.build(await _instrument(db, instrument_id), revision=1)
        # A whole ingest runs and settles in between - but its invalidation is
        # what could not be recorded (best effort after the rows land).
        service = _service(db, _Ingest(db))

        async def _lost_invalidation(document_id, *, strict=False):
            return None

        service._invalidate_contract_master_projection = _lost_invalidation  # type: ignore[method-assign]
        await service.process_ingest_job(_payload(tmp_path, "between"))
        outcome = None
        try:
            await worker.complete(claim, publish=lambda session: builder.publish_rows(built, session))
        except Exception as exc:  # noqa: BLE001
            outcome = exc
        return outcome, await _instrument(db, instrument_id)

    outcome, record = database.run(race)
    assert outcome is not None, "a build of the superseded source was published"
    assert record["projection_status"] != "CURRENT"


# --------------------------------------------------------------------------- #
# lease lifecycle
# --------------------------------------------------------------------------- #


def test_a_stale_owner_cannot_finalize_over_the_owner_that_reclaimed(database, tmp_path):
    instrument_id = _current(database)

    async def race(db, client):
        hold_a = asyncio.Event()
        first = _Ingest(db, hold=hold_a, text="STALE OWNER TEXT")
        task_a = asyncio.create_task(_service(db, first).process_ingest_job(_payload(tmp_path, "a")))
        await asyncio.wait_for(first.entered.wait(), 10)
        # A stalls past its lease (a blocked loop, a paused VM).
        await db[LEASES].update_many(
            {}, {"$set": {"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}}
        )
        hold_b = asyncio.Event()
        second = _Ingest(db, hold=hold_b)
        task_b = asyncio.create_task(_service(db, second).process_ingest_job(_payload(tmp_path, "b")))
        await asyncio.wait_for(second.entered.wait(), 10)

        hold_a.set()
        outcome_a = None
        try:
            await task_a
        except Exception as exc:  # noqa: BLE001
            outcome_a = exc
        after_a = await db["documents"].find_one({"_id": runtime.DOC})
        report = await _contract_worker_pass(db, worker_id="W-during-b")
        hold_b.set()
        outcome_b = None
        try:
            await task_b
        except Exception as exc:  # noqa: BLE001
            outcome_b = exc
        return outcome_a, after_a, report, outcome_b

    outcome_a, after_a, report, outcome_b = database.run(race)
    from rbac_backend.services.contract_source_lease import SourceTainted

    # B cannot vouch for text A kept writing into: it reports a failed ingest.
    assert isinstance(outcome_b, SourceTainted), repr(outcome_b)
    assert outcome_a is not None, "the stale owner finalized as if it still owned the source"
    assert after_a["status"] == "processing", "the stale owner wrote its outcome over the new owner"
    assert report.completed == [], "a projection was published while the new owner was writing"

    # The stale owner kept writing pages after its lease lapsed, interleaved
    # with the new owner's writes: the source both left behind is not known to
    # be coherent, so it is not projected - fail closed until a clean ingest.
    report = database.run(lambda db, client: _contract_worker_pass(db, worker_id="W-final"))
    assert report.completed == []
    lease = database.run(lambda db, client: db[LEASES].find_one({}))
    assert lease["status"] == "failed" and lease.get("tainted_at") is not None

    async def reindex(db, client):
        await _service(db, _Ingest(db)).process_ingest_job(_payload(tmp_path, "c"))

    database.run(reindex)
    report = database.run(lambda db, client: _contract_worker_pass(db, worker_id="W-clean"))
    assert report.completed == [instrument_id]


def test_a_crashed_ingest_is_not_projected_and_the_next_ingest_recovers(database, tmp_path):
    instrument_id = _current(database)

    async def crash(db, client):
        ingest = _Ingest(db, hold=asyncio.Event())
        task = asyncio.create_task(_service(db, ingest).process_ingest_job(_payload(tmp_path, "a")))
        await asyncio.wait_for(ingest.entered.wait(), 10)
        task.cancel()  # the process died mid-write: nothing after this runs
        with pytest.raises(asyncio.CancelledError):
            await task
        await db[LEASES].update_many(
            {}, {"$set": {"lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}}
        )
        # Whatever status the crash left behind, the source is half-written.
        await db["documents"].update_one({"_id": runtime.DOC}, {"$set": {"status": "completed"}})

    database.run(crash)
    report = database.run(lambda db, client: _contract_worker_pass(db, worker_id="W-after-crash"))
    assert report.completed == []
    record = database.run(lambda db, client: _instrument(db, instrument_id))
    assert record["projection_status"] != "CURRENT"

    async def recover(db, client):
        await _service(db, _Ingest(db)).process_ingest_job(_payload(tmp_path, "b"))
        await db["contract_reprojection_claims"].update_many({}, {"$set": {"next_attempt_at": datetime.now(timezone.utc)}})

    database.run(recover)
    report = database.run(lambda db, client: _contract_worker_pass(db, worker_id="W-recovered"))
    assert report.completed == [instrument_id]


# --------------------------------------------------------------------------- #
# the legacy identifier
# --------------------------------------------------------------------------- #


HEX_STRING_ID = "5f1e0c0de0c0de0c0de0c0de"


def test_a_string_keyed_document_that_looks_like_an_objectid_is_seen_in_flight(
    database, tmp_path, monkeypatch
):
    monkeypatch.setattr(runtime, "DOC", HEX_STRING_ID)
    instrument_id = _current(database)

    async def race(db, client):
        document = await db["documents"].find_one({"_id": HEX_STRING_ID})
        assert document is not None and isinstance(document["_id"], str)
        hold = asyncio.Event()
        ingest = _Ingest(db, hold=hold)
        task = asyncio.create_task(_service(db, ingest).process_ingest_job(_payload(tmp_path, "a")))
        await asyncio.wait_for(ingest.entered.wait(), 10)
        in_flight = await db["documents"].find_one({"_id": HEX_STRING_ID})
        report = await _contract_worker_pass(db, worker_id="W-string-key")
        mid = await _instrument(db, instrument_id)
        hold.set()
        await task
        return in_flight, report, mid

    in_flight, report, mid = database.run(race)
    assert in_flight["status"] == "processing", "the reindex never marked the legacy document in flight"
    assert report.completed == []
    assert mid["projection_status"] != "CURRENT"

    report = database.run(lambda db, client: _contract_worker_pass(db, worker_id="W-after"))
    assert report.completed == [instrument_id]


def test_the_expected_clause_is_evidence_after_an_ordinary_reindex(database, tmp_path):
    """Positive control: serialisation must not stop the normal path."""
    instrument_id = _current(database)
    assert EXPECTED_CLAUSE in _results_text(_search(database.name))

    database.run(lambda db, client: _service(db, _Ingest(db)).process_ingest_job(_payload(tmp_path, "a")))
    report = database.run(lambda db, client: _contract_worker_pass(db, worker_id="W2"))
    assert report.completed == [instrument_id]
    body = _search(database.name, query="Contractor shall give notice within 28 days")
    assert body["outcome"] == "complete"
    assert NEW_CLAUSE in _results_text(body)


# --------------------------------------------------------------------------- #
# review round 4: the lease's own failure paths
# --------------------------------------------------------------------------- #


class _Failing(_Ingest):
    async def __call__(self, *args, **kwargs):
        await super().__call__(*args, **kwargs)
        raise RuntimeError("OCR worker crashed after rewriting the pages")


def test_a_failed_ingest_whose_failure_cannot_be_recorded_is_never_settled(
    database, tmp_path, monkeypatch
):
    """The ingest rewrote the source and failed; recording `failed` then hit a
    transient datastore error. The fallback put the pre-ingest status back -
    settled - over half-written text, and the next pass published it."""
    from pymongo.errors import AutoReconnect

    from rbac_backend.services import contract_source_lease as source_lease

    instrument_id = _current(database)
    real_fail = source_lease.fail
    calls = {"n": 0}

    async def flaky_fail(db, lease, *, error):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AutoReconnect("primary stepped down (test)")
        return await real_fail(db, lease, error=error)

    monkeypatch.setattr(source_lease, "fail", flaky_fail)

    async def run(db, client):
        with pytest.raises(Exception):
            await _service(db, _Failing(db)).process_ingest_job(_payload(tmp_path, "a"))
        return await db[LEASES].find_one({})

    lease = database.run(run)
    assert lease["status"] != "settled", "a half-written source was restored as settled"
    report = database.run(lambda db, client: _contract_worker_pass(db, worker_id="W-after"))
    assert report.completed == []
    record = database.run(lambda db, client: _instrument(db, instrument_id))
    assert record["projection_status"] != "CURRENT"


def test_an_ingest_that_cannot_renew_stops_itself_before_its_lease_can_lapse(
    database, tmp_path, monkeypatch
):
    """Renewals fail (a partition, a stalled loop). The ingest must stop writing
    before its lease lapses - otherwise the next owner and this one write the
    same source at once, and nothing fences the page and row writes."""
    from datetime import timedelta as _td

    from pymongo.errors import AutoReconnect

    from rbac_backend.services import contract_source_lease as source_lease

    _current(database)
    monkeypatch.setattr(source_lease, "SOURCE_LEASE_RENEW_EVERY", _td(milliseconds=100))
    monkeypatch.setattr(source_lease, "SOURCE_LEASE_SELF_FENCE", _td(milliseconds=500))

    async def no_renewal(db, lease, **kwargs):
        raise AutoReconnect("partitioned from the primary (test)")

    monkeypatch.setattr(source_lease, "renew", no_renewal)

    async def run(db, client):
        hold = asyncio.Event()  # never set: the ingest would write forever
        ingest = _Ingest(db, hold=hold)
        outcome = None
        try:
            await asyncio.wait_for(
                _service(db, ingest).process_ingest_job(_payload(tmp_path, "a")), 20
            )
        except BaseException as exc:  # noqa: BLE001
            outcome = exc
        page2 = await db.contract_ocr_pages.find_one({"document_id": runtime.DOC, "page_number": 2})
        return outcome, await db[LEASES].find_one({}), page2

    outcome, lease, page2 = database.run(run)
    # No other owner is known: an ordinary, retryable failed attempt - not a
    # lost lease (which the queue would discard as superseded).
    assert isinstance(outcome, source_lease.SourceLeaseFenced), repr(outcome)
    assert NEW_CLAUSE not in page2["cleaned_text"], "the ingest kept writing after it fenced itself"
    assert lease["status"] != "settled"
    document = database.run(lambda db, client: db["documents"].find_one({"_id": runtime.DOC}))
    assert document["status"] == "failed", "a fenced ingest left the document in flight"


def test_a_tainted_source_is_reported_as_a_failed_ingest(database, tmp_path):
    """settle() fell back to failed silently: the ingest reported a completed
    contract that nothing could ever project."""
    _current(database)

    async def run(db, client):
        hold = asyncio.Event()
        ingest = _Ingest(db, hold=hold)
        task = asyncio.create_task(_service(db, ingest).process_ingest_job(_payload(tmp_path, "a")))
        await asyncio.wait_for(ingest.entered.wait(), 10)
        from rbac_backend.services.contract_source_lease import taint

        key = (await db[LEASES].find_one({}))["_id"]
        await taint(db, key, reason="a superseded writer (test)")
        hold.set()
        outcome = None
        try:
            await task
        except Exception as exc:  # noqa: BLE001
            outcome = exc
        job = await db["contract_ingest_jobs"].find_one({"upload_id": "upload-reproj"})
        return outcome, await db["documents"].find_one({"_id": runtime.DOC}), job

    outcome, document, job = database.run(run)
    from rbac_backend.services.contract_source_lease import SourceTainted

    assert isinstance(outcome, SourceTainted), repr(outcome)
    assert document["status"] == "failed"
    assert job["status"] == "failed"


def test_a_lease_lost_while_writing_taints_the_source(database, tmp_path, monkeypatch):
    """The renewal task learned of the takeover (not settle): the source was left
    untainted, so the new owner settled text this ingest had written into."""
    from datetime import timedelta as _td

    from rbac_backend.services import contract_source_lease as source_lease

    _current(database)
    monkeypatch.setattr(source_lease, "SOURCE_LEASE_RENEW_EVERY", _td(milliseconds=100))

    real_renew = source_lease.renew
    wrote = asyncio.Event()

    async def taken_over(db, lease, **kwargs):
        if not wrote.is_set():
            return await real_renew(db, lease, **kwargs)
        raise source_lease.LostSourceLease("taken over (test)", holder="ingest:upload-other")

    monkeypatch.setattr(source_lease, "renew", taken_over)

    async def run(db, client):
        ingest = _Ingest(db, hold=asyncio.Event())
        ingest.entered = wrote  # the takeover happens once a page is rewritten
        outcome = None
        try:
            await asyncio.wait_for(_service(db, ingest).process_ingest_job(_payload(tmp_path, "a")), 20)
        except BaseException as exc:  # noqa: BLE001
            outcome = exc
        return outcome, await db[LEASES].find_one({})

    outcome, lease = database.run(run)
    assert isinstance(outcome, source_lease.LostSourceLease), repr(outcome)
    assert outcome.holder == "ingest:upload-other"
    assert lease.get("tainted_at") is not None, "a takeover seen by the renewal left the source untainted"


def test_a_finished_ingest_is_never_fenced_by_a_late_renewal(database, tmp_path, monkeypatch):
    """After settle the lease is no longer mutating; a renewal tick then read
    that as a lost lease and cancelled the ingest while it wrote "completed"."""
    from datetime import timedelta as _td

    from rbac_backend.services import contract_source_lease as source_lease

    _current(database)
    monkeypatch.setattr(source_lease, "SOURCE_LEASE_RENEW_EVERY", _td(milliseconds=20))

    async def run(db, client):
        service = _service(db, _Ingest(db))
        real_update = service.update_contract_document

        async def slow_completion(document_id, **kwargs):
            if kwargs.get("status_value") == "completed":
                await asyncio.sleep(0.3)  # several renewal ticks after settle
            return await real_update(document_id, **kwargs)

        service.update_contract_document = slow_completion  # type: ignore[method-assign]
        await service.process_ingest_job(_payload(tmp_path, "a"))
        return await db["documents"].find_one({"_id": runtime.DOC}), await db[LEASES].find_one({})

    document, lease = database.run(run)
    assert document["status"] == "completed"
    assert lease["status"] == "settled"


# --------------------------------------------------------------------------- #
# review round 6
# --------------------------------------------------------------------------- #


def test_a_renewal_tick_during_an_early_failure_never_cancels_the_caller(database, tmp_path, monkeypatch):
    """An exception before the owned handler (a missing file) released the lease
    while the renewal was still running; its next tick read the released lease
    as lost and cancelled the caller - the queue's only worker loop."""
    from datetime import timedelta as _td

    from rbac_backend.services import contract_source_lease as source_lease
    from rbac_backend.utils.error_handler import ContractError

    _current(database)
    monkeypatch.setattr(source_lease, "SOURCE_LEASE_RENEW_EVERY", _td(milliseconds=20))
    real_release = source_lease.release_untouched

    async def slow_release(db, lease):
        released = await real_release(db, lease)
        await asyncio.sleep(0.3)  # released, and several renewal ticks later
        return released

    monkeypatch.setattr(source_lease, "release_untouched", slow_release)

    async def run(db, client):
        payload = _payload(tmp_path, "a")
        payload["processing_path"] = str(tmp_path / "missing.pdf")
        outcome = None
        try:
            await _service(db, _Ingest(db)).process_ingest_job(payload)
        except BaseException as exc:  # noqa: BLE001
            outcome = exc
        await asyncio.sleep(0.2)  # the caller must still be alive and not cancelled
        return outcome

    outcome = database.run(run)
    assert isinstance(outcome, ContractError), repr(outcome)


def test_a_lease_lost_with_no_live_owner_is_a_retried_failure(database, tmp_path, monkeypatch):
    """Lost, but nobody holds the source now (the taker settled or lapsed): the
    tainted source needs a clean ingest, so the job must retry, not be dropped."""
    from datetime import timedelta as _td

    from rbac_backend.services import contract_source_lease as source_lease

    _current(database)
    monkeypatch.setattr(source_lease, "SOURCE_LEASE_RENEW_EVERY", _td(milliseconds=100))
    real_renew = source_lease.renew
    wrote = asyncio.Event()

    async def gone(db, lease, **kwargs):
        if not wrote.is_set():
            return await real_renew(db, lease, **kwargs)
        raise source_lease.LostSourceLease("taken and settled (test)", holder=None)

    monkeypatch.setattr(source_lease, "renew", gone)

    async def run(db, client):
        ingest = _Ingest(db, hold=asyncio.Event())
        ingest.entered = wrote
        outcome = None
        try:
            await asyncio.wait_for(_service(db, ingest).process_ingest_job(_payload(tmp_path, "a")), 20)
        except BaseException as exc:  # noqa: BLE001
            outcome = exc
        return outcome, await db[LEASES].find_one({}), await db["documents"].find_one({"_id": runtime.DOC})

    outcome, lease, document = database.run(run)
    assert isinstance(outcome, source_lease.SourceLeaseFenced), repr(outcome)
    assert lease.get("tainted_at") is not None
    assert document["status"] == "failed"


def test_a_taint_that_fails_once_is_retried(database, tmp_path, monkeypatch):
    from pymongo.errors import AutoReconnect

    from rbac_backend.services import contract_source_lease as source_lease

    _current(database)
    real_taint = source_lease.taint
    calls = {"n": 0}

    async def flaky(db, key, *, reason):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AutoReconnect("blip (test)")
        return await real_taint(db, key, reason=reason)

    monkeypatch.setattr(source_lease, "taint", flaky)

    async def run(db, client):
        service = _service(db, _Ingest(db))
        await service.process_ingest_job(_payload(tmp_path, "a"))
        lease = await db[LEASES].find_one({})
        await service._taint_source(db, type("L", (), {"key": lease["_id"]})(), runtime.DOC, "test")
        return await db[LEASES].find_one({})

    lease = database.run(run)
    assert calls["n"] == 2 and lease["status"] == "failed" and lease.get("tainted_at")


def test_a_lease_lost_at_fail_with_no_owner_records_the_failure(database, tmp_path, monkeypatch):
    from rbac_backend.services import contract_source_lease as source_lease

    _current(database)

    async def lapsed(db, lease, *, error):
        raise source_lease.LostSourceLease("lapsed, nobody holds it (test)", holder=None)

    monkeypatch.setattr(source_lease, "fail", lapsed)

    async def run(db, client):
        with pytest.raises(source_lease.LostSourceLease):
            await _service(db, _Failing(db)).process_ingest_job(_payload(tmp_path, "a"))
        return await db["documents"].find_one({"_id": runtime.DOC}), await db["contract_ingest_jobs"].find_one(
            {"upload_id": "upload-reproj"}
        )

    document, job = database.run(run)
    assert document["status"] == "failed", "the last attempt left the contract processing forever"
    assert job["status"] == "failed"


def test_an_unreadable_holder_is_never_taken_for_no_owner(database, tmp_path, monkeypatch):
    from rbac_backend.services import contract_source_lease as source_lease

    _current(database)

    async def run(db, client):
        lease = await source_lease.acquire(db, runtime.DOC, owner="ingest:a")
        other = await db[LEASES].find_one({})
        await db[LEASES].update_one({"_id": other["_id"]}, {"$set": {"owner_token": "someone-else"}})

        class _Broken:
            def __getattr__(self, name):
                return getattr(db[LEASES], name)

            async def find_one(self, *args, **kwargs):
                raise ConnectionError("read failed (test)")

        monkeypatch.setattr(source_lease, "_leases", lambda _db: _Broken())
        try:
            await source_lease.renew(db, lease)
        except source_lease.LostSourceLease as lost:
            return lost.holder

    assert database.run(run) == source_lease.UNKNOWN_HOLDER


# --------------------------------------------------------------------------- #
# renewal shutdown: no renewal once the ingest has quiesced it
# --------------------------------------------------------------------------- #


def test_no_lease_renewal_happens_after_the_ingest_quiesced_it(database, tmp_path, monkeypatch):
    from datetime import timedelta as _td

    from rbac_backend.services import contract_source_lease as source_lease

    _current(database)
    interval = _td(milliseconds=20)
    monkeypatch.setattr(source_lease, "SOURCE_LEASE_RENEW_EVERY", interval)
    real_renew = source_lease.renew
    renewals = {"count": 0}

    async def counting_renew(db, lease, **kwargs):
        renewals["count"] += 1
        return await real_renew(db, lease, **kwargs)

    monkeypatch.setattr(source_lease, "renew", counting_renew)

    async def run(db, client):
        service = _service(db, _Ingest(db, hold=None))
        real_settle = source_lease.settle

        async def settle_after_renewals(db_, lease):
            # The renewal has been renewing throughout the ingest.
            assert renewals["count"] > 0
            return await real_settle(db_, lease)

        monkeypatch.setattr(source_lease, "settle", settle_after_renewals)
        original_ingest = service.ingest_contract

        async def slow_ingest(*args, **kwargs):
            await asyncio.sleep(interval.total_seconds() * 10)  # several renewals
            return await original_ingest(*args, **kwargs)

        service.ingest_contract = slow_ingest  # type: ignore[method-assign]
        await service.process_ingest_job(_payload(tmp_path, "a"))
        at_return = renewals["count"]
        await asyncio.sleep(interval.total_seconds() * 25)
        leftover = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        return at_return, renewals["count"], await db[LEASES].find_one({}), leftover

    at_return, later, lease, leftover = database.run(run)
    assert at_return > 0
    assert later == at_return, f"{later - at_return} renewal(s) after the ingest quiesced its lease"
    assert lease["status"] == "settled", lease
    assert not [t for t in leftover if "keep_source_lease" in repr(t)], leftover


def test_a_shutdown_racing_a_lost_lease_still_propagates_as_a_cancellation(database, tmp_path, monkeypatch):
    """The worker is stopped while the renewal is stopping the ingest for a lost
    lease. Shutdown must win on every Python version: a cancellation turned into
    an ordinary exception lets the queue loop carry on instead of stopping."""
    from datetime import timedelta as _td

    from rbac_backend.services import contract_source_lease as source_lease

    _current(database)
    monkeypatch.setattr(source_lease, "SOURCE_LEASE_RENEW_EVERY", _td(milliseconds=50))

    async def run(db, client):
        outer = {}
        wrote = asyncio.Event()

        async def taken(db_, lease, **kwargs):
            if not wrote.is_set():
                return None
            raise source_lease.LostSourceLease("taken (test)", holder="ingest:other")

        monkeypatch.setattr(source_lease, "renew", taken)

        class _StoppedWhileUnwinding(_Ingest):
            async def __call__(self, *args, **kwargs):
                try:
                    return await super().__call__(*args, **kwargs)
                except asyncio.CancelledError:
                    # The renewal has recorded the loss and is stopping this
                    # ingest; the process shutdown arrives right now.
                    outer["task"].cancel()
                    raise

        ingest = _StoppedWhileUnwinding(db, hold=asyncio.Event())
        ingest.entered = wrote
        task = asyncio.create_task(_service(db, ingest).process_ingest_job(_payload(tmp_path, "a")))
        outer["task"] = task
        try:
            await asyncio.wait_for(asyncio.shield(task), 20)
        except BaseException:  # noqa: BLE001
            pass
        return task

    task = database.run(run)
    assert task.done()
    assert task.cancelled(), f"shutdown was turned into {task.exception()!r}"


def test_a_fenced_ingest_that_finds_its_lease_gone_taints_the_source(database, tmp_path, monkeypatch):
    from datetime import timedelta as _td

    from pymongo.errors import AutoReconnect

    from rbac_backend.services import contract_source_lease as source_lease

    _current(database)
    monkeypatch.setattr(source_lease, "SOURCE_LEASE_RENEW_EVERY", _td(milliseconds=50))
    monkeypatch.setattr(source_lease, "SOURCE_LEASE_SELF_FENCE", _td(milliseconds=300))
    wrote = asyncio.Event()
    real_renew = source_lease.renew

    async def failing(db, lease, **kwargs):
        if not wrote.is_set():
            return await real_renew(db, lease, **kwargs)
        raise AutoReconnect("partitioned (test)")

    async def gone(db, lease, *, error):
        raise source_lease.LostSourceLease("already taken (test)", holder=None)

    monkeypatch.setattr(source_lease, "renew", failing)
    monkeypatch.setattr(source_lease, "fail", gone)

    async def run(db, client):
        ingest = _Ingest(db, hold=asyncio.Event())
        ingest.entered = wrote
        with pytest.raises(source_lease.SourceLeaseFenced):
            await asyncio.wait_for(_service(db, ingest).process_ingest_job(_payload(tmp_path, "a")), 20)
        return await db[LEASES].find_one({})

    lease = database.run(run)
    assert lease.get("tainted_at") is not None, "a fenced ingest that lost its lease left the source untainted"
