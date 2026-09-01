from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId

from rbac_backend.services.document_service import (
    DocumentDependencyError,
    DocumentService,
)


class _Collection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = [deepcopy(item) for item in documents]

    async def count_documents(self, query: dict[str, Any], **kwargs: Any) -> int:
        return len(
            [
                item
                for item in self.documents
                if str(item.get("document_id")) == str(query.get("document_id"))
                and item.get("removed_at") is None
            ]
        )

    async def find_one(self, query: dict[str, Any], **kwargs: Any):
        candidates = query.get("_id", {}).get("$in") if isinstance(query.get("_id"), dict) else None
        for item in self.documents:
            if candidates is not None and item.get("_id") in candidates:
                return deepcopy(item)
            if candidates is None and item.get("_id") == query.get("_id"):
                return deepcopy(item)
        return None

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]):
        for item in self.documents:
            if item.get("_id") == query.get("_id"):
                item.update(deepcopy(update.get("$set", {})))
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class _Claims:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = [deepcopy(item) for item in documents]

    async def count_documents(self, query: dict[str, Any], **kwargs: Any) -> int:
        return len(
            [
                item
                for item in self.documents
                if item.get("organization_id") == query.get("organization_id")
                and item.get("project_id") == query.get("project_id")
                and str(query.get("linked_document_ids"))
                in {str(value) for value in item.get("linked_document_ids") or []}
            ]
        )

    def find(self, query: dict[str, Any], **kwargs: Any):
        rows = [
            deepcopy(item)
            for item in self.documents
            if item.get("organization_id") == query.get("organization_id")
            and item.get("project_id") == query.get("project_id")
            and str(query.get("linked_document_ids"))
            in {str(value) for value in item.get("linked_document_ids") or []}
        ]

        class _Cursor:
            def __aiter__(self):
                self.iterator = iter(rows)
                return self

            async def __anext__(self):
                try:
                    return next(self.iterator)
                except StopIteration as exc:
                    raise StopAsyncIteration from exc

        return _Cursor()


class _Database:
    def __init__(self, document_id: ObjectId) -> None:
        self.documents = _Collection(
            [
                {
                    "_id": document_id,
                    "lifecycle_state": "active",
                    "processing_status": "metadata_extracted",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "_revision": 1,
                }
            ]
        )
        self.entity_document_links = _Collection(
            [
                {
                    "_id": "link-1",
                    "document_id": str(document_id),
                    "target_type": "claim",
                    "target_id": "claim-1",
                    "removed_at": None,
                }
            ]
        )
        self.claims = _Claims([])
        self.ipc_bills = _Claims([])
        self.bank_guarantees = _Claims([])


@pytest.mark.asyncio
async def test_default_document_deletion_is_blocked_by_active_claim_relationships() -> None:
    document_id = ObjectId()
    db = _Database(document_id)
    service = DocumentService(db=db)

    with pytest.raises(DocumentDependencyError) as exc_info:
        await service.delete_document(str(document_id), expected_revision=1)

    assert exc_info.value.active_relationship_count == 1
    assert db.documents.documents[0]["lifecycle_state"] == "active"


@pytest.mark.asyncio
async def test_default_document_deletion_is_blocked_by_authoritative_legacy_claim_membership() -> None:
    document_id = ObjectId()
    db = _Database(document_id)
    db.entity_document_links.documents.clear()
    db.claims.documents.append(
        {
            "_id": "claim-legacy",
            "organization_id": "org-1",
            "project_id": "project-1",
            "linked_document_ids": [str(document_id)],
        }
    )

    with pytest.raises(DocumentDependencyError) as exc_info:
        await DocumentService(db=db).delete_document(str(document_id), expected_revision=1)

    assert exc_info.value.active_relationship_count == 1
    assert db.documents.documents[0]["lifecycle_state"] == "active"


@pytest.mark.asyncio
async def test_default_document_deletion_is_blocked_by_authoritative_legacy_ipc_membership() -> None:
    document_id = ObjectId()
    db = _Database(document_id)
    db.entity_document_links.documents.clear()
    db.ipc_bills.documents.append(
        {
            "_id": "ipc-legacy",
            "organization_id": "org-1",
            "project_id": "project-1",
            "linked_document_ids": [str(document_id)],
        }
    )

    with pytest.raises(DocumentDependencyError) as exc_info:
        await DocumentService(db=db).delete_document(str(document_id), expected_revision=1)

    assert exc_info.value.active_relationship_count == 1
    assert db.documents.documents[0]["lifecycle_state"] == "active"
