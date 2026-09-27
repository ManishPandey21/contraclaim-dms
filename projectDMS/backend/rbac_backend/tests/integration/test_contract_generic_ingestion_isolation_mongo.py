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
        # Hide the instrument for the entry check, restore it while embedding.
        await db[CONTRACT_DOCUMENTS_COLLECTION].delete_many({})

        class _RestoringEmbedder(_Embedder):
            async def embed(self, texts, model=None, **kwargs):
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
