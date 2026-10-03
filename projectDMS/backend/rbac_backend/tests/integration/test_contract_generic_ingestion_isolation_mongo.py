"""Generic ingestion must not rewrite the evidence of a governed contract.

``POST /api/v1/ingestion/jobs`` runs ``IngestionPipeline.process_job``, which chunks
a document its own way, upserts those chunks and then *prunes every point of the
document it did not just write*. In production its namespace defaults to the
configured collection, ``document_vectors`` - the namespace Contract Master
evidence searches. For a governed contract that prune deleted the projection's
points while the instrument stayed ``CURRENT``: evidence answered ``valid_empty``
from a "current" projection, the staging incident by another route.

A governed contract has one writer of derived evidence - the contract-worker's
reprojection. So the generic pipeline refuses it, before any write, for both
identifier forms a canonical Document can carry.

Real replica-set Mongo (promotion is a transaction); the embedder and the vector
store are the only doubles.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import pytest

pytestmark = pytest.mark.integration

if not os.environ.get("CONTRACT_REPROJECTION_RUNTIME_MONGODB_URI"):  # pragma: no cover
    pytest.skip(
        "CONTRACT_REPROJECTION_RUNTIME_MONGODB_URI is not set; this suite needs a "
        "disposable replica-set Mongo",
        allow_module_level=True,
    )

from bson import ObjectId  # noqa: E402

from rbac_backend.ingestion.models import IngestionJobCreate  # noqa: E402
from rbac_backend.ingestion.pipeline import IngestionPipeline  # noqa: E402
from rbac_backend.services.contract_document_store import (  # noqa: E402
    CONTRACT_DOCUMENTS_COLLECTION,
)
from rbac_backend.tests.integration.test_contract_reprojection_runtime_mongo import (  # noqa: E402
    DOC,
    ORG,
    PAGES,
    PROJECT,
    RecordingVectorStore,
    _contract_worker_pass,
    _Database,
    _instrument,
    _promote,
)

SOURCE_TEXT = "\n".join(text for _, text in PAGES)


@pytest.fixture
def database():
    handle = _Database()
    try:
        yield handle
    finally:
        handle.drop()


class _Embedder:
    """The generic pipeline's embedder: real-shaped vectors, any mode."""

    async def embed(self, texts, model=None, **_kwargs):
        return [[float(len(text) % 5 + 1), 0.5, 1.0] for text in texts]


class _Observability:
    async def log_run(self, **_kwargs):
        return None


class _VectorConfig:
    qdrant_vector_size = 3


def _pipeline(db, store) -> IngestionPipeline:
    store.config = _VectorConfig()
    return IngestionPipeline(
        db=db,
        embedding_client=_Embedder(),
        vector_client=store,
        observability_service=_Observability(),
    )


def _current_projection(database, store: RecordingVectorStore) -> str:
    """Promote, give the Document the text a legacy upload leaves, project it."""
    instrument_id = _promote(database)

    async def project(db, client):
        await db["documents"].update_one({"_id": DOC}, {"$set": {"ocrText": SOURCE_TEXT}})
        report = await _contract_worker_pass(db, vector_store=store)
        assert report.completed == [instrument_id], report
        record = await _instrument(db, instrument_id)
        assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 1)

    database.run(project)
    assert store.points, "the fixture must start from a real projection"
    return instrument_id


async def _derived_state(db) -> Dict[str, Any]:
    return {
        "rows": sorted(
            str(row["_id"])
            for row in await db.document_vectors.find({"document_id": DOC}).to_list(length=None)
        ),
        "chunks": await db.chunks.count_documents({}),
        "sync": await db.vector_sync_status.count_documents({}),
    }


def _run_generic_job(database, store, document_id: str) -> Dict[str, Any]:
    async def run(db, client):
        pipeline = _pipeline(db, store)
        job = await pipeline.create_job(
            IngestionJobCreate(org_id=ORG, project_id=PROJECT, document_id=document_id)
        )
        await pipeline.process_job(job.id)
        return await db.ingestion_jobs.find_one({"job_id": job.id})

    return database.run(run)


