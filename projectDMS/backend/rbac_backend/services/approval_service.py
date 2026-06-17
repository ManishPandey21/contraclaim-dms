"""Generalised approval workflow service (Phase 4 / Module 3).

Persists one ApprovalRecord per ``(resource_type, resource_id)`` and enforces the
review/approval state machine. Authorization (permission + tenant scope) is the
router's job via PolicyService; this layer owns the *workflow* rules:

* legal transitions only (you can't approve work that isn't in review);
* **no self-approval** — the drafter who owns the work can never approve or
  return it themselves, regardless of their permissions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from ..core.database import get_database
from ..models.approval import ApprovalEvent, ApprovalRecord, ApprovalState
from .audit_event_service import AuditEventService


class ApprovalError(Exception):
    """Raised on an illegal workflow transition or a self-approval attempt."""


class ApprovalService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    async def get(self, resource_type: str, resource_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        return await db.approvals.find_one(
            {"resource_type": resource_type, "resource_id": str(resource_id)}
        )

    async def get_or_create(
        self,
        resource_type: str,
        resource_id: str,
        *,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        drafter_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        existing = await self.get(resource_type, resource_id)
        if existing:
            return existing
        db = await self._get_db()
        record = ApprovalRecord(
            resource_type=resource_type,
            resource_id=str(resource_id),
            organization_id=organization_id,
            project_id=project_id,
            drafter_id=drafter_id,
        ).model_dump(by_alias=True)
        await db.approvals.insert_one(record)
        return record

    async def _persist(
        self,
        record: Dict[str, Any],
        *,
        actor_id: Optional[str],
        action: str,
        to_state: ApprovalState,
        set_fields: Dict[str, Any],
        comment: Optional[str] = None,
    ) -> Dict[str, Any]:
        db = await self._get_db()
        now = datetime.utcnow()
        from_state = record.get("state")
        event = ApprovalEvent(
            action=action,
            actor_id=actor_id,
            from_state=from_state,
            to_state=to_state.value,
            comment=comment,
        ).model_dump()
        update = {**set_fields, "state": to_state.value, "updated_at": now}
        updated = await db.approvals.find_one_and_update(
            {"_id": record["_id"]},
            {"$set": update, "$push": {"history": event}},
            return_document=True,
        )
        result = updated or {**record, **update}
        await self.audit.emit(
            action=f"approval.{action}",
            actor_id=actor_id,
            resource_type=record.get("resource_type", "approval"),
            resource_id=str(record.get("resource_id")),
            organization_id=record.get("organization_id"),
            project_id=record.get("project_id"),
            after={"state": to_state.value, "comment": comment},
        )
        return result

    # --- transitions ------------------------------------------------------

    async def assign(
        self,
        record: Dict[str, Any],
        reviewer_id: str,
        actor_id: Optional[str],
        *,
        due_at: Optional[datetime] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        if record.get("state") not in (ApprovalState.DRAFT.value, ApprovalState.ASSIGNED.value, ApprovalState.RETURNED.value):
            raise ApprovalError(f"Cannot assign a reviewer while state is '{record.get('state')}'")
        return await self._persist(
            record,
            actor_id=actor_id,
            action="assigned",
            to_state=ApprovalState.ASSIGNED,
            set_fields={
                "reviewer_id": reviewer_id,
                "assigned_by": actor_id,
                "assigned_at": datetime.utcnow(),
                "due_at": due_at,
            },
            comment=note,
        )

    async def submit_for_review(self, record: Dict[str, Any], actor_id: Optional[str]) -> Dict[str, Any]:
        if record.get("state") not in (ApprovalState.ASSIGNED.value, ApprovalState.RETURNED.value):
            raise ApprovalError("A reviewer must be assigned before submitting for review")
        return await self._persist(
            record,
            actor_id=actor_id,
            action="submitted",
            to_state=ApprovalState.IN_REVIEW,
            set_fields={"submitted_by": actor_id, "submitted_at": datetime.utcnow()},
        )

    def _guard_decider(self, record: Dict[str, Any], actor_id: Optional[str]) -> None:
        if record.get("state") != ApprovalState.IN_REVIEW.value:
            raise ApprovalError("Only work that is in review can be approved or returned")
        if actor_id is not None and actor_id == record.get("drafter_id"):
            raise ApprovalError("The drafter cannot approve or return their own work")

    async def approve(
        self, record: Dict[str, Any], actor_id: Optional[str], *, comment: Optional[str] = None
    ) -> Dict[str, Any]:
        self._guard_decider(record, actor_id)
        return await self._persist(
            record,
            actor_id=actor_id,
            action="approved",
            to_state=ApprovalState.APPROVED,
            set_fields={"decided_by": actor_id, "decided_at": datetime.utcnow(), "decision_comment": comment},
            comment=comment,
        )

    async def return_for_changes(
        self, record: Dict[str, Any], actor_id: Optional[str], *, comment: Optional[str] = None
    ) -> Dict[str, Any]:
        self._guard_decider(record, actor_id)
        return await self._persist(
            record,
            actor_id=actor_id,
            action="returned",
            to_state=ApprovalState.RETURNED,
            set_fields={"decided_by": actor_id, "decided_at": datetime.utcnow(), "decision_comment": comment},
            comment=comment,
        )
