"""Contract Master reprojection runtime: promotion -> PENDING -> worker -> CURRENT.

The 2026-09-25 staging rehearsal promoted a real contract whose clause text was
physically present and indexed, and Contract Master evidence search answered
``200 valid_empty`` for it forever. Promotion wrote ``projection_status=PENDING``
and nothing in any process ever moved it: the reprojection worker and the
post-promotion sequencer were fences with no runtime owner, and every suite that
exercised them called ``mark_projection_current()`` by hand - which is exactly
the step production never performed.

So this suite never marks a projection current itself. It drives the real
promotion service, lets the contract-worker's own reprojection pass run, and
asks the real HTTP evidence route. The only substitutes are the two external
network dependencies (the embedder and the vector store), and each is a strict
double: it either returns real-shaped vectors or raises - there is no silent
fallback for a projection to hide behind.

Real replica-set Mongo, because promotion is a transaction and the claim lease is
an atomic upsert: a fake collection can express neither.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Dict, List, Optional

import pytest

pytestmark = pytest.mark.integration

MONGODB_URI = os.environ.get("CONTRACT_REPROJECTION_RUNTIME_MONGODB_URI")

if not MONGODB_URI:  # pragma: no cover - environment gate
    pytest.skip(
        "CONTRACT_REPROJECTION_RUNTIME_MONGODB_URI is not set; this suite needs a "
        "disposable replica-set Mongo",
        allow_module_level=True,
    )

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rbac_backend.core.permissions import Permissions  # noqa: E402
from rbac_backend.core.security import CurrentUser, get_current_user  # noqa: E402
from rbac_backend.models.contract_document import ContractDocumentType  # noqa: E402
from rbac_backend.routers import contract_master_api  # noqa: E402
from rbac_backend.services.contract_document_store import (  # noqa: E402
    CONTRACT_DOCUMENTS_COLLECTION,
)
from rbac_backend.services.contract_migration_adjudication import (  # noqa: E402
    ContractMigrationAdjudication,
)
from rbac_backend.services.contract_migration_reconciliation import (  # noqa: E402
    ContractMigrationReconciliation,
    ScopeClassificationState,
)
from rbac_backend.services.contract_promotion import ContractPromotionService  # noqa: E402
from rbac_backend.tests.selection_fixtures import pin_selection  # noqa: E402

ORG = "org-reproj"
PROJECT = "project-reproj"
CONTRACT = "contract-reproj"
DOC = "doc-reproj"

#: The clause the staging harness searched for. It exists only in the source
#: pages: no document_vectors row and no vector point is seeded, so a non-empty
#: answer proves the worker built the projection rather than found a leftover.
EXPECTED_CLAUSE = "The Engineer may instruct a Variation at any time prior to taking over"

PAGES = [
    (
        1,
        "1. DEFINITIONS\n"
        "1.1 In the Contract the following words shall have the meanings stated.\n"
        "1.2 Headings shall not be taken into consideration in interpretation.\n",
    ),
    (
        2,
        "20. VARIATIONS\n"
        f"20.1 {EXPECTED_CLAUSE} of the Works, either by an instruction or by a "
        "request for the Contractor to submit a proposal.\n"
        "20.2 The Contractor shall execute and be bound by each Variation.\n",
    ),
]


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


def _fresh_db(name: str):
    """A Motor handle bound to the running loop (TestClient uses its own)."""
    return AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=6000)[name]


class _AllowPolicy:
    """Grants the Contract Master permissions; the routes still have to ask."""

    def __init__(self, db_name: str) -> None:
        self._db_name = db_name

    @property
    def scope_service(self):
        from rbac_backend.services.scope_service import ScopeService

        return ScopeService(_fresh_db(self._db_name))

    async def authorize(self, current_user, permission, **kwargs):
        from fastapi import HTTPException, status

        if kwargs.get("organization_id") not in (None, ORG):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="cross-org")
        return True


def _http(db_name: str) -> TestClient:
    app = FastAPI()
    app.include_router(contract_master_api.router, prefix="/api")
    user = CurrentUser(
        id="alice",
        username="alice",
        email="alice@example.com",
        roles=["orgadmin"],
        organization_id=ORG,
        projects=[PROJECT],
    )
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[contract_master_api.get_db] = lambda: _fresh_db(db_name)
    app.dependency_overrides[contract_master_api.get_policy] = lambda: _AllowPolicy(db_name)
    pin_selection(app, None, ORG, PROJECT)
    return TestClient(app)


def _search_response(db_name: str, query: str = "Engineer may instruct a Variation"):
    return _http(db_name).post(
        "/api/contract-master/evidence/search",
        json={
            "project_id": PROJECT,
            "contract_id": CONTRACT,
            "query": query,
            "query_mode": "current_state",
        },
    )


def _search(db_name: str, query: str = "Engineer may instruct a Variation") -> Dict[str, Any]:
    response = _search_response(db_name, query)
    assert response.status_code == 200, response.text
    return response.json()


def _assert_evidence_withheld_for_projection(db_name: str) -> None:
    """Applicable but not projected: a hard failure, never ``valid_empty``."""
    response = _search_response(db_name)
    assert response.status_code == 409, response.text
    assert "projection_not_current" in response.json()["detail"]


async def _seed_completed_contract(db) -> None:
    """A completed canonical contract Document, as the ingest leaves it."""
    await db["projects"].insert_one({"_id": PROJECT, "organization_id": ORG})
    await db["documents"].insert_one(
        {
            "_id": DOC,
            "organization_id": ORG,
            "project_id": PROJECT,
            "uploadType": "contract",
            "upload_id": "upload-reproj",
            "filename": "Particular Conditions.pdf",
            "checksum": "sha-original",
            "current_version_id": "v1",
            "status": "completed",
            "processing_status": "completed",
            "publication_status": "published",
            "is_active": True,
        }
    )
    await db["contract_ocr_pages"].insert_many(
        [
            {
                "document_id": DOC,
                "upload_id": "upload-reproj",
                "organization_id": ORG,
                "project_id": PROJECT,
                "page_number": number,
                "status": "native_text",
                "raw_text": text,
                "cleaned_text": text,
                "cleaned_text_length": len(text),
            }
            for number, text in PAGES
        ]
    )
    await db["contract_upload_sessions"].insert_one(
        {"_id": "session-reproj", "upload_id": "upload-reproj", "project_id": PROJECT}
    )


async def _promote_through_migration(db, client) -> str:
    """inventory -> materialise -> claim -> adjudicate -> promote, all real."""
    reconciliation = ContractMigrationReconciliation(db)
    candidates = await reconciliation.inventory(organization_id=ORG)
    await reconciliation.materialise_inventory(candidates)
    candidate = next(c for c in candidates if c.canonical_document_id == DOC)

    adjudication = ContractMigrationAdjudication(db)
    await adjudication.ensure_indexes()
    claim = await adjudication.claim(candidate.candidate_id, organization_id=ORG, operator_id="alice")
    await adjudication.adjudicate(
        claim,
        organization_id=ORG,
        scope_state=ScopeClassificationState.PROJECT_SCOPE_CONFIRMED,
        contract_document_type=ContractDocumentType.PARTICULAR_CONDITIONS,
        reason="runtime gate",
    )
    await adjudication.release(claim)

    receipt = await ContractPromotionService(db, client).promote(
        candidate.candidate_id,
        organization_id=ORG,
        actor_id="alice",
        contract_id=CONTRACT,
        effective_from="2021-04-01",
    )
    return receipt.contract_document_id


class _Database:
    def __init__(self) -> None:
        self.name = f"contract_reproj_{uuid.uuid4().hex[:10]}"

    def run(self, factory):
        async def _inner():
            client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=6000)
            try:
                return await factory(client[self.name], client)
            finally:
                client.close()

        return asyncio.run(_inner())

    def drop(self) -> None:
        async def _inner():
            client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=6000)
            await client.drop_database(self.name)
            client.close()

        asyncio.run(_inner())


@pytest.fixture
def database():
    handle = _Database()
    try:
        yield handle
    finally:
        handle.drop()


class StrictEmbedder:
    """Stands in for the embedding provider: real-shaped vectors, or it raises.

    It has no fallback on purpose. The production client's deterministic
    fallback is what let an ingest look complete; the projection must ask for
    ``strict=True``, and this double refuses anything else.
    """

    def __init__(self) -> None:
        self.available = True
        self.calls = 0

    async def embed(self, texts, model=None, *, strict=False):
        from rbac_backend.retrieval.embeddings import EmbeddingUnavailable

        assert strict is True, "the projection asked for embeddings without strict=True"
        self.calls += 1
        if not self.available:
            raise EmbeddingUnavailable("provider unavailable (test outage)")
        return [[float((len(text) + index) % 7 + 1), 1.0, 0.5] for index, text in enumerate(texts)]


class RecordingVectorStore:
    """Stands in for Qdrant: accepts points by id (upsert semantics), or raises."""

    def __init__(self) -> None:
        self.enabled = True
        self.available = True
        self.points: Dict[str, Dict[str, Any]] = {}

    async def upsert(self, vectors, chunks, namespace=None):
        if not self.available:
            raise ConnectionError("vector store unavailable (test outage)")
        for vector, chunk in zip(vectors, chunks):
            self.points[str(chunk["chunk_id"])] = {
                "vector": vector,
                "document_id": chunk["document_id"],
                "payload": dict(chunk.get("payload") or {}),
            }
        return len(chunks)

    async def list_chunk_ids(self, filters, namespace=None, limit=1000, allow_global=False):
        if not self.available:
            raise ConnectionError("vector store unavailable (test outage)")
        return [
            chunk_id
            for chunk_id, point in self.points.items()
            if point["document_id"] == filters.get("document_id")
        ]

    async def delete(self, chunk_ids, namespace=None):
        for chunk_id in chunk_ids:
            self.points.pop(str(chunk_id), None)
        return len(chunk_ids)


async def _contract_worker_pass(db, *, embedder=None, vector_store=None, worker_id="W1"):
    """Run the contract-worker's reprojection pass - the code the process runs.

    Only the two network dependencies are substituted; claim, build, fences and
    the publication transaction are the production path.
    """
    from rbac_backend import worker
    from rbac_backend.services.contract_projection_builder import ContractProjectionBuilder
    from rbac_backend.services.contract_reprojection_runtime import ContractReprojectionRuntime

    builder = ContractProjectionBuilder(
        db,
        embedder=embedder or StrictEmbedder(),
        vector_store=vector_store or RecordingVectorStore(),
    )
    runtime = ContractReprojectionRuntime(db, builder=builder, worker_id=worker_id)
    return await worker.run_contract_reprojection_pass(db, runtime=runtime)


# --------------------------------------------------------------------------- #
# the release gate
# --------------------------------------------------------------------------- #


async def _authority_snapshot(db, instrument_id: str) -> Dict[str, Any]:
    """Everything reprojection must never write, captured for comparison."""
    from rbac_backend.services.contract_document_store import LEGAL_COLLECTIONS
    from rbac_backend.services.contract_promotion import PROMOTION_RECEIPTS_COLLECTION

    record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": instrument_id})
    legal_fields = {
        key: value
        for key, value in record.items()
        if key not in {"projection_status", "projection_revision"}
    }
    collections = {}
    for name in (*LEGAL_COLLECTIONS, PROMOTION_RECEIPTS_COLLECTION):
        if name == CONTRACT_DOCUMENTS_COLLECTION:
            continue
        collections[name] = sorted(
            (await db[name].find({}).to_list(length=None)), key=lambda row: str(row["_id"])
        )
    document = await db["documents"].find_one({"_id": DOC})
    return {"instrument": legal_fields, "collections": collections, "document": document}


async def _instrument(db, instrument_id: str) -> Dict[str, Any]:
    return await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": instrument_id})


async def _rows(db) -> List[Dict[str, Any]]:
    return await db.document_vectors.find({"document_id": DOC}).to_list(length=None)


def _promote(database) -> str:
    async def promote(db, client):
        await _seed_completed_contract(db)
        return await _promote_through_migration(db, client)

    return database.run(promote)


def _results_text(body: Dict[str, Any]) -> str:
    return " ".join(str(item.get("text") or "") for item in body["results"])


# --------------------------------------------------------------------------- #
# the release gate: the exact staging failure
# --------------------------------------------------------------------------- #


def test_a_promoted_contract_becomes_evidence_after_the_worker_runs(database):
    instrument_id = _promote(database)

    async def before(db, client):
        record = await _instrument(db, instrument_id)
        assert (record["projection_status"], record["classification_revision"]) == ("PENDING", 1)
        assert await _rows(db) == [], "the fixture must not pre-seed a projection"
        return await _authority_snapshot(db, instrument_id)

    authority_before = database.run(before)

    # Promoted, applicable, not yet projected: withheld, and said so.
    _assert_evidence_withheld_for_projection(database.name)

    store = RecordingVectorStore()

    async def work(db, client):
        report = await _contract_worker_pass(db, vector_store=store)
        return report, await _instrument(db, instrument_id), await _rows(db)

    report, record, rows = database.run(work)
    assert report.completed == [instrument_id]
    assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 1)

    body = _search(database.name)
    assert body["outcome"] == "complete", body
    assert body["outcome"] != "valid_empty"
    assert EXPECTED_CLAUSE in _results_text(body)

    # Every derived record the evidence path reads carries the generation.
    assert rows and {row["source_classification_revision"] for row in rows} == {1}
    assert store.points and {
        point["payload"]["source_classification_revision"] for point in store.points.values()
    } == {1}
    assert {point["document_id"] for point in store.points.values()} == {DOC}

    # Reprojection is derived work: nothing legal moved.
    authority_after = database.run(lambda db, client: _authority_snapshot(db, instrument_id))
    assert authority_after == authority_before


def test_a_second_pass_does_not_rebuild_a_current_projection(database):
    instrument_id = _promote(database)
    embedder = StrictEmbedder()

    async def work(db, client):
        first = await _contract_worker_pass(db, embedder=embedder)
        calls = embedder.calls
        second = await _contract_worker_pass(db, embedder=embedder)
        return first, second, calls, len(await _rows(db))

    first, second, calls_after_first, row_count = database.run(work)
    assert first.completed == [instrument_id]
    assert second.completed == [] and second.skipped == []
    assert embedder.calls == calls_after_first
    assert row_count > 0


def test_the_worker_loop_itself_drives_pending_to_current(database):
    """Not one hand-called pass: the loop the contract-worker process runs."""
    from rbac_backend.services.contract_projection_builder import ContractProjectionBuilder
    from rbac_backend.services.contract_reprojection_runtime import (
        ContractReprojectionRuntime,
        run_forever,
    )

    instrument_id = _promote(database)

    async def work(db, client):
        runtime = ContractReprojectionRuntime(
            db,
            builder=ContractProjectionBuilder(
                db, embedder=StrictEmbedder(), vector_store=RecordingVectorStore()
            ),
            worker_id="loop",
        )
        task = asyncio.create_task(run_forever(db, interval_seconds=0.05, runtime=runtime))
        try:
            for _ in range(200):
                record = await _instrument(db, instrument_id)
                if record["projection_status"] == "CURRENT":
                    return record
                await asyncio.sleep(0.05)
            return record
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    record = database.run(work)
    assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 1)
    assert EXPECTED_CLAUSE in _results_text(_search(database.name))


# --------------------------------------------------------------------------- #
# classification correction: N -> N+1 -> CURRENT, and the late N worker
# --------------------------------------------------------------------------- #


def test_a_correction_withdraws_evidence_at_once_and_the_worker_rebuilds_it(database):
    from rbac_backend.services.contract_classification_service import (
        ContractClassificationService,
    )
    from rbac_backend.services.contract_projection_builder import ContractProjectionBuilder
    from rbac_backend.services.contract_reprojection_worker import (
        ContractReprojectionWorker,
        StaleWorkerGeneration,
    )

    instrument_id = _promote(database)
    database.run(lambda db, client: _contract_worker_pass(db))
    assert EXPECTED_CLAUSE in _results_text(_search(database.name))

    async def correct(db, client):
        # A revision-1 worker starts a (re)build and is still busy...
        old = ContractReprojectionWorker(db)
        # (a rebuild of revision 1 was requested - e.g. an OCR retry - so the
        # generation is legitimately due again. A CURRENT generation is never
        # claimable: the claim hands it straight back.)
        await old.request_retry(instrument_id, reason="rebuild before correction")
        late_claim = await old.claim(instrument_id, revision=1, worker_id="W-late")
        assert late_claim is not None
        late_builder = ContractProjectionBuilder(
            db, embedder=StrictEmbedder(), vector_store=RecordingVectorStore()
        )
        late_build = await late_builder.build(
            await _instrument(db, instrument_id), revision=1
        )

        # ...when the operator corrects the classification.
        revision = await ContractClassificationService(db).confirm(
            instrument_id,
            contract_document_type=ContractDocumentType.GENERAL_CONDITIONS,
            expected_revision=1,
            actor_id="operator",
        )
        record = await _instrument(db, instrument_id)
        return late_claim, late_builder, late_build, revision, record

    late_claim, late_builder, late_build, revision, record = database.run(correct)
    assert revision == 2
    assert (record["classification_revision"], record["projection_status"]) == (2, "PENDING")
    # Revision-1 evidence stops counting immediately - before any worker runs.
    _assert_evidence_withheld_for_projection(database.name)

    async def rebuild(db, client):
        report = await _contract_worker_pass(db, worker_id="W2")
        return report, await _instrument(db, instrument_id), await _rows(db)

    report, record, rows = database.run(rebuild)
    assert report.completed == [instrument_id]
    assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 2)
    assert {row["source_classification_revision"] for row in rows} == {2}

    # The revision-1 worker finally finishes. The real fence discards it, and its
    # rows never land: they were to be written in the same transaction.
    async def late(db, client):
        from rbac_backend.services.contract_reprojection_worker import ContractReprojectionWorker

        with pytest.raises(StaleWorkerGeneration):
            await ContractReprojectionWorker(db).complete(
                late_claim, publish=lambda session: late_builder.publish_rows(late_build, session)
            )
        return await _instrument(db, instrument_id), await _rows(db)

    record, rows = database.run(late)
    assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 2)
    assert record["classification_revision"] == 2
    assert {row["source_classification_revision"] for row in rows} == {2}
    assert EXPECTED_CLAUSE in _results_text(_search(database.name))


# --------------------------------------------------------------------------- #
# derived-store outages: promotion stands, CURRENT is never claimed, retry works
# --------------------------------------------------------------------------- #


def _outage_then_recovery(database, *, embedder=None, vector_store=None, builder_patch=None):
    instrument_id = _promote(database)

    async def outage(db, client):
        before = await _authority_snapshot(db, instrument_id)
        runtime_kwargs = {"embedder": embedder, "vector_store": vector_store}
        if builder_patch is None:
            report = await _contract_worker_pass(db, **runtime_kwargs)
        else:
            report = await builder_patch(db)
        return report, before, await _instrument(db, instrument_id), await _rows(db)

    report, before, record, rows = database.run(outage)
    assert report.failed == [instrument_id]
    assert record["projection_status"] == "FAILED"
    assert record.get("projection_revision") is None
    assert rows == [], "a failed generation published lexical rows"
    after = database.run(lambda db, client: _authority_snapshot(db, instrument_id))
    assert after == before, "a derived-store outage changed authoritative state"
    _assert_evidence_withheld_for_projection(database.name)

    async def claim_row(db, client):
        return await db["contract_reprojection_claims"].find_one(
            {"_id": f"contract-reprojection:{instrument_id}:1"}
        )

    claim = database.run(claim_row)
    assert claim["status"] == "failed" and claim["last_error"]
    assert claim["next_attempt_at"] is not None, "a first failure must be retried"

    async def recover(db, client):
        # Inside the backoff nothing is retried...
        early = await _contract_worker_pass(db)
        # ...then the clock passes it and the dependency is back.
        await db["contract_reprojection_claims"].update_one(
            {"_id": claim["_id"]},
            {"$set": {"next_attempt_at": claim["next_attempt_at"].replace(year=2000)}},
        )
        late = await _contract_worker_pass(db)
        return early, late, await _instrument(db, instrument_id)

    early, late, record = database.run(recover)
    assert early.completed == [] and early.skipped == [instrument_id]
    assert late.completed == [instrument_id]
    assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 1)
    assert EXPECTED_CLAUSE in _results_text(_search(database.name))


def test_an_embedder_outage_fails_visibly_and_the_same_revision_recovers(database):
    embedder = StrictEmbedder()
    embedder.available = False
    _outage_then_recovery(database, embedder=embedder)


def test_a_vector_store_outage_fails_visibly_and_the_same_revision_recovers(database):
    store = RecordingVectorStore()
    store.available = False
    _outage_then_recovery(database, vector_store=store)


def test_an_unconfigured_vector_store_is_not_a_projection(database):
    store = RecordingVectorStore()
    store.enabled = False
    _outage_then_recovery(database, vector_store=store)


def test_a_lexical_row_write_failure_rolls_the_whole_publication_back(database):
    from rbac_backend.services.contract_projection_builder import ContractProjectionBuilder
    from rbac_backend.services.contract_reprojection_runtime import ContractReprojectionRuntime

    class FailingPublication(ContractProjectionBuilder):
        async def publish_rows(self, built, session):
            await super().publish_rows(built, session)
            raise RuntimeError("document_vectors write failed after insert (test outage)")

    async def failing_pass(db):
        builder = FailingPublication(
            db, embedder=StrictEmbedder(), vector_store=RecordingVectorStore()
        )
        return await ContractReprojectionRuntime(db, builder=builder, worker_id="W1").run_once()

    _outage_then_recovery(database, builder_patch=failing_pass)


# --------------------------------------------------------------------------- #
# worker crash: lease expiry, reclaim, convergence
# --------------------------------------------------------------------------- #


def test_a_worker_that_dies_after_claiming_does_not_leave_pending_forever(database):
    from datetime import timedelta

    from rbac_backend.services.contract_reprojection_worker import (
        ContractReprojectionWorker,
        LostProjectionClaim,
    )

    instrument_id = _promote(database)

    async def crash_and_recover(db, client):
        worker = ContractReprojectionWorker(db)
        dead = await worker.claim(
            instrument_id, revision=1, worker_id="W-dead", lease=timedelta(seconds=1)
        )
        assert dead is not None

        # While the dead worker's lease is live, nobody else may build.
        blocked = await _contract_worker_pass(db, worker_id="W2")
        await asyncio.sleep(1.2)
        recovered = await _contract_worker_pass(db, worker_id="W2")

        # The dead worker wakes up and tries to finish: it no longer owns it.
        with pytest.raises(LostProjectionClaim):
            await worker.complete(dead)
        claims = await db["contract_reprojection_claims"].find({}).to_list(length=None)
        return blocked, recovered, claims, await _instrument(db, instrument_id)

    blocked, recovered, claims, record = database.run(crash_and_recover)
    assert blocked.completed == [] and blocked.skipped == [instrument_id]
    assert recovered.completed == [instrument_id]
    assert len(claims) == 1 and claims[0]["status"] == "complete"
    assert claims[0]["worker_id"] == "W2" and claims[0]["attempts"] == 2
    assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 1)
    assert EXPECTED_CLAUSE in _results_text(_search(database.name))


# --------------------------------------------------------------------------- #
# review round: org scope, text files, re-ingest, shutdown, orphans, diagnostics
# --------------------------------------------------------------------------- #


def test_an_organisation_scoped_instrument_is_readable_once_current(database):
    """Its rows carry no project (ingest provenance); evidence must still read them."""
    from rbac_backend.services.contract_migration_reconciliation import ScopeClassificationState

    async def promote_org(db, client):
        await _seed_completed_contract(db)
        await db["documents"].update_one({"_id": DOC}, {"$set": {"project_id": ""}})
        await db["contract_ocr_pages"].update_many({}, {"$set": {"project_id": None}})
        reconciliation = ContractMigrationReconciliation(db)
        candidates = await reconciliation.inventory(organization_id=ORG)
        await reconciliation.materialise_inventory(candidates)
        candidate = next(c for c in candidates if c.canonical_document_id == DOC)
        adjudication = ContractMigrationAdjudication(db)
        await adjudication.ensure_indexes()
        claim = await adjudication.claim(candidate.candidate_id, organization_id=ORG, operator_id="alice")
        await adjudication.adjudicate(
            claim,
            organization_id=ORG,
            scope_state=ScopeClassificationState.ORG_SCOPE_CONFIRMED,
            contract_document_type=ContractDocumentType.GENERAL_CONDITIONS,
            reason="org instrument",
        )
        await adjudication.release(claim)
        receipt = await ContractPromotionService(db, client).promote(
            candidate.candidate_id, organization_id=ORG, actor_id="alice"
        )
        return receipt.contract_document_id

    instrument_id = database.run(promote_org)
    applied = _http(database.name).post(
        f"/api/contract-master/instruments/{instrument_id}/applicability",
        json={"project_id": PROJECT, "contract_id": CONTRACT, "kind": "APPLIED", "effective_at": "2021-01-01"},
    )
    assert applied.status_code == 201, applied.text
    _assert_evidence_withheld_for_projection(database.name)

    async def work(db, client):
        report = await _contract_worker_pass(db)
        return report, await _rows(db)

    report, rows = database.run(work)
    assert report.completed == [instrument_id]
    assert {row.get("project_id") for row in rows} == {None}
    body = _search(database.name)
    assert body["outcome"] == "complete", body
    assert EXPECTED_CLAUSE in _results_text(body)


def test_a_text_contract_without_a_page_store_is_projected_from_its_stored_file(database, tmp_path):
    source = tmp_path / "Particular Conditions.txt"
    source.write_text("\n".join(text for _, text in PAGES), encoding="utf-8")

    async def promote(db, client):
        await _seed_completed_contract(db)
        await db["contract_ocr_pages"].delete_many({})
        await db["documents"].update_one(
            {"_id": DOC},
            {"$set": {"filename": source.name, "filepath_local": str(source)}},
        )
        return await _promote_through_migration(db, client)

    instrument_id = database.run(promote)
    report = database.run(lambda db, client: _contract_worker_pass(db))
    assert report.completed == [instrument_id], report
    assert EXPECTED_CLAUSE in _results_text(_search(database.name))


def test_a_pdf_without_page_text_fails_visibly(database):
    async def promote(db, client):
        await _seed_completed_contract(db)
        await db["contract_ocr_pages"].delete_many({})
        return await _promote_through_migration(db, client)

    instrument_id = database.run(promote)
    report = database.run(lambda db, client: _contract_worker_pass(db))
    assert report.failed == [instrument_id]
    state = database.run(
        lambda db, client: db["contract_reprojection_claims"].find_one({"contract_document_id": instrument_id})
    )
    assert "no persisted page text" in state["last_error"]
    _assert_evidence_withheld_for_projection(database.name)


def test_a_re_ingest_after_current_withdraws_and_rebuilds_the_projection(database):
    from rbac_backend.services.contract_service import ContractService

    instrument_id = _promote(database)
    database.run(lambda db, client: _contract_worker_pass(db))
    assert EXPECTED_CLAUSE in _results_text(_search(database.name))

    async def reingest(db, client):
        service = ContractService()
        service._db = db
        await service._invalidate_contract_master_projection(DOC)
        return await _instrument(db, instrument_id)

    record = database.run(reingest)
    assert record["projection_status"] == "PENDING"
    _assert_evidence_withheld_for_projection(database.name)

    report = database.run(lambda db, client: _contract_worker_pass(db, worker_id="W2"))
    assert report.completed == [instrument_id]
    assert EXPECTED_CLAUSE in _results_text(_search(database.name))


def test_stopping_the_worker_mid_build_releases_the_generation(database):
    from rbac_backend.services.contract_projection_builder import ContractProjectionBuilder
    from rbac_backend.services.contract_reprojection_runtime import ContractReprojectionRuntime

    instrument_id = _promote(database)

    class HangingEmbedder(StrictEmbedder):
        async def embed(self, texts, model=None, *, strict=False):
            await asyncio.sleep(3600)

    async def stop_mid_build(db, client):
        runtime = ContractReprojectionRuntime(
            db,
            builder=ContractProjectionBuilder(
                db, embedder=HangingEmbedder(), vector_store=RecordingVectorStore()
            ),
            worker_id="W-stopping",
        )
        task = asyncio.create_task(runtime.run_once())
        await asyncio.sleep(1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        claim = await db["contract_reprojection_claims"].find_one({})
        return claim, await _instrument(db, instrument_id)

    claim, record = database.run(stop_mid_build)
    assert claim["status"] == "failed" and claim["attempts"] == 0
    assert record["projection_status"] == "PENDING"

    report = database.run(lambda db, client: _contract_worker_pass(db, worker_id="W-next"))
    assert report.completed == [instrument_id], "a redeploy stranded the generation"


def test_a_rebuild_removes_the_documents_superseded_vector_points(database):
    instrument_id = _promote(database)
    store = RecordingVectorStore()
    store.points["orphan-point"] = {"vector": [1.0], "document_id": DOC, "payload": {}}
    store.points["other-doc-point"] = {"vector": [1.0], "document_id": "doc-other", "payload": {}}

    report = database.run(lambda db, client: _contract_worker_pass(db, vector_store=store))
    assert report.completed == [instrument_id]
    assert "orphan-point" not in store.points
    assert "other-doc-point" in store.points
    assert all(point["document_id"] == DOC for key, point in store.points.items() if key != "other-doc-point")


def test_the_projection_route_explains_a_failed_generation(database):
    instrument_id = _promote(database)
    embedder = StrictEmbedder()
    embedder.available = False
    database.run(lambda db, client: _contract_worker_pass(db, embedder=embedder))

    body = _http(database.name).get(f"/api/contract-master/instruments/{instrument_id}/projection").json()
    assert body["projection_status"] == "FAILED"
    work = body["projection_work"]
    assert work["status"] == "failed" and work["failures"] == 1
    assert "embedding unavailable" in work["last_error"]
    assert work["next_attempt_at"] is not None


def test_a_re_ingest_during_a_build_revokes_that_build(database):
    """The build read the old source; the ingest's retry must stop it publishing."""
    from rbac_backend.services.contract_projection_builder import ContractProjectionBuilder
    from rbac_backend.services.contract_reprojection_worker import (
        ContractReprojectionWorker,
        LostProjectionClaim,
    )
    from rbac_backend.services.contract_service import ContractService

    instrument_id = _promote(database)

    async def race(db, client):
        worker = ContractReprojectionWorker(db)
        claim = await worker.claim(instrument_id, revision=1, worker_id="W-old-text")
        builder = ContractProjectionBuilder(db, embedder=StrictEmbedder(), vector_store=RecordingVectorStore())
        built = await builder.build(await _instrument(db, instrument_id), revision=1)

        service = ContractService()
        service._db = db
        await service._invalidate_contract_master_projection(DOC)

        with pytest.raises(LostProjectionClaim):
            await worker.complete(claim, publish=lambda session: builder.publish_rows(built, session))
        record = await _instrument(db, instrument_id)
        report = await _contract_worker_pass(db, worker_id="W-new-text")
        return record, report, await _instrument(db, instrument_id)

    stopped, report, record = database.run(race)
    assert stopped["projection_status"] != "CURRENT"
    assert report.completed == [instrument_id]
    assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 1)


