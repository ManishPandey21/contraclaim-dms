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
