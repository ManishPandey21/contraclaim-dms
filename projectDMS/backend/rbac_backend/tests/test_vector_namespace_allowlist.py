"""A caller may select only the configured default vector namespace.

``POST /ingestion/jobs`` took ``options.vector_namespace`` from the request
body, and the superadmin ``POST /admin/vector/reconcile`` a ``namespace``
query. Both flowed into ``VectorClient``, whose upsert creates a missing
collection: an upload-authorised user could create and fill arbitrary Qdrant
collections, including Contract Master's internal ones. No product caller
sets a namespace (``git grep vector_namespace``: the model field, the
pipeline's three uses, one test), so the configured default - omitted, or
named exactly - is the only selectable one.

Real Qdrant (``CORRESPONDENCE_QDRANT_TEST_URL``) is the point; the local
in-memory client runs every case too.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest
from bson import ObjectId
from fastapi import HTTPException

from rbac_backend.ingestion.models import IngestionJob, IngestionJobCreate, IngestionOptions
from rbac_backend.ingestion.pipeline import IngestionPipeline
from rbac_backend.retrieval.namespaces import UnsupportedVectorNamespace
from rbac_backend.retrieval.reconcile import VectorReconciler
from rbac_backend.services.source_text import OCR_TEXT_KIND_SOURCE
from rbac_backend.tests.correspondence_vector_harness import (
    Database,
    DeterministicEmbeddingClient,
    correspondence_document,
)
from rbac_backend.tests.test_vector_namespace_isolation import BACKENDS, Store

ORG, PROJECT = str(ObjectId()), str(ObjectId())
LETTER = (
    "Dear Sir, we give notice of delay to the viaduct pier foundations caused "
    "by late access to the railway corridor. "
) * 3


@pytest.fixture(params=BACKENDS)
def store(request):
    built = Store(request.param)
    yield built
    built.drop()


class _Observability:
    async def log_run(self, **_kwargs: Any) -> None:
        return None


def _letter() -> Dict[str, Any]:
    return correspondence_document(
        organization_id=ORG, project_id=PROJECT, letter_no="L/1", subject="Delay",
        processing_status="completed", status="completed", publication_status="published",
        is_active=True, ocrText=LETTER, ocr_text_kind=OCR_TEXT_KIND_SOURCE,
    )


def _pipeline(store: Store, db: Database) -> IngestionPipeline:
    return IngestionPipeline(
        db=db,
        embedding_client=DeterministicEmbeddingClient(),
        vector_client=store.vc,
        observability_service=_Observability(),
    )


def _payload(document_id: str, namespace: Any = None, omit: bool = False) -> IngestionJobCreate:
    options = IngestionOptions() if omit else IngestionOptions(vector_namespace=namespace)
    return IngestionJobCreate(org_id=ORG, project_id=PROJECT, document_id=document_id, options=options)


def _ingest(store: Store, payload_for) -> tuple[Dict[str, Any], Database, str]:
    document = _letter()
    document_id = str(document["_id"])
    db = Database([document])
    pipeline = _pipeline(store, db)

    async def go():
        job = await pipeline.create_job(payload_for(document_id))
        await pipeline.process_job(job.id)
        return await db.ingestion_jobs.find_one({"job_id": job.id})

    return asyncio.run(go()), db, document_id


# --- A / B: the default is selectable, omitted or named ------------------------------------


def test_an_omitted_namespace_ingests_into_the_default(store: Store) -> None:
    job, db, document_id = _ingest(store, lambda d: _payload(d, omit=True))
    assert job["status"] == "done", job
    rows = sorted(c["chunk_id"] for c in db.chunks._docs.values())
    assert rows and store.chunk_ids(store.default) == rows


def test_the_default_named_exactly_ingests_into_the_default(store: Store) -> None:
    job, db, _ = _ingest(store, lambda d: _payload(d, store.default))
    assert job["status"] == "done", job
    rows = sorted(c["chunk_id"] for c in db.chunks._docs.values())
    assert rows and store.chunk_ids(store.default) == rows
    # Stored as the default itself, not as a name.
    assert (job.get("options") or {}).get("vector_namespace") is None


# --- C / D / E / F: anything else is refused before Qdrant ------------------------------------


def _refused_names(store: Store) -> List[str]:
    return [
        "attacker-namespace",
        "contract_clauses",  # the clause embedder's internal namespace
        "document_vectors",  # Contract Master's evidence namespace (not this default)
        store.default.upper(),
        f" {store.default}",
        f"{store.default} ",
        f"{store.default}\n",
        f"{store.default}\t",
        "",
    ]


def test_an_unsupported_namespace_is_refused_before_any_job_or_collection(store: Store) -> None:
    for requested in _refused_names(store):
        document = _letter()
        db = Database([document])
        pipeline = _pipeline(store, db)
        before = store.collection_names()

        with pytest.raises(UnsupportedVectorNamespace):
            asyncio.run(pipeline.create_job(_payload(str(document["_id"]), requested)))

        assert db.ingestion_jobs._docs == {}, requested
        assert store.collection_names() == before, requested


def test_a_stored_job_with_an_unsupported_namespace_fails_without_touching_qdrant(
    store: Store,
) -> None:
    """A job queued before the allowlist (or written around it) is refused when
    it runs: failed, with nothing written and no collection created."""
    document = _letter()
    document_id = str(document["_id"])
    db = Database([document])
    pipeline = _pipeline(store, db)
    job = IngestionJob(
        org_id=ORG, project_id=PROJECT, document_id=document_id,
        options=IngestionOptions(vector_namespace="contract_clauses"),
    )
    asyncio.run(db.ingestion_jobs.insert_one(job.model_dump(by_alias=True, exclude_none=True)))
    before = store.collection_names()

    asyncio.run(pipeline.process_job(job.id))

    stored = asyncio.run(db.ingestion_jobs.find_one({"job_id": job.id}))
    assert stored["status"] == "failed", stored
    assert "namespace" in (stored.get("error") or ""), stored
    assert store.collection_names() == before
    assert db.chunks._docs == {}


def test_the_reconciler_refuses_an_unsupported_namespace(store: Store) -> None:
    document = _letter()
    db = Database([document])
    reconciler = VectorReconciler(db, DeterministicEmbeddingClient(), store.vc)
    before = store.collection_names()

    for requested in _refused_names(store):
        with pytest.raises(UnsupportedVectorNamespace):
            asyncio.run(
                reconciler.reconcile_document(str(document["_id"]), ORG, PROJECT, namespace=requested)
            )
    assert store.collection_names() == before


# --- the HTTP answers ---------------------------------------------------------------------------


def test_the_reconcile_route_answers_422(store: Store) -> None:
    from types import SimpleNamespace

    from rbac_backend.routers.retrieval_engine import reconcile_vectors

    document = _letter()
    reconciler = VectorReconciler(Database([document]), DeterministicEmbeddingClient(), store.vc)
    superadmin = SimpleNamespace(id="root", roles=["superadmin"])
    before = store.collection_names()

    with pytest.raises(HTTPException) as refused:
        asyncio.run(
            reconcile_vectors(
                document_id=str(document["_id"]), org_id=ORG, project_id=PROJECT,
                namespace="attacker-namespace", reconciler=reconciler, current_user=superadmin,
            )
        )
    assert refused.value.status_code == 422
    assert store.collection_names() == before
