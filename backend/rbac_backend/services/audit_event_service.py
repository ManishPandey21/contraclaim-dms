from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from ..core.database import get_database


class AuditEventService:
    """Writes normalized security, RBAC, billing, and drafting audit events."""

    def __init__(self, db: Any = None) -> None:
        self.db = db

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    async def emit(
        self,
        *,
        action: str,
        actor_id: Optional[str],
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        package_id: Optional[str] = None,
        result: str = "success",
        reason: Optional[str] = None,
        before: Optional[Dict[str, Any]] = None,
        after: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        db = await self._get_db()
        audit_events = getattr(db, "audit_events", None)
        if audit_events is None:
            return
        await audit_events.insert_one(
            {
                "action": action,
                "actor_id": actor_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "organization_id": organization_id,
                "project_id": project_id,
                "package_id": package_id,
                "result": result,
                "reason": reason,
                "before": before,
                "after": after,
                "metadata": metadata or {},
                "correlation_id": correlation_id,
                "created_at": datetime.utcnow(),
            }
        )

    async def query_events(
        self,
        *,
        organization_id: Optional[str],
        project_id: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        action: Optional[str] = None,
        actor_id: Optional[str] = None,
        limit: int = 1000,
    ) -> list[Dict[str, Any]]:
        """Return audit events for a tenant scope, newest first.

        Always constrained by ``organization_id`` (and optionally ``project_id``);
        the caller is responsible for authorizing that scope before calling.
        """
        db = await self._get_db()
        audit_events = getattr(db, "audit_events", None)
        if audit_events is None:
            return []
        query: Dict[str, Any] = {}
        if organization_id:
            query["organization_id"] = str(organization_id)
        if project_id:
            query["project_id"] = str(project_id)
        if action:
            query["action"] = action
        if actor_id:
            query["actor_id"] = str(actor_id)
        if start or end:
            created: Dict[str, Any] = {}
            if start:
                created["$gte"] = start
            if end:
                created["$lte"] = end
            query["created_at"] = created

        cursor = audit_events.find(query).sort("created_at", -1).limit(max(1, int(limit)))
        events: list[Dict[str, Any]] = []
        async for event in cursor:
            event["_id"] = str(event.get("_id", ""))
            created_at = event.get("created_at")
            if isinstance(created_at, datetime):
                event["created_at"] = created_at.isoformat()
            events.append(event)
        return events
