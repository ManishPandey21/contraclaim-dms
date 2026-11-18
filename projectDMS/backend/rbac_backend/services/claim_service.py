"""Claim register service (Phase 4 / Module 1).

DB persistence + audit for claims. Authorization (permission + tenant scope) is
enforced by the router via PolicyService; this layer assumes the caller is
already authorized for the org/project it operates on.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from ..core.database import get_database
from ..models.claim import Claim, ClaimCreate
from .audit_event_service import AuditEventService


class ClaimService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    async def create(
        self, payload: ClaimCreate, current_user: Any, *, type_confirmed: bool = False
    ) -> Dict[str, Any]:
        """``type_confirmed``: whether a person chose the claim's type (False: provisional)."""
        db = await self._get_db()
        doc = Claim(**payload.model_dump()).model_dump(by_alias=True)
        doc["type_confirmed"] = bool(type_confirmed)
        doc.pop("linked_document_ids", None)
        if not doc.get("organization_id"):
            doc["organization_id"] = getattr(current_user, "organization_id", None)
        now = datetime.utcnow()
        doc["created_at"] = now
        doc["created_by"] = getattr(current_user, "id", None)
        result = await db.claims.insert_one(doc)
        created = await db.claims.find_one({"_id": result.inserted_id}) or doc
        await self.audit.emit(
            action="claim.created",
            actor_id=getattr(current_user, "id", None),
            resource_type="claim",
            resource_id=str(result.inserted_id),
            organization_id=doc.get("organization_id"),
            project_id=doc.get("project_id"),
            after=created,
        )
        return created

    async def get(self, claim_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        return await db.claims.find_one({"_id": claim_id})

    async def list(
        self,
        scope_filter: Dict[str, Any],
        *,
        claim_type: Optional[str] = None,
        status: Optional[str] = None,
        responsible_party_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        if claim_type:
            query["type"] = claim_type
        if status:
            query["status"] = status
        if responsible_party_id:
            query["responsible_party_id"] = responsible_party_id
        cursor = db.claims.find(query).sort("created_at", -1).skip(skip).limit(limit)
        return [claim async for claim in cursor]

    async def update(
        self,
        claim_id: str,
        update: Dict[str, Any],
        current_user: Any,
        *,
        before: Optional[Dict[str, Any]] = None,
        expected: Optional[Dict[str, Any]] = None,
        clear: Iterable[str] = (),
    ) -> Optional[Dict[str, Any]]:
        """Write ``update`` to the claim.

        ``expected``: fields the stored claim must still hold (compare-and-set, 4B-12);
        if it no longer does, nothing is written and None is returned.
        ``clear``: fields to store as null, which a None in ``update`` alone never does.
        """
        db = await self._get_db()
        update.pop("linked_document_ids", None)
        update = {k: v for k, v in update.items() if v is not None}
        for key in clear:
            update[key] = None
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = getattr(current_user, "id", None)
        updated = await db.claims.find_one_and_update(
            {"_id": claim_id, **(expected or {})}, {"$set": update}, return_document=True
        )
        if updated is None and expected:
            return None
        await self.audit.emit(
            action="claim.updated",
            actor_id=getattr(current_user, "id", None),
            resource_type="claim",
            resource_id=str(claim_id),
            organization_id=(updated or before or {}).get("organization_id"),
            project_id=(updated or before or {}).get("project_id"),
            before=before,
            after=updated,
        )
        return updated

    async def clear_legacy_document_ids(self, claim_id: str) -> None:
        db = await self._get_db()
        await db.claims.update_one(
            {"_id": claim_id}, {"$unset": {"linked_document_ids": ""}}
        )

    async def delete(self, claim_id: str, current_user: Any, *, before: Optional[Dict[str, Any]] = None) -> bool:
        db = await self._get_db()
        result = await db.claims.delete_one({"_id": claim_id})
        await self.audit.emit(
            action="claim.deleted",
            actor_id=getattr(current_user, "id", None),
            resource_type="claim",
            resource_id=str(claim_id),
            organization_id=(before or {}).get("organization_id"),
            project_id=(before or {}).get("project_id"),
            before=before,
        )
        return result.deleted_count > 0
