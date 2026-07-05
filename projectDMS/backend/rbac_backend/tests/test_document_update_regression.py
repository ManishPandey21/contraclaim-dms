"""Regression tests for document update behaviour."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Optional

import pytest

pytest.importorskip("bson")

from bson import ObjectId

from rbac_backend.models.document import Document, DocumentSummaryMetadataUpdate
from rbac_backend.routers.documents import _build_summary_metadata_update
from rbac_backend.services.document_service import DocumentService


def _document_payload(**overrides: Any) -> Dict[str, Any]:
    now = datetime.utcnow()
    payload: Dict[str, Any] = {
        "_id": overrides.get("_id", ObjectId()),
        "organization_id": overrides.get("organization_id", "org-1"),
        "project_id": overrides.get("project_id", "proj-1"),
        "project_name": overrides.get("project_name"),
        "filename": overrides.get("filename", "example.pdf"),
        "filepath_local": overrides.get("filepath_local", "/tmp/example.pdf"),
        "filepath_s3": overrides.get("filepath_s3", "s3://bucket/example.pdf"),
        "presigned_url": overrides.get("presigned_url"),
        "filetype": overrides.get("filetype", "application/pdf"),
        "filesize": overrides.get("filesize", 1234),
        "uploadType": overrides.get("uploadType", "incoming"),
        "letterNo": overrides.get("letterNo", "LTR-001"),
        "date": overrides.get("date", now),
        "subject": overrides.get("subject", "Original subject"),
        "from": overrides.get("from", "Sender"),
        "to": overrides.get("to", "Recipient"),
        "tags": overrides.get("tags", []),
        "subTags": overrides.get("subTags", []),
        "status": overrides.get("status", "Received"),
        "ocrEnabled": overrides.get("ocrEnabled", False),
        "compressionEnabled": overrides.get("compressionEnabled", False),
        "createdAt": overrides.get("createdAt", now),
        "updatedAt": overrides.get("updatedAt", now),
        "createdBy": overrides.get("createdBy", "tester@example.com"),
        "version": overrides.get("version", "1.0"),
        "enclosures": overrides.get("enclosures", []),
        "references": overrides.get("references", []),
        "referencedBy": overrides.get("referencedBy", []),
    }
    payload.update(overrides)
    return payload


class _FakeUpdateResult:
    def __init__(self, matched: int, modified: int) -> None:
        self.matched_count = matched
        self.modified_count = modified


class RecordingCollection:
    """Async collection stub that records update payloads."""

    def __init__(self, documents: Iterable[Dict[str, Any]] | None = None) -> None:
        self._docs: Dict[str, Dict[str, Any]] = {}
        self.last_update_filter: Optional[Dict[str, Any]] = None
        self.last_update_payload: Optional[Dict[str, Any]] = None

        for doc in documents or []:
            key = self._key(doc.get("_id"))
            self._docs[key] = dict(doc)

    @staticmethod
    def _key(value: Any) -> str:
        if isinstance(value, ObjectId):
            return str(value)
        return str(value)

    async def find_one(self, filter: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        identifier = filter.get("_id")
        if identifier is None:
            return None
        key = self._key(identifier)
        document = self._docs.get(key)
        return dict(document) if document else None

    async def update_one(self, filter: Dict[str, Any], update: Dict[str, Any]) -> _FakeUpdateResult:
        self.last_update_filter = dict(filter)
        self.last_update_payload = dict(update)

        identifier = filter.get("_id")
        identifiers = identifier.get("$in") if isinstance(identifier, dict) and "$in" in identifier else [identifier]
        key = next((self._key(candidate) for candidate in identifiers if self._key(candidate) in self._docs), None)
        existing = self._docs.get(key) if key else None
        matched = 1 if existing else 0
        modified = 0

        if existing and "$set" in update:
            before = dict(existing)
            existing.update(update["$set"] or {})
            if existing != before:
                modified = 1
            self._docs[key] = existing

        return _FakeUpdateResult(matched, modified)


@pytest.mark.asyncio
async def test_update_document_strips_identifier_fields_from_update_payload() -> None:
    document_id = ObjectId()
    stored = _document_payload(_id=document_id)
    collection = RecordingCollection([stored])
    fake_db = SimpleNamespace(documents=collection)

    service = DocumentService(fake_db)

    updated_model = Document(**{**stored, "subject": "Updated subject"})

    result = await service.update_document(str(document_id), updated_model)

    assert result is not None
    assert result.subject == "Updated subject"

    assert collection.last_update_payload is not None
    set_payload = collection.last_update_payload.get("$set", {})
    assert "_id" not in set_payload
    assert "id" not in set_payload

    assert collection.last_update_filter == {
        "_id": document_id,
        "lifecycle_state": {"$ne": "deleted"},
    }


def test_summary_metadata_update_payload_normalizes_manual_edits() -> None:
    payload = DocumentSummaryMetadataUpdate(
        extracted_metadata={
            "asset_type": " Tunnel ",
            "location": " Kanpur ",
            "work_type": " Borewell construction ",
            "issue_nature": " Variation ",
            "claim_category": " Additional Work Claim ",
            "responsibility": " Employer ",
        },
        keywords=[" borewell ", "Kanpur", "borewell", "variation, payment"],
        additional_keywords=[" contract\npayment ", "contract"],
        extracted_tags=["Tunnel", "Variation", "Tunnel"],
        extracted_sub_tags=["Kanpur", "Borewell", "", "Kanpur"],
    )

    update = _build_summary_metadata_update(payload)

    assert update["asset_type"] == "Tunnel"
    assert update["metadata.asset_type"] == "Tunnel"
    assert update["alleged_responsibility"] == "Employer"
    assert update["metadata.responsibility"] == "Employer"
    assert update["keywords"] == ["borewell", "Kanpur", "variation", "payment"]
    assert update["metadata.keywords"] == ["borewell", "Kanpur", "variation", "payment"]
    assert update["additional_keywords"] == ["contract", "payment"]
    assert update["extracted_tags"] == ["Tunnel", "Variation"]
    assert update["extracted_subTags"] == ["Kanpur", "Borewell"]
    assert update["metadata.subTags"] == ["Kanpur", "Borewell"]


@pytest.mark.asyncio
async def test_update_summary_metadata_persists_manual_fields_without_reprocessing() -> None:
    document_id = ObjectId()
    stored = _document_payload(_id=document_id, asset_type="Old asset")
    collection = RecordingCollection([stored])
    fake_db = SimpleNamespace(documents=collection)

    service = DocumentService(fake_db)

    result = await service.update_summary_metadata(
        str(document_id),
        {
            "asset_type": "Tunnel",
            "metadata.asset_type": "Tunnel",
            "keywords": ["borewell", "Kanpur"],
        },
        updated_by="user-1",
    )

    assert result is not None
    assert result.asset_type == "Tunnel"
    assert result.keywords == ["borewell", "Kanpur"]

    assert collection.last_update_payload is not None
    set_payload = collection.last_update_payload.get("$set", {})
    assert set_payload["asset_type"] == "Tunnel"
    assert set_payload["metadata.asset_type"] == "Tunnel"
    assert set_payload["keywords"] == ["borewell", "Kanpur"]
    assert set_payload["updated_by"] == "user-1"
    assert set_payload["summary_metadata_updated_by"] == "user-1"
    assert set_payload["manual_summary_metadata_override"] is True
    assert "processing_status" not in set_payload
    assert collection.last_update_payload.get("$inc") == {"_revision": 1}
