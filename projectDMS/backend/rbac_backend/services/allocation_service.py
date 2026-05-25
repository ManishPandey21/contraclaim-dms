from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId

from ..core.database import get_database
from ..models.rbac_monetization import ExpertAllocationCreate, ExpertAllocationUpdate
from ..services.audit_event_service import AuditEventService


class AllocationService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit_service = AuditEventService(db)

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    @staticmethod
    def _lookup_id(value: str) -> Any:
        try:
            return ObjectId(str(value))
        except Exception:
            return str(value)

    @staticmethod
    def _normalize(doc: Dict[str, Any]) -> Dict[str, Any]:
        doc = dict(doc)
        doc["id"] = str(doc.pop("_id"))
        return doc

    async def list_allocations(
        self,
        *,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        expert_user_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        skip: int = 0,
    ) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = {}
        if organization_id:
            query["organization_id"] = organization_id
        if project_id:
            query["project_id"] = project_id
        if expert_user_id:
            query["expert_user_id"] = expert_user_id
        if status:
            query["status"] = status
        rows = await db.expert_allocations.find(query).sort("updated_at", -1).skip(skip).limit(limit).to_list(length=limit)
        return [self._normalize(row) for row in rows]

    async def create_allocation(self, payload: ExpertAllocationCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        now = datetime.utcnow()
        doc = payload.model_dump(mode="json")
        doc.update(
            {
                "created_by": getattr(current_user, "id", None),
                "updated_by": getattr(current_user, "id", None),
                "created_at": now,
                "updated_at": now,
            }
        )
        result = await db.expert_allocations.insert_one(doc)
        saved = await db.expert_allocations.find_one({"_id": result.inserted_id})
        await self.audit_service.emit(
            action="expert_allocation.created",
            actor_id=getattr(current_user, "id", None),
            resource_type="expert_allocation",
            resource_id=str(result.inserted_id),
            organization_id=payload.organization_id,
            project_id=payload.project_id,
            after=doc,
        )
        return self._normalize(saved)

    async def update_allocation(self, allocation_id: str, payload: ExpertAllocationUpdate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        lookup = self._lookup_id(allocation_id)
        before = await db.expert_allocations.find_one({"_id": lookup})
        update_doc = {
            key: value
            for key, value in payload.model_dump(mode="json", exclude_unset=True).items()
            if value is not None
        }
        update_doc["updated_at"] = datetime.utcnow()
        update_doc["updated_by"] = getattr(current_user, "id", None)
        if update_doc.get("status") == "revoked":
            update_doc["revoked_at"] = datetime.utcnow()
            update_doc["revoked_by"] = getattr(current_user, "id", None)
        await db.expert_allocations.update_one({"_id": lookup}, {"$set": update_doc})
        saved = await db.expert_allocations.find_one({"_id": lookup})
        await self.audit_service.emit(
            action="expert_allocation.updated",
            actor_id=getattr(current_user, "id", None),
            resource_type="expert_allocation",
            resource_id=allocation_id,
            organization_id=str(saved.get("organization_id")) if saved else None,
            project_id=str(saved.get("project_id")) if saved else None,
            before=before,
            after=saved,
        )
        return self._normalize(saved)
