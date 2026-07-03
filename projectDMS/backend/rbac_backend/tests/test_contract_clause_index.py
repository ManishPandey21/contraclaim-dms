"""Phase 4 tests for the Clause Index service: list, edit title, verify,
supersede, regenerate embedding, merge, split, and scope authorization.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from rbac_backend.core.permissions import Permissions
from rbac_backend.services.contract_clause import (
    ClauseIndexService,
    ClauseMergeError,
    ClauseNotFoundError,
    ClauseStorageService,
)


# --------------------------------------------------------------------------- #
# Async Mongo double
# --------------------------------------------------------------------------- #
class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self):
        self.docs = {}

    def find(self, filt):
        did = filt.get("document_id")
        return _Cursor([d for d in self.docs.values() if d.get("document_id") == did])

    async def find_one(self, filt):
        d = self.docs.get(filt.get("clause_uid"))
        return dict(d) if d else None

    async def update_one(self, filt, update, upsert=False):
        uid = filt["clause_uid"]
        if uid in self.docs:
            self.docs[uid].update(update.get("$set", {}))
        elif upsert:
            self.docs[uid] = {"clause_uid": uid, **update.get("$set", {})}

    async def delete_one(self, filt):
        self.docs.pop(filt.get("clause_uid"), None)


class FakeDB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


class AllowPolicy:
    def __init__(self):
        self.calls = []

    async def authorize(self, current_user, permission, **kwargs):
        self.calls.append({"permission": permission, **kwargs})


class DenyPolicy:
    async def authorize(self, current_user, permission, **kwargs):
        raise HTTPException(status_code=403, detail="denied")


class _User:
    id = "user-1"


SCOPE = dict(org_id="org-A", project_id="proj-A", contract_id="ct-1", document_id="doc-1")


def _seed(db, **overrides):
    storage = ClauseStorageService(db=None)
    rec = storage.build_record(
        **{**SCOPE, "clause_no": "8.4", "clause_title": "EOT", "text": "clause body",
           "document_type": "GCC", **overrides}
    )
    db[ClauseIndexService.COLLECTION].docs[rec.clause_uid] = rec.to_mongo()
    return rec


# --------------------------------------------------------------------------- #
# List
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_list_clauses_returns_display_rows():
    db = FakeDB()
    _seed(db, clause_no="8", clause_title="General", page_start=1)
    _seed(db, clause_no="8.4", clause_title="EOT", page_start=2)
    svc = ClauseIndexService(db, policy_service=AllowPolicy())

    rows = await svc.list_clauses(_User(), document_id="doc-1", org_id="org-A", project_id="proj-A")
    assert len(rows) == 2
    assert {r["clause_no"] for r in rows} == {"8", "8.4"}
    assert "embedding_status" in rows[0] and "quality_status" in rows[0]


@pytest.mark.asyncio
async def test_list_requires_clause_read_permission():
    db = FakeDB()
    _seed(db)
    svc = ClauseIndexService(db, policy_service=DenyPolicy())
    with pytest.raises(HTTPException):
        await svc.list_clauses(_User(), document_id="doc-1", org_id="org-A", project_id="proj-A")


# --------------------------------------------------------------------------- #
# Edit title / verify / supersede
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_edit_title_and_verify():
    db = FakeDB()
    rec = _seed(db, confidence="low")  # starts needs_review
    svc = ClauseIndexService(db, policy_service=AllowPolicy())

    updated = await svc.update_clause(_User(), rec.clause_uid, clause_title="Time for Completion", mark_verified=True)
    assert updated["clause_title"] == "Time for Completion"
    stored = db[ClauseIndexService.COLLECTION].docs[rec.clause_uid]
    assert stored["quality_status"] == "validated"
    assert stored["human_review_required"] is False
    assert stored["is_authorised_for_ai"] is True
    assert stored["verified_by"] == "user-1"
    assert stored["manually_edited"] is True


@pytest.mark.asyncio
async def test_mark_superseded_clears_current():
    db = FakeDB()
    rec = _seed(db)
    svc = ClauseIndexService(db, policy_service=AllowPolicy())
    await svc.update_clause(_User(), rec.clause_uid, is_superseded=True, superseded_by_clause_id="uid-new")
    stored = db[ClauseIndexService.COLLECTION].docs[rec.clause_uid]
    assert stored["is_superseded"] is True
    assert stored["is_current"] is False
    assert stored["superseded_by_clause_id"] == "uid-new"


@pytest.mark.asyncio
async def test_update_uses_clause_create_permission_and_scope():
    db = FakeDB()
    rec = _seed(db)
    policy = AllowPolicy()
    svc = ClauseIndexService(db, policy_service=policy)
    await svc.update_clause(_User(), rec.clause_uid, clause_title="x")
    assert policy.calls[0]["permission"] == Permissions.CONTRACT_CLAUSE_CREATE
    assert policy.calls[0]["organization_id"] == "org-A"


@pytest.mark.asyncio
async def test_update_missing_clause_raises():
    svc = ClauseIndexService(FakeDB(), policy_service=AllowPolicy())
    with pytest.raises(ClauseNotFoundError):
        await svc.update_clause(_User(), "nope", clause_title="x")


# --------------------------------------------------------------------------- #
# Regenerate embedding
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_regenerate_embedding_resets_status():
    db = FakeDB()
    rec = _seed(db)
    db[ClauseIndexService.COLLECTION].docs[rec.clause_uid]["embedding_status"] = "done"
    policy = AllowPolicy()
    svc = ClauseIndexService(db, policy_service=policy)

    await svc.regenerate_embedding(_User(), rec.clause_uid)
    stored = db[ClauseIndexService.COLLECTION].docs[rec.clause_uid]
    assert stored["embedding_status"] == "pending"
    assert stored["qdrant_point_id"] is None
    assert policy.calls[0]["permission"] == Permissions.AI_CONTRACT_PROCESSING_RUN


# --------------------------------------------------------------------------- #
# Merge / split
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_merge_combines_and_deletes_others():
    db = FakeDB()
    a = _seed(db, clause_no="8.4", text="Part A")
    b = _seed(db, clause_no="8.4", text="Part B", chunk_part=2, chunk_index=1)
    assert a.clause_uid != b.clause_uid
    svc = ClauseIndexService(db, policy_service=AllowPolicy())

    merged = await svc.merge_clauses(_User(), [a.clause_uid, b.clause_uid])
    docs = db[ClauseIndexService.COLLECTION].docs
    assert b.clause_uid not in docs           # second deleted
    assert a.clause_uid in docs               # first kept
    assert "Part A" in docs[a.clause_uid]["text"] and "Part B" in docs[a.clause_uid]["text"]
    assert docs[a.clause_uid]["embedding_status"] == "pending"
    assert merged["chunk_total"] == 1


@pytest.mark.asyncio
async def test_merge_rejects_cross_document():
    db = FakeDB()
    a = _seed(db, clause_no="8.4")
    b = _seed(db, clause_no="8.4", document_id="doc-2")
    svc = ClauseIndexService(db, policy_service=AllowPolicy())
    with pytest.raises(ClauseMergeError):
        await svc.merge_clauses(_User(), [a.clause_uid, b.clause_uid])


@pytest.mark.asyncio
async def test_split_produces_two_parts():
    db = FakeDB()
    rec = _seed(db, clause_no="8.4", text="First half. Second half.")
    svc = ClauseIndexService(db, policy_service=AllowPolicy())

    parts = await svc.split_clause(_User(), rec.clause_uid, split_at=12)
    assert len(parts) == 2
    assert parts[0]["chunk_part"] == 1 and parts[1]["chunk_part"] == 2
    docs = db[ClauseIndexService.COLLECTION].docs
    assert f"{rec.clause_uid}:split:2" in docs
    assert docs[rec.clause_uid]["embedding_status"] == "pending"


@pytest.mark.asyncio
async def test_split_out_of_range_rejected():
    db = FakeDB()
    rec = _seed(db, text="short")
    svc = ClauseIndexService(db, policy_service=AllowPolicy())
    with pytest.raises(ClauseMergeError):
        await svc.split_clause(_User(), rec.clause_uid, split_at=999)