def test_a_generic_ingestion_job_leaves_a_current_projection_untouched(database):
    store = RecordingVectorStore()
    instrument_id = _current_projection(database, store)
    points_before = dict(store.points)
    derived_before = database.run(lambda db, client: _derived_state(db))

    job = _run_generic_job(database, store, DOC)

    record = database.run(lambda db, client: _instrument(db, instrument_id))
    assert (record["projection_status"], record["projection_revision"]) == ("CURRENT", 1)
    # The incident shape: the instrument is still CURRENT, so every projection
    # point it was published with must still be there.
    pruned = sorted(set(points_before) - set(store.points))
    assert pruned == [], f"generic ingestion pruned {len(pruned)} projection point(s) of a CURRENT instrument"
    # Nothing else it would have written exists either: no generic point, no
    # token chunk, no sync bookkeeping over the owner's.
    assert store.points == points_before
    assert database.run(lambda db, client: _derived_state(db)) == derived_before
    # Refused as a domain outcome that names the contract lifecycle.
    assert job["status"] == "failed", job
    assert "Contract Master" in (job.get("error") or ""), job


def test_the_job_is_refused_under_the_legacy_objectid_spelling_too(database):
    """The instrument names the Document as a string; the Document may be
    ObjectId-keyed. A job naming either spelling reaches the same Document."""
    store = RecordingVectorStore()
    _current_projection(database, store)
    oid = ObjectId()

    async def rekey(db, client):
        # The same canonical Document under an ObjectId key, named by the
        # instrument in its string form - the shape promotion stores.
        document = await db["documents"].find_one({"_id": DOC})
        await db["documents"].delete_one({"_id": DOC})
        await db["documents"].insert_one({**document, "_id": oid})
        await db[CONTRACT_DOCUMENTS_COLLECTION].update_many(
            {"document_id": DOC}, {"$set": {"document_id": str(oid)}}
        )
        for point in store.points.values():
            point["document_id"] = str(oid)

    database.run(rekey)
    points_before = {key: dict(value) for key, value in store.points.items()}

    job = _run_generic_job(database, store, str(oid))

    assert sorted(set(points_before) - set(store.points)) == []
    assert store.points == points_before
    assert job["status"] == "failed", job
    assert "Contract Master" in (job.get("error") or ""), job


def test_a_contract_promoted_while_the_job_embeds_is_still_not_rewritten(database):
    """The guard is re-read at the write boundary, not trusted from the start."""
    store = RecordingVectorStore()
    instrument_id = _current_projection(database, store)
    points_before = dict(store.points)

    async def run(db, client):
        pipeline = _pipeline(db, store)
        instruments = await db[CONTRACT_DOCUMENTS_COLLECTION].find({}).to_list(length=None)
        # The entry check must see a document that is not a contract source at
        # all: no instrument, and an upload type that is not "contract" (an
        # editable field). Both come back while the job embeds.
        await db[CONTRACT_DOCUMENTS_COLLECTION].delete_many({})
        await db["documents"].update_one({"_id": DOC}, {"$set": {"uploadType": "incoming"}})

        class _RestoringEmbedder(_Embedder):
            async def embed(self, texts, model=None, **kwargs):
                await db["documents"].update_one(
                    {"_id": DOC}, {"$set": {"uploadType": "contract"}}
                )
                await db[CONTRACT_DOCUMENTS_COLLECTION].insert_many(instruments)
                return await super().embed(texts, model=model, **kwargs)

        pipeline.embedding_client = _RestoringEmbedder()
        job = await pipeline.create_job(
            IngestionJobCreate(org_id=ORG, project_id=PROJECT, document_id=DOC)
        )
        await pipeline.process_job(job.id)
        return await db.ingestion_jobs.find_one({"job_id": job.id})

    job = database.run(run)
    assert sorted(set(points_before) - set(store.points)) == []
    assert store.points == points_before
    assert job["status"] == "failed", job
    record = database.run(lambda db, client: _instrument(db, instrument_id))
    assert record["projection_status"] == "CURRENT"


def test_an_ungoverned_document_is_still_ingested(database):
    """Positive control: the refusal is about governance, not about the pipeline."""
    store = RecordingVectorStore()

    async def run(db, client):
        await db["documents"].insert_one(
            {
                "_id": "plain-doc",
                "organization_id": ORG,
                "project_id": PROJECT,
                "status": "completed",
                "processing_status": "completed",
                "publication_status": "published",
                "is_active": True,
                "ocrText": SOURCE_TEXT,
            }
        )
        pipeline = _pipeline(db, store)
        job = await pipeline.create_job(
            IngestionJobCreate(org_id=ORG, project_id=PROJECT, document_id="plain-doc")
        )
        await pipeline.process_job(job.id)
        return await db.ingestion_jobs.find_one({"job_id": job.id})

    job = database.run(run)
    assert job["status"] == "done", job
    assert store.points, "an ungoverned document must still be indexed"


# --------------------------------------------------------------------------- #
# the HTTP route
# --------------------------------------------------------------------------- #


