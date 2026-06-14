from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId

from ..core.database import get_database
from ..models.representative import (
    Representative,
    RepresentativeCreate,
    RepresentativeLevel,
    RepresentativeUpdate,
)
from ..utils.error_handler import RepresentativeError


class RepresentativeService:
    """Service for managing representatives across parties, organizations, and projects."""

    def __init__(self) -> None:
        self._collection = None

    async def _collection_handle(self):
        if self._collection is None:
            db = await get_database()
            self._collection = db.representatives
        return self._collection

    async def create_representative(
        self,
        *,
        party_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        rep_data: RepresentativeCreate,
        level: RepresentativeLevel,
        current_user: Any,
    ) -> Representative:
        col = await self._collection_handle()
        payload = rep_data.model_dump(exclude_none=True)
        payload.setdefault("level", level.value)
        payload["party_id"] = party_id or rep_data.party_id
        if organization_id or rep_data.organization_id:
            payload["organization_id"] = organization_id or rep_data.organization_id
        if project_id or rep_data.project_id:
            payload["project_id"] = project_id or rep_data.project_id
        payload["created_at"] = datetime.utcnow()
        payload["updated_at"] = datetime.utcnow()
        payload["created_by"] = getattr(current_user, "id", None)
        payload["updated_by"] = getattr(current_user, "id", None)
        result = await col.insert_one(payload)
        payload["_id"] = str(result.inserted_id)
        return Representative(**payload)

    async def get_representatives_paginated(
        self, filters: Dict[str, Any], pagination: Dict[str, int]
    ) -> Tuple[List[Representative], int]:
        col = await self._collection_handle()
        criteria = {k: v for k, v in (filters or {}).items() if v is not None}
        if level := criteria.get("level"):
            criteria["level"] = level.value if isinstance(level, RepresentativeLevel) else level
        skip = max(pagination.get("skip", 0), 0)
        limit = max(pagination.get("limit", 20), 1)
        total = await col.count_documents(criteria)
        docs = await col.find(criteria).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)
        return [self._to_model(doc) for doc in docs], total

    async def get_representatives_for_party(self, party_id: str) -> List[Representative]:
        col = await self._collection_handle()
        docs = await col.find({"party_id": party_id}).to_list(length=None)
        return [self._to_model(doc) for doc in docs]

    async def get_representatives_for_project(
        self,
        project_id: str,
        organization_id: Optional[str] = None,
        include_head_office: bool = True,
    ) -> List[Representative]:
        col = await self._collection_handle()
        query = {"project_id": project_id}
        docs = await col.find(query).to_list(length=None)
        reps = [self._to_model(doc) for doc in docs]
        if include_head_office:
            ho_query: Dict[str, Any] = {"use_head_office": True}
            if organization_id:
                ho_query["organization_id"] = organization_id
            else:
                ho_query["organization_id"] = {"$exists": True}
            ho_docs = await col.find(ho_query).to_list(length=None)
            reps.extend(self._to_model(doc) for doc in ho_docs)
        return reps

    async def get_representatives_for_organization(self, organization_id: str) -> List[Representative]:
        col = await self._collection_handle()
        docs = await col.find({"organization_id": organization_id}).to_list(length=None)
        return [self._to_model(doc) for doc in docs]

    async def get_representative_by_id(self, rep_id: str) -> Optional[Representative]:
        col = await self._collection_handle()
        query_id = self._to_query_id(rep_id)
        doc = await col.find_one({"_id": query_id})
        return self._to_model(doc) if doc else None

    async def update_representative(
        self, rep_id: str, update: RepresentativeUpdate, current_user: Any
    ) -> Representative:
        col = await self._collection_handle()
        update_fields = update.model_dump(exclude_none=True)
        if not update_fields:
            existing = await self.get_representative_by_id(rep_id)
            if not existing:
                raise RepresentativeError("Representative not found", 404)
            return existing
        if "level" in update_fields and isinstance(update_fields["level"], RepresentativeLevel):
            update_fields["level"] = update_fields["level"].value
        update_fields["updated_at"] = datetime.utcnow()
        update_fields["updated_by"] = getattr(current_user, "id", None)
        query_id = self._to_query_id(rep_id)
        result = await col.update_one({"_id": query_id}, {"$set": update_fields})
        if result.matched_count == 0:
            raise RepresentativeError("Representative not found", 404)
        updated = await col.find_one({"_id": query_id})
        return self._to_model(updated)

    async def delete_representative(self, rep_id: str, current_user: Any) -> None:
        col = await self._collection_handle()
        query_id = self._to_query_id(rep_id)
        result = await col.delete_one({"_id": query_id})
        if result.deleted_count == 0:
            raise RepresentativeError("Representative not found", 404)

    async def unset_primary_representatives(
        self,
        party_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> None:
        col = await self._collection_handle()
        criteria: Dict[str, Any] = {"is_primary": True}
        if party_id:
            criteria["party_id"] = party_id
        if organization_id:
            criteria["organization_id"] = organization_id
        if project_id:
            criteria["project_id"] = project_id
        if len(criteria) == 1:
            return
        await col.update_many(criteria, {"$set": {"is_primary": False, "updated_at": datetime.utcnow()}})

    def _to_query_id(self, value: str):
        try:
            return ObjectId(value)
        except Exception:
            return value

    def _to_model(self, doc: Optional[Dict[str, Any]]) -> Representative:
        if not doc:
            raise RepresentativeError("Representative payload missing", 500)
        payload = dict(doc)
        payload["_id"] = str(payload.get("_id"))
        return Representative(**payload)
