from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId

from ..core.database import get_database
from ..models.concern import (
    Concern,
    ConcernCreate,
    ConcernUpdate,
    ConcernStatus,
    ConcernPriority,
)
from ..utils.error_handler import ConcernError


class ConcernService:
    """Data access and business logic for concerns."""

    def __init__(self) -> None:
        self._collection = None

    async def _collection_handle(self):
        if self._collection is None:
            db = await get_database()
            self._collection = db.concerns
        return self._collection

    async def create_concern(self, data: ConcernCreate, current_user: Any) -> Concern:
        col = await self._collection_handle()
        payload = data.model_dump(exclude_none=True)
        payload.setdefault("status", ConcernStatus.OPEN.value)
        payload.setdefault("priority", ConcernPriority.MEDIUM.value)
        payload["is_active"] = True
        payload["created_at"] = datetime.utcnow()
        payload["updated_at"] = datetime.utcnow()
        payload["created_by"] = getattr(current_user, "id", None)
        payload["updated_by"] = getattr(current_user, "id", None)
        result = await col.insert_one(payload)
        payload["_id"] = str(result.inserted_id)
        return Concern(**payload)

    async def get_concerns_paginated(
        self, query: Dict[str, Any], pagination: Dict[str, int]
    ) -> Tuple[List[Concern], int]:
        col = await self._collection_handle()
        criteria = dict(query or {})
        if criteria.get("status") and isinstance(criteria["status"], ConcernStatus):
            criteria["status"] = criteria["status"].value
        if criteria.get("priority") and isinstance(criteria["priority"], ConcernPriority):
            criteria["priority"] = criteria["priority"].value
        if criteria.get("party_id"):
            criteria["party_id"] = criteria["party_id"]
        criteria.setdefault("is_active", True)
        skip = max(pagination.get("skip", 0), 0)
        limit = max(pagination.get("limit", 20), 1)
        total = await col.count_documents(criteria)
        docs = await col.find(criteria).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)
        return [self._to_model(doc) for doc in docs], total

    async def get_concern_by_id(self, concern_id: str) -> Optional[Concern]:
        col = await self._collection_handle()
        query_id = self._to_query_id(concern_id)
        doc = await col.find_one({"_id": query_id, "is_active": True})
        return self._to_model(doc) if doc else None

    async def update_concern(
        self, concern_id: str, update: ConcernUpdate, current_user: Any
    ) -> Concern:
        col = await self._collection_handle()
        update_fields = update.model_dump(exclude_none=True)
        if not update_fields:
            existing = await self.get_concern_by_id(concern_id)
            if not existing:
                raise ConcernError("Concern not found", 404)
            return existing
        if "status" in update_fields and isinstance(update_fields["status"], ConcernStatus):
            update_fields["status"] = update_fields["status"].value
        if "priority" in update_fields and isinstance(update_fields["priority"], ConcernPriority):
            update_fields["priority"] = update_fields["priority"].value
        update_fields["updated_at"] = datetime.utcnow()
        update_fields["updated_by"] = getattr(current_user, "id", None)
        query_id = self._to_query_id(concern_id)
        result = await col.update_one(
            {"_id": query_id, "is_active": True}, {"$set": update_fields}
        )
        if result.matched_count == 0:
            raise ConcernError("Concern not found", 404)
        updated = await col.find_one({"_id": query_id})
        return self._to_model(updated)

    async def delete_concern(self, concern_id: str, current_user: Any) -> None:
        col = await self._collection_handle()
        query_id = self._to_query_id(concern_id)
        result = await col.update_one(
            {"_id": query_id, "is_active": True},
            {
                "$set": {
                    "is_active": False,
                    "updated_at": datetime.utcnow(),
                    "updated_by": getattr(current_user, "id", None),
                }
            },
        )
        if result.matched_count == 0:
            raise ConcernError("Concern not found", 404)

    def _to_query_id(self, value: str):
        try:
            return ObjectId(value)
        except Exception:
            return value

    def _to_model(self, doc: Optional[Dict[str, Any]]) -> Concern:
        if not doc:
            raise ConcernError("Concern payload missing", 500)
        payload = dict(doc)
        payload["_id"] = str(payload.get("_id"))
        return Concern(**payload)
