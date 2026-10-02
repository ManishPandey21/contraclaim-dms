"""Review round 3: the derived-store races and repair paths the final reviews found.

* a stale build's orphan cleanup deleting a newer generation's points;
* a CURRENT projection whose points are physically gone staying evidence-ready;
* bulk storage resync re-selecting governed documents on every run, using up
  its limit and resetting the reprojection retry backoff;
* the cheap fences: the retry reads the live revision inside its transaction,
  storage repair re-opens only a FAILED generation, and the crash sweep moves the
  claim and the instrument together.

Real replica-set Mongo; the embedder and the vector store are the only doubles.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

pytestmark = pytest.mark.integration

if not os.environ.get("CONTRACT_REPROJECTION_RUNTIME_MONGODB_URI"):  # pragma: no cover
    pytest.skip(
        "CONTRACT_REPROJECTION_RUNTIME_MONGODB_URI is not set; this suite needs a "
        "disposable replica-set Mongo",
        allow_module_level=True,
    )

from rbac_backend.services.contract_document_store import (  # noqa: E402
    CONTRACT_DOCUMENTS_COLLECTION,
)
from rbac_backend.services.contract_reprojection_worker import (  # noqa: E402
    REPROJECTION_CLAIMS_COLLECTION,
    ContractReprojectionWorker,
    claim_identity,
)
from rbac_backend.tests.integration.test_contract_reprojection_runtime_mongo import (  # noqa: E402
    DOC,
    ORG,
    RecordingVectorStore,
    StrictEmbedder,
    _contract_worker_pass,
    _Database,
    _instrument,
    _promote,
    _results_text,
    _search,
)

NEW_CLAUSE = "The Contractor shall give notice within 28 days of becoming aware"


@pytest.fixture
def database():
    handle = _Database()
    try:
        yield handle
    finally:
        handle.drop()


# --------------------------------------------------------------------------- #
# stale generation orphan delete
# --------------------------------------------------------------------------- #


def test_a_stale_build_does_not_delete_the_points_of_the_generation_that_superseded_it(database):
    """Worker N builds from the old text. While N is about to clean up, the
    source is re-ingested and N+1 publishes from the new text. N's listing then
    holds N+1's points - none of which N wrote - and N must not delete them."""
    from rbac_backend.services.contract_service import ContractService

    instrument_id = _promote(database)

    class RacingStore(RecordingVectorStore):
        def __init__(self, db) -> None:
            super().__init__()
            self.db = db
            self.raced = False

        async def list_chunk_ids(self, filters, namespace=None, limit=1000, allow_global=False):
            if not self.raced:
                # The stale build's orphan listing: it has published its points
                # and is about to delete everything else of the document.
                self.raced = True
                # The re-ingest: new page text, then the ingest's invalidation.
                text = f"20. VARIATIONS\n20.1 {NEW_CLAUSE} of the event.\n"
                await self.db.contract_ocr_pages.update_one(
                    {"document_id": DOC, "page_number": 2},
                    {"$set": {"cleaned_text": text, "raw_text": text}},
                )
                service = ContractService()
                service._db = self.db
                await service._invalidate_contract_master_projection(DOC)
                report = await _contract_worker_pass(self.db, vector_store=self, worker_id="W-new")
                assert report.completed == [instrument_id], report
                self.new_generation = set(self.points)
            return await super().list_chunk_ids(filters, namespace, limit, allow_global)

    async def race(db, client):
        store = RacingStore(db)
        report = await _contract_worker_pass(db, vector_store=store, worker_id="W-stale")
        return store, report, await _instrument(db, instrument_id)

    store, report, record = database.run(race)
    assert report.completed == [], "the stale build published"
    missing = sorted(store.new_generation - set(store.points))
    assert missing == [], f"the stale build deleted {len(missing)} point(s) of the current generation"
    assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 1)
    assert NEW_CLAUSE in _results_text(_search(database.name, query="Contractor shall give notice within 28 days"))


