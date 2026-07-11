"""Tests for the two-stage duplicate-upload detection workflow."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import pytest
from bson import ObjectId

from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.duplicate_detection_service import (
    CLASSIFICATION_DUPLICATE,
    CLASSIFICATION_REVISION,
    CLASSIFICATION_SEPARATE,
    DuplicateDetectionService,
    VERDICT_EXACT_FILE_DUPLICATE,
    VERDICT_POSSIBLE_DOCUMENT_DUPLICATE,
    VERDICT_UNIQUE,
    content_similarity,
    text_fingerprint,
)


class _FakeUpdateResult:
    def __init__(self, matched: int, modified: int) -> None:
        self.matched_count = matched
        self.modified_count = modified


def _value_matches(actual: Any, expected: Any) -> bool:
    """Small Mongo-filter interpreter: equality, $ne, $nin, $in."""
    if isinstance(expected, dict):
        if "$ne" in expected:
            if _norm(actual) == _norm(expected["$ne"]):
                return False
        if "$nin" in expected:
            if _norm(actual) in {_norm(v) for v in expected["$nin"]}:
                return False
        if "$in" in expected:
            if _norm(actual) not in {_norm(v) for v in expected["$in"]}:
                return False
        return True
    return _norm(actual) == _norm(expected)


def _norm(value: Any) -> Any:
    return str(value) if isinstance(value, ObjectId) else value


class FakeCollection:
    def __init__(self, documents: Iterable[Dict[str, Any]] | None = None) -> None:
        self._docs: List[Dict[str, Any]] = [dict(doc) for doc in documents or []]

    def _matches(self, doc: Dict[str, Any], filter: Dict[str, Any]) -> bool:
        return all(_value_matches(doc.get(key), expected) for key, expected in filter.items())

    async def find_one(self, filter: Dict[str, Any], *_args: Any, **_kwargs: Any):
        for doc in self._docs:
            if self._matches(doc, filter):
                return doc
        return None

    async def insert_one(self, document: Dict[str, Any]) -> SimpleNamespace:
        document.setdefault("_id", ObjectId())
        self._docs.append(document)
        return SimpleNamespace(inserted_id=document["_id"])

    async def update_one(
        self,
        filter: Dict[str, Any],
        update: Dict[str, Any],
        upsert: bool = False,
    ) -> _FakeUpdateResult:
        doc = await self.find_one(filter)
        if not doc:
            if upsert:
                base = {k: v for k, v in filter.items() if not isinstance(v, dict)}
                base.update(update.get("$setOnInsert", {}))
                base.update(update.get("$set", {}))
                await self.insert_one(base)
                return _FakeUpdateResult(0, 1)
            return _FakeUpdateResult(0, 0)
        for key, value in update.get("$set", {}).items():
            doc[key] = value
        return _FakeUpdateResult(1, 1)

    async def update_many(self, filter: Dict[str, Any], update: Dict[str, Any]) -> _FakeUpdateResult:
        matched = 0
        for doc in self._docs:
            if self._matches(doc, filter):
                matched += 1
                for key, value in update.get("$set", {}).items():
                    doc[key] = value
        return _FakeUpdateResult(matched, matched)


class FakeDatabase:
    def __init__(self, documents: Iterable[Dict[str, Any]] | None = None) -> None:
        self.documents = FakeCollection(documents)
        self.document_processing_jobs = FakeCollection()
        self.reference_sync_queue = FakeCollection()
        self.projects = FakeCollection()
        self.tags = FakeCollection()
        self.subtags = FakeCollection()


def _doc(**overrides: Any) -> Dict[str, Any]:
    now = datetime.utcnow()
    base: Dict[str, Any] = {
        "_id": ObjectId(),
        "organization_id": "org-1",
        "project_id": "proj-1",
        "filename": "letter.pdf",
        "filepath_local": "/tmp/letter.pdf",
        "filetype": "application/pdf",
        "filesize": 100,
        "uploadType": "incoming",
        "letterNo": "LTR-100",
        "letterNoNormalized": "ltr-100",
        "sha256": "hash-original",
        "date": now,
        "subject": "Original subject",
        "status": "Received",
        "lifecycle_state": "active",
        "createdAt": now,
        "updatedAt": now,
        "createdBy": "tester@example.com",
        "reference": [],
        "references": [],
        "referencedBy": [],
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def test_text_fingerprint_ignores_case_and_whitespace() -> None:
    assert text_fingerprint("Hello   World\n") == text_fingerprint("hello world")
    assert text_fingerprint("") is None
    assert text_fingerprint("a") != text_fingerprint("b")


def test_content_similarity_bounds() -> None:
    text = "the contractor shall submit the construction cost for the borewell " * 20
    assert content_similarity(text, text) == 1.0
    other = "completely unrelated content about signalling and rolling stock " * 20
    similarity = content_similarity(text, other)
    assert similarity is not None and similarity < 0.1
    assert content_similarity(text, "") is None


# --------------------------------------------------------------------------- #
# Stage 1 - upload precheck                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_precheck_blocks_exact_file_duplicate_in_same_scope() -> None:
    existing = _doc(sha256="abc123")
    service = DuplicateDetectionService(FakeDatabase([existing]))

    result = await service.precheck_upload(
        organization_id="org-1",
        project_id="proj-1",
        letter_no="TOTALLY-DIFFERENT-NO",
        sha256="abc123",
    )

    assert result["verdict"] == VERDICT_EXACT_FILE_DUPLICATE
    assert result["existing_document_id"] == str(existing["_id"])


@pytest.mark.asyncio
async def test_precheck_hash_match_in_other_project_is_not_a_duplicate() -> None:
    existing = _doc(sha256="abc123", project_id="proj-OTHER")
    service = DuplicateDetectionService(FakeDatabase([existing]))

    result = await service.precheck_upload(
        organization_id="org-1",
        project_id="proj-1",
        letter_no="LTR-999",
        sha256="abc123",
    )

    assert result["verdict"] == VERDICT_UNIQUE


@pytest.mark.asyncio
async def test_precheck_ignores_deleted_documents() -> None:
    existing = _doc(sha256="abc123", lifecycle_state="deleted")
    service = DuplicateDetectionService(FakeDatabase([existing]))

    result = await service.precheck_upload(
        organization_id="org-1",
        project_id="proj-1",
        letter_no="LTR-100",
        sha256="abc123",
    )

    assert result["verdict"] == VERDICT_UNIQUE


@pytest.mark.asyncio
async def test_precheck_normalized_letter_match_with_new_file_is_pending() -> None:
    existing = _doc(letterNo="AFC/PM/KNPCC-06/4905", letterNoNormalized="afc-pm-knpcc-06-4905")
    service = DuplicateDetectionService(FakeDatabase([existing]))

    # Same letter number modulo case, spacing, and punctuation; new file bytes.
    result = await service.precheck_upload(
        organization_id="org-1",
        project_id="proj-1",
        letter_no="  afc pm KNPCC 06 4905 ",
        sha256="different-hash",
    )

    assert result["verdict"] == VERDICT_POSSIBLE_DOCUMENT_DUPLICATE
    assert result["existing_document_id"] == str(existing["_id"])


# --------------------------------------------------------------------------- #
# Stage 2 - post-extraction classification                                     #
# --------------------------------------------------------------------------- #
SHARED_TEXT = (
    "Reminder for submission of construction cost for additional borewell near "
    "Kuda Ghar Ravidas Mandir Harbansh Mohal. The contractor is requested to "
    "submit the detailed cost breakdown at the earliest. " * 10
)


def _pending_pair(
    *,
    uploaded_text: str,
    existing_text: str,
    uploaded_letter: str = "LTR-100",
    existing_letter: str = "LTR-100",
) -> tuple[FakeDatabase, Dict[str, Any], Dict[str, Any]]:
    existing = _doc(
        letterNo=existing_letter,
        letterNoNormalized=existing_letter.lower(),
        full_text=existing_text,
        subject="Reminder for borewell construction cost",
    )
    uploaded = _doc(
        letterNo=uploaded_letter,
        letterNoNormalized=uploaded_letter.lower(),
        sha256="hash-new",
        full_text=uploaded_text,
        subject="Reminder for borewell construction cost",
        duplicate_status="pending",
        duplicate_of=str(existing["_id"]),
        lifecycle_state="duplicate_review",
    )
    return FakeDatabase([existing, uploaded]), uploaded, existing


@pytest.mark.asyncio
async def test_classify_identical_content_as_duplicate_and_quarantine() -> None:
    db, uploaded, existing = _pending_pair(
        uploaded_text=SHARED_TEXT.upper(),  # fingerprint is case-insensitive
        existing_text=SHARED_TEXT,
    )
    service = DuplicateDetectionService(db)

    review = await service.classify_pending_document(str(uploaded["_id"]))

    assert review["classification"] == CLASSIFICATION_DUPLICATE
    stored = await db.documents.find_one({"_id": uploaded["_id"]})
    assert stored["duplicate_status"] == "duplicate"
    assert stored["lifecycle_state"] == "duplicate"
    assert stored["duplicate_of"] == str(existing["_id"])
    assert stored["duplicate_review"]["signals"]["text_fingerprint_match"] is True


@pytest.mark.asyncio
async def test_classify_same_letter_similar_content_as_revision() -> None:
    revised = SHARED_TEXT + (
        "Additionally the completion timeline has been revised to March and the "
        "rates shall follow the updated schedule of rates. " * 3
    )
    db, uploaded, existing = _pending_pair(
        uploaded_text=revised,
        existing_text=SHARED_TEXT,
    )
    service = DuplicateDetectionService(db)

    review = await service.classify_pending_document(str(uploaded["_id"]))

    assert review["classification"] == CLASSIFICATION_REVISION
    stored = await db.documents.find_one({"_id": uploaded["_id"]})
    assert stored["duplicate_status"] == "revision"
    assert stored["revision_of"] == str(existing["_id"])
    assert stored["lifecycle_state"] == "active"


@pytest.mark.asyncio
async def test_classify_same_letter_different_content_as_separate() -> None:
    different = (
        "Completely different correspondence about track alignment drawings and "
        "signalling interface approvals for the viaduct section. " * 12
    )
    db, uploaded, _existing = _pending_pair(
        uploaded_text=different,
        existing_text=SHARED_TEXT,
    )
    service = DuplicateDetectionService(db)

    review = await service.classify_pending_document(str(uploaded["_id"]))

    assert review["classification"] == CLASSIFICATION_SEPARATE
    stored = await db.documents.find_one({"_id": uploaded["_id"]})
    assert stored["duplicate_status"] == "unique"
    assert stored["lifecycle_state"] == "active"


@pytest.mark.asyncio
async def test_classify_releases_document_when_candidate_deleted() -> None:
    db, uploaded, existing = _pending_pair(
        uploaded_text=SHARED_TEXT,
        existing_text=SHARED_TEXT,
    )
    existing_doc = await db.documents.find_one({"_id": existing["_id"]})
    existing_doc["lifecycle_state"] = "deleted"
    service = DuplicateDetectionService(db)

    review = await service.classify_pending_document(str(uploaded["_id"]))

    assert review["classification"] == CLASSIFICATION_SEPARATE
    stored = await db.documents.find_one({"_id": uploaded["_id"]})
    assert stored["duplicate_status"] == "unique"
    assert stored["lifecycle_state"] == "active"


# --------------------------------------------------------------------------- #
# Downstream gating in process_document_async                                  #
# --------------------------------------------------------------------------- #
class _Recorder:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def sync(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    async def async_call(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append({"args": args, "kwargs": kwargs})
        return {"resolved": 0, "missing": [], "updated_targets": 0, "removed_targets": 0}


class _StubDatabaseService:
    def __init__(self) -> None:
        self.embedding_calls: List[str] = []

    async def create_embeddings_for_document(self, document_id: str) -> int:
        self.embedding_calls.append(str(document_id))
        return 3


class _StubProcessor:
    def __init__(self, metadata: SimpleNamespace) -> None:
        self.metadata = metadata
        self.calls: List[Dict[str, Any]] = []
        self.database_service = _StubDatabaseService()

    async def process_document(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            success=True,
            metadata=self.metadata,
            processing_time=1.0,
            chunks_created=0,
            processed_path="/processed/letter.pdf",
        )


def _metadata(letter_no: str, full_content: str) -> SimpleNamespace:
    return SimpleNamespace(
        summary="Summary",
        keywords=[],
        contractual_clauses=[],
        references=[],
        full_content=full_content,
        subject="Reminder for borewell construction cost",
        letter_no=letter_no,
        from_company="Typsa",
        to_company="Afcons",
        date="2025-11-20",
    )


async def _run_pipeline(
    db: FakeDatabase,
    uploaded_id: str,
    processor: _StubProcessor,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> tuple[DocumentService, Dict[str, _Recorder]]:
    service = DocumentService(db)
    recorders = {
        "graph_ingest": _Recorder(),
        "evidence": _Recorder(),
        "falkor": _Recorder(),
        "reference_sync": _Recorder(),
    }

    async def _graph_ingest(**kwargs: Any) -> None:
        recorders["graph_ingest"].calls.append(kwargs)

    async def _evidence(**kwargs: Any) -> None:
        recorders["evidence"].calls.append(kwargs)

    monkeypatch.setattr(
        "rbac_backend.services.document_service.create_document_processor",
        lambda: processor,
    )
    monkeypatch.setattr(service.graph_ingestion, "ingest_document", _graph_ingest)
    monkeypatch.setattr(service.evidence_graph, "ingest_document_metadata", _evidence)
    monkeypatch.setattr(
        service.graph_ingestion,
        "sync_document_to_falkor",
        lambda **kwargs: recorders["falkor"].calls.append(kwargs),
    )
    monkeypatch.setattr(
        service.reference_sync_service,
        "sync_bidirectional",
        recorders["reference_sync"].async_call,
    )

    pdf = tmp_path / "letter.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% test")
    ok = await service.process_document_async(
        uploaded_id,
        file_path=str(pdf),
        organization_id="org-1",
        project_id="proj-1",
        upload_type="incoming",
    )
    assert ok is True
    return service, recorders


@pytest.mark.asyncio
async def test_pending_duplicate_skips_all_downstream_artifacts(monkeypatch, tmp_path) -> None:
    db, uploaded, existing = _pending_pair(
        uploaded_text="",  # text arrives via extraction below
        existing_text=SHARED_TEXT,
    )
    processor = _StubProcessor(_metadata("LTR-100", SHARED_TEXT))

    _service, recorders = await _run_pipeline(
        db, str(uploaded["_id"]), processor, monkeypatch, tmp_path
    )

    # OCR ran with embeddings deferred.
    assert processor.calls and processor.calls[0]["skip_embeddings"] is True
    # Classified duplicate: quarantined, and nothing downstream was created.
    stored = await db.documents.find_one({"_id": uploaded["_id"]})
    assert stored["duplicate_status"] == "duplicate"
    assert stored["lifecycle_state"] == "duplicate"
    assert stored["duplicate_of"] == str(existing["_id"])
    assert processor.database_service.embedding_calls == []
    assert recorders["graph_ingest"].calls == []
    assert recorders["evidence"].calls == []
    assert recorders["reference_sync"].calls == []
    assert recorders["falkor"].calls == []


@pytest.mark.asyncio
async def test_pending_separate_document_is_released_with_deferred_artifacts(
    monkeypatch, tmp_path
) -> None:
    different = (
        "Completely different correspondence about track alignment drawings and "
        "signalling interface approvals for the viaduct section. " * 12
    )
    db, uploaded, _existing = _pending_pair(
        uploaded_text="",
        existing_text=SHARED_TEXT,
    )
    processor = _StubProcessor(_metadata("LTR-100", different))

    _service, recorders = await _run_pipeline(
        db, str(uploaded["_id"]), processor, monkeypatch, tmp_path
    )

    assert processor.calls and processor.calls[0]["skip_embeddings"] is True
    stored = await db.documents.find_one({"_id": uploaded["_id"]})
    assert stored["duplicate_status"] == "unique"
    assert stored["lifecycle_state"] == "active"
    # Deferred artifacts were created on release.
    assert processor.database_service.embedding_calls == [str(uploaded["_id"])]
    assert len(recorders["graph_ingest"].calls) == 1
    assert len(recorders["evidence"].calls) == 1
    assert len(recorders["reference_sync"].calls) == 1
    assert len(recorders["falkor"].calls) == 1


@pytest.mark.asyncio
async def test_unflagged_document_processes_normally(monkeypatch, tmp_path) -> None:
    uploaded = _doc(sha256="hash-new", letterNo="LTR-500", letterNoNormalized="ltr-500")
    db = FakeDatabase([uploaded])
    processor = _StubProcessor(_metadata("LTR-500", SHARED_TEXT))

    _service, recorders = await _run_pipeline(
        db, str(uploaded["_id"]), processor, monkeypatch, tmp_path
    )

    # No duplicate hold: embeddings are not deferred and downstream runs once.
    assert processor.calls and processor.calls[0]["skip_embeddings"] is False
    assert processor.database_service.embedding_calls == []
    assert len(recorders["graph_ingest"].calls) == 1
    assert len(recorders["reference_sync"].calls) == 1
    assert len(recorders["falkor"].calls) == 1