def test_a_superseded_point_that_will_not_delete_fails_the_build(database):
    instrument_id = _promote(database)

    class StubbornStore(RecordingVectorStore):
        async def delete(self, chunk_ids, namespace=None):
            return 0  # the real client logs and swallows a delete failure

    store = StubbornStore()
    store.points["orphan-point"] = {"vector": [1.0], "document_id": DOC, "payload": {}}
    report = database.run(lambda db, client: _contract_worker_pass(db, vector_store=store))
    assert report.failed == [instrument_id]
    _assert_evidence_withheld_for_projection(database.name)


def test_a_source_mid_ingest_is_not_projected_until_the_ingest_settles(database):
    """Review: pre-ingest invalidation must not let the worker build from text being rewritten."""
    from rbac_backend.services.contract_service import ContractService

    instrument_id = _promote(database)
    database.run(lambda db, client: _contract_worker_pass(db))

    async def start_reingest(db, client):
        service = ContractService()
        service._db = db
        await service._invalidate_contract_master_projection(DOC, strict=True)
        await db["documents"].update_one({"_id": DOC}, {"$set": {"status": "processing"}})

    database.run(start_reingest)
    report = database.run(lambda db, client: _contract_worker_pass(db, worker_id="W2"))
    record = database.run(lambda db, client: _instrument(db, instrument_id))
    assert report.completed == [] and report.failed == [instrument_id]
    assert record["projection_status"] == "FAILED"
    _assert_evidence_withheld_for_projection(database.name)

    async def settle(db, client):
        await db["documents"].update_one({"_id": DOC}, {"$set": {"status": "completed"}})
        service = ContractService()
        service._db = db
        await service._invalidate_contract_master_projection(DOC)

    database.run(settle)
    report = database.run(lambda db, client: _contract_worker_pass(db, worker_id="W3"))
    assert report.completed == [instrument_id]
    assert EXPECTED_CLAUSE in _results_text(_search(database.name))


