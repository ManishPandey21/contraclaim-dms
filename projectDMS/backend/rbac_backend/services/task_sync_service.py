"""Keep the assignment board (tasks) in sync with workflow lifecycles.

Phase 2: letters. As a letter moves through draft -> review -> approval ->
completed, this service maintains exactly one active task per stage linked to the
letter (resource_type="letter", resource_id=<letter id>). All methods are
best-effort: they never raise into the caller, so a task-sync hiccup can never
block a letter transition.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from ..models.task import Task

logger = logging.getLogger(__name__)

RESOURCE_LETTER = "letter"


def _field(obj: Any, *names: str) -> Optional[str]:
    for name in names:
        value = getattr(obj, name, None)
        if value is None and isinstance(obj, dict):
            value = obj.get(name)
        if value:
            return str(value)
    return None


class TaskSyncService:
    def __init__(self, db: Any) -> None:
        self.db = db

    # -- public hooks ------------------------------------------------------

    async def on_drafter_assigned(self, letter: Any, drafter_id: Optional[str], actor_id: Optional[str]) -> None:
        """A drafter was assigned: (re)open a single draft task for them."""
        try:
            rid = _field(letter, "id", "_id")
            if not rid:
                return
            await self._close_stage(rid, "draft")
            await self._create_stage(letter, "draft", "Draft", assigned_to=drafter_id)
        except Exception:  # pragma: no cover - best effort
            logger.debug("on_drafter_assigned task sync skipped", exc_info=True)

    async def on_letter_status_changed(self, letter: Any, new_status: str, actor_id: Optional[str]) -> None:
        """Advance the board when a letter changes lifecycle status.

        Closes the previous stage's task and opens the next. Review/approve tasks
        start unassigned (open for pickup); the board assigns them, and the
        approve endpoint's separation-of-duties guard still applies on assignment.
        """
        try:
            rid = _field(letter, "id", "_id")
            if not rid:
                return
            normalized = (new_status or "").strip().lower()
            if normalized == "review":
                await self._close_stage(rid, "draft")
                await self._create_stage(letter, "review", "Review", assigned_to=None)
            elif normalized == "approval":
                await self._close_stage(rid, "review")
                await self._create_stage(letter, "approve", "Approval", assigned_to=None)
            elif normalized in {"completed", "approved"}:
                for stage in ("draft", "review", "approve"):
                    await self._close_stage(rid, stage)
            elif normalized == "rejected":
                for stage in ("draft", "review", "approve"):
                    await self._close_stage(rid, stage)
        except Exception:  # pragma: no cover - best effort
            logger.debug("on_letter_status_changed task sync skipped", exc_info=True)

    # -- helpers -----------------------------------------------------------

    async def _close_stage(self, resource_id: str, task_type: str) -> None:
        await self.db.tasks.update_many(
            {
                "resource_type": RESOURCE_LETTER,
                "resource_id": resource_id,
                "task_type": task_type,
                "status": {"$ne": "done"},
            },
            {"$set": {"status": "done", "updated_at": datetime.utcnow()}},
        )

    async def _create_stage(
        self,
        letter: Any,
        task_type: str,
        stage: str,
        *,
        assigned_to: Optional[str],
    ) -> None:
        rid = _field(letter, "id", "_id")
        title = _field(letter, "title") or rid or "Letter"
        verb = {"draft": "Draft", "review": "Review", "approve": "Approve"}.get(task_type, task_type.title())
        task = Task(
            title=f"{verb}: {title}",
            task_type=task_type,
            resource_type=RESOURCE_LETTER,
            resource_id=rid,
            workflow_stage=stage,
            assigned_to=assigned_to,
            organization_id=_field(letter, "organization_id"),
            project_id=_field(letter, "project_id"),
            status="open",
        )
        await self.db.tasks.insert_one(task.model_dump(by_alias=True))