def _route_client(database, store) -> Any:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from motor.motor_asyncio import AsyncIOMotorClient

    from rbac_backend.core.security import CurrentUser, get_current_user
    from rbac_backend.ingestion.service import IngestionService
    from rbac_backend.routers import retrieval_engine
    from rbac_backend.tests.integration.test_contract_reprojection_runtime_mongo import (
        MONGODB_URI,
    )

    submitted: List[Any] = []

    class _Service(IngestionService):
        def __init__(self) -> None:
            self.db = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=6000)[
                database.name
            ]
            self.pipeline = _pipeline(self.db, store)

        async def create_job(self, payload):
            job = await self.pipeline.create_job(payload)
            submitted.append(job.id)
            return job

    class _Policy:
        def __init__(self) -> None:
            self.calls: List[Any] = []

        async def authorize(self, current_user, permission, **kwargs):
            self.calls.append((permission, kwargs))
            return True

        async def authorize_document(self, current_user, permission, document, **kwargs):
            self.calls.append((permission, {"document": document.get("_id")}))
            return True

    policy = _Policy()
    app = FastAPI()
    app.include_router(retrieval_engine.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="alice",
        username="alice",
        email="alice@example.com",
        roles=["orgadmin"],
        organization_id=ORG,
        projects=[PROJECT],
    )
    app.dependency_overrides[retrieval_engine.get_ingestion_service] = _Service
    app.dependency_overrides[retrieval_engine.get_policy_service] = lambda: policy
    return TestClient(app), submitted, policy


def test_the_route_refuses_a_governed_contract_after_authorising(database):
    store = RecordingVectorStore()
    _current_projection(database, store)
    client, submitted, policy = _route_client(database, store)

    response = client.post(
        "/api/v1/ingestion/jobs",
        json={"org_id": ORG, "project_id": PROJECT, "document_id": DOC},
    )

    assert response.status_code == 409, response.text
    assert "Contract Master" in response.json()["detail"]
    assert submitted == [], "no job may be created for a governed contract"
    assert policy.calls, "the refusal must come after authorisation, never before"
    assert database.run(lambda db, client: db.ingestion_jobs.count_documents({})) == 0


def test_the_route_does_not_ingest_another_organisations_document(database):
    """The payload names the caller's org; the document belongs to another.
    Pre-existing: only the payload scope was authorised, so another tenant's
    text was chunked under the caller's org - and the governance 409 told the
    caller which foreign documents are contracts."""
    store = RecordingVectorStore()
    _current_projection(database, store)  # DOC is governed, in ORG

    async def seed_foreign(db, client):
        await db["documents"].insert_one(
            {
                "_id": "foreign-doc",
                "organization_id": "org-foreign",
                "project_id": "project-foreign",
                "status": "completed",
                "processing_status": "completed",
                "publication_status": "published",
                "is_active": True,
                "ocrText": SOURCE_TEXT,
            }
        )
        await db["documents"].update_one({"_id": DOC}, {"$set": {"organization_id": "org-foreign"}})

    database.run(seed_foreign)
    client, submitted, _policy = _route_client(database, store)

    for document_id in ("foreign-doc", DOC, "no-such-doc"):
        response = client.post(
            "/api/v1/ingestion/jobs",
            json={"org_id": ORG, "project_id": PROJECT, "document_id": document_id},
        )
        # Same answer for "not yours", "governed but not yours" and "absent".
        assert response.status_code == 404, (document_id, response.text)
    assert submitted == []


def test_a_job_whose_scope_is_not_the_documents_is_refused_by_the_pipeline(database):
    store = RecordingVectorStore()

    async def run(db, client):
        await db["documents"].insert_one(
            {
                "_id": "foreign-doc",
                "organization_id": "org-foreign",
                "project_id": "project-foreign",
                "status": "completed",
                "processing_status": "completed",
                "publication_status": "published",
                "is_active": True,
                "ocrText": SOURCE_TEXT,
            }
        )
        pipeline = _pipeline(db, store)
        job = await pipeline.create_job(
            IngestionJobCreate(org_id=ORG, project_id=PROJECT, document_id="foreign-doc")
        )
        await pipeline.process_job(job.id)
        return await db.ingestion_jobs.find_one({"job_id": job.id}), await db.chunks.count_documents({})

    job, chunks = database.run(run)
    assert job["status"] == "failed", job
    assert chunks == 0 and store.points == {}


