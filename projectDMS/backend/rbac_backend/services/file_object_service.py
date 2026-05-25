"""Provider-backed immutable file object storage service."""

from __future__ import annotations

import hashlib
import logging
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from bson import ObjectId

from ..config.document_processing_config import DocumentProcessingConfig
from ..core.config import settings
from ..core.database import get_database
from ..models.storage_architecture import DocumentVersion, FileObject, StorageProviderLocation
from ..models.storage_settings import StorageProviderConfig
from ..utils.file_validation import sniff_mime_from_bytes
from .file_service import SecureFileService
from .s3_service import S3Service
from .storage_settings_service import StorageSettingsService

logger = logging.getLogger(__name__)


class FileObjectService:
    """Stores immutable file objects while preserving legacy path fields."""

    def __init__(
        self,
        db: Any = None,
        *,
        file_service: Optional[SecureFileService] = None,
        s3_service: Optional[S3Service] = None,
        storage_settings: Optional[StorageSettingsService] = None,
    ) -> None:
        self.db = db
        self.file_service = file_service or self._default_file_service()
        self.s3_service = s3_service or S3Service()
        self.storage_settings = storage_settings or StorageSettingsService()

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    @staticmethod
    def _default_file_service() -> SecureFileService:
        config = DocumentProcessingConfig()
        if getattr(settings, "SECURE_UPLOADS_DIR", None):
            config.uploads_dir = settings.SECURE_UPLOADS_DIR
        return SecureFileService(config=config)

    @staticmethod
    def _actor_id(current_user: Any) -> Optional[str]:
        return getattr(current_user, "id", None) or getattr(current_user, "email", None)

    @staticmethod
    def _primary_provider(locations: List[StorageProviderLocation]) -> str:
        for location in locations:
            if location.primary:
                return location.provider
        return locations[0].provider if locations else "local"

    async def resolve_storage_context(
        self, organization_id: str, project_id: Optional[str]
    ) -> Dict[str, Any]:
        try:
            resolved = await self.storage_settings.resolve_settings(organization_id, project_id)
        except Exception:
            resolved = None
        providers = (resolved.providers if resolved else None) or [
            StorageProviderConfig(id="local", enabled=True, primary=True)
        ]
        return {
            "organization": getattr(resolved, "org_short_name", None) or organization_id,
            "project": getattr(resolved, "project_short_name", None) or project_id or "default",
            "providers": sorted(providers, key=lambda p: (not p.primary, p.id)),
        }

    async def store_bytes(
        self,
        *,
        content: bytes,
        organization_id: str,
        project_id: Optional[str],
        original_filename: str,
        storage_key: str,
        current_user: Any = None,
        document_type: str = "document",
        upload_id: Optional[str] = None,
        content_type: Optional[str] = None,
        providers: Optional[List[StorageProviderConfig]] = None,
        document_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        digest = hashlib.sha256(content or b"").hexdigest()
        db = await self._get_db()
        existing = await db.file_objects.find_one(
            {
                "sha256": digest,
                "organization_id": organization_id,
                "project_id": project_id or "",
                "document_type": document_type,
            }
        )
        if existing:
            return self._result_from_file_object(existing, deduped=True)

        mime_type = content_type or sniff_mime_from_bytes(content or b"", original_filename)
        storage_key = "/" + storage_key.lstrip("/")
        posix_key = PurePosixPath(storage_key)
        stored_filename = posix_key.name
        path_structure = str(posix_key.parent).lstrip("/")
        locations: List[StorageProviderLocation] = []
        filepath_local: Optional[str] = None
        filepath_s3: Optional[str] = None
        active_providers = providers or [
            StorageProviderConfig(id="local", enabled=True, primary=True)
        ]
        active_providers = sorted(active_providers, key=lambda p: (not p.primary, p.id))

        for provider in active_providers:
            if not provider.enabled:
                continue
            try:
                if provider.id == "local":
                    local_path = await self.file_service.store_document(
                        content,
                        organization_id,
                        project_id or "default",
                        original_filename,
                        stored_filename=stored_filename,
                        path_structure=path_structure,
                    )
                    filepath_local = filepath_local or local_path
                    locations.append(
                        StorageProviderLocation(
                            provider="local",
                            path=local_path,
                            status="ok",
                            primary=provider.primary,
                        )
                    )
                elif provider.id == "s3":
                    key_prefix = (provider.prefix or str(posix_key.parent)).strip("/")
                    object_key = f"{key_prefix.rstrip('/')}/{stored_filename}"
                    saved_key = await self.s3_service.upload_bytes(
                        object_key,
                        content,
                        content_type=mime_type,
                    )
                    filepath_s3 = filepath_s3 or saved_key
                    locations.append(
                        StorageProviderLocation(
                            provider="s3",
                            path=saved_key,
                            status="ok",
                            primary=provider.primary,
                        )
                    )
                else:
                    locations.append(
                        StorageProviderLocation(
                            provider=provider.id,
                            path=None,
                            status="skipped",
                            primary=provider.primary,
                        )
                    )
            except Exception as exc:
                logger.error("Failed to write file object via %s: %s", provider.id, exc)
                locations.append(
                    StorageProviderLocation(
                        provider=provider.id,
                        path=None,
                        status=f"error: {exc}",
                        primary=provider.primary,
                    )
                )

        ok_locations = [loc for loc in locations if loc.status == "ok" and loc.path]
        if not ok_locations:
            raise RuntimeError("Failed to store file object to any provider")

        file_object = FileObject(
            sha256=digest,
            size=len(content or b""),
            mime_type=mime_type,
            original_filename=original_filename,
            stored_filename=stored_filename,
            storage_key=storage_key,
            primary_provider=self._primary_provider(ok_locations),
            locations=locations,
            organization_id=organization_id,
            project_id=project_id or "",
            document_id=document_id,
            document_ids=[document_id] if document_id else [],
            document_type=document_type,
            upload_id=upload_id,
            created_by=self._actor_id(current_user),
        )
        await db.file_objects.insert_one(file_object.model_dump(by_alias=True))
        result = self._result_from_file_object(file_object.model_dump(by_alias=True), deduped=False)
        result["filepath_local"] = filepath_local
        result["filepath_s3"] = filepath_s3
        return result

    async def store_path(
        self,
        *,
        source_path: Path,
        size: int,
        sha256: str,
        mime_type: str,
        organization_id: str,
        project_id: Optional[str],
        original_filename: str,
        storage_key: str,
        current_user: Any = None,
        document_type: str = "document",
        upload_id: Optional[str] = None,
        providers: Optional[List[StorageProviderConfig]] = None,
        document_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        db = await self._get_db()
        existing = await db.file_objects.find_one(
            {
                "sha256": sha256,
                "organization_id": organization_id,
                "project_id": project_id or "",
                "document_type": document_type,
            }
        )
        if existing:
            return self._result_from_file_object(existing, deduped=True)

        source = Path(source_path).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Source file not found: {source}")

        storage_key = "/" + storage_key.lstrip("/")
        posix_key = PurePosixPath(storage_key)
        stored_filename = posix_key.name
        path_structure = str(posix_key.parent).lstrip("/")
        locations: List[StorageProviderLocation] = []
        filepath_local: Optional[str] = None
        filepath_s3: Optional[str] = None
        active_providers = providers or [
            StorageProviderConfig(id="local", enabled=True, primary=True)
        ]
        active_providers = sorted(active_providers, key=lambda p: (not p.primary, p.id))

        for provider in active_providers:
            if not provider.enabled:
                continue
            try:
                if provider.id == "local":
                    local_path = await self.file_service.store_existing_file(
                        source,
                        organization_id,
                        project_id or "default",
                        original_filename,
                        stored_filename=stored_filename,
                        path_structure=path_structure,
                    )
                    filepath_local = filepath_local or local_path
                    locations.append(
                        StorageProviderLocation(
                            provider="local",
                            path=local_path,
                            status="ok",
                            primary=provider.primary,
                        )
                    )
                elif provider.id == "s3":
                    key_prefix = (provider.prefix or str(posix_key.parent)).strip("/")
                    object_key = f"{key_prefix.rstrip('/')}/{stored_filename}"
                    saved_key = await self.s3_service.upload_path(
                        object_key,
                        str(source),
                        content_type=mime_type,
                    )
                    filepath_s3 = filepath_s3 or saved_key
                    locations.append(
                        StorageProviderLocation(
                            provider="s3",
                            path=saved_key,
                            status="ok",
                            primary=provider.primary,
                        )
                    )
                else:
                    locations.append(
                        StorageProviderLocation(
                            provider=provider.id,
                            path=None,
                            status="skipped",
                            primary=provider.primary,
                        )
                    )
            except Exception as exc:
                logger.error("Failed to write file object via %s: %s", provider.id, exc)
                locations.append(
                    StorageProviderLocation(
                        provider=provider.id,
                        path=None,
                        status=f"error: {exc}",
                        primary=provider.primary,
                    )
                )

        ok_locations = [loc for loc in locations if loc.status == "ok" and loc.path]
        if not ok_locations:
            raise RuntimeError("Failed to store file object to any provider")

        file_object = FileObject(
            sha256=sha256,
            size=size,
            mime_type=mime_type,
            original_filename=original_filename,
            stored_filename=stored_filename,
            storage_key=storage_key,
            primary_provider=self._primary_provider(ok_locations),
            locations=locations,
            organization_id=organization_id,
            project_id=project_id or "",
            document_id=document_id,
            document_ids=[document_id] if document_id else [],
            document_type=document_type,
            upload_id=upload_id,
            created_by=self._actor_id(current_user),
        )
        await db.file_objects.insert_one(file_object.model_dump(by_alias=True))
        result = self._result_from_file_object(file_object.model_dump(by_alias=True), deduped=False)
        result["filepath_local"] = filepath_local
        result["filepath_s3"] = filepath_s3
        return result

    def _result_from_file_object(self, file_object: Dict[str, Any], *, deduped: bool) -> Dict[str, Any]:
        locations = file_object.get("locations") or []
        filepath_local = None
        filepath_s3 = None
        storage_locations: List[Dict[str, Any]] = []
        for location in locations:
            provider = location.get("provider")
            path = location.get("path")
            status = location.get("status", "ok")
            if provider == "local" and path and not filepath_local:
                filepath_local = path
            if provider == "s3" and path and not filepath_s3:
                filepath_s3 = path
            storage_locations.append(
                {
                    "provider": provider,
                    "path": path,
                    "status": status,
                }
            )
        return {
            "file_object_id": str(file_object.get("_id")),
            "sha256": file_object.get("sha256"),
            "size": file_object.get("size"),
            "mime_type": file_object.get("mime_type"),
            "storage_key": file_object.get("storage_key"),
            "filepath_local": filepath_local,
            "filepath_s3": filepath_s3,
            "storage_locations": storage_locations,
            "deduped": deduped,
        }

    async def attach_document_version(
        self,
        *,
        document_id: str,
        file_object_id: Optional[str],
        metadata_snapshot: Optional[Dict[str, Any]] = None,
        current_user: Any = None,
        reason: str = "upload",
    ) -> Optional[str]:
        if not document_id:
            return None
        db = await self._get_db()
        await db.document_versions.update_many(
            {"document_id": document_id, "is_current": True},
            {"$set": {"is_current": False}},
        )
        previous = await db.document_versions.find_one(
            {"document_id": document_id},
            sort=[("version_number", -1)],
        )
        version_number = int((previous or {}).get("version_number") or 0) + 1
        version = DocumentVersion(
            document_id=document_id,
            version_number=version_number,
            file_object_id=file_object_id,
            metadata_snapshot=metadata_snapshot or {},
            reason=reason,
            created_by=self._actor_id(current_user),
        )
        result = await db.document_versions.insert_one(version.model_dump(by_alias=True))
        version_id = str(result.inserted_id)
        await db.documents.update_one(
            {"_id": self._lookup_id(document_id)},
            {"$set": {"current_version_id": version_id, "file_object_id": file_object_id}},
        )
        if file_object_id:
            current_file_object = await db.file_objects.find_one({"_id": file_object_id})
            update: Dict[str, Any] = {"$addToSet": {"document_ids": document_id}}
            if current_file_object and not current_file_object.get("document_id"):
                update["$set"] = {"document_id": document_id}
            await db.file_objects.update_one({"_id": file_object_id}, update)
        return version_id

    @staticmethod
    def _lookup_id(value: str) -> Any:
        try:
            return ObjectId(value)
        except Exception:
            return value

    def assert_local_path_allowed(self, path: str) -> None:
        candidate = Path(path).resolve()
        bases = [
            getattr(settings, "SECURE_UPLOADS_DIR", None),
            getattr(settings, "UPLOADS_DIR", None),
            tempfile.gettempdir(),
        ]
        resolved_bases = []
        for base in bases:
            if not base:
                continue
            try:
                resolved_bases.append(Path(str(base)).expanduser().resolve())
            except Exception:
                continue
        if not any(candidate == base or base in candidate.parents for base in resolved_bases):
            raise PermissionError("Local file path is outside configured storage roots")

    async def materialize_to_temp(self, file_object_id: str, suffix: Optional[str] = None) -> Path:
        db = await self._get_db()
        file_object = await db.file_objects.find_one({"_id": file_object_id})
        if not file_object:
            try:
                file_object = await db.file_objects.find_one({"_id": ObjectId(file_object_id)})
            except Exception:
                file_object = None
        if not file_object:
            raise FileNotFoundError("file_object not found")
        data: Optional[bytes] = None
        for location in file_object.get("locations") or []:
            if location.get("provider") == "local" and location.get("path"):
                path = Path(str(location["path"]))
                if path.exists() and path.is_file():
                    self.assert_local_path_allowed(str(path))
                    data = path.read_bytes()
                    break
        if data is None:
            for location in file_object.get("locations") or []:
                if location.get("provider") == "s3" and location.get("path"):
                    data = await self.s3_service.download_bytes(str(location["path"]))
                    break
        if data is None:
            raise FileNotFoundError("file_object has no readable provider location")
        ext = suffix or Path(str(file_object.get("original_filename") or "")).suffix
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        try:
            handle.write(data)
            return Path(handle.name)
        finally:
            handle.close()
