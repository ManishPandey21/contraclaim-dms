from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId

from ..core.database import get_database
from ..models.email_group import EmailGroup, EmailGroupCreate, EmailGroupUpdate
from ..utils.error_handler import EmailGroupError


class EmailGroupService:
    """Service layer for email group operations."""

    def __init__(self) -> None:
        self._collection = None

    async def _collection_handle(self):
        if self._collection is None:
            db = await get_database()
            self._collection = db.email_groups
        return self._collection

    async def get_groups_paginated(
        self, filters: Dict[str, Any], pagination: Dict[str, int]
    ) -> Tuple[List[EmailGroup], int]:
        col = await self._collection_handle()
        criteria = {k: v for k, v in (filters or {}).items() if v is not None}
        if search := criteria.pop("search", None):
            criteria["$or"] = [
                {"name": {"$regex": search, "$options": "i"}},
                {"description": {"$regex": search, "$options": "i"}},
            ]
        criteria.setdefault("is_active", True)
        skip = max(pagination.get("skip", 0), 0)
        limit = max(pagination.get("limit", 20), 1)
        total = await col.count_documents(criteria)
        docs = await col.find(criteria).sort("updated_at", -1).skip(skip).limit(limit).to_list(length=limit)
        return [self._to_model(doc) for doc in docs], total

    async def group_exists_in_scope(
        self, name: str, organization_id: Optional[str], project_id: Optional[str]
    ) -> bool:
        col = await self._collection_handle()
        query: Dict[str, Any] = {
            "name": name,
            "is_active": True,
        }
        if organization_id:
            query["organization_id"] = organization_id
        if project_id:
            query["project_id"] = project_id
        existing = await col.find_one(query)
        return existing is not None

    async def create_group(self, data: EmailGroupCreate, current_user: Any) -> EmailGroup:
        col = await self._collection_handle()
        payload = data.model_dump(exclude_none=True)
        payload["is_active"] = True
        payload["created_at"] = datetime.utcnow()
        payload["updated_at"] = datetime.utcnow()
        payload["created_by"] = getattr(current_user, "id", None)
        payload["updated_by"] = getattr(current_user, "id", None)
        result = await col.insert_one(payload)
        payload["_id"] = str(result.inserted_id)
        return EmailGroup(**payload)

    async def get_group_by_id(self, group_id: str) -> Optional[EmailGroup]:
        col = await self._collection_handle()
        query_id = self._to_query_id(group_id)
        doc = await col.find_one({"_id": query_id, "is_active": True})
        return self._to_model(doc) if doc else None

    async def update_group(
        self, group_id: str, update: EmailGroupUpdate, current_user: Any
    ) -> EmailGroup:
        col = await self._collection_handle()
        update_fields = update.model_dump(exclude_none=True)
        if not update_fields:
            existing = await self.get_group_by_id(group_id)
            if not existing:
                raise EmailGroupError("Email group not found", 404)
            return existing
        update_fields["updated_at"] = datetime.utcnow()
        update_fields["updated_by"] = getattr(current_user, "id", None)
        query_id = self._to_query_id(group_id)
        result = await col.update_one(
            {"_id": query_id, "is_active": True}, {"$set": update_fields}
        )
        if result.matched_count == 0:
            raise EmailGroupError("Email group not found", 404)
        updated = await col.find_one({"_id": query_id})
        return self._to_model(updated)

    async def delete_group(self, group_id: str, current_user: Any) -> None:
        col = await self._collection_handle()
        query_id = self._to_query_id(group_id)
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
            raise EmailGroupError("Email group not found", 404)

    async def get_unique_emails(self, emails: List[str]) -> List[str]:
        seen = set()
        unique: List[str] = []
        for email in emails:
            lower = email.lower()
            if lower not in seen:
                seen.add(lower)
                unique.append(email)
        return unique

    def _to_query_id(self, value: str):
        try:
            return ObjectId(value)
        except Exception:
            return value

    def _to_model(self, doc: Optional[Dict[str, Any]]) -> EmailGroup:
        if not doc:
            raise EmailGroupError("Email group payload missing", 500)
        payload = dict(doc)
        payload["_id"] = str(payload.get("_id"))
        return EmailGroup(**payload)