def test_a_document_level_refusal_answers_the_same_404(database):
    from fastapi import HTTPException

    store = RecordingVectorStore()
    _current_projection(database, store)
    client, submitted, policy = _route_client(database, store)

    async def refuse(current_user, permission, document, **kwargs):
        raise HTTPException(status_code=403, detail="not this document")

    policy.authorize_document = refuse
    response = client.post(
        "/api/v1/ingestion/jobs", json={"org_id": ORG, "project_id": PROJECT, "document_id": DOC}
    )
    assert response.status_code == 404, response.text
    assert submitted == []


# --------------------------------------------------------------------------- #
# A contract upload with no Contract Master record yet (not promoted)
# --------------------------------------------------------------------------- #
#
# Promotion is a later, separate step, so for a while a contract upload has no
# instrument. Its evidence is still contract ingest's: clause rows with page
# provenance and their points. The generic writers - this pipeline, the route
# that submits to it, and the general reprocess path - answered "not governed"
# and rewrote it. They now ask the one contract-source predicate.

CONTRACT_ROWS = [
    ("20.1", "Variations", [2]),
    ("1.1", "Definitions", [1]),
]


def _contract_point_id(number: str) -> str:
    return f"{DOC}-clause-{number}"


def _unpromoted_contract(database, store: RecordingVectorStore) -> None:
    """A completed contract upload as contract ingest leaves it, never promoted."""

    async def seed(db, client):
        from rbac_backend.tests.integration.test_contract_reprojection_runtime_mongo import (
            _seed_completed_contract,
        )

        await _seed_completed_contract(db)
        await db["documents"].update_one({"_id": DOC}, {"$set": {"ocrText": SOURCE_TEXT}})
        await db.document_vectors.insert_many(
            [
                {
                    "document_id": DOC,
                    "organization_id": ORG,
                    "project_id": PROJECT,
                    "uploadType": "contract",
                    "chunk_id": _contract_point_id(number),
                    "clause_number": number,
                    "clause_title": title,
                    "page_numbers": pages,
                    "page_start": pages[0],
                    "page_end": pages[-1],
                    "text": f"Clause {number} {title}",
                }
                for number, title, pages in CONTRACT_ROWS
            ]
        )

    database.run(seed)
    for number, title, pages in CONTRACT_ROWS:
        store.points[_contract_point_id(number)] = {
            "vector": [1.0, 0.5, 0.25],
            "document_id": DOC,
            "payload": {
                "uploadType": "contract",
                "clause_number": number,
                "clause_title": title,
                "page_start": pages[0],
                "page_end": pages[-1],
            },
        }


async def _contract_rows(db) -> List[Dict[str, Any]]:
    rows = await db.document_vectors.find({"document_id": DOC}, {"_id": 0}).to_list(length=None)
    return sorted(rows, key=lambda row: row["chunk_id"])


def test_generic_ingestion_leaves_an_unpromoted_contract_untouched(database):
    store = RecordingVectorStore()
    _unpromoted_contract(database, store)
    points_before = {key: dict(value) for key, value in store.points.items()}
    rows_before = database.run(lambda db, client: _contract_rows(db))
    assert (
        database.run(lambda db, client: db[CONTRACT_DOCUMENTS_COLLECTION].count_documents({}))
        == 0
    ), "the fixture must have no Contract Master record"

    job = _run_generic_job(database, store, DOC)

    # Clause points, their clause and page payloads, and the rows they came
    # from: exactly as contract ingest left them. Nothing generic was written.
    assert store.points == points_before
    assert database.run(lambda db, client: _contract_rows(db)) == rows_before
    assert database.run(lambda db, client: db.chunks.count_documents({})) == 0
    assert database.run(lambda db, client: db.vector_sync_status.count_documents({})) == 0
    assert job["status"] == "failed", job
    assert "contract" in (job.get("error") or "").lower(), job


def test_the_route_refuses_an_unpromoted_contract(database):
    store = RecordingVectorStore()
    _unpromoted_contract(database, store)
    client, submitted, policy = _route_client(database, store)

    response = client.post(
        "/api/v1/ingestion/jobs",
        json={"org_id": ORG, "project_id": PROJECT, "document_id": DOC},
    )

    assert response.status_code == 409, response.text
    assert "contract" in response.json()["detail"].lower()
    assert submitted == []
    assert policy.calls, "the refusal must come after authorisation, never before"


