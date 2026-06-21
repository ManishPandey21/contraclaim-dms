"""Tests for the reference_sync deferred-queue reaper (Phase 3).

The reaper drains ``reference_sync_queue``: it links references whose targets have
since been ingested, expires stale entries, and drops orphaned ones. Here we drive
the orchestration with a fake queue collection and stub the heavy link/resolve
internals (covered by the document-reference tests).
"""

from datetime import datetime, timedelta

import pytest

from backend.rbac_backend.services.reference_sync_service import ReferenceSyncService


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    async def to_list(self, length=None):
        return list(self._docs)


def _matches(doc, query):
    for key, cond in query.items():
        val = doc.get(key)
        if isinstance(cond, dict):
            if "$lt" in cond and not (val is not None and val < cond["$lt"]):
                return False
            if "$in" in cond and val not in cond["$in"]:
                return False
        elif val != cond:
            return False
    return True


class _FakeQueue:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query):
        return _FakeCursor([d for d in self.docs if _matches(d, query)])

    async def update_many(self, query, update):
        n = 0
        for d in self.docs:
            if _matches(d, query):
                d.update(update["$set"])
                n += 1

        class _R:
            modified_count = n

        return _R()

    async def update_one(self, query, update):
        for d in self.docs:
            if _matches(d, query):
                d.update(update["$set"])
                break


class _FakeDocuments:
    def __init__(self, by_id):
        self._by_id = by_id

    async def find_one(self, query, *_a, **_k):
        return self._by_id.get(query.get("_id"))


class _FakeDB:
    def __init__(self, queue_docs, documents):
        self.reference_sync_queue = _FakeQueue(queue_docs)
        self.documents = _FakeDocuments(documents)


@pytest.mark.asyncio
async def test_reaper_resolves_expires_and_orphans(monkeypatch):
    now = datetime.utcnow()
    old = now - timedelta(days=40)

    queue = [
        # fresh, target now resolvable -> resolved
        {"_id": "q1", "document_id": "src1", "reference": {"letterNo": "L-1"}, "status": "pending", "createdAt": now},
        # fresh, target still missing -> stays pending
        {"_id": "q2", "document_id": "src1", "reference": {"letterNo": "L-2"}, "status": "pending", "createdAt": now},
        # stale -> expired (before grouping)
        {"_id": "q3", "document_id": "src1", "reference": {"letterNo": "L-3"}, "status": "pending", "createdAt": old},
        # source document deleted -> orphaned
        {"_id": "q4", "document_id": "gone", "reference": {"letterNo": "L-4"}, "status": "pending", "createdAt": now},
    ]
    documents = {"src1": {"_id": "src1", "references": []}}  # "gone" intentionally absent
    db = _FakeDB(queue, documents)

    svc = ReferenceSyncService(db=db)

    async def fake_sync(*_a, **_k):
        return {"resolved": 0, "missing": [], "updated_targets": 0, "removed_targets": 0}

    async def fake_resolve(*, db, reference, skip_ids):  # noqa: ARG001
        # Only L-1 has been ingested by the time the reaper runs.
        return {"_id": "tgt1"} if reference.get("letterNo") == "L-1" else None

    monkeypatch.setattr(svc, "sync_bidirectional", fake_sync)
    monkeypatch.setattr(svc, "_resolve_target_document", fake_resolve)
    # _to_object_id would reject our string ids; treat them as-is for the test.
    monkeypatch.setattr(svc, "_to_object_id", lambda v: v)

    summary = await svc.drain_reference_queue(ttl_days=30)

    status = {d["_id"]: d["status"] for d in queue}
    assert status["q1"] == "resolved"
    assert status["q2"] == "pending"
    assert status["q3"] == "expired"
    assert status["q4"] == "orphaned"

    assert summary["resolved"] == 1
    assert summary["expired"] == 1
    assert summary["orphaned"] == 1
    assert summary["processed_docs"] == 1  # only src1; gone is orphaned
