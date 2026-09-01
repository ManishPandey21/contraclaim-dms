"""G31 normal document-embedding publication authority regressions.

The production seam is ``DatabaseService.save_document_data``: it persists the
canonical OCR/metadata update and then invokes the normal embedding writer.
Mongo and Qdrant are persistent boundary fakes so the assertions observe the
final stored vector state, not helper calls.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable, Iterable

import pytest
from bson import ObjectId

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.database_service import DatabaseService


MARKER = "NORMAL_EMBEDDING_BLOCKED_MARKER_20260819"
SOURCE_TEXT = (
    f"{MARKER} viaduct alignment drawings and signalling interface approvals. "
    * 24
)

AUTHORITY_MATRIX = [
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
]

TOCTOU_MATRIX = [
    ("human_review", {"processing_status": "human_review_required"}),
    ("duplicate", {"duplicate_status": "duplicate"}),
    ("deleted", {"lifecycle_state": "deleted"}),
]


def _normal(value: Any) -> Any:
    return str(value) if isinstance(value, ObjectId) else value


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    return all(_normal(document.get(key)) == _normal(expected) for key, expected in query.items())


class _Cursor:
    def __init__(self, documents: Iterable[dict[str, Any]]) -> None:
        self.documents = [deepcopy(document) for document in documents]

    async def to_list(self, length=None):
        return deepcopy(self.documents if length is None else self.documents[:length])


class _Collection:
    def __init__(self, documents: Iterable[dict[str, Any]] = ()) -> None:
        self.documents = [deepcopy(document) for document in documents]
        self.after_next_update: Callable[["_Collection", dict[str, Any]], None] | None = None

    async def find_one(self, query, *_args, **_kwargs):
        for document in self.documents:
            if _matches(document, query):
                return deepcopy(document)
        return None

    def find(self, query, *_args, **_kwargs):
        return _Cursor(document for document in self.documents if _matches(document, query))

    async def update_one(self, query, update, upsert=False):
        for document in self.documents:
            if _matches(document, query):
                document.update(deepcopy(update.get("$set", {})))
                hook, self.after_next_update = self.after_next_update, None
                if hook is not None:
                    hook(self, document)
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            inserted = {
                **query,
                **deepcopy(update.get("$setOnInsert", {})),
                **deepcopy(update.get("$set", {})),
            }
            self.documents.append(inserted)
        return SimpleNamespace(matched_count=0, modified_count=int(upsert))

    async def insert_one(self, document):
        inserted = deepcopy(document)
        inserted.setdefault("_id", ObjectId())
        self.documents.append(inserted)
        return SimpleNamespace(inserted_id=inserted["_id"])

    async def insert_many(self, documents):
        inserted = deepcopy(list(documents))
        self.documents.extend(inserted)
        return SimpleNamespace(inserted_ids=[document.get("_id") for document in inserted])

    async def delete_many(self, query):
        retained = [document for document in self.documents if not _matches(document, query)]
        deleted = len(self.documents) - len(retained)
        self.documents = retained
        return SimpleNamespace(deleted_count=deleted)


class _Database:
    def __init__(self, documents: Iterable[dict[str, Any]]) -> None:
        self.documents = _Collection(documents)
        self.document_vectors = _Collection()
        self.vector_sync_status = _Collection()


class _PersistentQdrant:
    enabled = True

    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}

    async def replace_document(self, payloads: list[dict[str, Any]]) -> int:
        if not payloads:
            return 0
        document_id = str(payloads[0]["metadata"]["document_id"])
        self.points = {
            point_id: point
            for point_id, point in self.points.items()
            if str(point["metadata"].get("document_id")) != document_id
        }
        for payload in payloads:
            self.points[str(payload["chunk_id"])] = deepcopy(payload)
        return len(payloads)

    def marker_present(self, document_id: str) -> bool:
        return any(
            str(point["metadata"].get("document_id")) == str(document_id)
            and MARKER in str(point.get("text") or "")
            for point in self.points.values()
        )

    def document_count(self, document_id: str) -> int:
        return sum(
            str(point["metadata"].get("document_id")) == str(document_id)
            for point in self.points.values()
        )


class _MongoVectorModel:
    embedding_model_name = "normal-authority-test-model"

    async def delete_vectors(self, _vector_refs):
        return None

    async def index_chunks(self, payloads, persist=False):
        assert persist is False
        return [
            {
                "metadata": deepcopy(payload["metadata"]),
                "vector_ref": f"vector-{payload['chunk_id']}",
                "embedding": [1.0, float(index + 1)],
                "text": payload["text"],
            }
            for index, payload in enumerate(payloads)
        ]


def _database_service(db: _Database) -> tuple[DatabaseService, _PersistentQdrant]:
    config = DocumentProcessingConfig()
    config.vector_store_enabled = True
    config.vector_dual_write_enabled = True
    config.dual_vector_write = True
    service = DatabaseService(config)
    service._db = db
    service._vector_service = _MongoVectorModel()
    qdrant = _PersistentQdrant()
    service._langchain_vector_service = qdrant
    service._langchain_service_initialized = True
    return service, qdrant


def _document(document_id: ObjectId, **overrides: Any) -> dict[str, Any]:
    now = datetime.utcnow()
    document = {
        "_id": document_id,
        "organization_id": "org-authority",
        "project_id": "project-authority",
        "filename": "normal-authority.pdf",
        "filepath_local": "normal-authority.pdf",
        "uploadType": "incoming",
        "processing_status": "completed",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
        "full_text": SOURCE_TEXT,
        "ocrText": SOURCE_TEXT,
        "createdAt": now,
        "updatedAt": now,
    }
    document.update(overrides)
    return document


def _metadata() -> SimpleNamespace:
    return SimpleNamespace(
        summary="Viaduct interface approval summary",
        keywords=[],
        contractual_clauses=[],
        references=[],
        full_content=SOURCE_TEXT,
        subject="Viaduct interface approvals",
        letter_no="LTR-NORMAL-20260819",
        from_company="Engineer",
        to_company="Contractor",
        date=None,
    )


async def _save(service: DatabaseService, document_id: ObjectId) -> int:
    return await service.save_document_data(
        document_id=str(document_id),
        file_path="normal-authority.pdf",
        parsed_metadata=_metadata(),
        full_text=SOURCE_TEXT,
        embedding_text=SOURCE_TEXT,
    )


def _physical_state(
    db: _Database,
    qdrant: _PersistentQdrant,
    document_id: ObjectId,
) -> tuple[bool, int, int]:
    mongo_count = sum(
        str(document.get("document_id")) == str(document_id)
        for document in db.document_vectors.documents
    )
    return (
        qdrant.marker_present(str(document_id)),
        qdrant.document_count(str(document_id)),
        mongo_count,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "authority", "should_publish"),
    AUTHORITY_MATRIX,
    ids=[case for case, _authority, _expected in AUTHORITY_MATRIX],
)
async def test_normal_save_publishes_vectors_only_for_current_canonical_authority(
    case: str,
    authority: dict[str, Any],
    should_publish: bool,
):
    document_id = ObjectId()
    db = _Database([_document(document_id, **authority)])
    service, qdrant = _database_service(db)

    chunks = await _save(service, document_id)

    marker_present, qdrant_count, mongo_count = _physical_state(db, qdrant, document_id)
    assert marker_present is should_publish, case
    assert (qdrant_count > 0) is should_publish, case
    assert (mongo_count > 0) is should_publish, case
    assert (chunks > 0) is should_publish, case


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "new_authority"),
    TOCTOU_MATRIX,
    ids=[case for case, _authority in TOCTOU_MATRIX],
)
async def test_normal_save_rechecks_authority_changed_after_earlier_caller_decision(
    case: str,
    new_authority: dict[str, Any],
):
    document_id = ObjectId()
    db = _Database([_document(document_id)])
    service, qdrant = _database_service(db)

    def change_current_authority(_collection: _Collection, document: dict[str, Any]) -> None:
        document.update(deepcopy(new_authority))

    db.documents.after_next_update = change_current_authority

    chunks = await _save(service, document_id)

    assert _physical_state(db, qdrant, document_id) == (False, 0, 0), case
    assert chunks == 0, case


@pytest.mark.asyncio
async def test_normal_save_fails_closed_when_canonical_document_disappears_before_writer():
    document_id = ObjectId()
    db = _Database([_document(document_id)])
    service, qdrant = _database_service(db)

    def delete_canonical(collection: _Collection, _document: dict[str, Any]) -> None:
        collection.documents.clear()

    db.documents.after_next_update = delete_canonical

    chunks = await _save(service, document_id)

    assert _physical_state(db, qdrant, document_id) == (False, 0, 0)
    assert chunks == 0
