"""IPC category master service.

Tenant-scoped CRUD for advance / deduction type catalogs, with org-wide +
project-override resolution and lazy seeding of canonical defaults per org.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.database import get_database
from ..models.ipc_category import DEFAULT_CATEGORIES, IPCCategory, IPCCategoryCreate
from .audit_event_service import AuditEventService


def _sort_key(c: Dict[str, Any]) -> tuple:
    return (str(c.get("kind") or ""), int(c.get("sort_order") or 0), str(c.get("name") or ""))


class IPCCategoryService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    async def _get_db(self) -> Any:
        return self.db if self.db is not None else await get_database()

    async def _ensure_seeded(self, db: Any, organization_id: Optional[str]) -> None:
        """Seed canonical org-wide defaults the first time an org is used."""
        existing = await db.ipc_categories.count_documents(
            {"organization_id": organization_id, "project_id": None}
        )
        if existing:
            return
        docs: List[Dict[str, Any]] = []
        for kind, entries in DEFAULT_CATEGORIES.items():
            for idx, (code, name) in enumerate(entries):
                doc = IPCCategory(
                    kind=kind, code=code, name=name, sort_order=idx,
                    organization_id=organization_id, project_id=None, is_default=True,
                ).model_dump(by_alias=True)
                docs.append(doc)
        if docs:
            try:
                await db.ipc_categories.insert_many(docs, ordered=False)
            except Exception:  # pragma: no cover - tolerate concurrent first-seed
                pass

    async def resolve(self, organization_id: Optional[str], project_id: Optional[str] = None,
                      kind: Optional[str] = None, include_inactive: bool = False) -> List[Dict[str, Any]]:
        """Merged catalog for the editor: org-wide entries overlaid by project
        entries of the same (kind, code), with the project entry winning."""
        db = await self._get_db()
        await self._ensure_seeded(db, organization_id)
        query: Dict[str, Any] = {"organization_id": organization_id}
        scopes = [None] if not project_id else [None, project_id]
        query["project_id"] = {"$in": scopes}
        if kind:
            query["kind"] = kind
        rows = [c async for c in db.ipc_categories.find(query)]
        merged: Dict[tuple, Dict[str, Any]] = {}
        # Org-level first, then project-level overrides by (kind, code).
        for row in sorted(rows, key=lambda r: 0 if r.get("project_id") is None else 1):
            key = (row.get("kind"), row.get("code"))
            merged[key] = row
        out = [c for c in merged.values() if include_inactive or c.get("active", True)]
        return sorted(out, key=_sort_key)

    async def list_manage(self, organization_id: Optional[str], project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Raw entries (org + this project) for the management UI, incl. inactive."""
        db = await self._get_db()
        await self._ensure_seeded(db, organization_id)
        scopes = [None] if not project_id else [None, project_id]
        rows = [c async for c in db.ipc_categories.find(
            {"organization_id": organization_id, "project_id": {"$in": scopes}}
        )]
        return sorted(rows, key=_sort_key)

    async def get(self, category_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        return await db.ipc_categories.find_one({"_id": category_id})

    async def create(self, payload: IPCCategoryCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        doc = IPCCategory(**payload.model_dump()).model_dump(by_alias=True)
        if not doc.get("organization_id"):
            doc["organization_id"] = getattr(current_user, "organization_id", None)
        doc["is_default"] = False
        doc["created_at"] = datetime.utcnow()
        doc["created_by"] = getattr(current_user, "id", None)
        # Uniqueness within (org, project, kind, code): upsert-like guard.
        clash = await db.ipc_categories.find_one({
            "organization_id": doc["organization_id"], "project_id": doc.get("project_id"),
            "kind": doc["kind"], "code": doc["code"],
        })
        if clash:
            raise ValueError("A category with this code already exists in this scope")
        await db.ipc_categories.insert_one(doc)
        created = await db.ipc_categories.find_one({"_id": doc["_id"]}) or doc
        await self._emit("ipc_category.created", current_user, created, after=created)
        return created

    async def update(self, category: Dict[str, Any], payload: Dict[str, Any], current_user: Any) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        update = {k: v for k, v in payload.items() if v is not None}
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = getattr(current_user, "id", None)
        updated = await db.ipc_categories.find_one_and_update(
            {"_id": category["_id"]}, {"$set": update}, return_document=True,
        )
        await self._emit("ipc_category.updated", current_user, category, before=category, after=updated)
        return updated or category

    async def delete(self, category: Dict[str, Any], current_user: Any) -> bool:
        db = await self._get_db()
        res = await db.ipc_categories.delete_one({"_id": category["_id"]})
        await self._emit("ipc_category.deleted", current_user, category, before=category)
        return res.deleted_count > 0

    async def _emit(self, action: str, current_user: Any, cat: Dict[str, Any], *, before: Any = None, after: Any = None) -> None:
        await self.audit.emit(
            action=action,
            actor_id=getattr(current_user, "id", None),
            resource_type="ipc_category",
            resource_id=str(cat.get("_id")),
            organization_id=cat.get("organization_id"),
            project_id=cat.get("project_id"),
            before=before,
            after=after,
        )
