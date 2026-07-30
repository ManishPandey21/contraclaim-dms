"""Tasks backend: fields, comments, claim link, and PolicyService gating."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.models.task import TaskCommentCreate, TaskCreate, TaskUpdate
from rbac_backend.routers.tasks import add_task_comment, create_task, list_tasks, update_task
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.scope_service import ScopeService


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs)


class _FakeTasks:
    def __init__(self, seed=None):
        self.docs = {d["_id"]: dict(d) for d in (seed or [])}

    async def find_one(self, query):
        doc = self.docs.get(query.get("_id"))
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

    async def delete_one(self, query):
        self.docs.pop(query.get("_id"), None)
        return SimpleNamespace(deleted_count=1)

    def find(self, query):
        scalar = {k: v for k, v in query.items() if not isinstance(v, dict)}
        matched = [d for d in self.docs.values() if all(d.get(k) == v for k, v in scalar.items())]
        return _Cursor(matched)


class _FakeDB:
    def __init__(self, seed=None):
        self.tasks = _FakeTasks(seed)


# --- policy fakes (mirror test_claims._policy) ----------------------------


class _ScopeCursor:
    async def to_list(self, length=None):
        return []


class _ScopeColl:
    def find(self, *_a, **_k):
        return _ScopeCursor()


class _ScopeProjects:
    """Minimal projects collection so tenant-isolation checks resolve.

    ScopeService.project_belongs_to_organization fails closed when the
    projects collection is absent, which otherwise masks the control under
    test with an unrelated 403 scope_denied.
    """

    _DOCS = {"proj-A": "org-A", "proj-B": "org-B"}

    async def find_one(self, query):
        raw = (query or {}).get("_id")
        candidates = raw.get("$in", []) if isinstance(raw, dict) else [raw]
        for candidate in candidates:
            org = self._DOCS.get(str(candidate))
            if org:
                return {"_id": str(candidate), "organization_id": org}
        return None


class _ScopeDB:
    organization_memberships = _ScopeColl()
    project_memberships = _ScopeColl()
    projects = _ScopeProjects()


class _PermAllow:
    async def user_has_permission(self, *_a, **_k):
        return True


class _EntAllow:
    async def check_permission_entitlement(self, **_k):
        return True, "ok"


class _Audit:
    async def emit(self, **_k):
        return None


def _policy():
    return PolicyService(
        permission_service=_PermAllow(),
        scope_service=ScopeService(db=_ScopeDB()),
        entitlement_service=_EntAllow(),
        audit_service=_Audit(),
    )


def _user(*, org="org-A", roles=("orguser",), uid="u-1", projects=()):
    return SimpleNamespace(
        id=uid, username="u1", email="u1@example.com",
        first_name="Ada", last_name="Lovelace",
        roles=list(roles), organization_id=org, organizations=[org] if org else [],
        projects=list(projects), account_type="client_user",
    )


# --- create / fields / claim link -----------------------------------------


@pytest.mark.asyncio
async def test_create_task_persists_fields_and_claim_link():
    db = _FakeDB()
    due = datetime(2026, 7, 1, 12, 0, 0)
    body = TaskCreate(
        title="Review EOT claim", description="Clause 8.4", due_date=due,
        document_id="doc-7", linked_claim_id="claim-9",
    )
    task = await create_task(body=body, db=db, current_user=_user(), policy=_policy())
    assert task.title == "Review EOT claim"
    assert task.due_date == due
    assert task.document_id == "doc-7"
    assert task.linked_claim_id == "claim-9"
    assert task.organization_id == "org-A"  # defaulted from user
    assert task.comments == []


@pytest.mark.asyncio
async def test_create_task_denies_cross_tenant():
    db = _FakeDB()
    body = TaskCreate(title="X", organization_id="org-B")
    with pytest.raises(HTTPException) as exc:
        await create_task(body=body, db=db, current_user=_user(org="org-A"), policy=_policy())
    assert exc.value.status_code == 403


# --- comments --------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_comment_appends_with_author():
    db = _FakeDB(seed=[{"_id": "t-1", "title": "T", "organization_id": "org-A"}])
    task = await add_task_comment(
        task_id="t-1", body=TaskCommentCreate(text="Looks good, approve."),
        db=db, current_user=_user(), policy=_policy(),
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
            db=db, current_user=_user(org="org-A"), policy=_policy(),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_add_comment_missing_task_404():
    db = _FakeDB()
    with pytest.raises(HTTPException) as exc:
        await add_task_comment(
            task_id="nope", body=TaskCommentCreate(text="x"),
            db=db, current_user=_user(), policy=_policy(),
        )
    assert exc.value.status_code == 404


# --- list + update ---------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_scoped_and_claim_filter():
    db = _FakeDB(seed=[
        {"_id": "t1", "title": "A", "organization_id": "org-A", "linked_claim_id": "c1"},
        {"_id": "t2", "title": "B", "organization_id": "org-A", "linked_claim_id": "c2"},
    ])
    user = _user(org="org-A")
    # Pass explicit args: calling the router fn directly would otherwise leave
    # FastAPI Query(None) sentinel objects (truthy) in place of None.
    all_tasks = await list_tasks(
        status_filter=None, assigned_to=None, project_id=None, organization_id=None,
        linked_claim_id=None, task_type=None, resource_type=None, resource_id=None,
        skip=0, limit=100, db=db, current_user=user, policy=_policy(),
    )
    assert {t.id for t in all_tasks} == {"t1", "t2"}
    by_claim = await list_tasks(
        status_filter=None, assigned_to=None, project_id=None, organization_id=None,
        linked_claim_id="c1", task_type=None, resource_type=None, resource_id=None,
        skip=0, limit=100, db=db, current_user=user, policy=_policy(),
    )
    assert [t.id for t in by_claim] == ["t1"]


@pytest.mark.asyncio
async def test_update_task_status():
    db = _FakeDB(seed=[{"_id": "t1", "title": "A", "organization_id": "org-A", "status": "open"}])
    updated = await update_task(
        task_id="t1", body=TaskUpdate(status="done"),
        db=db, current_user=_user(), policy=_policy(),
    )
    assert updated.status == "done"