# --------------------------------------------------------------------------- #
# storage repair: CURRENT with missing points, bulk starvation, backoff
# --------------------------------------------------------------------------- #


class _Config:
    qdrant_collection = "document_vectors"


def _resync(db, store, document_id: str = DOC) -> Dict[str, Any]:
    from rbac_backend.routers import storage_sync

    return storage_sync._resync_document_vectors(document_id, db, _Config(), None, store)


def test_a_current_projection_whose_points_are_gone_is_reopened_by_repair(database):
    """A Qdrant wipe (or a stray prune) leaves CURRENT describing points that do
    not exist. Repair must notice and hand the generation back to reprojection -
    never leave it evidence-ready, and never touch a legal field."""
    instrument_id = _promote(database)
    store = RecordingVectorStore()
    database.run(lambda db, client: _contract_worker_pass(db, vector_store=store))
    before = database.run(lambda db, client: _instrument(db, instrument_id))
    assert before["projection_status"] == "CURRENT"

    store.points.clear()  # the physical projection is gone
    result = database.run(lambda db, client: _resync(db, store))
    record = database.run(lambda db, client: _instrument(db, instrument_id))

    assert record["projection_status"] != "CURRENT", "a projection with no points stayed evidence-ready"
    assert result["status"] == "delegated"
    assert result["projections"][0]["action"] == "reprojection_required"
    legal = {k: v for k, v in record.items() if k not in {"projection_status", "projection_revision"}}
    assert legal == {k: v for k, v in before.items() if k not in {"projection_status", "projection_revision"}}

    report = database.run(lambda db, client: _contract_worker_pass(db, vector_store=store, worker_id="W2"))
    assert report.completed == [instrument_id]
    assert store.points


def test_a_healthy_current_projection_is_left_alone_by_repair(database):
    instrument_id = _promote(database)
    store = RecordingVectorStore()
    database.run(lambda db, client: _contract_worker_pass(db, vector_store=store))
    points = dict(store.points)
    result = database.run(lambda db, client: _resync(db, store))
    record = database.run(lambda db, client: _instrument(db, instrument_id))
    assert record["projection_status"] == "CURRENT"
    assert result["projections"][0]["action"] == "current"
    assert store.points == points


def test_a_repair_cannot_withdraw_a_generation_that_became_current_meanwhile(database):
    """FAILED-only: the re-open is conditional on FAILED in the same write."""
    instrument_id = _promote(database)
    database.run(lambda db, client: _contract_worker_pass(db))

    async def reopen(db, client):
        return await ContractReprojectionWorker(db).request_retry(
            instrument_id, reason="storage vector repair requested", only_if_failed=True
        )

    result = database.run(reopen)
    record = database.run(lambda db, client: _instrument(db, instrument_id))
    assert result["retry_scheduled"] is False
    assert record["projection_status"] == "CURRENT"