def test_an_ingest_that_cannot_withdraw_the_projection_does_not_start(database):
    from rbac_backend.services.contract_service import ContractService

    class _Broken:
        def __getitem__(self, name):
            raise ConnectionError("datastore down (test outage)")

    service = ContractService()
    service._db = _Broken()
    with pytest.raises(ConnectionError):
        asyncio.run(service._invalidate_contract_master_projection(DOC, strict=True))
    # Best effort outside the pre-ingest step: logged, not raised.
    asyncio.run(service._invalidate_contract_master_projection(DOC))


# --------------------------------------------------------------------------- #
# review round 2: publication paths that could lie beside a CURRENT stamp
# --------------------------------------------------------------------------- #


def test_vectors_written_outside_the_evidence_namespace_are_not_a_projection(database):
    """A dimension-mismatch fallback (``<name>_dim<N>``) is a collection evidence never reads."""
    instrument_id = _promote(database)

    class ElsewhereStore(RecordingVectorStore):
        collection_name = "document_vectors_dim3"

    report = database.run(
        lambda db, client: _contract_worker_pass(db, vector_store=ElsewhereStore())
    )
    record = database.run(lambda db, client: _instrument(db, instrument_id))
    assert report.failed == [instrument_id] and record["projection_status"] == "FAILED"
    _assert_evidence_withheld_for_projection(database.name)

    class HomeStore(RecordingVectorStore):
        collection_name = "document_vectors"

    async def retry(db, client):
        from rbac_backend.services.contract_reprojection_worker import ContractReprojectionWorker

        await ContractReprojectionWorker(db).request_retry(instrument_id)
        return await _contract_worker_pass(db, vector_store=HomeStore(), worker_id="W2")

    assert database.run(retry).completed == [instrument_id]


