"""G31 normal DocumentService graph/evidence publication authority regressions.

The public service seam and both real publication helpers are exercised.  OCR is
the upstream system-boundary fake; FalkorDB and Mongo are persistent fakes whose
final state is asserted directly.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Iterable

import pytest
from bson import ObjectId

from rbac_backend.graph.graph_ingestion_service import GraphIngestionService
from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.evidence_graph_service import EvidenceGraphService


GRAPH_MARKER = "DOCUMENT_GRAPH_BLOCKED_MARKER_20260819"
EVIDENCE_MARKER = "DOCUMENT_EVIDENCE_BLOCKED_MARKER_20260819"


def _normal(value: Any) -> Any:
    return str(value) if isinstance(value, ObjectId) else value


def _field(document: dict[str, Any], dotted: str) -> Any:
    value: Any = document
    for part in dotted.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in (query or {}).items():
        if key == "$and":
            if not all(_matches(document, item) for item in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(document, item) for item in expected):
                return False
            continue
        actual = _field(document, key)
        if isinstance(expected, dict):
            if "$in" in expected and _normal(actual) not in {
                _normal(item) for item in expected["$in"]
            }:
                return False
            if "$ne" in expected and _normal(actual) == _normal(expected["$ne"]):
                return False
            if "$exists" in expected and (actual is not None) is not bool(expected["$exists"]):
                return False
            continue
        if _normal(actual) != _normal(expected):
            return False
    return True


class _Cursor:
    def __init__(self, documents: Iterable[dict[str, Any]]) -> None:
        self.documents = [deepcopy(document) for document in documents]

    def sort(self, key, direction=1):
        reverse = direction == -1
        self.documents.sort(key=lambda row: _field(row, key), reverse=reverse)
        return self

    def skip(self, count):
        self.documents = self.documents[count:]
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    def __aiter__(self):
        self._iterator = iter(self.documents)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def to_list(self, length=None):
        values = self.documents if length is None else self.documents[:length]
        return deepcopy(values)


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

    async def insert_one(self, document):
        inserted = deepcopy(document)
        inserted.setdefault("_id", f"id-{len(self.documents) + 1}")
        self.documents.append(inserted)
        return SimpleNamespace(inserted_id=inserted["_id"])

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
            return SimpleNamespace(matched_count=0, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def delete_one(self, query):
        for index, document in enumerate(self.documents):
            if _matches(document, query):
                self.documents.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class _Database:
    def __init__(self, documents: Iterable[dict[str, Any]] = ()) -> None:
        self.documents = _Collection(documents)
        self.document_processing_jobs = _Collection()
        self.ai_extractions = _Collection()
        self.project_events = _Collection()
        self.event_links = _Collection()


class _PersistentFalkor:
    enabled = True

    def __init__(self, on_upsert=None) -> None:
        self.by_owner: dict[str, dict[str, Any]] = {}
        self.on_upsert = on_upsert

    def upsert_letter_with_refs(
        self,
        letter,
        references,
        *,
        cleanup=False,
        owner_document_id=None,
    ) -> None:
        assert cleanup is True
        self.by_owner[str(owner_document_id)] = {
            "letter": deepcopy(letter),
            "references": deepcopy(references),
        }
        if self.on_upsert is not None:
            callback, self.on_upsert = self.on_upsert, None
            callback()

    def owner_count(self, document_id: str) -> int:
        return int(str(document_id) in self.by_owner)

    def marker_present(self, document_id: str, marker: str) -> bool:
        return marker in repr(self.by_owner.get(str(document_id), {}))

    def material_influence(self, document_id: str) -> tuple[str, int]:
        publication = self.by_owner.get(str(document_id), {})
        return (
            str(publication.get("letter", {}).get("subject") or ""),
            len(publication.get("references", [])),
        )


class _DisabledAdapter:
    config = SimpleNamespace(enabled=False)


class _AuditBoundary:
    async def emit(self, **_kwargs):
        return None


class _ReferenceBoundary:
    async def sync_bidirectional(self, **_kwargs):
        return {"resolved": 0, "missing": [], "updated_targets": 0}


class _TransitioningMetadata:
    def __init__(
        self,
        transition,
        *,
        graph_marker: str = GRAPH_MARKER,
        evidence_marker: str = EVIDENCE_MARKER,
    ) -> None:
        self._transition = transition
        self._transitioned = False
        self._full_content = f"{evidence_marker} supporting delay evidence"
        self.summary = f"{evidence_marker} support-ready summary"
        self.subject = f"{graph_marker} graph-visible subject"
        self.keywords = ["authority"]
        self.contractual_clauses = ["Clause 8.4"]
        self.references = []
        self.letter_no = "LTR-G31-AUTHORITY"
        self.from_company = "Engineer"
        self.to_company = "Contractor"
        self.date = "2026-08-19"

    @property
    def full_content(self) -> str:
        # DocumentService first reads this while constructing update_fields,
        # after the workflow's publishable decision but before graph/evidence
        # publication.  This is the deterministic TOCTOU barrier.
        if not self._transitioned:
            self._transitioned = True
            self._transition()
        return self._full_content


class _Processor:
    def __init__(self, metadata: _TransitioningMetadata, *, publishable: bool = True) -> None:
        self.metadata = metadata
        self.publishable = publishable
        self.database_service = SimpleNamespace()

    async def process_document(self, **kwargs):
        return SimpleNamespace(
            success=True,
            publishable=self.publishable,
            metadata=self.metadata,
            metadata_source="authority_test",
            processing_time=1.0,
            chunks_created=1,
            processed_path=kwargs["pdf_path"],
            partial_failures={},
            pages_human_review=[1] if not self.publishable else [],
        )


def _document(document_id: ObjectId, **overrides: Any) -> dict[str, Any]:
    now = datetime.utcnow()
    document = {
        "_id": document_id,
        "organization_id": "org-authority",
        "project_id": "project-authority",
        "filename": "authority.pdf",
        "filepath_local": "authority.pdf",
        "filetype": "application/pdf",
        "filesize": 100,
        "uploadType": "incoming",
        "letterNo": "LTR-G31-AUTHORITY",
        "letterNoNormalized": "ltr-g31-authority",
        "date": now,
        "subject": "Initial clean subject",
        "status": "Received",
        "processing_status": "completed",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
        "createdAt": now,
        "updatedAt": now,
        "createdBy": "authority-test@example.com",
        "reference": [],
        "references": [],
        "referencedBy": [],
    }
    document.update(overrides)
    return document


def _transition(db: _Database, document_id: ObjectId, authority: dict[str, Any] | None):
    def apply() -> None:
        for index, document in enumerate(db.documents.documents):
            if _normal(document.get("_id")) != _normal(document_id):
                continue
            if authority is None:
                db.documents.documents.pop(index)
            else:
                document.update(deepcopy(authority))
            break

    return apply


def _physical_state(db: _Database, falkor: _PersistentFalkor, document_id: str):
    source_rows = [
        row
        for collection in (db.ai_extractions, db.project_events, db.event_links)
        for row in collection.documents
    ]

    def status_value(row: dict[str, Any]) -> str:
        status = row.get("status")
        return str(getattr(status, "value", status))

    return {
        "falkor_owner": falkor.owner_count(document_id),
        "falkor_graph_marker": falkor.marker_present(document_id, GRAPH_MARKER),
        "mongo_rows": len(source_rows),
        "mongo_evidence_marker": EVIDENCE_MARKER in repr(source_rows),
        "support_statuses": {
            status_value(row) for row in source_rows if row.get("status") is not None
        },
    }


def _service(db: _Database, falkor: _PersistentFalkor) -> DocumentService:
    service = DocumentService(db)
    service.graph_ingestion = GraphIngestionService(
        adapter=_DisabledAdapter(), falkor=falkor
    )
    service.evidence_graph = EvidenceGraphService(db)
    service.evidence_graph.audit = _AuditBoundary()
    service.reference_sync_service = _ReferenceBoundary()
    return service


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    db: _Database,
    document_id: ObjectId,
    metadata: _TransitioningMetadata,
    *,
    publishable: bool = True,
    falkor: _PersistentFalkor | None = None,
):
    falkor = falkor or _PersistentFalkor()
    service = _service(db, falkor)
    processor = _Processor(metadata, publishable=publishable)
    monkeypatch.setattr(
        "rbac_backend.services.document_service.create_document_processor",
        lambda: processor,
    )
    pdf = tmp_path / f"{document_id}.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% graph evidence authority test")
    result = await service.process_document_async(
        str(document_id),
        file_path=str(pdf),
        organization_id="org-authority",
        project_id="project-authority",
        upload_type="incoming",
    )
    return result, falkor


STATIC_MATRIX = [
    ("clean", {}, True, True),
    ("operational_failed", {"processing_status": "failed"}, True, True),
    ("human_review", {"processing_status": "human_review_required"}, True, False),
    ("duplicate_status", {"duplicate_status": "duplicate"}, True, False),
    ("duplicate_lifecycle", {"lifecycle_state": "duplicate"}, True, False),
    ("deleted", {"lifecycle_state": "deleted"}, True, False),
    ("missing", None, True, False),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "authority", "publishable", "should_publish"),
    STATIC_MATRIX,
    ids=[case for case, *_rest in STATIC_MATRIX],
)
async def test_static_authority_matrix_controls_final_graph_and_evidence_state(
    monkeypatch,
    tmp_path,
    case,
    authority,
    publishable,
    should_publish,
):
    document_id = ObjectId()
    documents = [] if authority is None else [_document(document_id, **authority)]
    db = _Database(documents)
    metadata = _TransitioningMetadata(lambda: None)

    _result, falkor = await _run(
        monkeypatch,
        tmp_path,
        db,
        document_id,
        metadata,
        publishable=publishable,
    )

    state = _physical_state(db, falkor, str(document_id))
    assert state["falkor_graph_marker"] is should_publish, (case, state)
    assert state["mongo_evidence_marker"] is should_publish, (case, state)
    assert (state["falkor_owner"] > 0) is should_publish, (case, state)
    assert (state["mongo_rows"] > 0) is should_publish, (case, state)
    if should_publish:
        assert falkor.material_influence(str(document_id))[0].startswith(GRAPH_MARKER)
        assert state["support_statuses"] >= {"applied", "open", "ai_suggested"}
    else:
        assert state["support_statuses"] == set()


TOCTOU_MATRIX = [
    ("human_review", {"processing_status": "human_review_required"}),
    ("duplicate_status", {"duplicate_status": "duplicate"}),
    ("deleted", {"lifecycle_state": "deleted"}),
    ("missing", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "current_authority"),
    TOCTOU_MATRIX,
    ids=[case for case, _authority in TOCTOU_MATRIX],
)
async def test_publication_rechecks_canonical_authority_after_payload_guard(
    monkeypatch,
    tmp_path,
    case,
    current_authority,
):
    document_id = ObjectId()
    db = _Database([_document(document_id)])
    metadata = _TransitioningMetadata(
        _transition(db, document_id, current_authority)
    )

    _result, falkor = await _run(
        monkeypatch, tmp_path, db, document_id, metadata
    )

    state = _physical_state(db, falkor, str(document_id))
    assert state == {
        "falkor_owner": 0,
        "falkor_graph_marker": False,
        "mongo_rows": 0,
        "mongo_evidence_marker": False,
        "support_statuses": set(),
    }, (case, state)


@pytest.mark.asyncio
async def test_evidence_rechecks_authority_after_falkor_publication(
    monkeypatch,
    tmp_path,
):
    document_id = ObjectId()
    db = _Database([_document(document_id)])
    falkor = _PersistentFalkor(
        on_upsert=_transition(
            db, document_id, {"processing_status": "human_review_required"}
        )
    )
    metadata = _TransitioningMetadata(lambda: None)

    await _run(
        monkeypatch,
        tmp_path,
        db,
        document_id,
        metadata,
        falkor=falkor,
    )

    state = _physical_state(db, falkor, str(document_id))
    # Falkor was authorized at its own boundary.  The transition happened only
    # after that write; the independent Mongo-evidence boundary must fail closed.
    assert state["falkor_graph_marker"] is True
    assert state["mongo_rows"] == 0
    assert state["mongo_evidence_marker"] is False
    assert state["support_statuses"] == set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "current_authority"),
    TOCTOU_MATRIX,
    ids=[f"neutralized-{case}" for case, _authority in TOCTOU_MATRIX],
)
async def test_transition_markers_return_when_only_current_gate_is_neutralized(
    monkeypatch,
    tmp_path,
    case,
    current_authority,
):
    document_id = ObjectId()
    db = _Database([_document(document_id)])
    metadata = _TransitioningMetadata(
        _transition(db, document_id, current_authority)
    )
    falkor = _PersistentFalkor()
    service = _service(db, falkor)
    processor = _Processor(metadata)
    monkeypatch.setattr(
        "rbac_backend.services.document_service.create_document_processor",
        lambda: processor,
    )

    async def stale_payload(_document_id, extracted_payload):
        return dict(extracted_payload)

    # Mutation proof: neutralize only the new current-Mongo boundary.  The
    # earlier result.publishable decision and both writer snapshot predicates
    # remain active.
    monkeypatch.setattr(service, "_current_publication_payload", stale_payload)
    pdf = tmp_path / f"neutralized-{case}.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% authority mutation proof")
    await service.process_document_async(
        str(document_id),
        file_path=str(pdf),
        organization_id="org-authority",
        project_id="project-authority",
        upload_type="incoming",
    )

    state = _physical_state(db, falkor, str(document_id))
    assert state["falkor_graph_marker"] is True, (case, state)
    assert state["mongo_evidence_marker"] is True, (case, state)
    assert state["support_statuses"] >= {"applied", "open", "ai_suggested"}


@pytest.mark.asyncio
async def test_denied_reentry_does_not_republish_new_graph_or_evidence_material(
    monkeypatch,
    tmp_path,
):
    document_id = ObjectId()
    db = _Database([_document(document_id)])
    falkor = _PersistentFalkor()

    first_metadata = _TransitioningMetadata(lambda: None)
    await _run(
        monkeypatch,
        tmp_path,
        db,
        document_id,
        first_metadata,
        falkor=falkor,
    )
    before = _physical_state(db, falkor, str(document_id))

    second_graph_marker = f"{GRAPH_MARKER}-ATTEMPT-2"
    second_evidence_marker = f"{EVIDENCE_MARKER}-ATTEMPT-2"
    second_metadata = _TransitioningMetadata(
        _transition(db, document_id, {"lifecycle_state": "deleted"}),
        graph_marker=second_graph_marker,
        evidence_marker=second_evidence_marker,
    )
    await _run(
        monkeypatch,
        tmp_path,
        db,
        document_id,
        second_metadata,
        falkor=falkor,
    )

    after = _physical_state(db, falkor, str(document_id))
    assert after == before
    assert second_graph_marker not in repr(falkor.by_owner)
    assert second_evidence_marker not in repr(
        db.ai_extractions.documents
        + db.project_events.documents
        + db.event_links.documents
    )