def test_both_generic_entry_checks_name_an_unpromoted_contract(database):
    """The reprocess route and the ingestion route ask these; a plain letter is
    still not a contract source."""
    store = RecordingVectorStore()
    _unpromoted_contract(database, store)

    async def ask(db, client):
        from rbac_backend.ingestion.service import IngestionService
        from rbac_backend.services.document_service import DocumentService

        await db["documents"].insert_one(
            {
                "_id": "plain-letter",
                "organization_id": ORG,
                "project_id": PROJECT,
                "uploadType": "incoming",
                "processing_status": "completed",
            }
        )
        ingestion = IngestionService.__new__(IngestionService)
        ingestion.db = db
        documents = DocumentService(db)
        return [
            await ingestion.is_governed_contract(DOC),
            await documents.is_governed_contract(DOC),
            await ingestion.is_governed_contract("plain-letter"),
            await documents.is_governed_contract("plain-letter"),
        ]

    assert database.run(ask) == [True, True, False, False]


# --------------------------------------------------------------------------- #
# Promotion racing the general reprocess path (DatabaseService)
# --------------------------------------------------------------------------- #


class _ReplacingStore:
    """LangChainVectorService.replace_document: every point of the document
    goes, the new ones arrive."""

    enabled = True

    def __init__(self, store: RecordingVectorStore) -> None:
        self.store = store
        self.calls = 0

    async def replace_document(self, payloads):
        self.calls += 1
        document_id = payloads[0]["payload"]["document_id"]
        doomed = [k for k, p in self.store.points.items() if p["document_id"] == document_id]
        for key in doomed:
            del self.store.points[key]
        for point in payloads:
            self.store.points[point["point_id"]] = {
                "vector": [0.0],
                "document_id": document_id,
                "payload": dict(point["payload"]),
            }
        return len(payloads)


class _IndexStub:
    embedding_model_name = "stub"

    async def delete_vectors(self, refs):
        return None

    async def index_chunks(self, payloads, persist=False):
        return []


LETTER_BODY = (
    "We refer to the instruction regarding the viaduct pier foundations and give "
    "notice of delay caused by late access to the railway corridor. "
) * 3


def _general_writer(db, store: RecordingVectorStore):
    from rbac_backend.config.document_processing_config import DocumentProcessingConfig
    from rbac_backend.services.database_service import DatabaseService

    config = DocumentProcessingConfig()
    config.vector_store_enabled = True
    config.vector_dual_write_enabled = True
    service = DatabaseService(config)
    service._db = db
    replacing = _ReplacingStore(store)
    service._langchain_vector_service = replacing
    service._langchain_service_initialized = True
    service._vector_service = _IndexStub()
    return service, replacing


async def _save(service):
    from rbac_backend.services.source_text import OCR_TEXT_KIND_SOURCE, SOURCE_TEXT_COMPLETE
    from rbac_backend.tests.correspondence_vector_harness import letter_metadata

    return await service.save_document_data(
        document_id=DOC,
        file_path="/uploads/Particular Conditions.pdf",
        parsed_metadata=letter_metadata(letter_no="L-1", subject="Delay", body=LETTER_BODY),
        full_text=LETTER_BODY,
        embedding_text=LETTER_BODY,
        source_provenance={
            "ocr_text_kind": OCR_TEXT_KIND_SOURCE,
            "source_text_status": SOURCE_TEXT_COMPLETE,
        },
    )


def test_a_promotion_during_a_general_reprocess_does_not_lose_contract_evidence(database):
    """G1 starts on a document that is not a contract source and parks just
    before its first destructive vector step (the awaited "pending" sync write,
    after its payloads are built). The document then becomes a contract and is
    promoted through the real migration path. G1 is released.

    Trusting its start-of-run answer, G1 replaced every point of the now
    governed document and deleted its document_vectors rows.
    """
    import asyncio

    from rbac_backend.tests.integration.test_contract_reprojection_runtime_mongo import (
        _promote_through_migration,
    )

    store = RecordingVectorStore()
    _unpromoted_contract(database, store)
    # The start-of-run checks must see a document that is not a contract.
    database.run(
        lambda db, client: db["documents"].update_one(
            {"_id": DOC}, {"$set": {"uploadType": "incoming"}}
        )
    )
    points_before = {key: dict(value) for key, value in store.points.items()}
    rows_before = database.run(lambda db, client: _contract_rows(db))

    async def scenario(db, client):
        reached, release = asyncio.Event(), asyncio.Event()
        service, replacing = _general_writer(db, store)
        record_status = service._update_vector_sync_status

        async def _parking_status(db_, document_id, *, status, **kwargs):
            if status == "pending" and not release.is_set():
                reached.set()
                await release.wait()
            return await record_status(db_, document_id, status=status, **kwargs)

        service._update_vector_sync_status = _parking_status

        g1 = asyncio.ensure_future(_save(service))
        await asyncio.wait_for(reached.wait(), timeout=60)
        await db["documents"].update_one({"_id": DOC}, {"$set": {"uploadType": "contract"}})
        await _promote_through_migration(db, client)
        release.set()
        try:
            outcome: Any = await g1
        except Exception as exc:
            outcome = exc
        return outcome, replacing.calls

    outcome, replace_calls = database.run(scenario)

    governed = database.run(
        lambda db, client: db[CONTRACT_DOCUMENTS_COLLECTION].count_documents({"document_id": DOC})
    )
    assert governed == 1, "the promotion must have committed during the race"
    assert replace_calls == 0, "a generic writer replaced the points of a document promoted under it"
    assert store.points == points_before
    assert database.run(lambda db, client: _contract_rows(db)) == rows_before
    assert isinstance(outcome, Exception), outcome
    assert "contract" in str(outcome).lower(), outcome


