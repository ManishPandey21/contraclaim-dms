"""Phase 2: TaskSyncService keeps the assignment board in step with letters."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from backend.rbac_backend.services.task_sync_service import TaskSyncService

pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, cond in query.items():
        value = doc.get(key)
        if isinstance(cond, dict):
            if "$ne" in cond and value == cond["$ne"]:
                return False
            if "$in" in cond and value not in cond["$in"]:
                return False
        elif value != cond:
            return False
    return True


class _Tasks:
    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []

    async def insert_one(self, doc: Dict[str, Any]):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("_id"))

    async def update_many(self, query: Dict[str, Any], update: Dict[str, Any]):
        modified = 0
        for doc in self.docs:
            if _matches(doc, query):
                doc.update(update.get("$set", {}))
                modified += 1
        return SimpleNamespace(modified_count=modified)


class _DB:
    def __init__(self) -> None:
        self.tasks = _Tasks()


LETTER = {"_id": "L1", "title": "EOT letter", "organization_id": "org-A", "project_id": "p1"}


def _open(db: _DB, task_type: str) -> List[Dict[str, Any]]:
    return [
        d for d in db.tasks.docs
        if d.get("task_type") == task_type and d.get("resource_id") == "L1" and d.get("status") != "done"
    ]


async def test_drafter_assignment_opens_one_draft_task():
    db = _DB()
    svc = TaskSyncService(db)

    await svc.on_drafter_assigned(LETTER, "drafter-1", "manager-1")

    drafts = _open(db, "draft")
    assert len(drafts) == 1
    assert drafts[0]["assigned_to"] == "drafter-1"
    assert drafts[0]["resource_type"] == "letter"
    assert drafts[0]["workflow_stage"] == "Draft"
    assert drafts[0]["title"] == "Draft: EOT letter"


async def test_drafter_assignment_is_idempotent():
    db = _DB()
    svc = TaskSyncService(db)

    await svc.on_drafter_assigned(LETTER, "drafter-1", "manager-1")
    await svc.on_drafter_assigned(LETTER, "drafter-2", "manager-1")

    drafts = _open(db, "draft")
    assert len(drafts) == 1  # prior draft closed, one active
    assert drafts[0]["assigned_to"] == "drafter-2"


async def test_review_closes_draft_and_opens_review():
    db = _DB()
    svc = TaskSyncService(db)
    await svc.on_drafter_assigned(LETTER, "drafter-1", "manager-1")

    await svc.on_letter_status_changed(LETTER, "Review", "drafter-1")

    assert _open(db, "draft") == []
    review = _open(db, "review")
    assert len(review) == 1
    assert review[0]["assigned_to"] is None  # open for pickup


async def test_approval_closes_review_and_opens_approve():
    db = _DB()
    svc = TaskSyncService(db)
    await svc.on_letter_status_changed(LETTER, "Review", "drafter-1")

    await svc.on_letter_status_changed(LETTER, "Approval", "reviewer-1")

    assert _open(db, "review") == []
    assert len(_open(db, "approve")) == 1


async def test_completed_closes_all_stages():
    db = _DB()
    svc = TaskSyncService(db)
    await svc.on_drafter_assigned(LETTER, "drafter-1", "manager-1")
    await svc.on_letter_status_changed(LETTER, "Review", "drafter-1")
    await svc.on_letter_status_changed(LETTER, "Approval", "reviewer-1")

    await svc.on_letter_status_changed(LETTER, "Completed", "approver-1")

    assert _open(db, "draft") == []
    assert _open(db, "review") == []
    assert _open(db, "approve") == []


async def test_never_raises_on_db_error():
    class _Broken:
        @property
        def tasks(self):
            raise RuntimeError("db down")

    # Best-effort contract: must swallow and return None, never propagate.
    await TaskSyncService(_Broken()).on_drafter_assigned(LETTER, "d", "a")
    await TaskSyncService(_Broken()).on_letter_status_changed(LETTER, "Review", "a")
