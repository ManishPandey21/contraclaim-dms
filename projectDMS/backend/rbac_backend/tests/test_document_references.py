"""Regression tests for the document reference/linking workflows."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Optional

import httpx
import sys

import pytest
from bson import ObjectId
from fastapi import FastAPI

from rbac_backend.core.security import CurrentUser
from rbac_backend.models.document import ReferenceCreate


class _StubProcessingResult:
    def __init__(
        self,
        *,
        success: bool = True,
        error: str | None = None,
        processing_time: float | None = None,
        metadata: Any = None,
        processed_path: str | None = None,
        document_id: str | None = None,
        chunks_created: int | None = None,
    ) -> None:
        self.success = success
        self.error = error
        self.processing_time = processing_time
        self.metadata = metadata
        self.processed_path = processed_path
        self.document_id = document_id
        self.chunks_created = chunks_created


class _StubParsedDocumentMetadata:
    def __init__(self, **kwargs: Any) -> None:
        defaults = {
            "date": None,
            "subject": None,
            "letter_no": None,
            "from_company": None,
            "to_company": None,
            "references": [],
            "summary": None,
            "keywords": [],
            "contractual_clauses": [],
            "full_content": None,
        }
        defaults.update(kwargs)
        for key, value in defaults.items():
            setattr(self, key, value)

    def model_dump(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        return {
            "date": self.date,
            "subject": self.subject,
            "letter_no": self.letter_no,
            "from_company": self.from_company,
            "to_company": self.to_company,
            "references": list(self.references or []),
            "summary": self.summary,
            "keywords": list(self.keywords or []),
            "contractual_clauses": list(self.contractual_clauses or []),
            "full_content": self.full_content,
        }


# NOTE: this file previously replaced rbac_backend.models.document_metadata in
# sys.modules with a SimpleNamespace stub. That poisoned the module for every
# test collected afterwards in the same process (fresh `from ... import X`
# statements hit the stub, which has no __spec__ and only two attributes) and
# broke this very file once document_service started importing
# extracted_metadata_updates. The real module imports cleanly; the local _Stub*
# classes below remain for constructing lightweight fixtures only.

from rbac_backend.routers.documents import (
    DocumentController,
    controller_add_reference,
    controller_list_references,
    controller_remove_reference,
    controller_sync_references,
    get_current_user,
    get_document_controller,
    router as documents_router,
)
from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.reference_sync_service import ReferenceSyncError


def _make_document_dict(**overrides: Any) -> Dict[str, Any]:
    """Create a Mongo-style document payload with sensible defaults."""

    now = datetime.utcnow()
    document_id = overrides.get("_id", ObjectId())
    base: Dict[str, Any] = {
        "_id": document_id,
        "organization_id": overrides.get("organization_id", "org-1"),
        "project_id": overrides.get("project_id", "proj-1"),
        "project_name": overrides.get("project_name"),
        "filename": overrides.get("filename", "example.pdf"),
        "filepath_local": overrides.get("filepath_local", "/tmp/example.pdf"),
        "filepath_s3": overrides.get("filepath_s3", ""),
        "presigned_url": overrides.get("presigned_url"),
        "filetype": overrides.get("filetype", "application/pdf"),
        "filesize": overrides.get("filesize", 1234),
        "uploadType": overrides.get("uploadType", "incoming"),
        "letterNo": overrides.get("letterNo", "LTR-001"),
        "date": overrides.get("date", now),
        "subject": overrides.get("subject", "Example subject"),
        "from_": overrides.get("from_"),
        "to": overrides.get("to"),
        "tags": overrides.get("tags", []),
        "subTags": overrides.get("subTags", []),
        "status": overrides.get("status", "active"),
        "ocrEnabled": overrides.get("ocrEnabled", False),
        "compressionEnabled": overrides.get("compressionEnabled", False),
        "ocrText": overrides.get("ocrText"),
        "full_text": overrides.get("full_text"),
        "keywords": overrides.get("keywords"),
        "contractual_clauses": overrides.get("contractual_clauses"),
        "reference": overrides.get("reference", []),
        "summary": overrides.get("summary"),
        "createdAt": overrides.get("createdAt", now),
        "updatedAt": overrides.get("updatedAt", now),
        "createdBy": overrides.get("createdBy", "tester@example.com"),
        "version": overrides.get("version", "1.0"),
        "enclosures": overrides.get("enclosures", []),
        "references": overrides.get("references", []),
        "referencedBy": overrides.get("referencedBy", []),
    }
    base.update(overrides)
    return base


class _FakeUpdateResult:
    def __init__(self, matched: int, modified: int) -> None:
        self.matched_count = matched
        self.modified_count = modified


class FakeCursor:
    def __init__(self, documents: Iterable[Dict[str, Any]]) -> None:
        self._documents = [dict(doc) for doc in documents]
        self._limit: Optional[int] = None

    def sort(self, *_args: Any, **_kwargs: Any):
        return self

    def limit(self, limit: int):
        self._limit = limit
        return self

    async def to_list(self, length: Optional[int] = None):
        rows = list(self._documents)
        if self._limit is not None:
            rows = rows[: self._limit]
        if length is not None:
            rows = rows[:length]
        return rows

    def __aiter__(self):
        async def _iterate():
            rows = list(self._documents)
            if self._limit is not None:
                rows = rows[: self._limit]
            for row in rows:
                yield dict(row)

        return _iterate()


class FakeCollection:
    """Minimal async collection stub implementing the methods the service uses."""

    def __init__(self, documents: Iterable[Dict[str, Any]] | None = None) -> None:
        self._docs: Dict[str, Dict[str, Any]] = {}
        for doc in documents or []:
            key = self._key(doc.get("_id"))
            self._docs[key] = doc

    @staticmethod
    def _key(value: Any) -> str:
        if isinstance(value, ObjectId):
            return str(value)
        return str(value)

    @staticmethod
    def _matches_filter(doc: Dict[str, Any], filter: Dict[str, Any]) -> bool:
        for key, expected in filter.items():
            actual = doc.get(key)
            if key == "_id":
                if isinstance(expected, dict) and "$in" in expected:
                    if all(str(actual) != str(candidate) for candidate in expected["$in"]):
                        return False
                elif str(actual) != str(expected):
                    return False
            elif isinstance(expected, dict) and "$regex" in expected:
                needle = str(expected["$regex"]).strip("^$").lower()
                if str(actual or "").lower() != needle:
                    return False
            elif actual != expected:
                return False
        return True

    async def find_one(self, filter: Dict[str, Any], *_args: Any, **_kwargs: Any) -> Optional[Dict[str, Any]]:
        if "_id" not in filter:
            if "letterNoNormalized" in filter:
                for doc in self._docs.values():
                    if self._matches_filter(doc, filter):
                        return doc
            return None
        identifier = filter["_id"]
        if isinstance(identifier, dict) and "$in" in identifier:
            for candidate in identifier["$in"]:
                key = self._key(candidate)
                doc = self._docs.get(key)
                if doc is not None and self._matches_filter(doc, filter):
                    return doc
            return None
        key = self._key(identifier)
        doc = self._docs.get(key)
        if doc is not None and self._matches_filter(doc, filter):
            return doc
        return None

    async def insert_one(self, document: Dict[str, Any]) -> SimpleNamespace:
        identifier = document.get("_id") or ObjectId()
        document["_id"] = identifier
        self._docs[self._key(identifier)] = document
        return SimpleNamespace(inserted_id=identifier)

    async def insert_many(self, documents: Iterable[Dict[str, Any]]) -> SimpleNamespace:
        inserted_ids = []
        for document in documents:
            result = await self.insert_one(dict(document))
            inserted_ids.append(result.inserted_id)
        return SimpleNamespace(inserted_ids=inserted_ids)

    async def update_one(self, filter: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> _FakeUpdateResult:
        document = await self.find_one(filter)
        if not document:
            if upsert:
                base = dict(filter)
                if "$setOnInsert" in update:
                    base.update(update["$setOnInsert"])
                if "$set" in update:
                    base.update(update["$set"])
                await self.insert_one(base)
                return _FakeUpdateResult(0, 1)
            return _FakeUpdateResult(0, 0)

        modified = False
        if "$set" in update:
            for key, value in update["$set"].items():
                document[key] = value
                modified = True
        if "$push" in update:
            for key, value in update["$push"].items():
                document.setdefault(key, []).append(value)
                modified = True
        if "$pull" in update:
            for key, criteria in update["$pull"].items():
                values = document.get(key) or []
                if isinstance(values, list) and isinstance(criteria, dict):
                    document[key] = [
                        item
                        for item in values
                        if not (
                            isinstance(item, dict)
                            and all(item.get(k) == v for k, v in criteria.items())
                        )
                    ]
                    modified = True

        self._docs[self._key(document["_id"])] = document
        return _FakeUpdateResult(1, 1 if modified else 0)

    async def update_many(self, filter: Dict[str, Any], update: Dict[str, Any]) -> _FakeUpdateResult:
        matched = 0
        modified = 0
        for document in self._docs.values():
            matches = True
            for key, expected in filter.items():
                actual = document.get(key)
                if isinstance(expected, dict) and "$in" in expected:
                    if actual not in expected["$in"]:
                        matches = False
                        break
                elif actual != expected:
                    matches = False
                    break
            if not matches:
                continue
            matched += 1
            for key, value in update.get("$set", {}).items():
                document[key] = value
                modified += 1
        return _FakeUpdateResult(matched, modified)

    def find(self, filter: Dict[str, Any], *_args: Any, **_kwargs: Any) -> FakeCursor:
        if "letterNo" in filter and isinstance(filter["letterNo"], dict) and "$regex" in filter["letterNo"]:
            matches = [
                doc
                for doc in self._docs.values()
                if self._matches_filter(doc, filter)
            ]
            return FakeCursor(matches)

        if "_id" in filter and isinstance(filter["_id"], dict) and "$in" in filter["_id"]:
            matches = [
                doc
                for candidate in filter["_id"]["$in"]
                if (doc := self._docs.get(self._key(candidate))) is not None
            ]
            return FakeCursor(matches)

        matches = []
        for doc in self._docs.values():
            if all(doc.get(key) == value for key, value in filter.items()):
                matches.append(doc)
        return FakeCursor(matches)


class FakeDatabase:
    def __init__(self, documents: Iterable[Dict[str, Any]] | None = None) -> None:
        self.documents = FakeCollection(documents)
        # Collections used indirectly by enrichment logic; populate with empty stubs
        self.projects = FakeCollection()
        self.tags = FakeCollection()
        self.subtags = FakeCollection()
        self.reference_sync_queue = FakeCollection()


class StubAuthorizationService:
    async def build_document_query(
        self, _user: CurrentUser, filters: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {k: v for k, v in filters.items() if v not in (None, "", [], {})}


class StubPolicyService:
    async def authorize_document(self, *_: Any, **__: Any) -> None:
        return None


def _make_controller(fake_db: FakeDatabase) -> DocumentController:
    service = DocumentService(fake_db)
    # Lazy imports avoided by using lightweight lambda stubs for other dependencies
    controller = DocumentController(
        document_service=service,
        file_service=SimpleNamespace(),
        export_service=SimpleNamespace(),
        auth_service=StubAuthorizationService(),
        bulk_upload_service=SimpleNamespace(),
    )
    controller.policy_service = StubPolicyService()  # type: ignore[assignment]
    return controller


def _make_user() -> CurrentUser:
    return CurrentUser(
        id="user-123",
        username="tester",
        email="tester@example.com",
        roles=["superadmin"],
        organizations=[],
        projects=[],
        disabled=False,
    )


@pytest.mark.asyncio
async def test_list_references_returns_parsed_and_linked_metadata() -> None:
    referenced_id = ObjectId()
    source_id = ObjectId()
    now = datetime.utcnow()

    source_doc = _make_document_dict(
        _id=source_id,
        reference=[{"text": "Spec clause 5"}],
        references=[
            {
                "documentId": str(referenced_id),
                "linkType": "direct",
                "description": "Related work",
                "linkedAt": now,
                "linkedBy": "user-123",
            }
        ],
    )
    referenced_doc = _make_document_dict(
        _id=referenced_id,
        letterNo="LTR-200",
        subject="Target letter",
    )
    fake_db = FakeDatabase([source_doc, referenced_doc])
    controller = _make_controller(fake_db)

    response = await controller_list_references(
        controller,
        str(source_id),
        _make_user(),
    )

    assert response["parsed"] == [{"raw": "Spec clause 5", "text": "Spec clause 5"}]
    assert len(response["linked"]) == 1
    linked = response["linked"][0]
    assert linked["documentId"] == str(referenced_id)
    assert linked["letterNo"] == "LTR-200"
    assert linked["title"] == "Target letter"
    # Preserve description metadata so the UI can surface it
    assert linked["description"] == "Related work"


@pytest.mark.asyncio
async def test_list_references_structures_legacy_parsed_reference_strings() -> None:
    source_id = ObjectId()
    raw_ref = "letter no. AFC/PM/KNPCC-06/4905 dated 05.11.2025"
    source_doc = _make_document_dict(
        _id=source_id,
        reference=[raw_ref],
        references=[],
    )
    fake_db = FakeDatabase([source_doc])
    controller = _make_controller(fake_db)

    response = await controller_list_references(
        controller,
        str(source_id),
        _make_user(),
    )

    assert response["parsed"] == [
        {
            "raw": raw_ref,
            "letterNo": "AFC/PM/KNPCC-06/4905",
            "letter_no": "AFC/PM/KNPCC-06/4905",
            "date": "05-11-2025",
        }
    ]
    assert response["linked"] == []


@pytest.mark.asyncio
async def test_manual_sync_reconciles_current_extracted_list_and_refreshes_graph(monkeypatch) -> None:
    source_id = ObjectId()
    target_id = ObjectId()
    raw_ref = "letter no. LTR-200 dated 05.11.2025"
    source_doc = _make_document_dict(
        _id=source_id,
        reference=[{"raw": raw_ref, "text": raw_ref}],
        references=[],
    )
    target_doc = _make_document_dict(
        _id=target_id,
        letterNo="LTR-200",
        letterNoNormalized="ltr-200",
    )
    fake_db = FakeDatabase([source_doc, target_doc])
    controller = _make_controller(fake_db)
    graph_calls: list[dict[str, Any]] = []

    def _fake_sync_document_to_falkor(**kwargs: Any) -> None:
        graph_calls.append(kwargs)

    monkeypatch.setattr(
        controller.document_service.graph_ingestion,
        "sync_document_to_falkor",
        _fake_sync_document_to_falkor,
    )

    first = await controller_sync_references(
        controller,
        str(source_id),
        _make_user(),
    )
    second = await controller_sync_references(
        controller,
        str(source_id),
        _make_user(),
    )

    stored_source = await controller.document_service.get_document(str(source_id))
    stored_target = await controller.document_service.get_document(str(target_id))
    assert first["sync"]["resolved"] == second["sync"]["resolved"] == 1
    assert stored_source is not None and len(stored_source.references) == 1
    assert stored_source.references[0].documentId == str(target_id)
    assert stored_target is not None and len(stored_target.referencedBy) == 1
    assert len(graph_calls) == 2
    assert graph_calls[-1]["document"]["references"][0]["documentId"] == str(target_id)


@pytest.mark.asyncio
async def test_manual_sync_with_empty_extracted_list_clears_stale_links_and_graph(monkeypatch) -> None:
    source_id = ObjectId()
    target_id = ObjectId()
    now = datetime.utcnow()
    source_doc = _make_document_dict(
        _id=source_id,
        reference=[],
        references=[
            {
                "documentId": str(target_id),
                "linkType": "indirect",
                "linkedAt": now,
                "linkedBy": "parser",
                "letterNo": "LTR-OLD",
                "source": "parser",
            }
        ],
    )
    target_doc = _make_document_dict(
        _id=target_id,
        letterNo="LTR-OLD",
        letterNoNormalized="ltr-old",
        referencedBy=[
            {
                "documentId": str(source_id),
                "linkType": "indirect",
                "linkedAt": now,
                "linkedBy": "parser",
                "letterNo": "LTR-001",
                "source": "parser",
            }
        ],
    )
    fake_db = FakeDatabase([source_doc, target_doc])
    controller = _make_controller(fake_db)
    graph_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        controller.document_service.graph_ingestion,
        "sync_document_to_falkor",
        lambda **kwargs: graph_calls.append(kwargs),
    )

    result = await controller_sync_references(
        controller,
        str(source_id),
        _make_user(),
    )

    stored_source = await controller.document_service.get_document(str(source_id))
    stored_target = await controller.document_service.get_document(str(target_id))
    assert result["sync"]["removed_targets"] == 1
    assert stored_source is not None and stored_source.references == []
    assert stored_target is not None and stored_target.referencedBy == []
    assert graph_calls[-1]["document"]["references"] == []


@pytest.mark.asyncio
async def test_add_and_remove_reference_round_trip(monkeypatch) -> None:
    source_id = ObjectId()
    target_id = ObjectId()

    source_doc = _make_document_dict(_id=source_id)
    target_doc = _make_document_dict(_id=target_id, letterNo="LTR-201")
    fake_db = FakeDatabase([source_doc, target_doc])
    controller = _make_controller(fake_db)
    user = _make_user()

    updated = await controller_add_reference(
        controller,
        str(source_id),
        ReferenceCreate(
            referenced_document_id=str(target_id),
            link_type="direct",
            description="Follow-up",
        ),
        user,
    )

    assert any(
        ref.documentId == str(target_id) and ref.description == "Follow-up"
        for ref in updated.references
    )

    target_after = await controller.document_service.get_document(str(target_id))
    assert target_after is not None
    assert any(ref.documentId == str(source_id) for ref in target_after.referencedBy)

    removed = await controller_remove_reference(
        controller,
        str(source_id),
        str(target_id),
        user,
    )
    assert removed.references == []
    target_after_removal = await controller.document_service.get_document(str(target_id))
    assert target_after_removal is not None
    assert target_after_removal.referencedBy == []


@pytest.mark.asyncio
async def test_add_reference_preserves_existing_manual_links(monkeypatch) -> None:
    source_id = ObjectId()
    target_a_id = ObjectId()
    target_b_id = ObjectId()

    source_doc = _make_document_dict(_id=source_id)
    target_a = _make_document_dict(_id=target_a_id, letterNo="LTR-A")
    target_b = _make_document_dict(_id=target_b_id, letterNo="LTR-B")
    fake_db = FakeDatabase([source_doc, target_a, target_b])
    controller = _make_controller(fake_db)
    user = _make_user()

    await controller_add_reference(
        controller,
        str(source_id),
        ReferenceCreate(
            referenced_document_id=str(target_a_id),
            link_type="direct",
            description="First manual link",
        ),
        user,
    )

    updated = await controller_add_reference(
        controller,
        str(source_id),
        ReferenceCreate(
            referenced_document_id=str(target_b_id),
            link_type="direct",
            description="Second manual link",
        ),
        user,
    )

    linked_ids = {ref.documentId for ref in updated.references}
    assert linked_ids == {str(target_a_id), str(target_b_id)}

    target_a_after = await controller.document_service.get_document(str(target_a_id))
    target_b_after = await controller.document_service.get_document(str(target_b_id))
    assert target_a_after is not None
    assert target_b_after is not None
    assert any(ref.documentId == str(source_id) for ref in target_a_after.referencedBy)
    assert any(ref.documentId == str(source_id) for ref in target_b_after.referencedBy)


@pytest.mark.asyncio
async def test_link_documents_endpoint_creates_bidirectional_relationship(monkeypatch) -> None:
    source_id = ObjectId()
    target_id = ObjectId()

    source_doc = _make_document_dict(_id=source_id)
    target_doc = _make_document_dict(_id=target_id)
    fake_db = FakeDatabase([source_doc, target_doc])

    app = FastAPI()
    app.include_router(documents_router, prefix="/api")

    def override_controller() -> DocumentController:
        return _make_controller(fake_db)

    app.dependency_overrides[get_document_controller] = override_controller
    app.dependency_overrides[get_current_user] = _make_user

    async def _fake_get_database() -> FakeDatabase:
        return fake_db

    monkeypatch.setattr("rbac_backend.routers.documents.get_database", _fake_get_database)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/documents/link",
            json={
                "source_document_id": str(source_id),
                "target_document_id": str(target_id),
                "link_type": "direct",
                "description": "Related",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "Documents linked successfully"
    assert payload["linked_documents"] == [str(target_id)]

    stored_source = await _make_controller(fake_db).document_service.get_document(
        str(source_id)
    )
    assert stored_source is not None
    assert any(ref.documentId == str(target_id) for ref in stored_source.references)

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_process_document_async_updates_metadata(monkeypatch, tmp_path) -> None:
    doc_id = ObjectId()
    target_id = ObjectId()
    document = _make_document_dict(_id=doc_id, summary=None, keywords=None)
    target = _make_document_dict(
        _id=target_id,
        letterNo="LTR-200",
        letterNoNormalized="ltr-200",
    )
    fake_db = FakeDatabase([document, target])
    service = DocumentService(fake_db)
    input_path = tmp_path / "example.pdf"
    input_path.write_bytes(b"%PDF-1.4\n% test pdf")

    class StubProcessor:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def process_document(self, **kwargs: Any) -> SimpleNamespace:
            self.calls.append(kwargs)
            metadata = SimpleNamespace(
                summary="Auto summary",
                keywords=["alpha", "beta"],
                contractual_clauses=None,
                references=[{"text": "LTR-200"}],
                full_content="Full text",
                subject="Updated subject",
                letter_no="LTR-300",
                from_company="Sender",
                to_company="Recipient",
                date="2024-02-01",
            )
            return SimpleNamespace(
                success=True,
                metadata=metadata,
                processing_time=1.5,
                chunks_created=3,
                processed_path="/processed/example.pdf",
            )

    stub_processor = StubProcessor()
    falkor_sync_calls: list[dict[str, Any]] = []

    async def _fake_ingest_document(**_kwargs: Any) -> None:
        return None

    def _fake_sync_document_to_falkor(**kwargs: Any) -> None:
        falkor_sync_calls.append(kwargs)

    monkeypatch.setattr(
        "rbac_backend.services.document_service.create_document_processor",
        lambda: stub_processor,
    )
    monkeypatch.setattr(service.graph_ingestion, "ingest_document", _fake_ingest_document)
    monkeypatch.setattr(service.graph_ingestion, "sync_document_to_falkor", _fake_sync_document_to_falkor)

    await service.process_document_async(
        str(doc_id),
        file_path=str(input_path),
        organization_id="org-1",
        project_id="proj-1",
        upload_type="incoming",
    )

    stored = await service.get_document(str(doc_id))
    assert stored is not None
    assert stored.ocrEnabled is True
    assert stored.summary == "Auto summary"
    assert stored.keywords == ["alpha", "beta"]
    assert stored.full_text == "Full text"
    assert stored.letterNo == "LTR-300"
    assert stored.subject == "Updated subject"
    assert len(stored.references) == 1
    assert stored.references[0].documentId == str(target_id)
    # Ensure processor invoked with identifying information
    assert stub_processor.calls and stub_processor.calls[0]["document_id"] == str(doc_id)
    assert falkor_sync_calls
    assert falkor_sync_calls[-1]["document_id"] == str(doc_id)
    assert falkor_sync_calls[-1]["document"]["references"][0]["documentId"] == str(target_id)


@pytest.mark.asyncio
async def test_process_document_async_persists_reference_linking_failure(
    monkeypatch,
    tmp_path,
) -> None:
    doc_id = ObjectId()
    fake_db = FakeDatabase([_make_document_dict(_id=doc_id)])
    service = DocumentService(fake_db)
    input_path = tmp_path / "reference-sync-failure.pdf"
    input_path.write_bytes(b"%PDF-1.4\n% test pdf")

    class StubProcessor:
        async def process_document(self, **_kwargs: Any) -> SimpleNamespace:
            return SimpleNamespace(
                success=True,
                metadata=SimpleNamespace(
                    summary="Metadata saved",
                    keywords=[],
                    contractual_clauses=[],
                    references=[{"text": "LTR-MISSING"}],
                    full_content="Full text",
                    subject="Reference failure",
                    letter_no="LTR-500",
                    from_company=None,
                    to_company=None,
                    date="2025-01-01",
                ),
                processing_time=1.0,
                chunks_created=1,
                processed_path=str(input_path),
            )

    async def _fake_ingest_document(**_kwargs: Any) -> None:
        return None

    async def _fail_reference_sync(**_kwargs: Any) -> Dict[str, Any]:
        raise ReferenceSyncError("simulated reference persistence failure")

    monkeypatch.setattr(
        "rbac_backend.services.document_service.create_document_processor",
        lambda: StubProcessor(),
    )
    monkeypatch.setattr(service.graph_ingestion, "ingest_document", _fake_ingest_document)
    monkeypatch.setattr(
        service.reference_sync_service,
        "sync_bidirectional",
        _fail_reference_sync,
    )

    processed = await service.process_document_async(
        str(doc_id),
        file_path=str(input_path),
        organization_id="org-1",
        project_id="proj-1",
        upload_type="incoming",
    )

    stored = await service.get_document(str(doc_id))
    assert processed is False
    assert stored is not None
    assert stored.summary == "Metadata saved"
    assert stored.processing_status == "failed"
    assert stored.processing_error is not None
    assert "references could not be linked" in stored.processing_error["message"]


@pytest.mark.asyncio
async def test_process_document_async_clears_stale_parser_references(monkeypatch, tmp_path) -> None:
    source_id = ObjectId()
    target_id = ObjectId()
    now = datetime.utcnow()
    source_doc = _make_document_dict(
        _id=source_id,
        reference=[{"letterNo": "LTR-OLD"}],
        references=[
            {
                "documentId": str(target_id),
                "linkType": "indirect",
                "linkedAt": now,
                "linkedBy": "parser",
                "letterNo": "LTR-OLD",
                "source": "parser",
            }
        ],
    )
    target_doc = _make_document_dict(
        _id=target_id,
        letterNo="LTR-OLD",
        referencedBy=[
            {
                "documentId": str(source_id),
                "linkType": "indirect",
                "linkedAt": now,
                "linkedBy": "parser",
                "letterNo": "LTR-001",
                "source": "parser",
            }
        ],
    )
    fake_db = FakeDatabase([source_doc, target_doc])
    service = DocumentService(fake_db)
    input_path = tmp_path / "example-no-refs.pdf"
    input_path.write_bytes(b"%PDF-1.4\n% test pdf")

    class StubProcessor:
        async def process_document(self, **_kwargs: Any) -> SimpleNamespace:
            metadata = SimpleNamespace(
                summary="No refs summary",
                keywords=[],
                contractual_clauses=[],
                references=[],
                full_content="Full text",
                subject="Updated without refs",
                letter_no="LTR-001",
                from_company=None,
                to_company=None,
                date="2024-02-01",
            )
            return SimpleNamespace(
                success=True,
                metadata=metadata,
                processing_time=1.0,
                chunks_created=1,
                processed_path="/processed/example-no-refs.pdf",
            )

    falkor_sync_calls: list[dict[str, Any]] = []

    async def _fake_ingest_document(**_kwargs: Any) -> None:
        return None

    def _fake_sync_document_to_falkor(**kwargs: Any) -> None:
        falkor_sync_calls.append(kwargs)

    monkeypatch.setattr(
        "rbac_backend.services.document_service.create_document_processor",
        lambda: StubProcessor(),
    )
    monkeypatch.setattr(service.graph_ingestion, "ingest_document", _fake_ingest_document)
    monkeypatch.setattr(service.graph_ingestion, "sync_document_to_falkor", _fake_sync_document_to_falkor)

    await service.process_document_async(
        str(source_id),
        file_path=str(input_path),
        organization_id="org-1",
        project_id="proj-1",
        upload_type="incoming",
    )

    stored_source = await service.get_document(str(source_id))
    stored_target = await service.get_document(str(target_id))
    assert stored_source is not None
    assert stored_target is not None
    assert stored_source.reference == []
    assert stored_source.references == []
    assert stored_target.referencedBy == []
    assert falkor_sync_calls
    assert falkor_sync_calls[-1]["document"]["references"] == []