def test_bulk_resync_does_not_spend_its_limit_or_the_backoff_on_governed_contracts(database, monkeypatch):
    """A FAILED governed contract is re-selected on every bulk run: each run used
    one slot of the limit on it and reset its reprojection backoff to zero."""
    from rbac_backend.routers import storage_sync

    instrument_id = _promote(database)
    embedder_down = StrictEmbedder()
    embedder_down.available = False
    database.run(lambda db, client: _contract_worker_pass(db, embedder=embedder_down))

    async def seed_plain(db, client):
        for index in range(3):
            await db["documents"].insert_one(
                {
                    "_id": f"plain-{index}",
                    "organization_id": ORG,
                    "project_id": "project-reproj",
                    "status": "completed",
                    "processing_status": "completed",
                    "publication_status": "published",
                    "is_active": True,
                    "updatedAt": datetime.utcnow() - timedelta(minutes=index + 1),
                }
            )
        await db["documents"].update_one({"_id": DOC}, {"$set": {"updatedAt": datetime.utcnow()}})
        claim = await db[REPROJECTION_CLAIMS_COLLECTION].find_one(
            {"_id": claim_identity(instrument_id, 1)}
        )
        return claim

    claim_before = database.run(seed_plain)
    assert claim_before["failures"] == 1 and claim_before["next_attempt_at"] is not None

    async def bulk_twice(db, client):
        visits: List[List[str]] = []

        async def get_database():
            return db

        async def no_step_up(*args, **kwargs):
            return None

        monkeypatch.setattr(storage_sync, "get_database", get_database)
        monkeypatch.setattr(storage_sync, "require_step_up", no_step_up)
        monkeypatch.setattr(storage_sync, "EmbeddingClient", lambda config: object())
        monkeypatch.setattr(storage_sync, "VectorClient", lambda config: RecordingVectorStore())
        original = storage_sync._resync_document_vectors
        for _ in range(2):
            seen: List[str] = []

            async def resync(document_id, db_, config, embedding_client=None, vector_client=None):
                seen.append(document_id)
                if document_id == DOC:
                    # The real delegation, where the backoff reset lived.
                    return await original(document_id, db_, config, embedding_client, vector_client)
                return {"document_id": document_id, "status": "synced"}

            monkeypatch.setattr(storage_sync, "_resync_document_vectors", resync)
            await storage_sync.resync_bulk_vectors(
                None, org_id=ORG, project_id=None, limit=3, include_synced=False, current_user=None
            )
            visits.append(seen)
        monkeypatch.setattr(storage_sync, "_resync_document_vectors", original)
        claim = await db[REPROJECTION_CLAIMS_COLLECTION].find_one(
            {"_id": claim_identity(instrument_id, 1)}
        )
        return visits, claim

    visits, claim_after = database.run(bulk_twice)
    for seen in visits:
        assert DOC not in seen, "bulk resync spent a slot of its limit on a governed contract"
        assert sorted(seen) == ["plain-0", "plain-1", "plain-2"], seen
    assert claim_after["failures"] == claim_before["failures"]
    assert claim_after["next_attempt_at"] == claim_before["next_attempt_at"], (
        "observing a governed contract reset its reprojection backoff"
    )


# --------------------------------------------------------------------------- #
# cheap fences
# --------------------------------------------------------------------------- #


def test_a_retry_racing_a_correction_reopens_the_live_revision(database, monkeypatch):
    """request_retry read the revision before its transaction: a correction in
    between made it re-open the superseded generation's claim and leave the live
    one's instrument untouched."""
    instrument_id = _promote(database)
    database.run(lambda db, client: _contract_worker_pass(db))

    async def race(db, client):
        worker = ContractReprojectionWorker(db)
        real_start = client.start_session

        async def start_session_after_correction(*args, **kwargs):
            # The correction commits between the read and the transaction.
            await db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
                {"_id": instrument_id},
                {"$set": {"classification_revision": 2, "projection_status": "CURRENT", "projection_revision": 2}},
            )
            return await real_start(*args, **kwargs)

        monkeypatch.setattr(client, "start_session", start_session_after_correction)
        result = await worker.request_retry(instrument_id, reason="source re-ingested")
        return result, await _instrument(db, instrument_id)

    result, record = database.run(race)
    assert result["revision"] == 2
    assert record["projection_status"] == "PENDING", "the live revision was not re-opened"