def test_a_superseded_point_with_a_foreign_point_id_is_deleted_by_its_point_id(database):
    """LangChain-written points: point id = uuid5(chunk), payload chunk_id = chunk."""
    instrument_id = _promote(database)

    class PointIdStore(RecordingVectorStore):
        async def list_points(self, filters, namespace=None, limit=1000):
            if not self.available:
                raise ConnectionError("vector store unavailable (test outage)")
            return [
                {"id": point_id, "chunk_id": str(point["payload"].get("chunk_id") or point_id)}
                for point_id, point in self.points.items()
                if point["document_id"] == filters.get("document_id")
            ]

        async def list_chunk_ids(self, *args, **kwargs):  # pragma: no cover - must not be used
            raise AssertionError("orphan removal must list point ids, not chunk ids")

    store = PointIdStore()
    store.points["5f0c6b1e-langchain-uuid"] = {
        "vector": [1.0],
        "document_id": DOC,
        "payload": {"chunk_id": "legacy-chunk-from-an-older-text"},
    }
    report = database.run(lambda db, client: _contract_worker_pass(db, vector_store=store))
    assert report.completed == [instrument_id]
    assert "5f0c6b1e-langchain-uuid" not in store.points


def test_a_storage_repair_of_a_governed_document_is_delegated_to_reprojection(database):
    from rbac_backend.config.document_processing_config import DocumentProcessingConfig
    from rbac_backend.routers.storage_sync import _resync_document_vectors
    from rbac_backend.services.contract_reprojection_worker import claim_identity

    instrument_id = _promote(database)
    store = RecordingVectorStore()
    database.run(lambda db, client: _contract_worker_pass(db, vector_store=store))

    class NoVectorsAllowed:
        """Reads the projection's points (repair verifies CURRENT); writes nothing."""

        enabled = True
        list_points = None  # like RecordingVectorStore: listing by chunk id

        async def list_chunk_ids(self, *args, **kwargs):
            return await store.list_chunk_ids(*args, **kwargs)

        def __getattr__(self, name):  # pragma: no cover - must not be touched
            raise AssertionError("repair of a governed document wrote vectors itself")

    async def repair(db, client):
        result = await _resync_document_vectors(
            DOC, db, DocumentProcessingConfig(), vector_client=NoVectorsAllowed()
        )
        claim = await db["contract_reprojection_claims"].find_one(
            {"_id": claim_identity(instrument_id, 1)}
        )
        return result, await _instrument(db, instrument_id), claim

    # A CURRENT projection is healthy: the repair delegates without withdrawing
    # it, so a bulk resync cannot move a whole organisation's evidence to 409.
    result, record, claim = database.run(repair)
    assert (result["status"], result["reason"]) == ("delegated", "contract_master_reprojection")
    assert result["projections"] == [
        {
            "contract_document_id": instrument_id,
            "revision": 1,
            "action": "current",
            "points": len(store.points),
        }
    ]
    assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 1)
    assert claim["status"] == "complete"
    sync = database.run(lambda db, client: db.vector_sync_status.find_one({"document_id": DOC}))
    assert sync["sync_status"] == "delegated"
    assert EXPECTED_CLAUSE in _results_text(_search(database.name))

    # A FAILED projection is re-opened for the worker, never repaired here.
    async def fail_it(db, client):
        await db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
            {"_id": instrument_id}, {"$set": {"projection_status": "FAILED"}}
        )

    database.run(fail_it)
    result, record, claim = database.run(repair)
    assert result["projections"][0]["action"] == "retry_scheduled"
    assert record["projection_status"] == "PENDING"
    assert claim["status"] == "failed" and claim["retry_reason"] == "storage vector repair requested"

    report = database.run(lambda db, client: _contract_worker_pass(db, worker_id="W2"))
    assert report.completed == [instrument_id]