def test_the_general_writer_still_indexes_a_plain_letter(database):
    """Positive control for the write-boundary re-check."""
    store = RecordingVectorStore()

    async def run(db, client):
        await db["documents"].insert_one(
            {
                "_id": DOC,
                "organization_id": ORG,
                "project_id": PROJECT,
                "uploadType": "incoming",
                "filename": "Particular Conditions.pdf",
                "status": "completed",
                "processing_status": "completed",
                "publication_status": "published",
                "is_active": True,
            }
        )
        service, replacing = _general_writer(db, store)
        await _save(service)
        return replacing.calls

    assert database.run(run) == 1
    assert store.points and all(p["document_id"] == DOC for p in store.points.values())


# --------------------------------------------------------------------------- #
# The job worker's human-review exit purges what an earlier run published
# --------------------------------------------------------------------------- #


def _human_review(database, *, upload_type: str, governed: bool):
    """A job ends in human review for a document that, by then, is (or is not)
    a contract source. Returns what the purge and the status writes did."""
    from bson import ObjectId

    from rbac_backend.services.document_service import DocumentService

    oid = ObjectId()

    async def run(db, client):
        await db["documents"].insert_one(
            {
                "_id": oid,
                "organization_id": ORG,
                "project_id": PROJECT,
                "uploadType": upload_type,
                "processing_status": "processing",
                "processing_job_id": "job-hr",
                "status": "completed",
                "publication_status": "published",
                "is_active": True,
            }
        )
        await db.document_vectors.insert_many(
            [
                {"document_id": str(oid), "chunk_id": f"{oid}-clause-{n}", "uploadType": upload_type,
                 "page_start": n, "text": f"clause {n}"}
                for n in (1, 2)
            ]
        )
        if governed:
            await db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
                {"_id": f"cd-{oid}", "document_id": str(oid), "classification_revision": 1}
            )
        await db.document_processing_jobs.insert_one(
            {"_id": "job-hr", "document_id": str(oid), "status": "processing",
             "previous_processing_status": "queued"}
        )
        service = DocumentService(db)
        purged: List[str] = []

        async def _record_delete(raw_doc, doc_id):
            purged.append(doc_id)
            return True

        async def _no_graph(*_args, **_kwargs):
            return None

        service._delete_qdrant_vectors = _record_delete
        service._invalidate_graph_contribution = _no_graph
        await service._mark_human_review("job-hr", str(oid), {"remaining_page_numbers": [3]})
        return {
            "purged": purged,
            "rows": await db.document_vectors.count_documents({"document_id": str(oid)}),
            "document_status": (await db["documents"].find_one({"_id": oid}))["processing_status"],
            "job": await db.document_processing_jobs.find_one({"_id": "job-hr"}),
        }

    return database.run(run)


def test_human_review_never_purges_a_document_that_became_a_contract_source(database):
    """The run passed its entry checks as a plain letter; by the time it ends
    in human review the document is a promoted contract."""
    outcome = _human_review(database, upload_type="contract", governed=True)

    assert outcome["purged"] == [], "the contract's points were purged"
    assert outcome["rows"] == 2, "the contract's clause rows were deleted"
    # Handed back as the run found it - never "processing", which the contract
    # projection reads as an ingest in flight.
    assert outcome["document_status"] == "queued", outcome["document_status"]
    assert (outcome["job"]["status"], outcome["job"]["stage"]) == (
        "dead_lettered",
        "skipped_governed_contract",
    )


def test_human_review_still_purges_a_plain_letter(database):
    outcome = _human_review(database, upload_type="incoming", governed=False)

    assert len(outcome["purged"]) == 1
    assert outcome["rows"] == 0
    assert outcome["document_status"] == "human_review_required"