def test_the_crash_sweep_moves_the_claim_and_the_instrument_together(database, monkeypatch):
    """Two separate writes: a failure between them left the claim exhausted and
    the instrument IN_PROGRESS forever - due, and never claimable again."""
    from rbac_backend.services import contract_reprojection_worker as worker_module

    instrument_id = _promote(database)

    async def crash_loop(db, client):
        worker = ContractReprojectionWorker(db)
        await worker.claim(instrument_id, revision=1, worker_id="W-dies")
        await db[REPROJECTION_CLAIMS_COLLECTION].update_one(
            {"_id": claim_identity(instrument_id, 1)},
            {
                "$set": {
                    "attempts": worker_module.REPROJECTION_MAX_ATTEMPTS,
                    "lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
                }
            },
        )
        instruments = db[CONTRACT_DOCUMENTS_COLLECTION]
        real_update = instruments.update_one

        class _Instruments:
            def __getattr__(self, name):
                return getattr(instruments, name)

            async def update_one(self, *args, **kwargs):
                raise ConnectionError("instrument write lost (test outage)")

        real_getitem = type(db).__getitem__

        def getitem(self, name):
            if name == CONTRACT_DOCUMENTS_COLLECTION:
                return _Instruments()
            return real_getitem(self, name)

        monkeypatch.setattr(type(db), "__getitem__", getitem)
        try:
            with pytest.raises(ConnectionError):
                await worker.exhaust_crashed_claims()
        finally:
            monkeypatch.setattr(type(db), "__getitem__", real_getitem)
        del real_update
        claim = await db[REPROJECTION_CLAIMS_COLLECTION].find_one({"_id": claim_identity(instrument_id, 1)})
        return claim, await _instrument(db, instrument_id)

    claim, record = database.run(crash_loop)
    # Neither write landed: the next sweep retries both.
    assert claim["status"] == "processing", "the claim was exhausted without its instrument"
    assert record["projection_status"] == "IN_PROGRESS"


# --------------------------------------------------------------------------- #
# operations: the post-deploy ownership check, run against a real runtime
# --------------------------------------------------------------------------- #


def _post_deploy_owner_check() -> str:
    """The Python the post-deploy script runs inside the backend container."""
    from pathlib import Path

    script = (Path(__file__).resolve().parents[4] / "scripts" / "post_deploy_verify.sh").read_text(
        encoding="utf-8"
    )
    start = script.index("RUNTIME_HEARTBEATS_COLLECTION")
    start = script.rindex("python -c '", 0, start) + len("python -c '")
    end = script.index("' >/tmp/reprojection_owner.out", start)
    return script[start:end]


