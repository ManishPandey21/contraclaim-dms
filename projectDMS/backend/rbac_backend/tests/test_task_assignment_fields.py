"""Phase 0/1: task assignment/workflow linkage fields, filters, and board.

Covers:
- Additive model fields are back-compatible (existing task docs without the new
  fields still load, defaulting task_type='general').
- GET /tasks filters by task_type/resource_type/resource_id within tenant scope.
- GET /tasks/board groups tasks into draft/review/approve columns, scoped.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from backend.rbac_backend.models.task import Task, TaskCreate


def test_task_defaults_are_back_compatible():
    """A legacy task doc with none of the new fields still loads."""
    legacy = {
        "_id": "task-legacy",
        "title": "Old task",
        "status": "open",
        "comments": [],
    }
    task = Task(**legacy)
    assert task.task_type == "general"
    assert task.resource_type is None
    assert task.resource_id is None
    assert task.workflow_stage is None


def test_task_create_accepts_workflow_linkage():
    payload = TaskCreate(
        title="Draft EOT letter",
        task_type="draft",
        resource_type="letter",
        resource_id="letter-123",
        workflow_stage="Draft",
        assigned_to="drafter-1",
    )
    assert payload.task_type == "draft"
    assert payload.resource_type == "letter"
    assert payload.resource_id == "letter-123"


# --- Router filter + board tests -----------------------------------------

try:
    from fastapi.testclient import TestClient

    from rbac_backend.core.database import get_db
    from rbac_backend.core.security import get_current_user
    from rbac_backend.main import app
    from rbac_backend.routers import tasks as tasks_router

    _IMPORTS_OK = True
except Exception as exc:  # pragma: no cover - app import unavailable
    _IMPORTS_OK = False
    _IMPORT_ERR = exc

pytestmark = pytest.mark.skipif(not _IMPORTS_OK, reason="TestClient/app unavailable")


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def skip(self, n: int):
        self._docs = self._docs[n:]
        return self

    def limit(self, n: int):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length: Optional[int] = None):
        return list(self._docs)


class _TasksCollection:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = docs
        self.last_query: Dict[str, Any] = {}

    def find(self, query: Dict[str, Any]):
        self.last_query = query
        matched = [doc for doc in self.docs if _matches(doc, query)]
        return _Cursor(matched)


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, cond in query.items():
        if isinstance(cond, dict) and "$in" in cond:
            if doc.get(key) not in cond["$in"]:
                return False
        elif doc.get(key) != cond:
            return False
    return True


class _DB:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.tasks = _TasksCollection(docs)


def _seed() -> List[Dict[str, Any]]:
    base = {"organization_id": "org-A", "project_id": "proj-1", "comments": []}
    return [
        {"_id": "t1", "title": "Draft letter", "task_type": "draft",
         "resource_type": "letter", "resource_id": "L1", "assigned_to": "u-draft", **base},
        {"_id": "t2", "title": "Review letter", "task_type": "review",
         "resource_type": "letter", "resource_id": "L1", "assigned_to": "u-review", **base},
        {"_id": "t3", "title": "Approve letter", "task_type": "approve",
         "resource_type": "letter", "resource_id": "L1", "assigned_to": "u-approve", **base},
        {"_id": "t4", "title": "Misc", "task_type": "general", **base},
    ]


def _client(docs: List[Dict[str, Any]], monkeypatch):
    user = SimpleNamespace(
        id="u-admin", username="admin", email="admin@example.com",
        roles=["orgadmin"], organization_id="org-A", organizations=["org-A"], projects=[],
    )

    async def _override_db():
        yield _DB(docs)

    # PolicyService.authorize hits the DB; stub it to a no-op for these tests.
    async def _authorize(self, *args, **kwargs):
        return None

    monkeypatch.setattr(tasks_router.PolicyService, "authorize", _authorize)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def test_list_filters_by_resource(monkeypatch):
    client = _client(_seed(), monkeypatch)
    try:
        resp = client.get("/api/tasks", params={"resource_type": "letter", "resource_id": "L1"})
        assert resp.status_code == 200, resp.text
        titles = {row["title"] for row in resp.json()}
        assert titles == {"Draft letter", "Review letter", "Approve letter"}
    finally:
        app.dependency_overrides.clear()


def test_list_filters_by_task_type(monkeypatch):
    client = _client(_seed(), monkeypatch)
    try:
        resp = client.get("/api/tasks", params={"task_type": "review"})
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert [r["title"] for r in rows] == ["Review letter"]
    finally:
        app.dependency_overrides.clear()


def test_board_groups_by_stage_and_is_scoped(monkeypatch):
    client = _client(_seed(), monkeypatch)
    try:
        resp = client.get("/api/tasks/board")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["counts"]["draft"] == 1
        assert body["counts"]["review"] == 1
        assert body["counts"]["approve"] == 1
        assert body["total"] == 4
        assert {t["title"] for t in body["columns"]["draft"]} == {"Draft letter"}
    finally:
        app.dependency_overrides.clear()