# --------------------------------------------------------------------------- #
# The job runner's end-of-run status writes
# --------------------------------------------------------------------------- #
#
# Every status a general processing job writes when it ends - completed,
# retrying/failed, resumed, human review - was decided by a contract-source
# check made at its start. A document that became a contract source while the
# job worked kept "processing" (which the contract projection reads as an
# ingest in flight, and nothing would clear), or had a contract extraction
# hold overwritten by "retrying"/"completed", which made unread pages
# consumable.


def _run_job_into_a_promotion(database, outcome: str, *, hold: bool = False):
    from bson import ObjectId

    from rbac_backend.services.document_service import DocumentService

    oid = ObjectId()
    job_id = f"job-{oid}"

    async def run(db, client):
        await db["documents"].insert_one(
            {
                "_id": oid,
                "organization_id": ORG,
                "project_id": PROJECT,
                "uploadType": "incoming",
                "processing_status": "queued",
                "processing_job_id": job_id,
                "status": "completed",
                "publication_status": "published",
                "is_active": True,
            }
        )
        await db.document_processing_jobs.insert_one(
            {
                "_id": job_id,
                "document_id": str(oid),
                "organization_id": ORG,
                "project_id": PROJECT,
                "file_path": "/uploads/x.pdf",
                "status": "queued",
                "attempts": 0,
                "max_attempts": 3,
            }
        )
        service = DocumentService(db)

        async def _becomes_a_contract(**_kwargs):
            # While the job extracts: the upload type is edited and Contract
            # Master adopts the document; a contract ingest may hold it.
            update = {"uploadType": "contract"}
            if hold:
                update.update(
                    processing_status="human_review_required",
                    processing_error={"source": "contract_extraction", "pages": [4]},
                )
            await db["documents"].update_one({"_id": oid}, {"$set": update})
            await db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
                {"_id": f"cd-{oid}", "document_id": str(oid), "classification_revision": 1}
            )
            if outcome == "raises":
                raise RuntimeError("extraction blew up")
            return outcome == "completed"

        service.process_document_async = _becomes_a_contract
        await service.process_document_job(job_id)
        return (
            await db["documents"].find_one({"_id": oid}),
            await db.document_processing_jobs.find_one({"_id": job_id}),
        )

    return database.run(run)


@pytest.mark.parametrize("outcome", ["completed", "failed", "raises"])
def test_a_job_that_ends_on_a_contract_source_hands_the_status_back(database, outcome):
    document, job = _run_job_into_a_promotion(database, outcome)

    assert (job["status"], job["stage"]) == ("dead_lettered", "skipped_governed_contract"), job
    # Not "processing" (an ingest in flight to the projection, for ever), not
    # this run's verdict: the status the run found.
    assert document["processing_status"] == "queued", document


@pytest.mark.parametrize("outcome", ["completed", "failed", "raises"])
def test_a_job_never_lifts_a_contract_extraction_hold(database, outcome):
    document, job = _run_job_into_a_promotion(database, outcome, hold=True)

    assert job["status"] == "dead_lettered", job
    assert document["processing_status"] == "human_review_required", document
    assert document["processing_error"]["source"] == "contract_extraction", document


def test_a_plain_letter_job_still_records_its_failure(database):
    from bson import ObjectId

    from rbac_backend.services.document_service import DocumentService

    oid = ObjectId()

    async def run(db, client):
        await db["documents"].insert_one(
            {"_id": oid, "organization_id": ORG, "project_id": PROJECT, "uploadType": "incoming",
             "processing_status": "queued", "status": "completed", "is_active": True}
        )
        await db.document_processing_jobs.insert_one(
            {"_id": "job-plain", "document_id": str(oid), "file_path": "/uploads/x.pdf",
             "status": "queued", "attempts": 0, "max_attempts": 3}
        )
        service = DocumentService(db)

        async def _fails(**_kwargs):
            raise RuntimeError("extraction blew up")

        service.process_document_async = _fails
        await service.process_document_job("job-plain")
        return (
            await db["documents"].find_one({"_id": oid}),
            await db.document_processing_jobs.find_one({"_id": "job-plain"}),
        )

    document, job = database.run(run)
    assert job["status"] == "retrying", job
    assert document["processing_status"] == "retrying", document


# --------------------------------------------------------------------------- #
# process_document_async's own terminal write (the no-job reprocess path, and
# the job path before the runner's end-of-run step)
# --------------------------------------------------------------------------- #