def _run_owner_check(database, **env) -> Any:
    import subprocess
    import sys
    from pathlib import Path

    from rbac_backend.tests.integration.test_contract_reprojection_runtime_mongo import MONGODB_URI

    base, _, query = MONGODB_URI.partition("?")
    url = f"{base.rstrip('/')}/{database.name}?{query}"
    return subprocess.run(
        [sys.executable, "-c", _post_deploy_owner_check()],
        env={**os.environ, "DATABASE_URL": url, **env},
        # The backend image's working directory: rbac_backend is importable.
        cwd=str(Path(__file__).resolve().parents[3]),
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_post_deploy_check_proves_a_live_owner_on_the_deployed_release(database, monkeypatch):
    import socket

    from rbac_backend.services import contract_reprojection_runtime as runtime_module
    from rbac_backend.services.contract_projection_builder import ContractProjectionBuilder

    instrument_id = _promote(database)
    monkeypatch.setenv("RELEASE_SHA", "release-abc")
    host = socket.gethostname()

    async def run_loop(db, client):
        runtime = runtime_module.ContractReprojectionRuntime(
            db,
            builder=ContractProjectionBuilder(
                db, embedder=StrictEmbedder(), vector_store=RecordingVectorStore()
            ),
            worker_id="contract-worker-1",
        )
        task = asyncio.create_task(runtime_module.run_forever(db, interval_seconds=0.2, runtime=runtime))
        heartbeats = db[runtime_module.RUNTIME_HEARTBEATS_COLLECTION]
        for _ in range(100):
            await asyncio.sleep(0.2)
            record = await _instrument(db, instrument_id)
            if record["projection_status"] == "CURRENT" and await heartbeats.find_one({}):
                break
        # A row left by the container this deploy replaced: fresh, but its host
        # is not a running container - it must not read as a second owner.
        await heartbeats.insert_one(
            {
                "_id": "old-container:1234",
                "host": "old-container",
                "release": "release-old",
                "interval_seconds": 0.2,
                "last_alive_at": datetime.now(timezone.utc),
            }
        )
        checks = {}
        for name, env in {
            # CW_RELEASES: the release baked into each running contract-worker
            # image (the shell half ties it to the deployed checkout).
            "ok": dict(
                CW_HOSTS=host, CW_RELEASES="release-abc", DEPLOYED_COMMIT="release-abc",
                PROBE_INSTRUMENT=instrument_id,
            ),
            # The runtime runs code older than the running image claims.
            "stale_release": dict(CW_HOSTS=host, CW_RELEASES="release-def", DEPLOYED_COMMIT="release-def"),
            # No image identity at all: nothing can vouch for the runtime.
            "no_image_release": dict(CW_HOSTS=host, CW_RELEASES="", DEPLOYED_COMMIT="release-abc"),
            "unknown_image": dict(CW_HOSTS=host, CW_RELEASES="unknown", DEPLOYED_COMMIT="release-abc"),
            "second_owner": dict(
                CW_HOSTS="new-contract-worker", NON_OWNER_HOSTS=host, CW_RELEASES="release-abc",
                DEPLOYED_COMMIT="release-abc",
            ),
            "not_started": dict(CW_HOSTS="new-contract-worker", CW_RELEASES="release-abc", DEPLOYED_COMMIT="release-abc"),
        }.items():
            checks[name] = await asyncio.to_thread(_run_owner_check, database, **env)
        beat = await heartbeats.find_one({"_id": "contract-worker-1"})
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        removed = await heartbeats.find_one({"_id": "contract-worker-1"}) is None
        return checks, beat, removed

    checks, beat, removed = database.run(run_loop)
    assert beat["release"] == "release-abc" and beat["host"] == host and beat["last_alive_at"]

    ok = checks["ok"]
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "reached CURRENT" in ok.stdout

    stale = checks["stale_release"]
    assert stale.returncode == 1, stale.stdout + stale.stderr
    assert "runtime release release-abc is not the release of a running contract-worker image (release-def)" in stale.stdout

    for name in ("no_image_release", "unknown_image"):
        refused = checks[name]
        assert refused.returncode == 1, name + ": " + refused.stdout + refused.stderr
        assert "(none known)" in refused.stdout, refused.stdout

    second = checks["second_owner"]
    assert second.returncode == 1, second.stdout
    assert f"non-contract-worker containers: {host}" in second.stdout

    missing = checks["not_started"]
    assert missing.returncode == 1
    assert "no live reprojection runtime recorded by a contract-worker" in missing.stdout
    assert "non-contract-worker" not in missing.stdout, "a replaced container's row read as a second owner"

    assert removed, "a stopped runtime left its heartbeat behind"


@pytest.mark.parametrize("left_behind", ["queued", "retrying"])
def test_a_refused_general_job_does_not_block_the_projection(database, left_behind):
    """The general queue writes ``queued`` (and its recovery ``retrying``) on the
    document before the worker refuses a governed contract; nothing settles it
    again. Those are the general pipeline's states, not a contract ingest."""
    instrument_id = _promote(database)
    database.run(
        lambda db, client: db["documents"].update_one(
            {"_id": DOC}, {"$set": {"processing_status": left_behind}}
        )
    )
    report = database.run(lambda db, client: _contract_worker_pass(db))
    assert report.completed == [instrument_id], report


def test_the_vector_reconcile_script_does_not_rewrite_a_governed_contract(database, monkeypatch):
    """`scripts/reconcile_vectors.py --repair` replaced every point of a
    mismatched document in the evidence collection with LangChain-shaped,
    untagged points - a CURRENT projection rewritten behind its fence."""
    import argparse
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[4] / "scripts" / "reconcile_vectors.py"
    spec = importlib.util.spec_from_file_location("reconcile_vectors_under_test", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)  # type: ignore[union-attr]

    instrument_id = _promote(database)
    database.run(lambda db, client: _contract_worker_pass(db))
    replaced: List[str] = []

    class _LangChain:
        enabled = True

        def __init__(self, config):
            pass

        async def replace_document(self, payloads):
            replaced.extend(p["metadata"]["document_id"] for p in payloads)
            return len(payloads)

    class _Config:
        qdrant_enabled = True
        qdrant_url = None
        qdrant_api_key = None
        qdrant_collection = "document_vectors"

    async def run(db, client):
        async def get_database():
            return db

        async def qdrant_count(client_, collection, document_id):
            return 0  # the points are "missing": a mismatch, so repair runs

        monkeypatch.setattr(script, "get_database", get_database)
        monkeypatch.setattr(script, "DocumentProcessingConfig", _Config)
        monkeypatch.setattr(script, "LangChainVectorService", _LangChain)
        monkeypatch.setattr(script, "_fetch_qdrant_count", qdrant_count)
        await script.reconcile(
            argparse.Namespace(document_id=[DOC], limit=None, repair=True, dry_run=False)
        )
        return await db.vector_sync_status.find_one({"document_id": DOC})

    status = database.run(run)
    assert replaced == [], "the script rewrote a governed contract's evidence points"
    assert status["sync_status"] == "delegated"
    record = database.run(lambda db, client: _instrument(db, instrument_id))
    assert record["projection_status"] == "CURRENT"


def test_repair_with_a_disabled_vector_store_withdraws_nothing(database):
    """A disabled client lists nothing without raising; read as "every point is
    missing" it moved every CURRENT instrument of the organisation to PENDING."""
    instrument_id = _promote(database)
    store = RecordingVectorStore()
    database.run(lambda db, client: _contract_worker_pass(db, vector_store=store))

    disabled = RecordingVectorStore()
    disabled.enabled = False
    result = database.run(lambda db, client: _resync(db, disabled))
    record = database.run(lambda db, client: _instrument(db, instrument_id))
    assert result["projections"][0]["action"] == "unverified"
    assert record["projection_status"] == "CURRENT"


def test_repair_notices_points_that_are_not_the_published_generation(database):
    """Extra points (a revoked build's late upsert, a legacy writer) under
    CURRENT rank beside the published ones; they are a damaged projection too."""
    instrument_id = _promote(database)
    store = RecordingVectorStore()
    database.run(lambda db, client: _contract_worker_pass(db, vector_store=store))
    store.points["late-upsert"] = {"vector": [1.0], "document_id": DOC, "payload": {}}

    result = database.run(lambda db, client: _resync(db, store))
    record = database.run(lambda db, client: _instrument(db, instrument_id))
    assert result["projections"][0]["action"] == "reprojection_required"
    assert result["projections"][0]["extra_points"] == 1
    assert record["projection_status"] == "PENDING"


def test_a_verification_never_withdraws_a_newer_generation(database):
    """Verified at revision N; N+1 is CURRENT by the time the retry commits."""
    instrument_id = _promote(database)
    database.run(lambda db, client: _contract_worker_pass(db))

    async def race(db, client):
        await db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
            {"_id": instrument_id},
            {"$set": {"classification_revision": 2, "projection_revision": 2, "projection_status": "CURRENT"}},
        )
        return await ContractReprojectionWorker(db).request_retry(
            instrument_id, reason="verified at 1", only_if_current_at=1
        )

    result = database.run(race)
    record = database.run(lambda db, client: _instrument(db, instrument_id))
    assert result["retry_scheduled"] is False
    assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 2)


