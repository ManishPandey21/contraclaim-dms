import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

pytestmark = pytest.mark.anyio("asyncio")

from bson.objectid import ObjectId

from backend.rbac_backend.services.reference_sync_service import ReferenceSyncService


class FakeCursor:
    def __init__(self, documents: List[Dict[str, Any]]):
        self._documents = documents

    def sort(self, *args, **kwargs):
        return self

    def limit(self, value: int):
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        if length is None:
            return list(self._documents)
        return list(self._documents[:length])


class FakeDocumentsCollection:
    def __init__(self, documents: Dict[str, Dict[str, Any]]):
        self._documents = documents
        self.updates: List[Dict[str, Any]] = []

    async def find_one(self, query: Dict[str, Any], projection: Optional[Dict[str, Any]] = None):
        doc_id = query.get("_id")
        if doc_id is not None:
            for document in self._documents.values():
                if document.get("_id") == doc_id:
                    return document
            return None

        norm_letter = query.get("letterNoNormalized")
        if norm_letter:
            for document in self._documents.values():
                if document.get("letterNoNormalized") == norm_letter:
                    return document
            return None
        return None

    def find(self, query: Dict[str, Any], projection: Optional[Dict[str, Any]] = None):
        regex = query.get("letterNo", {}).get("$regex")
        if not regex:
            return FakeCursor([])
        needle = regex.strip("^$")
        matches = [
            document
            for document in self._documents.values()
            if str(document.get("letterNo", "")).lower() == needle.lower()
        ]
        return FakeCursor(matches)

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], **kwargs):
        self.updates.append({"query": query, "update": update})


class FakeQueueCollection:
    def __init__(self):
        self.items: List[Dict[str, Any]] = []

    async def insert_many(self, items: List[Dict[str, Any]]):
        self.items.extend(items)


def test_sync_bidirectional_updates_source_and_target_documents():
    source_id = ObjectId()
    target_id = ObjectId()
    documents = {
        "source": {
            "_id": source_id,
            "letterNo": "DOC-001",
            "letterNoNormalized": "doc-001",
            "references": [],
            "referencedBy": [],
        },
        "target": {
            "_id": target_id,
            "letterNo": "DOC-002",
            "letterNoNormalized": "doc-002",
            "references": [],
            "referencedBy": [],
        },
    }
    fake_documents = FakeDocumentsCollection(documents)
    fake_queue = FakeQueueCollection()
    fake_db = SimpleNamespace(documents=fake_documents, reference_sync_queue=fake_queue)

    service = ReferenceSyncService(fake_db)

    result = asyncio.run(service.sync_bidirectional(
        str(source_id),
        [{"letterNo": "DOC-002"}],
        source="parser",
    ))

    assert result["resolved"] == 1
    assert not result["missing"]
    assert fake_queue.items == []
    assert len(fake_documents.updates) >= 2

    source_update = fake_documents.updates[0]
    target_update = fake_documents.updates[1]

    assert source_update["query"] == {"_id": source_id}
    source_refs = source_update["update"]["$set"]["references"]
    assert source_refs[0]["documentId"] == str(target_id)
    assert source_refs[0]["source"] == "parser"

    assert target_update["query"] == {"_id": target_id}
    target_refs = target_update["update"]["$set"]["referencedBy"]
    assert target_refs[0]["documentId"] == str(source_id)
    assert target_refs[0]["source"] == "parser"


def test_sync_bidirectional_records_missing_references():
    source_id = ObjectId()
    documents = {
        "source": {
            "_id": source_id,
            "letterNo": "DOC-100",
            "letterNoNormalized": "doc-100",
            "references": [],
            "referencedBy": [],
        },
    }
    fake_documents = FakeDocumentsCollection(documents)
    fake_queue = FakeQueueCollection()
    fake_db = SimpleNamespace(documents=fake_documents, reference_sync_queue=fake_queue)

    service = ReferenceSyncService(fake_db)

    result = asyncio.run(service.sync_bidirectional(
        str(source_id),
        [{"letterNo": "UNKNOWN-REF"}],
        source="parser",
    ))

    assert result["resolved"] == 0
    assert len(result["missing"]) == 1
    assert len(fake_queue.items) == 1
    queued = fake_queue.items[0]
    assert queued["document_id"] == str(source_id)
    assert queued["reference"]["letterNo"] == "UNKNOWN-REF"