# --------------------------------------------------------------------------- #
# review round 3
# --------------------------------------------------------------------------- #


class ConfiguredElsewhereStore(RecordingVectorStore):
    """Behaves like ``VectorClient``: a configured default collection that is
    NOT the evidence namespace, and ``collection_name`` tracks the collection
    each call actually used."""

    def __init__(self) -> None:
        super().__init__()
        self.collection_name = "contracts"
        self.namespaces: List[Optional[str]] = []

    async def upsert(self, vectors, chunks, namespace=None):
        self.namespaces.append(namespace)
        self.collection_name = namespace or "contracts"
        if self.collection_name != "document_vectors":
            # written where evidence never looks
            return len(chunks)
        return await super().upsert(vectors, chunks, namespace=namespace)

    async def list_chunk_ids(self, filters, namespace=None, limit=1000, allow_global=False):
        self.namespaces.append(namespace)
        return await super().list_chunk_ids(filters, namespace=namespace, limit=limit)

    async def delete(self, chunk_ids, namespace=None):
        self.namespaces.append(namespace)
        return await super().delete(chunk_ids, namespace=namespace)


def test_the_projection_writes_the_evidence_namespace_whatever_the_configured_collection(database):
    """``QDRANT_COLLECTION=contracts`` (the example env) must not strand evidence."""
    instrument_id = _promote(database)
    store = ConfiguredElsewhereStore()
    report = database.run(lambda db, client: _contract_worker_pass(db, vector_store=store))
    assert report.completed == [instrument_id]
    assert store.namespaces and set(store.namespaces) == {"document_vectors"}
    assert store.points, "vectors must land in the namespace evidence searches"
    assert EXPECTED_CLAUSE in _results_text(_search(database.name))


