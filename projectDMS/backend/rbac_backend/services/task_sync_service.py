"""Keep the assignment board (tasks) in sync with workflow lifecycles.

Phase 2/4. As a letter or an arbitration pleading moves through draft -> review
-> approval, this service maintains at most one active task per stage linked to
the artifact (resource_type + resource_id). All methods are best-effort: they
never raise into the caller, so a task-sync hiccup can never block a transition.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from ..models.task import Task

logger = logging.getLogger(__name__)

RESOURCE_LETTER = "letter"
RESOURCE_ARBITRATION = "arbitration_draft"

_VERB = {"draft": "Draft", "review": "Review", "approve": "Approve"}

# draft_type enum value -> short pleading label for the task title/board.
_ARB_TYPE_LABEL = {
    "statement_of_claim": "SOC",
    "statement_of_defence": "SOD",
    "rejoinder": "Rejoinder",
    "counterclaim": "Counterclaim",
}


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

    # -- letters -----------------------------------------------------------

    async def on_drafter_assigned(self, letter: Any, drafter_id: Optional[str], actor_id: Optional[str]) -> None:
        """A drafter was assigned: (re)open a single draft task for them."""
        try:
            rid = _field(letter, "id", "_id")
            if not rid:
                return
            await self._close_stage(RESOURCE_LETTER, rid, "draft")
            await self._create_stage(RESOURCE_LETTER, letter, "draft", "Draft", assigned_to=drafter_id)
        except Exception:  # pragma: no cover - best effort
            logger.debug("on_drafter_assigned task sync skipped", exc_info=True)

    async def on_letter_status_changed(self, letter: Any, new_status: str, actor_id: Optional[str]) -> None:
        """Advance the board when a letter changes lifecycle status."""
        try:
            rid = _field(letter, "id", "_id")
            if not rid:
                return
            normalized = (new_status or "").strip().lower()
            if normalized == "review":
                await self._close_stage(RESOURCE_LETTER, rid, "draft")
                await self._create_stage(RESOURCE_LETTER, letter, "review", "Review", assigned_to=None)
            elif normalized == "approval":
                await self._close_stage(RESOURCE_LETTER, rid, "review")
                await self._create_stage(RESOURCE_LETTER, letter, "approve", "Approval", assigned_to=None)
            elif normalized in {"completed", "approved", "rejected"}:
                for stage in ("draft", "review", "approve"):
                    await self._close_stage(RESOURCE_LETTER, rid, stage)
        except Exception:  # pragma: no cover - best effort
            logger.debug("on_letter_status_changed task sync skipped", exc_info=True)

    # -- arbitration pleadings (SOC / SOD / Rejoinder / Counterclaim) -------

    async def on_arbitration_draft_created(self, draft: Any, actor_id: Optional[str]) -> None:
        """A pleading draft was created: open a draft task for its author."""
        try:
            rid = _field(draft, "id", "_id")
            if not rid:
                return
            drafter = _field(draft, "created_by")
            await self._close_stage(RESOURCE_ARBITRATION, rid, "draft")
            await self._create_stage(
                RESOURCE_ARBITRATION, draft, "draft", "Draft",
                assigned_to=drafter, label=self._arb_label(draft),
            )
        except Exception:  # pragma: no cover - best effort
            logger.debug("on_arbitration_draft_created task sync skipped", exc_info=True)

    async def on_arbitration_status_changed(self, draft: Any, new_status: str, actor_id: Optional[str]) -> None:
        """Advance the board when a pleading draft changes status."""
        try:
            rid = _field(draft, "id", "_id")
            if not rid:
                return
            normalized = (new_status or "").strip().lower()
            label = self._arb_label(draft)
            if normalized == "under_review":
                await self._close_stage(RESOURCE_ARBITRATION, rid, "draft")
                await self._create_stage(
                    RESOURCE_ARBITRATION, draft, "review", "Under Review",
                    assigned_to=None, label=label,
                )
            elif normalized in {"approved", "exported"}:
                for stage in ("draft", "review", "approve"):
                    await self._close_stage(RESOURCE_ARBITRATION, rid, stage)
        except Exception:  # pragma: no cover - best effort
            logger.debug("on_arbitration_status_changed task sync skipped", exc_info=True)

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _arb_label(draft: Any) -> Optional[str]:
        title = _field(draft, "title")
        short = _ARB_TYPE_LABEL.get((_field(draft, "draft_type") or "").lower())
        if short and title:
            return f"{short} — {title}"
        return short or title

    async def _close_stage(self, resource_type: str, resource_id: str, task_type: str) -> None:
        await self.db.tasks.update_many(
            {
                "resource_type": resource_type,
                "resource_id": resource_id,
                "task_type": task_type,
                "status": {"$ne": "done"},
            },
            {"$set": {"status": "done", "updated_at": datetime.utcnow()}},
        )

    async def _create_stage(
        self,
        resource_type: str,
        obj: Any,
        task_type: str,
        stage: str,
        *,
        assigned_to: Optional[str],
        label: Optional[str] = None,
    ) -> None:
        rid = _field(obj, "id", "_id")
        title = label or _field(obj, "title") or rid or "Item"
        verb = _VERB.get(task_type, task_type.title())
        task = Task(
            title=f"{verb}: {title}",
            task_type=task_type,
            resource_type=resource_type,
            resource_id=rid,
            workflow_stage=stage,
            assigned_to=assigned_to,
            organization_id=_field(obj, "organization_id"),
            project_id=_field(obj, "project_id"),
            status="open",
        )
        await self.db.tasks.insert_one(task.model_dump(by_alias=True))
