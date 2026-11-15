"""G31 IngestionPipeline direct-Qdrant publication authority regressions.

The public service seam is ``IngestionPipeline.process_job``.  The actual
pipeline loads, gates, chunks, embeds and persists; Mongo, the embedding model,
observability and Qdrant are persistent system-boundary fakes.  Assertions read
final Qdrant and Mongo chunk state rather than internal call counts.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from pymongo import ReplaceOne

from rbac_backend.ingestion.models import IngestionJobCreate, IngestionOptions
from rbac_backend.ingestion.pipeline import IngestionPipeline


MARKER = "INGESTION_PIPELINE_BLOCKED_MARKER_20260819"
SOURCE_TEXT = (
    f"{MARKER} viaduct alignment drawings and signalling interface approvals. "
    * 24
)
DOCUMENT_ID = "ingestion-authority-document"

STATIC_AUTHORITY_MATRIX = [
    ("clean", {}, True),
    ("operational_failed", {"processing_status": "failed"}, True),
    (
        "human_review",
        {"processing_status": "human_review_required"},
        False,
    ),
    ("duplicate_status", {"duplicate_status": "duplicate"}, False),
    ("duplicate_lifecycle", {"lifecycle_state": "duplicate"}, False),
    ("deleted", {"lifecycle_state": "deleted"}, False),
    ("missing_canonical", None, False),
]

TOCTOU_AUTHORITY_MATRIX = [
    ("human_review", {"processing_status": "human_review_required"}),
    ("duplicate", {"duplicate_status": "duplicate"}),
    ("deleted", {"lifecycle_state": "deleted"}),
    ("missing_canonical", None),
]


def _matches(document, query) -> bool:
    for key, expected in query.items():
        actual = document.get(key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, documents, query) -> None:
        self.documents = documents
        self.query = query

    def __aiter__(self):
        async def iterate():
            for document in list(self.documents):
                if _matches(document, self.query):
                    yield deepcopy(document)

        return iterate()


class _Collection:
    def __init__(self, documents=()) -> None:
        self.documents = [deepcopy(document) for document in documents]

    async def insert_one(self, document):
        self.documents.append(deepcopy(document))
        return type("InsertResult", (), {"inserted_id": document.get("_id")})()

    async def find_one(self, query, *_args, **_kwargs):
        for document in self.documents:
            if _matches(document, query):
                return deepcopy(document)
        return None

    def find(self, query, *_args, **_kwargs):
        return _Cursor(self.documents, query)

    async def update_one(self, query, update, upsert=False):
        for document in self.documents:
            if _matches(document, query):
                document.update(deepcopy(update.get("$set", {})))
                return type(
                    "UpdateResult",
                    (),
                    {"matched_count": 1, "modified_count": 1, "upserted_id": None},
                )()
        if upsert:
            inserted = {
                **query,
                **deepcopy(update.get("$setOnInsert", {})),
                **deepcopy(update.get("$set", {})),
            }
            self.documents.append(inserted)
            return type(
                "UpdateResult",
                (),
                {"matched_count": 0, "modified_count": 0, "upserted_id": inserted.get("_id")},
            )()
        return type(
            "UpdateResult",
            (),
            {"matched_count": 0, "modified_count": 0, "upserted_id": None},
        )()

    async def bulk_write(self, operations, ordered=False):
        assert ordered is False
        for operation in operations:
            assert isinstance(operation, ReplaceOne)
            for index, document in enumerate(self.documents):
                if _matches(document, operation._filter):
                    self.documents[index] = deepcopy(operation._doc)
                    break
            else:
                if operation._upsert:
                    self.documents.append(deepcopy(operation._doc))
        return type("BulkResult", (), {})()

    async def delete_many(self, query):
        retained = [document for document in self.documents if not _matches(document, query)]
        deleted = len(self.documents) - len(retained)
        self.documents = retained
        return type("DeleteResult", (), {"deleted_count": deleted})()


class _Database:
    def __init__(self, documents=()) -> None:
        self.documents = _Collection(documents)
        self.ingestion_jobs = _Collection()
        self.chunks = _Collection()
        self.vector_sync_status = _Collection()
        self.contract_documents = _Collection()


class _PersistentQdrant:
    enabled = True
    # The job names it: a request may select only the configured default.
    default_collection = "authority-test"

    def __init__(self) -> None:
        self.config = type("Config", (), {"qdrant_vector_size": 2})()
        self.points: dict[str, dict] = {}

    async def upsert(self, vectors, chunks, namespace=None, point_id_for=None) -> int:
        for vector, chunk in zip(vectors, chunks):
            self.points[str(chunk["chunk_id"])] = {
                "vector": deepcopy(vector),
                "payload": deepcopy(chunk),
                "namespace": namespace,
            }
        return len(chunks)

    async def list_chunk_ids(self, filters, namespace=None, limit=1000):
        return [
            point_id
            for point_id, point in self.points.items()
            if point["namespace"] == namespace
            and all(point["payload"].get(key) == value for key, value in filters.items())
        ][:limit]

    async def delete(self, chunk_ids, namespace=None, point_id_for=None) -> int:
        deleted = 0
        for chunk_id in list(chunk_ids):
            point = self.points.get(str(chunk_id))
            if point is not None and point["namespace"] == namespace:
                self.points.pop(str(chunk_id))
                deleted += 1
        return deleted

    def document_points(self, document_id: str) -> list[dict]:
        return [
            point
            for point in self.points.values()
            if str(point["payload"].get("document_id")) == str(document_id)
        ]

    def marker_present(self, document_id: str) -> bool:
        return any(
            MARKER in str(point["payload"].get("text") or "")
            for point in self.document_points(document_id)
        )


class _EmbeddingBoundary:
    def __init__(self, transition=None, fail_once=False) -> None:
        self.transition = transition
        self.fail_once = fail_once

    async def embed(self, texts, model=None):
        if self.transition is not None:
            transition, self.transition = self.transition, None
            transition()
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("deterministic embedding boundary failure")
        return [[float(len(text)), 1.0] for text in texts]


class _ObservabilityBoundary:
    async def log_run(self, **_kwargs):
        return None


def _document(**overrides):
    document = {
        "_id": DOCUMENT_ID,
        "organization_id": "org-authority",
        "project_id": "project-authority",
        "processing_status": "completed",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
        "full_text": SOURCE_TEXT,
    }
    document.update(overrides)
    return document


async def _pipeline_job(db: _Database, embedding: _EmbeddingBoundary):
    qdrant = _PersistentQdrant()
    pipeline = IngestionPipeline(
        db=db,
        embedding_client=embedding,
        vector_client=qdrant,
        observability_service=_ObservabilityBoundary(),
    )
    job = await pipeline.create_job(
        IngestionJobCreate(
            org_id="org-authority",
            project_id="project-authority",
            document_id=DOCUMENT_ID,
            options=IngestionOptions(
                chunk_size=400,
                chunk_overlap=0,
                embedding_dim=2,
                vector_namespace="authority-test",
            ),
        )
    )
    return pipeline, job, qdrant


def _mongo_chunk_count(db: _Database) -> int:
    return sum(
        str(chunk.get("document_id")) == DOCUMENT_ID
        for chunk in db.chunks.documents
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "authority", "should_publish"),
    STATIC_AUTHORITY_MATRIX,
    ids=[case for case, _authority, _expected in STATIC_AUTHORITY_MATRIX],
)
async def test_process_job_publishes_only_static_authoritative_documents(
    case: str,
    authority: dict | None,
    should_publish: bool,
):
    documents = [] if authority is None else [_document(**authority)]
    db = _Database(documents)
    pipeline, job, qdrant = await _pipeline_job(db, _EmbeddingBoundary())

    await pipeline.process_job(job.id)

    assert qdrant.marker_present(DOCUMENT_ID) is should_publish, case
    assert (len(qdrant.document_points(DOCUMENT_ID)) > 0) is should_publish, case
    assert (_mongo_chunk_count(db) > 0) is should_publish, case


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "new_authority"),
    TOCTOU_AUTHORITY_MATRIX,
    ids=[case for case, _authority in TOCTOU_AUTHORITY_MATRIX],
)
async def test_process_job_rechecks_current_authority_after_embedding_before_publication(
    case: str,
    new_authority: dict | None,
):
    db = _Database([_document()])

    def change_current_authority() -> None:
        if new_authority is None:
            db.documents.documents.clear()
        else:
            db.documents.documents[0].update(deepcopy(new_authority))

    pipeline, job, qdrant = await _pipeline_job(
        db,
        _EmbeddingBoundary(transition=change_current_authority),
    )

    await pipeline.process_job(job.id)

    assert qdrant.marker_present(DOCUMENT_ID) is False, case
    assert len(qdrant.document_points(DOCUMENT_ID)) == 0, case
    assert _mongo_chunk_count(db) == 0, case


@pytest.mark.asyncio
async def test_same_job_reentry_does_not_publish_after_authority_becomes_denied():
    db = _Database([_document()])
    embedding = _EmbeddingBoundary(fail_once=True)
    pipeline, job, qdrant = await _pipeline_job(db, embedding)

    await pipeline.process_job(job.id)
    db.documents.documents[0]["processing_status"] = "human_review_required"

    await pipeline.process_job(job.id)

    assert qdrant.marker_present(DOCUMENT_ID) is False
    assert len(qdrant.document_points(DOCUMENT_ID)) == 0
    assert _mongo_chunk_count(db) == 0