def test_a_claim_that_races_a_completion_hands_the_generation_straight_back(database):
    """The due read precedes the upsert; a completion committing in between must
    not be rebuilt under its CURRENT stamp."""
    from rbac_backend.services.contract_reprojection_worker import (
        ContractReprojectionWorker,
        claim_identity,
    )

    instrument_id = _promote(database)

    async def race(db, client):
        worker = ContractReprojectionWorker(db)
        first = await worker.claim(instrument_id, revision=1, worker_id="A")
        collection = db[CONTRACT_DOCUMENTS_COLLECTION]
        original_find_one = collection.find_one
        completed = {"done": False}

        class RacingCollection:
            """The completion commits right after B's due read."""

            def __getattr__(self, name):
                return getattr(collection, name)

            async def find_one(self, *args, **kwargs):
                row = await original_find_one(*args, **kwargs)
                if not completed["done"]:
                    completed["done"] = True
                    await worker.complete(first, publish=lambda session: _noop(session))
                return row

        class RacingDb:
            def __init__(self) -> None:
                self.client = db.client

            def __getitem__(self, name):
                if name == CONTRACT_DOCUMENTS_COLLECTION:
                    return RacingCollection()
                return db[name]

        second = await ContractReprojectionWorker(RacingDb()).claim(
            instrument_id, revision=1, worker_id="B"
        )
        claim = await db["contract_reprojection_claims"].find_one(
            {"_id": claim_identity(instrument_id, 1)}
        )
        return second, claim, await _instrument(db, instrument_id)

    second, claim, record = database.run(race)
    assert second is None
    assert claim["status"] == "complete" and claim["attempts"] == 1
    assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 1)


