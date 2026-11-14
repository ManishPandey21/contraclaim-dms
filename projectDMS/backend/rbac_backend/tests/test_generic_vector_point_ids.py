"""Generic chunk vectors get a valid, deterministic Qdrant point id.

A generic chunk's logical id is ``<document>-<digest>``. The ingestion
pipeline, the vector reconciler and storage-sync's chunk repair used it as the
Qdrant point id, which real Qdrant rejects (400) on upsert and on delete: on a
real store those paths published nothing and pruned nothing, and the errors
were logged. They now write and delete under ``generic_chunk_point_id`` - the
logical id stays in the payload and in ``chunks`` rows.

Contract writers are untouched: they call without ``point_id_for`` and keep
their own ids (UUID5 for the projection and contract ingest; the clause
embedder's SHA-1 ``clause_uid`` is a separate, recorded residual).

Real Qdrant (``CORRESPONDENCE_QDRANT_TEST_URL``) is the point; the in-memory
client runs the cases it can.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

import pytest
from bson import ObjectId

from rbac_backend.ingestion.chunk_ids import deterministic_chunk_id
from rbac_backend.retrieval.point_ids import generic_chunk_point_id
from rbac_backend.services.source_text import OCR_TEXT_KIND_SOURCE
from rbac_backend.tests.correspondence_vector_harness import (
    QDRANT_BACKENDS,
    Database,
    DeterministicEmbeddingClient,
    QdrantHarness,
    correspondence_document,
    embed_text,
    run,
)

ORG, PROJECT = str(ObjectId()), str(ObjectId())
LETTER = (
    "Dear Sir, we give notice of delay to the viaduct pier foundations caused "
    "by late access to the railway corridor. "
) * 3


@pytest.fixture(params=QDRANT_BACKENDS)
def harness(request, monkeypatch):
    built = QdrantHarness(request.param, monkeypatch)
    yield built
    built.drop()


def _points(harness: QdrantHarness, document_id: str) -> List[Any]:
    return [p for p in harness.scroll() if (p.payload or {}).get("document_id") == document_id]


def _chunk(document_id: str, chunk_id: str, text: str = LETTER) -> Dict[str, Any]:
    return {"chunk_id": chunk_id, "document_id": document_id, "org_id": ORG,
            "project_id": PROJECT, "text": text}


# --- the identity itself -----------------------------------------------------------


def test_a_logical_chunk_id_is_not_a_qdrant_point_id(harness: QdrantHarness) -> None:
    """The proof the fix rests on: real Qdrant refuses the logical id."""
    if harness.backend != "server":
        pytest.skip("only a real Qdrant validates point ids this way")
    document_id = str(ObjectId())
    logical = deterministic_chunk_id(document_id, 0, None, LETTER)
    models = harness.writer._qdrant_models
    with pytest.raises(Exception) as refused:
        harness.client.upsert(
            collection_name=harness.config.qdrant_collection,
            points=[models.PointStruct(id=logical, vector=embed_text(LETTER), payload={})],
            wait=True,
        )
    assert "400" in str(refused.value) or "format" in str(refused.value).lower()


def test_the_mapping_is_deterministic_valid_and_collision_free() -> None:
    document_id = str(ObjectId())
    first = deterministic_chunk_id(document_id, 0, None, "a")
    second = deterministic_chunk_id(document_id, 1, None, "b")
    assert generic_chunk_point_id(first) == generic_chunk_point_id(first)
    assert generic_chunk_point_id(first) != generic_chunk_point_id(second)
    assert str(uuid.UUID(generic_chunk_point_id(first))) == generic_chunk_point_id(first)
    assert generic_chunk_point_id(first) != first


# --- the writer and the delete --------------------------------------------------------


def test_a_generic_upsert_writes_one_mapped_point_that_keeps_its_logical_id(
    harness: QdrantHarness,
) -> None:
    document_id = str(ObjectId())
    logical = deterministic_chunk_id(document_id, 0, None, LETTER)

    for _ in range(2):  # the second write updates the same physical point
        written = run(
            harness.reader.upsert(
                [embed_text(LETTER)], [_chunk(document_id, logical)],
                point_id_for=generic_chunk_point_id,
            )
        )
        assert written == 1

    [point] = _points(harness, document_id)
    assert str(point.id) == generic_chunk_point_id(logical)
    assert point.payload["chunk_id"] == logical


def test_a_generic_delete_removes_exactly_the_mapped_point(harness: QdrantHarness) -> None:
    document_id = str(ObjectId())
    keep = deterministic_chunk_id(document_id, 0, None, "keep")
    drop = deterministic_chunk_id(document_id, 1, None, "drop")
    run(
        harness.reader.upsert(
            [embed_text("keep"), embed_text("drop")],
            [_chunk(document_id, keep, "keep"), _chunk(document_id, drop, "drop")],
            point_id_for=generic_chunk_point_id,
        )
    )

    run(harness.reader.delete([drop], point_id_for=generic_chunk_point_id))

    assert [p.payload["chunk_id"] for p in _points(harness, document_id)] == [keep]


def test_tenant_filters_read_the_mapped_points_unchanged(harness: QdrantHarness) -> None:
    document_id = str(ObjectId())
    logical = deterministic_chunk_id(document_id, 0, None, LETTER)
    run(
        harness.reader.upsert(
            [embed_text(LETTER)], [_chunk(document_id, logical)],
            point_id_for=generic_chunk_point_id,
        )
    )

    own = {"org_id": ORG, "project_id": PROJECT, "document_id": document_id}
    foreign = {"org_id": str(ObjectId()), "project_id": PROJECT, "document_id": document_id}
    assert run(harness.reader.list_chunk_ids(own)) == [logical]
    assert run(harness.reader.list_chunk_ids(foreign)) == []


def test_a_contract_style_write_keeps_its_own_uuid5_point_id(harness: QdrantHarness) -> None:
    """Without ``point_id_for`` the chunk id is the point id, as before - the
    contract projection and contract ingest write exactly as they did."""
    document_id = str(ObjectId())
    contract_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"upload:clause-1:0:{document_id}"))
    run(harness.reader.upsert([embed_text(LETTER)], [_chunk(document_id, contract_id)]))

    [point] = _points(harness, document_id)
    assert str(point.id) == contract_id
    assert point.payload["chunk_id"] == contract_id


# --- the three generic writers, end to end ---------------------------------------------


class _Observability:
    async def log_run(self, **_kwargs: Any) -> None:
        return None


def _letter() -> Dict[str, Any]:
    return correspondence_document(
        organization_id=ORG, project_id=PROJECT, letter_no="L/1", subject="Delay",
        processing_status="completed", status="completed", publication_status="published",
        is_active=True, ocrText=LETTER, ocr_text_kind=OCR_TEXT_KIND_SOURCE,
    )


def test_the_ingestion_pipeline_publishes_and_prunes_on_qdrant(harness: QdrantHarness) -> None:
    from rbac_backend.ingestion.models import IngestionJobCreate
    from rbac_backend.ingestion.pipeline import IngestionPipeline

    document = _letter()
    document_id = str(document["_id"])
    db = Database([document])
    pipeline = IngestionPipeline(
        db=db,
        embedding_client=DeterministicEmbeddingClient(),
        vector_client=harness.reader,
        observability_service=_Observability(),
    )

    async def ingest():
        job = await pipeline.create_job(
            IngestionJobCreate(org_id=ORG, project_id=PROJECT, document_id=document_id)
        )
        await pipeline.process_job(job.id)
        return await db.ingestion_jobs.find_one({"job_id": job.id})

    job = run(ingest())
    assert job["status"] == "done", job
    rows = {c["chunk_id"] for c in db.chunks._docs.values()}
    points = _points(harness, document_id)
    assert rows and {p.payload["chunk_id"] for p in points} == rows
    assert {str(p.id) for p in points} == {generic_chunk_point_id(c) for c in rows}

    # New text: the old chunks' points are stale and the prune removes them.
    run(db.documents.update_one({"_id": document["_id"]}, {"$set": {"ocrText": "Revised. " + LETTER}}))
    job = run(ingest())
    assert job["status"] == "done", job
    rows_now = {c["chunk_id"] for c in db.chunks._docs.values()}
    assert {p.payload["chunk_id"] for p in _points(harness, document_id)} == rows_now


def test_the_vector_reconciler_publishes_and_removes_on_qdrant(harness: QdrantHarness) -> None:
    from rbac_backend.retrieval.reconcile import VectorReconciler

    document = _letter()
    document_id = str(document["_id"])
    db = Database([document])
    missing = deterministic_chunk_id(document_id, 0, None, LETTER)
    orphan = deterministic_chunk_id(document_id, 9, None, "gone")
    run(db.chunks.insert_one({"chunk_id": missing, "document_id": document_id, "org_id": ORG,
                              "project_id": PROJECT, "chunk_index": 0, "text_original": LETTER}))
    run(
        harness.reader.upsert(
            [embed_text("gone")], [_chunk(document_id, orphan, "gone")],
            point_id_for=generic_chunk_point_id,
        )
    )

    result = run(
        VectorReconciler(db, DeterministicEmbeddingClient(), harness.reader).reconcile_document(
            document_id, ORG, PROJECT
        )
    )

    assert (result["repaired"], result["removed"]) == (1, 1), result
    [point] = _points(harness, document_id)
    assert (str(point.id), point.payload["chunk_id"]) == (generic_chunk_point_id(missing), missing)


def test_storage_sync_chunk_repair_publishes_on_qdrant(harness: QdrantHarness) -> None:
    from rbac_backend.routers import storage_sync

    document = _letter()
    document_id = str(document["_id"])
    db = Database([document])
    logical = deterministic_chunk_id(document_id, 0, None, LETTER)
    run(db.chunks.insert_one({"chunk_id": logical, "document_id": document_id, "org_id": ORG,
                              "project_id": PROJECT, "chunk_index": 0, "text_original": LETTER}))

    result = run(
        storage_sync._resync_document_vectors(
            document_id, db, harness.config, DeterministicEmbeddingClient(), harness.reader
        )
    )

    assert result["status"] == "synced", result
    [point] = _points(harness, document_id)
    assert (str(point.id), point.payload["chunk_id"]) == (generic_chunk_point_id(logical), logical)
