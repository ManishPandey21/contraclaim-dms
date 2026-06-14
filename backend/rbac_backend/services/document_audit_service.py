"""Append-only audit events for document and contract storage actions."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ..core.database import get_database
from ..models.storage_architecture import DocumentAuditEvent
from .observability import observability_registry

logger = logging.getLogger(__name__)


class DocumentAuditService:
    def __init__(self, db: Any = None) -> None:
        self.db = db

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    async def emit(
        self,
        *,
        resource_type: str,
        resource_id: str,
        event_type: str,
        actor_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            event = DocumentAuditEvent(
                resource_type=resource_type,
                resource_id=resource_id,
                event_type=event_type,
                actor_id=actor_id,
                organization_id=organization_id,
                project_id=project_id,
                metadata=metadata or {},
            )
            await (await self._get_db()).document_audit_events.insert_one(
                event.model_dump(by_alias=True)
            )
            await observability_registry.record_domain_event(
                resource_type=resource_type,
                event_type=event_type,
            )
        except Exception:
            logger.warning("Failed to write document audit event", exc_info=True)

    async def list_events(
        self,
        *,
        resource_type: str,
        resource_id: str,
        limit: int = 100,
        skip: int = 0,
    ) -> list[Dict[str, Any]]:
        bounded_limit = min(max(int(limit or 100), 1), 500)
        bounded_skip = max(int(skip or 0), 0)
        cursor = (
            (await self._get_db())
            .document_audit_events.find(
                {"resource_type": resource_type, "resource_id": resource_id}
            )
            .sort("createdAt", -1)
            .skip(bounded_skip)
            .limit(bounded_limit)
        )
        rows = await cursor.to_list(length=bounded_limit)
        for row in rows:
            if "_id" in row:
                row["_id"] = str(row["_id"])
        return rows