def test_storage_reconcile_does_not_select_governed_contracts(database, monkeypatch):
    from rbac_backend.routers import storage_sync

    _promote(database)
    database.run(lambda db, client: _contract_worker_pass(db))

    async def run(db, client):
        async def get_database():
            return db

        async def no_step_up(*args, **kwargs):
            return None

        seen: List[str] = []

        async def resync(document_id, *args, **kwargs):
            seen.append(document_id)
            return {"document_id": document_id, "status": "synced"}

        monkeypatch.setattr(storage_sync, "get_database", get_database)
        monkeypatch.setattr(storage_sync, "require_step_up", no_step_up)
        async def qdrant_count(config, document_id):
            return 0  # the projection's points look missing: a mismatch

        monkeypatch.setattr(storage_sync, "_resync_document_vectors", resync)
        monkeypatch.setattr(storage_sync, "_fetch_qdrant_document_count", qdrant_count)
        monkeypatch.setattr(storage_sync, "VectorClient", lambda config: RecordingVectorStore())
        monkeypatch.setattr(storage_sync, "EmbeddingClient", lambda config: object())
        try:
            await storage_sync.reconcile_vectors(
                None, org_id=ORG, project_id=None, limit=10, dry_run=False, current_user=None
            )
        except Exception:
            pass  # nothing else to reconcile in this org is fine
        return seen

    assert DOC not in database.run(run)