async def _noop(session) -> None:
    return None


def test_a_late_failure_report_never_withdraws_a_published_generation(database):
    from rbac_backend.services.contract_reprojection_worker import (
        ContractReprojectionWorker,
        StaleWorkerGeneration,
    )

    instrument_id = _promote(database)

    async def scenario(db, client):
        worker = ContractReprojectionWorker(db)
        claim = await worker.claim(instrument_id, revision=1, worker_id="A")
        # Another writer published this revision meanwhile (the stalled-between-
        # two-writes case): the claim is still ``processing`` under A's token.
        await db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
            {"_id": instrument_id},
            {"$set": {"projection_status": "CURRENT", "projection_revision": 1}},
        )
        with pytest.raises(StaleWorkerGeneration):
            await worker.fail(claim, reason="late transient failure")
        return await _instrument(db, instrument_id)

    record = database.run(scenario)
    assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 1)


def test_the_crash_sweep_spares_an_owner_that_renewed_after_the_scan(database):
    from datetime import datetime, timedelta, timezone

    from rbac_backend.services.contract_reprojection_worker import (
        REPROJECTION_MAX_ATTEMPTS,
        ContractReprojectionWorker,
        claim_identity,
    )

    instrument_id = _promote(database)

    async def scenario(db, client):
        worker = ContractReprojectionWorker(db)
        claim = await worker.claim(instrument_id, revision=1, worker_id="A")
        claims = db["contract_reprojection_claims"]
        await claims.update_one(
            {"_id": claim_identity(instrument_id, 1)},
            {
                "$set": {
                    "attempts": REPROJECTION_MAX_ATTEMPTS,
                    "lease_expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
                }
            },
        )
        original_find = claims.find

        class RenewingClaims:
            """The owner renews between the sweep's scan and its write."""

            def __getattr__(self, name):
                return getattr(claims, name)

            def find(self, *args, **kwargs):
                cursor = original_find(*args, **kwargs)

                class _Cursor:
                    async def to_list(self, length=None):
                        rows = await cursor.to_list(length=length)
                        await worker.renew(claim)
                        return rows

                return _Cursor()

        class SweepDb:
            def __getitem__(self, name):
                if name == "contract_reprojection_claims":
                    return RenewingClaims()
                return db[name]

        exhausted = await ContractReprojectionWorker(SweepDb()).exhaust_crashed_claims()
        return exhausted, await claims.find_one({"_id": claim_identity(instrument_id, 1)})

    exhausted, row = database.run(scenario)
    assert exhausted == 0
    assert row["status"] == "processing" and not row.get("exhausted")