def _reprocess_into_a_promotion(database, monkeypatch, tmp_path, outcome: str, *, hold: bool, with_job: bool):
    from types import SimpleNamespace

    from bson import ObjectId

    from rbac_backend.services import document_service as document_service_module
    from rbac_backend.services.document_service import DocumentService
    from rbac_backend.tests.correspondence_vector_harness import correspondence_document

    source = tmp_path / "letter.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    row = correspondence_document(
        organization_id=ORG,
        project_id=PROJECT,
        letter_no="L/RACE",
        subject="Race",
        processing_status="completed",
        filepath_local=str(source),
    )
    oid = row["_id"]
    job_id = f"job-{ObjectId()}" if with_job else None

    class _RacingProcessor:
        """Stands in for OCR/extraction: while it runs the document becomes a
        promoted contract (and, optionally, a contract ingest holds it)."""

        def __init__(self, db):
            self.db = db

        async def process_document(self, **_kwargs):
            update: Dict[str, Any] = {"uploadType": "contract"}
            if hold:
                update.update(
                    processing_status="human_review_required",
                    processing_error={"source": "contract_extraction", "pages": [4]},
                )
            await self.db["documents"].update_one({"_id": oid}, {"$set": update})
            await self.db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
                {"_id": f"cd-{oid}", "document_id": str(oid), "classification_revision": 1}
            )
            if outcome == "raises":
                raise RuntimeError("extraction blew up")
            return SimpleNamespace(success=False, error="refused: a contract source", metadata=None)

    async def run(db, client):
        await db["documents"].insert_one(row)
        if with_job:
            await db.document_processing_jobs.insert_one(
                {"_id": job_id, "document_id": str(oid), "file_path": str(source),
                 "status": "queued", "attempts": 0, "max_attempts": 3}
            )
        service = DocumentService(db)
        monkeypatch.setattr(
            document_service_module, "create_document_processor", lambda: _RacingProcessor(db)
        )
        if with_job:
            await service.process_document_job(job_id)
        else:
            await service.process_document_async(document_id=str(oid), file_path=str(source))
        return await db["documents"].find_one({"_id": oid})

    return database.run(run)


@pytest.mark.parametrize("outcome", ["failed", "raises"])
def test_a_no_job_reprocess_hands_a_contract_source_back(database, monkeypatch, tmp_path, outcome):
    document = _reprocess_into_a_promotion(
        database, monkeypatch, tmp_path, outcome, hold=False, with_job=False
    )
    # Not left "processing" (an ingest in flight to the projection, with no job
    # or recovery ever to clear it), not this run's failure: what it found.
    assert document["processing_status"] == "completed", document


@pytest.mark.parametrize("with_job", [False, True], ids=["no_job", "job"])
@pytest.mark.parametrize("outcome", ["failed", "raises"])
def test_a_reprocess_never_erases_a_contract_extraction_hold(
    database, monkeypatch, tmp_path, outcome, with_job
):
    document = _reprocess_into_a_promotion(
        database, monkeypatch, tmp_path, outcome, hold=True, with_job=with_job
    )
    assert document["processing_status"] == "human_review_required", document
    assert document["processing_error"]["source"] == "contract_extraction", document


def test_the_route_answers_422_for_an_unselectable_vector_namespace(database):
    """``options.vector_namespace`` is request input; only the configured
    default may be selected. Anything else is a validation error, before any
    job exists (and so before any vector write)."""
    store = RecordingVectorStore()

    async def seed(db, client):
        await db["documents"].insert_one(
            {
                "_id": "plain-doc",
                "organization_id": ORG,
                "project_id": PROJECT,
                "status": "completed",
                "processing_status": "completed",
                "publication_status": "published",
                "is_active": True,
                "ocrText": SOURCE_TEXT,
            }
        )

    database.run(seed)
    client, submitted, _policy = _route_client(database, store)

    for namespace in ("contract_clauses", "attacker-namespace", "", " document_vectors"):
        response = client.post(
            "/api/v1/ingestion/jobs",
            json={
                "org_id": ORG,
                "project_id": PROJECT,
                "document_id": "plain-doc",
                "options": {"vector_namespace": namespace},
            },
        )
        assert response.status_code == 422, (namespace, response.text)
    assert submitted == []
    assert database.run(lambda db, client: db.ingestion_jobs.count_documents({})) == 0
    assert store.points == {}

    # Positive control: omitted, the same request is accepted.
    response = client.post(
        "/api/v1/ingestion/jobs",
        json={"org_id": ORG, "project_id": PROJECT, "document_id": "plain-doc"},
    )
    assert response.status_code == 200, response.text