def test_a_long_pass_keeps_the_runtime_visibly_alive(database, monkeypatch):
    """The heartbeat was written only after a whole pass; a pass over a backlog
    outlasted the verification window and a healthy owner read as dead."""
    from rbac_backend.services import contract_reprojection_runtime as runtime_module
    from rbac_backend.services.contract_projection_builder import ContractProjectionBuilder

    _promote(database)
    monkeypatch.setattr(runtime_module, "_ALIVE_EVERY_SECONDS", 0.0)

    class SlowEmbedder(StrictEmbedder):
        async def embed(self, texts, model=None, *, strict=False):
            await asyncio.sleep(3600)  # a build that takes far longer than the window

    async def run(db, client):
        runtime = runtime_module.ContractReprojectionRuntime(
            db,
            builder=ContractProjectionBuilder(db, embedder=SlowEmbedder(), vector_store=RecordingVectorStore()),
            worker_id="contract-worker-slow",
        )
        task = asyncio.create_task(runtime_module.run_forever(db, interval_seconds=0.2, runtime=runtime))
        await asyncio.sleep(3.0)
        beat = await db[runtime_module.RUNTIME_HEARTBEATS_COLLECTION].find_one({})
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return beat

    beat = database.run(run)
    assert beat is not None, "no liveness recorded while the first pass was still running"
    assert beat.get("last_alive_at") is not None and beat.get("last_pass_at") is None


# --------------------------------------------------------------------------- #
# review round 5
# --------------------------------------------------------------------------- #


