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

    def _matches_scope(self, document: Dict[str, Any], query: Dict[str, Any]) -> bool:
        for key, value in query.items():
            if key in {"_id", "letterNoNormalized", "letterNo"}:
                continue
            if document.get(key) != value:
                return False
        return True

    async def find_one(self, query: Dict[str, Any], projection: Optional[Dict[str, Any]] = None):
        doc_id = query.get("_id")
        if doc_id is not None:
            for document in self._documents.values():
                if document.get("_id") == doc_id and self._matches_scope(document, query):
                    return document
            return None

        norm_letter = query.get("letterNoNormalized")
        if norm_letter:
            for document in self._documents.values():
                if (
                    document.get("letterNoNormalized") == norm_letter
                    and self._matches_scope(document, query)
                ):
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
            and self._matches_scope(document, query)
        ]
        return FakeCursor(matches)

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], **kwargs):
        self.updates.append({"query": query, "update": update})
        doc_id = query.get("_id")
        for document in self._documents.values():
            if document.get("_id") != doc_id:
                continue
            for key, value in update.get("$set", {}).items():
                document[key] = value
            return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


class FakeQueueCollection:
    def __init__(self):
        self.items: List[Dict[str, Any]] = []

    async def update_one(
        self,
        query: Dict[str, Any],
        update: Dict[str, Any],
        *,
        upsert: bool = False,
    ):
        existing = next(
            (
                item
                for item in self.items
                if all(item.get(key) == value for key, value in query.items())
            ),
            None,
        )
        if existing is None and upsert:
            existing = dict(query)
            existing.update(update.get("$setOnInsert", {}))
            self.items.append(existing)
        if existing is not None:
            existing.update(update.get("$set", {}))
        return SimpleNamespace(modified_count=1 if existing is not None else 0)

    async def update_many(self, query: Dict[str, Any], update: Dict[str, Any]):
        modified = 0
        for item in self.items:
            matches = True
            for key, value in query.items():
                if isinstance(value, dict) and "$in" in value:
                    if item.get(key) not in value["$in"]:
                        matches = False
                        break
                elif item.get(key) != value:
                    matches = False
                    break
            if matches:
                item.update(update.get("$set", {}))
                modified += 1
        return SimpleNamespace(modified_count=modified)


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


def test_sync_bidirectional_scopes_letter_resolution_to_source_project():
    source_id = ObjectId()
    wrong_target_id = ObjectId()
    target_id = ObjectId()
    documents = {
        "source": {
            "_id": source_id,
            "organization_id": "org-1",
            "project_id": "proj-1",
            "letterNo": "DOC-001",
            "letterNoNormalized": "doc-001",
            "references": [],
            "referencedBy": [],
        },
        "wrong_target": {
            "_id": wrong_target_id,
            "organization_id": "org-2",
            "project_id": "proj-9",
            "letterNo": "DOC-002",
            "letterNoNormalized": "doc-002",
            "references": [],
            "referencedBy": [],
        },
        "target": {
            "_id": target_id,
            "organization_id": "org-1",
            "project_id": "proj-1",
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
    source_refs = fake_documents.updates[0]["update"]["$set"]["references"]
    assert source_refs[0]["documentId"] == str(target_id)


def test_sync_bidirectional_is_idempotent_for_existing_relationships():
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
    fake_db = SimpleNamespace(
        documents=fake_documents,
        reference_sync_queue=FakeQueueCollection(),
    )
    service = ReferenceSyncService(fake_db)

    first = asyncio.run(
        service.sync_bidirectional(
            str(source_id),
            [{"letterNo": "DOC-002"}],
            source="parser",
        )
    )
    stored_reference = dict(documents["source"]["references"][0])
    fake_documents.updates.clear()

    second = asyncio.run(
        service.sync_bidirectional(
            str(source_id),
            [{"letterNo": "DOC-002"}, {"text": "DOC-002"}],
            source="parser",
        )
    )

    assert first["resolved"] == second["resolved"] == 1
    assert second["updated_targets"] == 0
    assert documents["source"]["references"] == [stored_reference]
    assert len(documents["target"]["referencedBy"]) == 1
    assert fake_documents.updates == []


def test_sync_bidirectional_matches_plain_text_reference_payloads():
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
    fake_db = SimpleNamespace(
        documents=fake_documents,
        reference_sync_queue=FakeQueueCollection(),
    )

    result = asyncio.run(
        ReferenceSyncService(fake_db).sync_bidirectional(
            str(source_id),
            [{"text": "DOC-002"}],
            source="parser",
        )
    )

    assert result["resolved"] == 1
    assert documents["source"]["references"][0]["documentId"] == str(target_id)


def test_sync_bidirectional_does_not_duplicate_missing_queue_entries():
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
    queue = FakeQueueCollection()
    fake_db = SimpleNamespace(
        documents=FakeDocumentsCollection(documents),
        reference_sync_queue=queue,
    )
    service = ReferenceSyncService(fake_db)

    for _ in range(2):
        asyncio.run(
            service.sync_bidirectional(
                str(source_id),
                [{"letterNo": "UNKNOWN-REF"}, {"text": "UNKNOWN-REF"}],
                source="parser",
            )
        )

    assert len(queue.items) == 1
    assert queue.items[0]["reference_key"] == "letter:unknown-ref"