def test_the_projection_route_does_not_disclose_endpoints_or_paths():
    from rbac_backend.services.contract_reprojection_worker import _operator_safe

    text = _operator_safe(
        "ServerSelectionTimeoutError: mongo1:27017: timed out; could not read "
        "'/app/uploads/org/contract.txt' via http://qdrant:6333/collections"
    )
    assert "mongo1" not in text and "27017" not in text
    assert "/app/uploads" not in text and "qdrant" not in text


def test_general_reprocessing_refuses_a_governed_contract(database):
    """The general pipeline would swap clause rows for token chunks under CURRENT."""
    from rbac_backend.services.document_service import (
        DocumentService,
        governed_by_contract_master,
    )

    async def before(db, client):
        return await governed_by_contract_master(db, DOC)

    assert database.run(before) is False, "not governed before promotion"
    _promote(database)

    async def after(db, client):
        return (
            await governed_by_contract_master(db, DOC),
            await DocumentService(db).is_governed_contract(DOC),
        )

    assert database.run(after) == (True, True)


# --------------------------------------------------------------------------- #
# provenance audit: tests for retained peer hunks that had none
# --------------------------------------------------------------------------- #


def test_a_claim_that_fails_before_the_caller_holds_it_is_given_back(database):
    """Cancelled or failed between the claim upsert and the return: no stranded lease."""
    from rbac_backend.services.contract_reprojection_worker import (
        ContractReprojectionWorker,
        claim_identity,
    )

    instrument_id = _promote(database)

    async def scenario(db, client):
        collection = db[CONTRACT_DOCUMENTS_COLLECTION]

        class BrokenStamp:
            def __getattr__(self, name):
                return getattr(collection, name)

            async def update_one(self, *args, **kwargs):
                raise ConnectionError("datastore blip after the claim landed (test)")

        class Db:
            client = db.client

            def __getitem__(self, name):
                return BrokenStamp() if name == CONTRACT_DOCUMENTS_COLLECTION else db[name]

        with pytest.raises(ConnectionError):
            await ContractReprojectionWorker(Db()).claim(instrument_id, revision=1, worker_id="A")
        row = await db["contract_reprojection_claims"].find_one(
            {"_id": claim_identity(instrument_id, 1)}
        )
        again = await ContractReprojectionWorker(db).claim(instrument_id, revision=1, worker_id="B")
        return row, again

    row, again = database.run(scenario)
    assert row["status"] == "failed" and row["attempts"] == 0, row
    assert again is not None, "the generation must be claimable at once, not after a lease"


def test_a_foreign_duplicate_of_a_kept_chunk_is_removed(database):
    """A LangChain-shaped point (uuid id) whose payload names a chunk the new
    generation wrote is still a stale, untagged duplicate."""
    from rbac_backend.services.contract_reprojection_worker import ContractReprojectionWorker

    instrument_id = _promote(database)

    class PointStore(RecordingVectorStore):
        async def list_points(self, filters, namespace=None, limit=1000):
            return [
                {"id": point_id, "chunk_id": str(point["payload"].get("chunk_id") or point_id)}
                for point_id, point in self.points.items()
                if point["document_id"] == filters.get("document_id")
            ]

    store = PointStore()
    assert database.run(
        lambda db, client: _contract_worker_pass(db, vector_store=store)
    ).completed == [instrument_id]
    kept = sorted(store.points)
    store.points["6b8d-foreign-uuid"] = {
        "vector": [1.0],
        "document_id": DOC,
        "payload": {"chunk_id": kept[0]},
    }

    async def rebuild(db, client):
        await ContractReprojectionWorker(db).request_retry(instrument_id)
        return await _contract_worker_pass(db, vector_store=store, worker_id="W2")

    assert database.run(rebuild).completed == [instrument_id]
    assert sorted(store.points) == kept


def test_general_reprocessing_of_a_governed_contract_writes_nothing(database):
    """The refusal itself, not only the helper: rows and document untouched."""
    from bson import ObjectId

    from rbac_backend.services.document_service import DocumentService

    oid = ObjectId()

    async def scenario(db, client):
        await db["documents"].insert_one(
            {
                "_id": oid,
                "organization_id": ORG,
                "project_id": PROJECT,
                "uploadType": "contract",
                "filename": "governed.pdf",
                "status": "completed",
                "processing_status": "completed",
            }
        )
        await db["document_vectors"].insert_one(
            {"document_id": str(oid), "uploadType": "contract", "text": "clause row"}
        )
        await db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
            {"_id": "cd-governed", "organization_id": ORG, "document_id": str(oid)}
        )
        before = (
            await db["documents"].find_one({"_id": oid}),
            await db["document_vectors"].find({"document_id": str(oid)}).to_list(length=None),
        )
        accepted = await DocumentService(db).process_document_async(
            str(oid), "does-not-exist.pdf"
        )
        after = (
            await db["documents"].find_one({"_id": oid}),
            await db["document_vectors"].find({"document_id": str(oid)}).to_list(length=None),
        )
        return accepted, before, after

    accepted, before, after = database.run(scenario)
    assert accepted is False
    assert after == before


def test_bulk_storage_reconcile_reports_a_governed_contract_as_delegated(database, monkeypatch):
    """``/storage-sync/reconcile`` must not count a governed contract as repaired.

    It no longer selects governed contracts at all (review round 4: selecting
    them spent the limit and reset a FAILED generation's backoff every run);
    the single-document repair verifies and delegates them.
    """
    from rbac_backend.routers import storage_sync

    instrument_id = _promote(database)
    database.run(lambda db, client: _contract_worker_pass(db))

    async def scenario(db, client):
        async def _db():
            return db

        async def _no_step_up(*_args, **_kwargs):
            return None

        async def _qdrant_count(*_args, **_kwargs):
            return 0  # Qdrant "behind" Mongo: the handler decides to repair

        class _NoVectors:
            def __init__(self, *_args, **_kwargs):
                pass

        monkeypatch.setattr(storage_sync, "get_database", _db)
        monkeypatch.setattr(storage_sync, "require_step_up", _no_step_up)
        monkeypatch.setattr(storage_sync, "_fetch_qdrant_document_count", _qdrant_count)
        monkeypatch.setattr(storage_sync, "VectorClient", _NoVectors)
        return await storage_sync.reconcile_vectors(
            None, org_id=ORG, project_id=None, limit=10, dry_run=False, current_user=None
        ), await _instrument(db, instrument_id)

    result, record = database.run(scenario)
    rows = [row for row in result["details"] if row["document_id"] == DOC]
    assert rows == [], result
    assert result["repaired"] == 0
    assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 1)
