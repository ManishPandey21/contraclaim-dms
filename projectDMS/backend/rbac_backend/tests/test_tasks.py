"""Tasks backend: new fields (due_date/document_id) + comments + tenant scope."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.models.task import TaskCommentCreate, TaskCreate
from rbac_backend.routers.tasks import add_task_comment, create_task


class _FakeTasks:
    def __init__(self, seed=None):
        self.docs = {d["_id"]: dict(d) for d in (seed or [])}

    async def find_one(self, query):
        _id = query.get("_id")
        doc = self.docs.get(_id)
        return dict(doc) if doc else None

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = dict(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    async def find_one_and_update(self, query, update, return_document=True):
        _id = query.get("_id")
        if _id not in self.docs:
            return None
        doc = self.docs[_id]
        for k, v in (update.get("$set") or {}).items():
            doc[k] = v
        for k, v in (update.get("$push") or {}).items():
            doc.setdefault(k, []).append(v)
        return dict(doc)


class _FakeDB:
    def __init__(self, seed=None):
        self.tasks = _FakeTasks(seed)


def _user(*, org="org-A", roles=("orguser",), uid="u-1"):
    return SimpleNamespace(
        id=uid, username="u1", email="u1@example.com",
        first_name="Ada", last_name="Lovelace",
        roles=list(roles), organization_id=org, projects=[], account_type="client_user",
    )


@pytest.mark.asyncio
async def test_create_task_persists_due_date_and_document_id():
    db = _FakeDB()
    due = datetime(2026, 7, 1, 12, 0, 0)
    body = TaskCreate(title="Review EOT claim", description="Clause 8.4", due_date=due, document_id="doc-7")
    task = await create_task(body=body, db=db, current_user=_user())
    assert task.title == "Review EOT claim"
    assert task.due_date == due
    assert task.document_id == "doc-7"
    assert task.organization_id == "org-A"  # defaulted from user
    assert task.comments == []


@pytest.mark.asyncio
async def test_add_comment_appends_with_author():
    db = _FakeDB(seed=[{"_id": "t-1", "title": "T", "organization_id": "org-A"}])
    task = await add_task_comment(
        task_id="t-1", body=TaskCommentCreate(text="Looks good, approve."),
        db=db, current_user=_user(),
    )
    assert len(task.comments) == 1
    assert task.comments[0].text == "Looks good, approve."
    assert task.comments[0].author_id == "u-1"
    assert task.comments[0].author_name == "Ada Lovelace"


@pytest.mark.asyncio
async def test_add_comment_denied_cross_tenant():
    db = _FakeDB(seed=[{"_id": "t-b", "title": "Other tenant", "organization_id": "org-B"}])
    with pytest.raises(HTTPException) as exc:
        await add_task_comment(
            task_id="t-b", body=TaskCommentCreate(text="should be blocked"),
            db=db, current_user=_user(org="org-A"),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_add_comment_missing_task_404():
    db = _FakeDB()
    with pytest.raises(HTTPException) as exc:
        await add_task_comment(
            task_id="nope", body=TaskCommentCreate(text="x"),
            db=db, current_user=_user(),
        )
    assert exc.value.status_code == 404
