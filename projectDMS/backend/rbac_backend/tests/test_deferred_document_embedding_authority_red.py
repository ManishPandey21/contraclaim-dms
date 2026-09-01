"""G31 deferred document-embedding publication authority regressions.

Two public/service seams are pinned:

* ``DocumentService.process_document_async`` reproduces an initially deferred
  duplicate check whose canonical authority changes after release but before
  embedding publication.
* ``DatabaseService.create_embeddings_for_document`` pins the reusable writer
  invariant directly.

The document service, duplicate classifier, database writer, chunker, payload
construction, and Mongo vector bookkeeping are real.  Qdrant and the embedding
model are persistent system-boundary fakes so final stored content is directly
observable.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Iterable

import pytest
from bson import ObjectId

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.database_service import DatabaseService
from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.duplicate_detection_service import CLASSIFICATION_SEPARATE


MARKER = "DEFERRED_EMBEDDING_BLOCKED_MARKER_20260819"
SOURCE_TEXT = (
    f"{MARKER} track alignment drawings and signalling interface approvals "
    "for the viaduct section. " * 24
)
EXISTING_TEXT = (
    "Reminder for submission of construction cost for the additional borewell "
    "near Kuda Ghar Ravidas Mandir. " * 24
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
    ("missing_canonical", None, False),
]


def _normal(value: Any) -> Any:
    return str(value) if isinstance(value, ObjectId) else value


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        actual = document.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and _normal(actual) == _normal(expected["$ne"]):
                return False
            if "$nin" in expected and _normal(actual) in {
                _normal(item) for item in expected["$nin"]
            }:
                return False
            if "$in" in expected and _normal(actual) not in {
                _normal(item) for item in expected["$in"]
            }:
                return False
            continue
        if _normal(actual) != _normal(expected):
            return False
    return True


class _Cursor:
    def __init__(self, documents: Iterable[dict[str, Any]]) -> None:
        self.documents = [deepcopy(document) for document in documents]

    async def to_list(self, length=None):
        return deepcopy(self.documents if length is None else self.documents[:length])


class _Collection:
    def __init__(self, documents: Iterable[dict[str, Any]] = ()) -> None:
        self.documents = [deepcopy(document) for document in documents]

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
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            inserted = {
                **{key: value for key, value in query.items() if not isinstance(value, dict)},
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
        self.documents.extend(deepcopy(list(documents)))
        return SimpleNamespace(inserted_ids=[document.get("_id") for document in documents])

    async def delete_many(self, query):
        retained = [document for document in self.documents if not _matches(document, query)]
        deleted = len(self.documents) - len(retained)
        self.documents = retained
        return SimpleNamespace(deleted_count=deleted)

    async def delete_one(self, query):
        for index, document in enumerate(self.documents):
            if _matches(document, query):
                self.documents.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class _Database:
    def __init__(self, documents: Iterable[dict[str, Any]] = ()) -> None:
        self.documents = _Collection(documents)
        self.document_vectors = _Collection()
        self.vector_sync_status = _Collection()
        self.document_processing_jobs = _Collection()


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
    embedding_model_name = "authority-test-model"

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
        "filename": "deferred-authority.pdf",
        "filepath_local": "deferred-authority.pdf",
        "filetype": "application/pdf",
        "filesize": 100,
        "uploadType": "incoming",
        "letterNo": "LTR-DEFERRED-20260819",
        "letterNoNormalized": "ltr-deferred-20260819",
        "date": now,
        "subject": "Track alignment approvals",
        "status": "Received",
        "processing_status": "completed",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
        "full_text": SOURCE_TEXT,
        "ocrText": SOURCE_TEXT,
        "createdAt": now,
        "updatedAt": now,
        "createdBy": "authority-test@example.com",
        "reference": [],
        "references": [],
        "referencedBy": [],
    }
    document.update(overrides)
    return document


class _ExtractionProcessor:
    def __init__(self, database_service: DatabaseService) -> None:
        self.database_service = database_service
        self.skip_embeddings_seen: list[bool] = []
        self.metadata = SimpleNamespace(
            summary="Track alignment approval summary",
            keywords=[],
            contractual_clauses=[],
            references=[],
            full_content=SOURCE_TEXT,
            subject="Track alignment approvals",
            letter_no="LTR-DEFERRED-20260819",
            from_company="Engineer",
            to_company="Contractor",
            date="2026-08-19",
        )

    async def process_document(self, **kwargs):
        skip_embeddings = bool(kwargs["skip_embeddings"])
        self.skip_embeddings_seen.append(skip_embeddings)
        chunks = await self.database_service.save_document_data(
            kwargs["document_id"],
            kwargs["pdf_path"],
            self.metadata,
            SOURCE_TEXT,
            SOURCE_TEXT,
            skip_embeddings=skip_embeddings,
        )
        return SimpleNamespace(
            success=True,
            publishable=True,
            metadata=self.metadata,
            metadata_source="authority_test",
            processing_time=1.0,
            chunks_created=chunks,
            processed_path=kwargs["pdf_path"],
            partial_failures={},
        )


class _GraphBoundary:
    async def ingest_document(self, **_kwargs):
        return None

    def sync_document_to_falkor(self, **_kwargs):
        return None


class _EvidenceBoundary:
    async def ingest_document_metadata(self, **_kwargs):
        return None


class _ReferenceBoundary:
    async def sync_bidirectional(self, **_kwargs):
        return {"resolved": 0, "missing": [], "updated_targets": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "current_authority", "should_publish"),
    AUTHORITY_MATRIX,
    ids=[case for case, _authority, _expected in AUTHORITY_MATRIX],
)
async def test_deferred_release_rechecks_current_canonical_authority_before_qdrant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    case: str,
    current_authority: dict[str, Any] | None,
    should_publish: bool,
):
    document_id = ObjectId()
    candidate_id = ObjectId()
    uploaded = _document(
        document_id,
        duplicate_status="pending",
        duplicate_of=str(candidate_id),
        lifecycle_state="duplicate_review",
    )
    candidate = _document(
        candidate_id,
        letterNo="LTR-DEFERRED-20260819",
        letterNoNormalized="ltr-deferred-20260819",
        subject="Borewell construction cost",
        full_text=EXISTING_TEXT,
        ocrText=EXISTING_TEXT,
    )
    db = _Database([uploaded, candidate])
    database_service, qdrant = _database_service(db)
    processor = _ExtractionProcessor(database_service)
    service = DocumentService(db)
    service.graph_ingestion = _GraphBoundary()
    service.evidence_graph = _EvidenceBoundary()
    service.reference_sync_service = _ReferenceBoundary()
    monkeypatch.setattr(
        "rbac_backend.services.document_service.create_document_processor",
        lambda: processor,
    )

    async def skip_audit_boundary(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "rbac_backend.services.document_audit_service.DocumentAuditService.emit",
        skip_audit_boundary,
    )

    real_classify = service.duplicate_detection.classify_pending_document
    transition_trace = ["initial:pending"]

    async def classify_then_change_authority(target_document_id: str):
        review = await real_classify(target_document_id)
        assert review["classification"] == CLASSIFICATION_SEPARATE
        released = await db.documents.find_one({"_id": document_id})
        assert released["duplicate_status"] == "unique"
        assert released["lifecycle_state"] == "active"
        transition_trace.append("released:unique/active")
        if current_authority is None:
            await db.documents.delete_one({"_id": document_id})
            transition_trace.append("current:missing")
        else:
            await db.documents.update_one(
                {"_id": document_id}, {"$set": deepcopy(current_authority)}
            )
            transition_trace.append(f"current:{case}")
        return review

    monkeypatch.setattr(
        service.duplicate_detection,
        "classify_pending_document",
        classify_then_change_authority,
    )

    pdf = tmp_path / f"{case}.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% deferred authority test")
    await service.process_document_async(
        str(document_id),
        file_path=str(pdf),
        organization_id="org-authority",
        project_id="project-authority",
        upload_type="incoming",
    )

    assert processor.skip_embeddings_seen == [True], case
    assert transition_trace[0:2] == ["initial:pending", "released:unique/active"], case
    assert transition_trace[-1] in {f"current:{case}", "current:missing"}, case
    physical_state = (
        qdrant.marker_present(str(document_id)),
        qdrant.document_count(str(document_id)) > 0,
    )
    assert physical_state == (should_publish, should_publish), case


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "current_authority", "should_publish"),
    AUTHORITY_MATRIX,
    ids=[case for case, _authority, _expected in AUTHORITY_MATRIX],
)
async def test_document_embedding_writer_enforces_current_canonical_authority(
    case: str,
    current_authority: dict[str, Any] | None,
    should_publish: bool,
):
    document_id = ObjectId()
    documents = []
    if current_authority is not None:
        documents.append(_document(document_id, **current_authority))
    db = _Database(documents)
    database_service, qdrant = _database_service(db)

    chunks = await database_service.create_embeddings_for_document(str(document_id))

    physical_state = (
        qdrant.marker_present(str(document_id)),
        qdrant.document_count(str(document_id)) > 0,
    )
    assert physical_state == (should_publish, should_publish), case
    assert (chunks > 0) is should_publish, case