def test_a_transient_mismatch_is_read_again_before_anything_is_withdrawn(database):
    """A same-revision rebuild publishing between the row read and the point
    listing looks like damage once; a second read shows a healthy generation."""
    instrument_id = _promote(database)
    store = RecordingVectorStore()
    database.run(lambda db, client: _contract_worker_pass(db, vector_store=store))

    class OnceEmpty(RecordingVectorStore):
        def __init__(self) -> None:
            super().__init__()
            self.points = store.points
            self.calls = 0

        async def list_chunk_ids(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return []
            return await super().list_chunk_ids(*args, **kwargs)

    racing = OnceEmpty()
    result = database.run(lambda db, client: _resync(db, racing))
    record = database.run(lambda db, client: _instrument(db, instrument_id))
    assert result["projections"][0]["action"] == "current"
    assert record["projection_status"] == "CURRENT"


def test_liveness_is_written_during_a_step_that_calls_no_heartbeat(database, monkeypatch):
    from rbac_backend.services import contract_reprojection_runtime as runtime_module
    from rbac_backend.services.contract_projection_builder import ContractProjectionBuilder

    _promote(database)
    monkeypatch.setattr(runtime_module, "_ALIVE_EVERY_SECONDS", 0.2)

    class SilentBuilder(ContractProjectionBuilder):
        async def build(self, instrument, *, revision, heartbeat=None):
            await asyncio.sleep(3600)  # clause extraction / graph sync: no heartbeat

    async def run(db, client):
        runtime = runtime_module.ContractReprojectionRuntime(
            db, builder=SilentBuilder(db), worker_id="contract-worker-silent"
        )
        task = asyncio.create_task(runtime_module.run_forever(db, interval_seconds=0.2, runtime=runtime))
        heartbeats = db[runtime_module.RUNTIME_HEARTBEATS_COLLECTION]
        await asyncio.sleep(1.0)
        first = (await heartbeats.find_one({}))["last_alive_at"]
        await asyncio.sleep(1.5)
        second = (await heartbeats.find_one({}))["last_alive_at"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return first, second

    first, second = database.run(run)
    assert second > first, "no liveness while a silent build step ran"


def test_a_conflicting_heartbeat_index_does_not_stop_reprojection(database):
    from rbac_backend.services import contract_reprojection_runtime as runtime_module

    instrument_id = _promote(database)

    async def run(db, client):
        await db[runtime_module.RUNTIME_HEARTBEATS_COLLECTION].create_index(
            [("last_alive_at", 1)], name="reprojection_heartbeat_ttl", expireAfterSeconds=60
        )
        from rbac_backend.services.contract_projection_builder import ContractProjectionBuilder

        runtime = runtime_module.ContractReprojectionRuntime(
            db,
            builder=ContractProjectionBuilder(db, embedder=StrictEmbedder(), vector_store=RecordingVectorStore()),
            worker_id="contract-worker-idx",
        )
        task = asyncio.create_task(runtime_module.run_forever(db, interval_seconds=0.2, runtime=runtime))
        for _ in range(50):
            await asyncio.sleep(0.2)
            if (await _instrument(db, instrument_id))["projection_status"] == "CURRENT":
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return await _instrument(db, instrument_id)

    assert database.run(run)["projection_status"] == "CURRENT"


def test_the_reconcile_script_never_masks_a_damaged_projection(database, monkeypatch):
    import argparse
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[4] / "scripts" / "reconcile_vectors.py"
    spec = importlib.util.spec_from_file_location("reconcile_vectors_mask_test", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)  # type: ignore[union-attr]
    _promote(database)

    class _Config:
        qdrant_enabled = True
        qdrant_url = None
        qdrant_api_key = None
        qdrant_collection = "document_vectors"

    async def run(db, client):
        await db.vector_sync_status.insert_one({"document_id": DOC, "sync_status": "mismatch"})

        async def get_database():
            return db

        monkeypatch.setattr(script, "get_database", get_database)
        monkeypatch.setattr(script, "DocumentProcessingConfig", _Config)
        for repair in (False, True):
            await script.reconcile(
                argparse.Namespace(document_id=[DOC], limit=None, repair=repair, dry_run=False)
            )
        return await db.vector_sync_status.find_one({"document_id": DOC})

    assert database.run(run)["sync_status"] == "mismatch"
    count = database.run(lambda db, client: db.vector_sync_status.count_documents({"document_id": DOC}))
    assert count == 1, "a second status row was inserted beside the mismatch"


def test_the_post_deploy_check_fails_a_pass_that_never_ends(database):
    """The liveness ticker keeps writing while a pass hangs; a pass running past
    the claim lease must read as stuck, not as a healthy owner."""
    import socket

    from rbac_backend.services.contract_reprojection_runtime import RUNTIME_HEARTBEATS_COLLECTION

    host = socket.gethostname()
    now = datetime.now(timezone.utc)

    async def seed(db, client):
        await db[RUNTIME_HEARTBEATS_COLLECTION].insert_one(
            {
                "_id": "contract-worker-hung",
                "host": host,
                "release": "release-abc",
                "interval_seconds": 15.0,
                "last_alive_at": now,
                "pass_started_at": now - timedelta(minutes=45),
                "last_pass_at": now - timedelta(minutes=46),
            }
        )

    database.run(seed)
    hung = _run_owner_check(database, CW_HOSTS=host, DEPLOYED_COMMIT="release-abc")
    assert hung.returncode == 1, hung.stdout
    assert "running for more than 30 minutes" in hung.stdout
