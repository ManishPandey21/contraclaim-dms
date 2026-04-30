from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from bson import ObjectId

from ..core.database import get_database
from ..models.folder_models import FolderItem
from ..utils.error_handler import FolderError


class FolderService:
    """Service for hierarchical folder and file metadata."""

    def __init__(self) -> None:
        self._collection = None

    async def _collection_handle(self):
        if self._collection is None:
            db = await get_database()
            self._collection = db.folders
            try:
                await self._collection.create_index("path", unique=True)
            except Exception:
                pass
        return self._collection

    def _parent_path(self, path: str) -> str:
        pure = PurePosixPath(path)
        return str(pure.parent) if pure.parent != PurePosixPath('.') else ""

    async def get_folder_by_path(
        self, path: str, organization_id: Optional[str], project_id: Optional[str]
    ) -> Optional[FolderItem]:
        col = await self._collection_handle()
        doc = await col.find_one(
            {
                "path": path,
                "type": "folder",
                "organization_id": organization_id,
                "project_id": project_id,
            }
        )
        return self._to_model(doc) if doc else None

    async def create_folder(
        self,
        name: str,
        path: str,
        organization_id: Optional[str],
        project_id: Optional[str],
        current_user: Any,
    ) -> FolderItem:
        col = await self._collection_handle()
        payload = {
            "name": name,
            "path": path,
            "type": "folder",
            "organization_id": organization_id,
            "project_id": project_id,
            "parent_path": self._parent_path(path),
            "created_at": datetime.utcnow(),
            "created_by": getattr(current_user, "id", None),
        }
        try:
            result = await col.insert_one(payload)
        except Exception as exc:
            raise FolderError(f"Unable to create folder: {exc}")
        payload["_id"] = str(result.inserted_id)
        return self._to_model(payload)

    async def get_root_folders(
        self, organization_id: str, project_id: Optional[str]
    ) -> List[FolderItem]:
        col = await self._collection_handle()
        docs = await col.find(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "parent_path": "",
            }
        ).to_list(length=None)
        return [self._to_model(doc) for doc in docs]

    async def get_file_by_path(
        self, path: str, organization_id: Optional[str], project_id: Optional[str]
    ) -> Optional[FolderItem]:
        col = await self._collection_handle()
        doc = await col.find_one(
            {
                "path": path,
                "type": "file",
                "organization_id": organization_id,
                "project_id": project_id,
            }
        )
        return self._to_model(doc) if doc else None

    async def create_file(
        self,
        name: str,
        path: str,
        parent_path: Optional[str],
        organization_id: Optional[str],
        project_id: Optional[str],
        size: Optional[int],
        file_extension: str,
        s3_key: Optional[str],
        current_user: Any,
    ) -> FolderItem:
        col = await self._collection_handle()
        payload = {
            "name": name,
            "path": path,
            "type": "file",
            "file_extension": file_extension,
            "size": size or 0,
            "organization_id": organization_id,
            "project_id": project_id,
            "parent_path": parent_path if parent_path is not None else self._parent_path(path),
            "storage_key": s3_key,
            "created_at": datetime.utcnow(),
            "created_by": getattr(current_user, "id", None),
        }
        await col.update_one(
            {"path": path},
            {"$set": payload},
            upsert=True,
        )
        doc = await col.find_one({"path": path})
        return self._to_model(doc)

    async def get_item_by_path(self, path: str) -> Optional[FolderItem]:
        col = await self._collection_handle()
        doc = await col.find_one({"path": path})
        return self._to_model(doc) if doc else None

    async def delete_item_recursive(self, item_id: str, current_user: Any) -> None:
        col = await self._collection_handle()
        query_id = self._to_query_id(item_id)
        doc = await col.find_one({"_id": query_id})
        if not doc:
            raise FolderError("Folder item not found", 404)
        path = doc.get("path")
        await col.delete_many({"path": {"$regex": f"^{path}"}})

    async def get_folder_with_children(
        self, path: str, organization_id: Optional[str], project_id: Optional[str]
    ) -> FolderItem:
        col = await self._collection_handle()
        folder_doc = await col.find_one(
            {
                "path": path,
                "organization_id": organization_id,
                "project_id": project_id,
            }
        )
        if not folder_doc:
            raise FolderError("Folder not found", 404)
        children_docs = await col.find({"parent_path": path}).to_list(length=None)
        folder = self._to_model(folder_doc)
        folder.children = [self._to_model(doc) for doc in children_docs]
        return folder

    def _to_model(self, doc: Optional[Dict[str, Any]]) -> FolderItem:
        if not doc:
            raise FolderError("Folder payload missing", 500)
        payload = dict(doc)
        payload["id"] = str(payload.get("_id", payload.get("id")))
        payload.pop("_id", None)
        payload.setdefault("children", [])
        if payload.get("created_at") and isinstance(payload["created_at"], datetime):
            payload["created_at"] = payload["created_at"].isoformat()
        return FolderItem(**payload)

    def _to_query_id(self, value: str):
        try:
            return ObjectId(value)
        except Exception:
            return value
