"""Bulk project document zip creation."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Literal, Optional

from bson import ObjectId
from fastapi import HTTPException, status

from ..core.config import settings
from ..core.database import get_database
from ..core.security import CurrentUser, authorize_scope
from ..services.document_audit_service import DocumentAuditService
from ..services.file_object_service import FileObjectService
from ..services.permission_service import PermissionService
from ..services.policy_service import PolicyService
from ..services.s3_service import S3Service
from ..services.storage_settings_service import StorageSettingsService
from ..utils.validation import sanitize_filename

logger = logging.getLogger(__name__)

DownloadAllType = Literal["letters", "contracts", "complete"]


@dataclass
class ZipEntry:
    source_path: Path
    archive_name: str
    source: str
    size: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BulkDownloadResult:
    zip_path: Path
    filename: str
    file_count: int
    total_size: int
    cleanup_paths: list[Path]


class DocumentBulkDownloadService:
    """Creates scoped, auditable project document archives."""

    VALID_TYPES = {"letters", "contracts", "complete"}

    def __init__(
        self,
        *,
        db: Any = None,
        permission_service: Optional[PermissionService] = None,
        audit_service: Optional[DocumentAuditService] = None,
        file_object_service: Optional[FileObjectService] = None,
        storage_settings: Optional[StorageSettingsService] = None,
        s3_service: Optional[S3Service] = None,
    ) -> None:
        self.db = db
        self.permission_service = permission_service or PermissionService()
        self.policy_service = PolicyService(db)
        self.audit_service = audit_service or DocumentAuditService(db)
        self.file_object_service = file_object_service or FileObjectService(db)
        self.storage_settings = storage_settings or StorageSettingsService(db)
        self.s3_service = s3_service or S3Service()

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    async def create_project_archive(
        self,
        *,
        project_id: str,
        download_type: DownloadAllType,
        current_user: CurrentUser,
        upload_type: Optional[str] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        document_id: Optional[str] = None,
    ) -> BulkDownloadResult:
        if download_type not in self.VALID_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="type must be one of: letters, contracts, complete",
            )
        normalized_upload_type = self._normalize_upload_type(upload_type)

        db = await self._get_db()
        project = await self._find_by_id(db.projects, project_id)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        organization_id = str(project.get("organization_id") or "")
        organization = await self._find_by_id(db.organizations, organization_id)
        if not organization:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project organization not found",
            )

        await self.policy_service.authorize(
            current_user,
            "dms.document.bulk_download",
            resource_type="project",
            resource_id=project_id,
            organization_id=organization_id,
            project_id=project_id,
        )

        generated_at = datetime.now(timezone.utc)
        org_short, project_short = await self._resolve_short_names(organization, project)
        cleanup_paths: list[Path] = []
        entries: list[ZipEntry] = []
        seen_sources: set[Path] = set()

        include_letters = download_type in {"letters", "complete"} and normalized_upload_type != "contract"
        include_contracts = download_type in {"contracts", "complete"} and normalized_upload_type not in {"incoming", "outgoing"}

        if include_letters:
            indexed_entries, indexed_temp = await self._collect_indexed_document_entries(
                organization_id=organization_id,
                project_id=project_id,
                upload_type=normalized_upload_type if normalized_upload_type in {"incoming", "outgoing"} else None,
                year=year,
                month=month,
                document_id=document_id,
                seen_sources=seen_sources,
            )
            entries.extend(indexed_entries)
            cleanup_paths.extend(indexed_temp)

        if include_contracts:
            contract_entries, contract_temp = await self._collect_contract_entries(
                organization_id=organization_id,
                project_id=project_id,
                year=year,
                month=month,
                document_id=document_id,
                seen_sources=seen_sources,
            )
            entries.extend(contract_entries)
            cleanup_paths.extend(contract_temp)

        zip_path = self._new_temp_zip_path(project_short)
        archive_names: set[str] = set()
        total_size = 0
        try:
            with zipfile.ZipFile(
                zip_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                allowZip64=True,
            ) as zip_file:
                for entry in entries:
                    if not entry.source_path.exists() or not entry.source_path.is_file():
                        continue
                    archive_name = self._unique_archive_name(
                        entry.archive_name,
                        archive_names,
                    )
                    zip_file.write(entry.source_path, archive_name)
                    entry.archive_name = archive_name
                    total_size += entry.size

                manifest = self._build_manifest(
                    project=project,
                    organization=organization,
                    download_type=download_type,
                    upload_type=normalized_upload_type,
                    year=year,
                    month=month,
                    document_id=document_id,
                    generated_at=generated_at,
                    current_user=current_user,
                    entries=entries,
                    total_size=total_size,
                    org_short=org_short,
                    project_short=project_short,
                )
                zip_file.writestr(
                    "manifest.json",
                    json.dumps(manifest, indent=2, default=str),
                )
        except Exception:
            self.cleanup_paths([zip_path, *cleanup_paths])
            logger.exception("Failed to create project document archive")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to prepare project document archive",
            )

        filename = f"{sanitize_filename(project_short)}_Complete_Documents_{generated_at:%Y-%m-%d}.zip"
        await self.audit_service.emit(
            resource_type="project",
            resource_id=project_id,
            event_type="documents.bulk_downloaded",
            actor_id=getattr(current_user, "id", None),
            organization_id=organization_id,
            project_id=project_id,
            metadata={
                "download_type": download_type,
                "upload_type": normalized_upload_type,
                "year": year,
                "month": month,
                "document_id": document_id,
                "filename": filename,
                "file_count": len(entries),
                "total_size": total_size,
                "generated_at": generated_at.isoformat(),
            },
        )

        return BulkDownloadResult(
            zip_path=zip_path,
            filename=filename,
            file_count=len(entries),
            total_size=total_size,
            cleanup_paths=[zip_path, *cleanup_paths],
        )

    async def _authorize_bulk_download(
        self,
        current_user: CurrentUser,
        organization_id: str,
        project_id: str,
    ) -> None:
        roles = {str(role).lower() for role in (getattr(current_user, "roles", []) or [])}

        if "superadmin" in roles:
            return

        if "orgadmin" in roles:
            allowed_orgs = {
                str(org) for org in (getattr(current_user, "organizations", []) or []) if org
            }
            if getattr(current_user, "organization_id", None):
                allowed_orgs.add(str(current_user.organization_id))
            if str(organization_id) in allowed_orgs:
                return
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized for this organization",
            )

        authorize_scope(
            current_user,
            organization_id=organization_id,
            project_id=project_id,
        )

        if "projectadmin" in roles:
            return

        assigned_projects = {
            str(project) for project in (getattr(current_user, "projects", []) or []) if project
        }
        if str(project_id) not in assigned_projects:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bulk downloads are limited to assigned projects",
            )

        allowed = await self.permission_service.user_has_permission(
            current_user.id,
            "documents:download_all",
            log=False,
            resource_type="project",
            resource_id=project_id,
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Missing required permission: documents:download_all",
            )

    async def _resolve_short_names(
        self,
        organization: Dict[str, Any],
        project: Dict[str, Any],
    ) -> tuple[str, str]:
        organization_id = str(organization.get("_id") or "")
        project_id = str(project.get("_id") or "")
        try:
            resolved = await self.storage_settings.resolve_settings(
                organization_id,
                project_id,
            )
        except Exception:
            resolved = None

        org_short = (
            getattr(resolved, "org_short_name", None)
            or organization.get("shortName")
            or self._make_short_name(organization.get("name"))
            or organization_id
        )
        project_short = (
            getattr(resolved, "project_short_name", None)
            or project.get("shortName")
            or self._make_short_name(project.get("name"))
            or project_id
        )
        return str(org_short), str(project_short)

    def _collect_project_folder_entries(
        self,
        *,
        org_short_names: list[str],
        project_short_names: list[str],
        include_contract_folders: bool,
        seen_sources: set[Path],
    ) -> list[ZipEntry]:
        entries: list[ZipEntry] = []
        for root in self._upload_roots():
            for org_short in org_short_names:
                for project_short in project_short_names:
                    project_root = (root / org_short / project_short).resolve()
                    if not project_root.exists() or not project_root.is_dir():
                        continue
                    for path in project_root.rglob("*"):
                        if not path.is_file():
                            continue
                        resolved = path.resolve()
                        if resolved in seen_sources:
                            continue
                        relative = resolved.relative_to(project_root)
                        if not include_contract_folders and any(
                            part.lower() == "contracts" for part in relative.parts
                        ):
                            continue
                        seen_sources.add(resolved)
                        entries.append(
                            ZipEntry(
                                source_path=resolved,
                                archive_name=self._zip_name("letters", relative),
                                source="project_folder",
                                size=resolved.stat().st_size,
                            )
                        )
                    # The first existing path for a root is the canonical project folder.
                    break
                else:
                    continue
                break
        return entries

    async def _collect_indexed_document_entries(
        self,
        *,
        organization_id: str,
        project_id: str,
        upload_type: Optional[str],
        year: Optional[int],
        month: Optional[int],
        document_id: Optional[str],
        seen_sources: set[Path],
    ) -> tuple[list[ZipEntry], list[Path]]:
        db = await self._get_db()
        query = {
            "organization_id": {"$in": self._id_variants(organization_id)},
            "project_id": {"$in": self._id_variants(project_id)},
            "lifecycle_state": {"$ne": "deleted"},
        }
        query["uploadType"] = upload_type if upload_type else {"$ne": "contract"}
        self._apply_optional_filters(query, year=year, month=month, document_id=document_id)
        docs = await db.documents.find(query).to_list(length=None)
        entries: list[ZipEntry] = []
        cleanup: list[Path] = []

        for doc in docs:
            path = self._local_document_path(doc)
            if path is None and doc.get("file_object_id"):
                path = await self._materialize_file_object(doc, cleanup)
            if path is None:
                continue
            resolved = path.resolve()
            if resolved in seen_sources:
                continue
            seen_sources.add(resolved)
            filename = sanitize_filename(doc.get("filename") or resolved.name)
            entries.append(
                ZipEntry(
                    source_path=resolved,
                    archive_name=f"letters/indexed/{filename}",
                    source="documents_collection",
                    size=int(doc.get("filesize") or resolved.stat().st_size),
                    metadata={"document_id": str(doc.get("_id"))},
                )
            )
        return entries, cleanup

    async def _collect_contract_entries(
        self,
        *,
        organization_id: str,
        project_id: str,
        year: Optional[int],
        month: Optional[int],
        document_id: Optional[str],
        seen_sources: set[Path],
    ) -> tuple[list[ZipEntry], list[Path]]:
        db = await self._get_db()
        query = {
            "organization_id": {"$in": self._id_variants(organization_id)},
            "project_id": {"$in": self._id_variants(project_id)},
            "uploadType": "contract",
            "lifecycle_state": {"$ne": "deleted"},
        }
        self._apply_optional_filters(query, year=year, month=month, document_id=document_id)
        docs = await db.documents.find(query).to_list(length=None)
        entries: list[ZipEntry] = []
        cleanup: list[Path] = []

        for doc in docs:
            path = self._local_document_path(doc)
            if path is None and doc.get("file_object_id"):
                path = await self._materialize_file_object(doc, cleanup)
            if path is None and doc.get("filepath_s3"):
                path = await self._materialize_s3_key(doc, cleanup)
            if path is None:
                continue
            resolved = path.resolve()
            if resolved in seen_sources:
                continue
            seen_sources.add(resolved)
            filename = sanitize_filename(doc.get("filename") or resolved.name)
            entries.append(
                ZipEntry(
                    source_path=resolved,
                    archive_name=f"contracts/{filename}",
                    source="contract_storage",
                    size=int(doc.get("filesize") or resolved.stat().st_size),
                    metadata={"document_id": str(doc.get("_id"))},
                )
            )
        return entries, cleanup

    def _local_document_path(self, doc: Dict[str, Any]) -> Optional[Path]:
        local_path = doc.get("filepath_local")
        if not local_path:
            return None
        path = Path(str(local_path))
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists() or not path.is_file():
            return None
        self.file_object_service.assert_local_path_allowed(str(path))
        return path

    async def _materialize_file_object(
        self,
        doc: Dict[str, Any],
        cleanup: list[Path],
    ) -> Optional[Path]:
        try:
            suffix = Path(str(doc.get("filename") or "")).suffix
            path = await self.file_object_service.materialize_to_temp(
                str(doc["file_object_id"]),
                suffix=suffix or None,
            )
            cleanup.append(path)
            return path
        except Exception:
            logger.debug("Unable to materialize file object for %s", doc.get("_id"), exc_info=True)
            return None

    async def _materialize_s3_key(
        self,
        doc: Dict[str, Any],
        cleanup: list[Path],
    ) -> Optional[Path]:
        try:
            data = await self.s3_service.download_bytes(str(doc["filepath_s3"]))
            suffix = Path(str(doc.get("filename") or "")).suffix
            handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            try:
                handle.write(data)
                path = Path(handle.name)
                cleanup.append(path)
                return path
            finally:
                handle.close()
        except Exception:
            logger.debug("Unable to materialize S3 object for %s", doc.get("_id"), exc_info=True)
            return None

    def _build_manifest(
        self,
        *,
        project: Dict[str, Any],
        organization: Dict[str, Any],
        download_type: str,
        upload_type: Optional[str],
        year: Optional[int],
        month: Optional[int],
        document_id: Optional[str],
        generated_at: datetime,
        current_user: CurrentUser,
        entries: list[ZipEntry],
        total_size: int,
        org_short: str,
        project_short: str,
    ) -> Dict[str, Any]:
        return {
            "project": {
                "id": str(project.get("_id")),
                "name": project.get("name"),
                "shortName": project_short,
                "organization_id": str(project.get("organization_id") or ""),
            },
            "organization": {
                "id": str(organization.get("_id")),
                "name": organization.get("name"),
                "shortName": org_short,
            },
            "download": {
                "type": download_type,
                "upload_type": upload_type,
                "year": year,
                "month": month,
                "document_id": document_id,
                "generated_at": generated_at.isoformat(),
                "file_count": len(entries),
                "total_size": total_size,
            },
            "user": {
                "id": getattr(current_user, "id", None),
                "username": getattr(current_user, "username", None),
                "email": getattr(current_user, "email", None),
                "roles": list(getattr(current_user, "roles", []) or []),
            },
            "files": [
                {
                    "path": entry.archive_name,
                    "size": entry.size,
                    "source": entry.source,
                    **({"metadata": entry.metadata} if entry.metadata else {}),
                }
                for entry in entries
            ],
        }

    @staticmethod
    async def _find_by_id(collection: Any, value: str) -> Optional[Dict[str, Any]]:
        candidates: list[Any] = [value]
        try:
            candidates.append(ObjectId(value))
        except Exception:
            pass
        doc = await collection.find_one({"_id": {"$in": candidates}})
        if doc and "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return doc

    @staticmethod
    def _id_variants(value: str) -> list[Any]:
        variants: list[Any] = [str(value)]
        try:
            variants.append(ObjectId(value))
        except Exception:
            pass
        return variants

    @staticmethod
    def _normalize_upload_type(value: Optional[str]) -> Optional[str]:
        if value in (None, ""):
            return None
        normalized = str(value).strip().lower()
        if normalized not in {"incoming", "outgoing", "contract"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="upload_type must be one of: incoming, outgoing, contract",
            )
        return normalized

    @classmethod
    def _apply_optional_filters(
        cls,
        query: Dict[str, Any],
        *,
        year: Optional[int],
        month: Optional[int],
        document_id: Optional[str],
    ) -> None:
        if document_id:
            query["_id"] = {"$in": cls._id_variants(str(document_id))}

        if year is None:
            return
        if year < 1900 or year > 3000:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="year must be between 1900 and 3000",
            )
        if month is not None and (month < 1 or month > 12):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="month must be between 1 and 12",
            )

        start_month = month or 1
        start = datetime(year, start_month, 1)
        if month is None:
            end = datetime(year + 1, 1, 1)
        elif month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)
        query["date"] = {"$gte": start, "$lt": end}

    @staticmethod
    def _make_short_name(value: Any) -> str:
        text = str(value or "untitled").lower()
        text = re.sub(r"[^a-z0-9]+", "-", text)
        text = re.sub(r"-{2,}", "-", text).strip("-")
        return (text[:10] or "untitled")

    @classmethod
    def _short_candidates(cls, entity: Dict[str, Any], preferred: str) -> list[str]:
        raw_values = [
            preferred,
            entity.get("shortName"),
            entity.get("short_name"),
            cls._make_short_name(entity.get("name")),
            entity.get("_id"),
            entity.get("name"),
        ]
        candidates: list[str] = []
        seen: set[str] = set()
        for value in raw_values:
            text = cls._safe_path_segment(value)
            if not text:
                continue
            for variant in (text, text.lower(), text.upper()):
                if variant and variant not in seen:
                    seen.add(variant)
                    candidates.append(variant)
        return candidates

    @staticmethod
    def _safe_path_segment(value: Any) -> str:
        text = str(value or "").strip().replace("\\", "_").replace("/", "_")
        if text in {"", ".", ".."} or ".." in text:
            return ""
        return text

    @staticmethod
    def _zip_name(prefix: str, relative: Path) -> str:
        safe_parts = [sanitize_filename(part) for part in relative.parts]
        return "/".join([prefix, *safe_parts])

    @staticmethod
    def _unique_archive_name(name: str, seen: set[str]) -> str:
        normalized = name.replace("\\", "/").lstrip("/")
        if normalized not in seen:
            seen.add(normalized)
            return normalized
        path = Path(normalized)
        stem = path.stem
        suffix = path.suffix
        parent = str(path.parent).replace("\\", "/")
        counter = 2
        while True:
            candidate_name = f"{stem}_{counter}{suffix}"
            candidate = f"{parent}/{candidate_name}" if parent != "." else candidate_name
            if candidate not in seen:
                seen.add(candidate)
                return candidate
            counter += 1

    @staticmethod
    def _new_temp_zip_path(project_short: str) -> Path:
        safe_project = sanitize_filename(project_short or "project")
        handle = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".zip",
            prefix=f"{safe_project}_documents_",
        )
        path = Path(handle.name)
        handle.close()
        return path

    @staticmethod
    def _upload_roots() -> Iterable[Path]:
        candidates = [
            getattr(settings, "UPLOADS_DIR", None),
            getattr(settings, "SECURE_UPLOADS_DIR", None),
            "uploads",
            "backend/uploads",
        ]
        seen: set[Path] = set()
        for candidate in candidates:
            if not candidate:
                continue
            path = Path(str(candidate))
            if not path.is_absolute():
                path = Path.cwd() / path
            try:
                resolved = path.resolve()
            except Exception:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            yield resolved

    @staticmethod
    def cleanup_paths(paths: Iterable[Path]) -> None:
        for path in paths:
            try:
                if path and Path(path).exists():
                    os.remove(path)
            except Exception:
                logger.debug("Failed to remove temporary file %s", path, exc_info=True)
