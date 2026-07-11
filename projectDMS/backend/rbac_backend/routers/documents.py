# Enhanced documents.py with Bulk Upload Support

"""
Document management module with secure file handling, proper authorization, and bulk upload capabilities.
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query, BackgroundTasks, Body, Header, Request
from starlette.background import BackgroundTask
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, date
import logging
import os
from pathlib import Path
import uuid
import csv
import io
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
import asyncio
from bson import ObjectId
import tempfile

from ..core.database import get_db, get_database
from ..core.permissions import Permissions
from ..core.security import get_current_user, CurrentUser
from ..core.config import settings
from ..config.document_processing_config import DocumentProcessingConfig
from ..services.document_service import (
    DocumentConflictError,
    DocumentService,
    DocumentServiceError,
)
from ..services.file_service import SecureFileService
from ..services.export_service import ExportService
from ..services.authorization_service import AuthorizationService
from ..services.bulk_upload_service import BulkUploadService
from ..services.langchain_vector_service import LangChainVectorService
from ..dependencies import get_notification_service
from ..services.reference_sync_service import ReferenceSyncError
from ..services.storage_settings_service import StorageSettingsService
from ..services.s3_service import S3Service
from ..services.storage_key_builder import StorageKeyBuilder
from ..services.file_object_service import FileObjectService
from ..services.document_audit_service import DocumentAuditService
from ..services.document_bulk_download_service import DocumentBulkDownloadService
from ..services.policy_service import PolicyService
from ..services.step_up_service import require_step_up
from ..services.upload_limits import upload_concurrency_limiter
from ..services.upload_streaming import SpooledUpload, spool_upload_file, validate_spooled_upload
from ..services.antivirus_service import AntivirusService
from ..utils.file_validation import sniff_mime_from_bytes
from ..models.document import (
    Document,
    DocumentUpdate,
    DocumentSummaryMetadataUpdate,
    DocumentListResponse,
    Enclosure,
    EnclosureResponse,
    DocumentReference,
    ReferenceCreate,
    LinkDocumentsRequest,
    BulkUploadRequest,
    BulkUploadResponse,
    BulkUploadStatus,
    DocumentProcessingResult,
)
from ..models.notification import NotificationContext, NotificationType
from ..models.storage_settings import StorageProviderConfig
from ..utils.validation import sanitize_filename
from ..utils.error_handler import handle_exceptions, DocumentError
from ..utils.date_parser import parse_date_safely
from ..utils.csv_validator import validate_csv_structure, parse_csv_row
from fastapi.responses import FileResponse, Response

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_policy_service() -> PolicyService:
    return PolicyService()


async def get_document_bulk_download_service() -> DocumentBulkDownloadService:
    return DocumentBulkDownloadService()


def verify_langgraph_token(x_api_token: str = Header(...)) -> None:
    expected = getattr(settings, "LANGGRAPH_API_TOKEN", None)
    if not expected or x_api_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service token",
        )


def _current_user_policy_scope(current_user: CurrentUser) -> tuple[Optional[str], Optional[str]]:
    organization_id = getattr(current_user, "organization_id", None)
    if not organization_id:
        organizations = getattr(current_user, "organizations", None) or []
        organization_id = organizations[0] if organizations else None
    projects = getattr(current_user, "projects", None) or []
    project_id = projects[0] if projects else None
    return (
        str(organization_id) if organization_id else None,
        str(project_id) if project_id else None,
    )


async def _ensure_document_access(
    current_user: CurrentUser,
    document_id: str,
    permission: str,
) -> None:
    db = await get_database()
    candidates: list[Any] = [document_id]
    try:
        candidates.append(ObjectId(document_id))
    except Exception:
        pass
    document = await db.documents.find_one({"_id": {"$in": candidates}})
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    await PolicyService(db).authorize_document(current_user, permission, document)


def _parse_revision_header(
    if_match: Optional[str] = None,
    x_document_revision: Optional[str] = None,
) -> Optional[int]:
    raw = if_match or x_document_revision
    if not raw:
        return None
    value = str(raw).strip()
    if value.startswith("W/"):
        value = value[2:].strip()
    value = value.strip('"')
    if not value:
        return None
    try:
        revision = int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid document revision header",
        ) from exc
    if revision < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid document revision header",
        )
    return revision


async def _set_document_revision_headers(
    response: Response,
    controller: "DocumentController",
    document_id: str,
) -> None:
    revision = await controller.document_service.get_document_revision(document_id)
    if revision is not None:
        response.headers["X-Document-Revision"] = str(revision)
        response.headers["ETag"] = f'"{revision}"'


def _clean_summary_scalar(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value).strip()


def _normalize_summary_list(values: Optional[List[str]]) -> List[str]:
    if not values:
        return []
    seen: set[str] = set()
    normalized: List[str] = []
    for raw in values:
        if raw is None:
            continue
        for part in str(raw).replace("\r", "\n").replace("\n", ",").split(","):
            text = " ".join(part.strip().split())
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(text)
    return normalized


SUMMARY_METADATA_UPDATE_PATHS: Dict[str, tuple[str, ...]] = {
    "asset_type": ("asset_type", "metadata.asset_type"),
    "location": ("location", "metadata.location"),
    "work_type": ("work_type", "metadata.work_type"),
    "issue_nature": ("issue_nature", "metadata.issue_nature"),
    "claim_category": ("claim_category", "metadata.claim_category"),
    "alleged_responsibility": (
        "alleged_responsibility",
        "metadata.alleged_responsibility",
        "metadata.responsibility",
    ),
}

SUMMARY_KEYWORD_UPDATE_PATHS: Dict[str, tuple[str, ...]] = {
    "keywords": ("keywords", "metadata.keywords"),
    "additional_keywords": ("additional_keywords", "metadata.additional_keywords"),
    "extracted_tags": ("extracted_tags", "metadata.tags"),
    "extracted_sub_tags": ("extracted_subTags", "metadata.subTags", "metadata.sub_tags"),
}


def _build_summary_metadata_update(payload: DocumentSummaryMetadataUpdate) -> Dict[str, Any]:
    update_fields: Dict[str, Any] = {}

    if payload.extracted_metadata is not None:
        metadata = payload.extracted_metadata.model_dump(exclude_unset=True)
        for field, value in metadata.items():
            for path in SUMMARY_METADATA_UPDATE_PATHS.get(field, ()):
                update_fields[path] = _clean_summary_scalar(value)

    for field, paths in SUMMARY_KEYWORD_UPDATE_PATHS.items():
        if field not in payload.model_fields_set:
            continue
        value = getattr(payload, field)
        normalized = _normalize_summary_list(value)
        for path in paths:
            update_fields[path] = normalized

    return update_fields


class DocumentController:
    """Document controller with clean separation of concerns and bulk upload support."""

    def __init__(
        self,
        document_service: DocumentService,
        file_service: SecureFileService,
        export_service: ExportService,
        auth_service: AuthorizationService,
        bulk_upload_service: BulkUploadService
    ):
        self.document_service = document_service
        self.file_service = file_service
        self.export_service = export_service
        self.auth_service = auth_service
        self.bulk_upload_service = bulk_upload_service
        self.storage_settings = StorageSettingsService()
        self.policy_service = PolicyService()
        self.s3_service = S3Service()
        self.storage_key_builder = StorageKeyBuilder()
        self.file_object_service = FileObjectService(
            file_service=self.file_service,
            s3_service=self.s3_service,
            storage_settings=self.storage_settings,
        )
        self.audit_service = DocumentAuditService()

    async def _write_to_providers(
        self,
        *,
        content: bytes,
        org_id: str,
        project_id: str,
        filename: str,
        upload_type: str,
        letter_no: Optional[str] = None,
        current_user: Optional[CurrentUser] = None,
    ) -> Dict[str, Any]:
        """
        Resolve storage settings and write to enabled providers.
        Returns dict with filepath_local, filepath_s3, storage_locations, and resolved_path.
        """
        try:
            context = await self.file_object_service.resolve_storage_context(org_id, project_id)
        except Exception as exc:  # pragma: no cover - fallback path
            logger.warning("Failed to resolve storage settings; using local only. %s", exc)
            context = {
                "organization": org_id,
                "project": project_id,
                "providers": [StorageProviderConfig(id="local", enabled=True, primary=True)],
            }

        upload_id = str(ObjectId())
        storage_key = self.storage_key_builder.build_document_key(
            organization=context["organization"],
            project=context["project"],
            upload_type=upload_type,
            letter_no=letter_no,
            original_filename=filename,
            upload_id=upload_id,
        )
        try:
            result = await self.file_object_service.store_bytes(
                content=content,
                organization_id=org_id,
                project_id=project_id,
                original_filename=filename,
                storage_key=storage_key,
                current_user=current_user,
                document_type=str(upload_type or "document").lower(),
                upload_id=upload_id,
                content_type=sniff_mime_from_bytes(content, filename),
                providers=context["providers"],
            )
        except Exception as exc:
            logger.error("Failed to store document through file object service: %s", exc)
            raise DocumentError(
                "Failed to store document to any provider",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            ) from exc
        result["resolved_path"] = str(Path(storage_key).parent)
        return result

    async def _write_spooled_to_providers(
        self,
        *,
        spooled: SpooledUpload,
        org_id: str,
        project_id: str,
        upload_type: str,
        letter_no: Optional[str] = None,
        current_user: Optional[CurrentUser] = None,
        document_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            context = await self.file_object_service.resolve_storage_context(org_id, project_id)
        except Exception as exc:  # pragma: no cover - fallback path
            logger.warning("Failed to resolve storage settings; using local only. %s", exc)
            context = {
                "organization": org_id,
                "project": project_id,
                "providers": [StorageProviderConfig(id="local", enabled=True, primary=True)],
            }

        upload_id = str(ObjectId())
        storage_key = self.storage_key_builder.build_document_key(
            organization=context["organization"],
            project=context["project"],
            upload_type=upload_type,
            letter_no=letter_no,
            original_filename=spooled.filename,
            upload_id=upload_id,
        )
        try:
            result = await self.file_object_service.store_path(
                source_path=spooled.path,
                size=spooled.size,
                sha256=spooled.sha256,
                mime_type=spooled.mime_type,
                organization_id=org_id,
                project_id=project_id,
                original_filename=spooled.filename,
                storage_key=storage_key,
                current_user=current_user,
                document_type=document_type or str(upload_type or "document").lower(),
                upload_id=upload_id,
                providers=context["providers"],
            )
        except Exception as exc:
            logger.error("Failed to store spooled document through file object service: %s", exc)
            raise DocumentError(
                "Failed to store document to any provider",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            ) from exc
        result["resolved_path"] = str(Path(storage_key).parent)
        return result

    async def _materialize_for_processing(self, document: Document) -> str:
        """
        Ensure a local file exists for processing. Prefer local path; otherwise download from S3.
        """
        local_path = getattr(document, "filepath_local", None)
        if local_path:
            p = Path(local_path)
            if p.exists():
                return str(p)

        s3_key = getattr(document, "filepath_s3", None)
        if s3_key:
            try:
                data = await self.s3_service.download_bytes(s3_key)
                temp_dir = Path(tempfile.gettempdir()) / "document_reprocess"
                temp_dir.mkdir(parents=True, exist_ok=True)
                fname = sanitize_filename(
                    getattr(document, "filename", None)
                    or Path(s3_key).name
                    or f"{document.id}.bin"
                )
                temp_path = temp_dir / fname
                temp_path.write_bytes(data)
                return str(temp_path)
            except Exception as exc:  # pragma: no cover - download fallback
                logger.error("Failed to download document %s from S3: %s", document.id, exc)
                raise DocumentError(
                    "Document file unavailable for processing",
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        raise DocumentError(
            "Document file path unavailable for processing",
            status.HTTP_400_BAD_REQUEST,
        )

    async def vector_search_documents(
        self,
        *,
        query: str,
        limit: int,
        filters: Dict[str, Optional[str]],
        current_user: CurrentUser,
    ) -> Dict[str, Any]:
        config = DocumentProcessingConfig()
        if not config.qdrant_enabled:
            raise DocumentError(
                "Vector search is disabled",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        vector_service = LangChainVectorService(config)
        if not vector_service.enabled:
            raise DocumentError(
                "Vector search is currently unavailable",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        scope_org, scope_project = _current_user_policy_scope(current_user)
        await self.policy_service.authorize(
            current_user,
            Permissions.DOCUMENT_VIEW,
            resource_type="document_vector_search",
            organization_id=filters.get("organization_id") or scope_org,
            project_id=filters.get("project_id") or scope_project,
        )

        validated_filters = await self.auth_service.build_document_query(
            current_user,
            filters,
        )

        must_conditions: List[Dict[str, Any]] = []

        def _add_match(key: str, value: Any) -> None:
            if value in (None, ""):
                return
            must_conditions.append(
                {"key": key, "match": {"value": str(value)}}
            )

        def _add_any(key: str, values: Any) -> None:
            try:
                candidates = [str(item) for item in values if item not in (None, "")]
            except TypeError:
                candidates = []
            if candidates:
                must_conditions.append(
                    {"key": key, "match": {"any": candidates}}
                )

        for key in ("organization_id", "project_id"):
            value = validated_filters.get(key)
            if isinstance(value, dict) and "$in" in value:
                _add_any(key, value.get("$in", []))
            elif isinstance(value, (list, tuple, set)):
                _add_any(key, value)
            elif value:
                _add_match(key, value)

        upload_type = filters.get("uploadType") or validated_filters.get("uploadType")
        if upload_type:
            _add_match("uploadType", upload_type)

        qdrant_filter: Optional[Dict[str, Any]] = None
        if must_conditions:
            qdrant_filter = {"must": must_conditions}

        search_results = await vector_service.similarity_search(
            query_text=query,
            top_k=limit,
            filters=qdrant_filter,
        )

        ordered_ids: List[str] = []
        seen_ids: set[str] = set()
        for item in search_results:
            metadata = item.get("metadata") or {}
            doc_id = str(metadata.get("document_id") or "").strip()
            if doc_id and doc_id not in seen_ids:
                seen_ids.add(doc_id)
                ordered_ids.append(doc_id)

        documents = await self.document_service.get_documents_by_ids(ordered_ids)
        doc_lookup = {str(doc.id): doc for doc in documents}

        response_results: List[Dict[str, Any]] = []
        for item in search_results:
            metadata = item.get("metadata") or {}
            doc_id = str(metadata.get("document_id") or "").strip()
            if not doc_id:
                continue

            document = doc_lookup.get(doc_id)
            doc_summary: Optional[Dict[str, Any]] = None
            if document:
                await self.policy_service.authorize_document(
                    current_user, Permissions.DOCUMENT_VIEW, document
                )
                doc_summary = {
                    "id": doc_id,
                    "filename": document.filename,
                    "letterNo": document.letterNo,
                    "subject": document.subject,
                    "uploadType": document.uploadType,
                    "organization_id": document.organization_id,
                    "project_id": document.project_id,
                    "date": document.date.isoformat() if document.date else None,
                    "tags": document.tags or [],
                    "subTags": document.subTags or [],
                    "summary": document.summary,
                    "keywords": document.keywords or [],
                }

            response_results.append(
                {
                    "document_id": doc_id if doc_id else None,
                    "score": float(item.get("score", 0.0) or 0.0),
                    "text": item.get("text") or "",
                    "chunk_metadata": metadata,
                    "document": doc_summary,
                }
            )

        def _serialize_filter_value(value: Any) -> Any:
            if isinstance(value, dict) and "$in" in value:
                return list(value.get("$in", []))
            return value

        applied_filters = {
            key: _serialize_filter_value(validated_filters.get(key, filters.get(key)))
            for key in ("organization_id", "project_id", "uploadType")
            if validated_filters.get(key) or filters.get(key)
        }

        return {
            "query": query,
            "limit": limit,
            "results": response_results,
            "applied_filters": applied_filters,
        }

    async def create_document(
        self,
        background_tasks: Optional[BackgroundTasks],
        file: UploadFile,
        organization_id: str,
        project_id: str,
        upload_type: str,
        letter_no: str,
        date_str: str,
        current_user: CurrentUser,
        emit_upload_notification: bool = True,
        **kwargs
    ) -> Document:
        """Create document with comprehensive validation and security."""
        try:
            await self.policy_service.authorize(
                current_user,
                Permissions.DOCUMENT_UPLOAD,
                resource_type="project",
                resource_id=project_id,
                organization_id=organization_id,
                project_id=project_id,
            )

            # Validate required fields
            if not file.filename or not letter_no:
                raise DocumentError("Missing required fields", status.HTTP_400_BAD_REQUEST)

            max_size = max(1, int(settings.GENERAL_UPLOAD_MAX_FILE_SIZE_MB)) * 1024 * 1024
            async with upload_concurrency_limiter.slot(
                f"user:{current_user.id}",
                int(settings.UPLOAD_MAX_CONCURRENT_PER_USER),
            ), upload_concurrency_limiter.slot(
                f"org:{organization_id}",
                int(settings.UPLOAD_MAX_CONCURRENT_PER_ORG),
            ):
                spooled = await spool_upload_file(file, max_size_bytes=max_size)
                try:
                    validation_result = validate_spooled_upload(
                        spooled, settings.ALLOWED_DOCUMENT_MIMES
                    )

                    if not validation_result.is_valid:
                        raise DocumentError(
                            f"Invalid file: {validation_result.error}",
                            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
                        )

                    # --- ANTIVIRUS STREAM SCAN ---
                    if settings.ANTIVIRUS_ENABLED:
                        antivirus = AntivirusService()
                        is_clean, scan_detail = await antivirus.scan_file(spooled.path)
                        if not is_clean:
                            raise DocumentError(
                                f"Antivirus scan rejected this file: {scan_detail}",
                                status.HTTP_400_BAD_REQUEST
                            )
                    # ------------------------------

                    # Parse date safely
                    parsed_date = parse_date_safely(date_str)

                    # Store file(s) according to storage settings
                    store_result = await self._write_spooled_to_providers(
                        spooled=spooled,
                        org_id=organization_id,
                        project_id=project_id,
                        upload_type=upload_type,
                        letter_no=letter_no,
                        current_user=current_user,
                    )
                finally:
                    await spooled.cleanup()

            # Create document record
            document = await self.document_service.create_document(
                filepath_local=store_result.get("filepath_local"),
                filepath_s3=store_result.get("filepath_s3"),
                storage_locations=store_result.get("storage_locations") or [],
                filename=file.filename,
                organization_id=organization_id,
                project_id=project_id,
                upload_type=upload_type,
                letter_no=letter_no,
                date=parsed_date,
                current_user=current_user,
                emit_upload_notification=emit_upload_notification,
                **kwargs
            )

            version_id = await self.file_object_service.attach_document_version(
                document_id=document.id,
                file_object_id=store_result.get("file_object_id"),
                metadata_snapshot=document.model_dump(by_alias=True, exclude_none=True),
                current_user=current_user,
                reason="upload",
            )
            try:
                db = await get_database()
                await db.documents.update_one(
                    {"_id": ObjectId(document.id)},
                    {
                        "$set": {
                            "file_object_id": store_result.get("file_object_id"),
                            "current_version_id": version_id,
                            "storage_key": store_result.get("storage_key"),
                            "sha256": store_result.get("sha256"),
                            "lifecycle_state": "active",
                        }
                    },
                )
            except Exception:
                logger.warning("Failed to attach file object metadata to document %s", document.id, exc_info=True)

            await self.audit_service.emit(
                resource_type="document",
                resource_id=document.id,
                event_type="document.created",
                actor_id=getattr(current_user, "id", None),
                organization_id=organization_id,
                project_id=project_id,
                metadata={
                    "file_object_id": store_result.get("file_object_id"),
                    "storage_key": store_result.get("storage_key"),
                    "sha256": store_result.get("sha256"),
                    "deduped": store_result.get("deduped", False),
                    "upload_streamed": True,
                },
            )

            # Schedule background processing if needed
            if kwargs.get('ocr_enabled', False):
                await self.document_service.queue_document_processing(
                    document,
                    store_result.get("filepath_local") or store_result.get("filepath_s3"),
                )

            return document

        except (DocumentError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Document creation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Document creation service temporarily unavailable"
            )

    async def bulk_upload_documents(
        self,
        background_tasks: BackgroundTasks,
        csv_file: UploadFile,
        files: List[UploadFile],
        organization_id: str,
        project_id: str,
        current_user: CurrentUser,
        csv_encoding: Optional[str] = None,
    ) -> BulkUploadResponse:
        """Handle bulk document upload with CSV metadata."""
        try:
            await self.policy_service.authorize(
                current_user,
                Permissions.DOCUMENT_UPLOAD,
                resource_type="project",
                resource_id=project_id,
                organization_id=organization_id,
                project_id=project_id,
            )

            # Validate CSV file
            if not csv_file.filename or not csv_file.filename.endswith('.csv'):
                raise DocumentError("Invalid CSV file", status.HTTP_400_BAD_REQUEST)

            # Use BulkUploadService for csv reading with proper encoding detection
            df = self.bulk_upload_service.read_csv_from_upload_file(
                csv_file, user_provided_encoding=csv_encoding
            )
            csv_data = []

            # Normalize columns to align with single-upload fields
            df = self.bulk_upload_service.normalize_csv_dataframe(df)

            # Validate CSV structure and rows (only the minimal required fields)
            required_columns = ['filename', 'upload_type', 'letter_no', 'date', 'ocr_enabled']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                raise DocumentError(
                    f"Missing required columns: {', '.join(missing_columns)}",
                    status.HTTP_400_BAD_REQUEST
                )

            for idx, row in df.iterrows():
                row_dict = row.to_dict()
                try:
                    validated_row = await self._validate_csv_row(row_dict, idx + 1)
                    csv_data.append(validated_row)
                except Exception as e:
                    logger.error(f"CSV row {idx + 1} validation failed: {str(e)}")
                    csv_data.append({
                        **row_dict,
                        '_validation_error': str(e),
                        '_row_number': idx + 1
                    })

            # Persist uploaded files so background processing can safely re-open them
            stored_files = await self.bulk_upload_service.persist_upload_files(files)

            # Create bulk upload job
            job_id = str(uuid.uuid4())
            
            # Initialize bulk upload tracking
            bulk_status = BulkUploadStatus(
                job_id=job_id,
                total_files=len(stored_files),
                processed_files=0,
                successful_uploads=0,
                failed_uploads=0,
                status="processing",
                created_at=datetime.utcnow(),
                results=[]
            )

            # Store job status
            await self.bulk_upload_service.create_bulk_job(bulk_status)

            # Schedule bulk processing
            background_tasks.add_task(
                self._process_bulk_upload,
                job_id, csv_data, stored_files, organization_id, project_id, current_user
            )

            return BulkUploadResponse(
                job_id=job_id,
                message="Bulk upload initiated successfully",
                total_files=len(stored_files),
                status="processing"
            )

        except (DocumentError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Bulk upload initiation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Bulk upload service temporarily unavailable"
            )

    def _decode_csv_content(self, csv_content: bytes) -> str:
        encodings = ['utf-8-sig', 'utf-16', 'utf-16le', 'utf-16be', 'cp1252', 'latin-1']
        last_err = None
        for enc in encodings:
            try:
                text = csv_content.decode(enc)
                # Normalize common problematic whitespace (e.g., NBSP 0xA0 from Excel)
                return text.replace('\u00A0', ' ')
            except UnicodeDecodeError as e:
                last_err = e
                continue
        try:
            # Fallback with replacement to avoid hard failure on rare bytes
            return csv_content.decode('utf-8', errors='replace').replace('\u00A0', ' ')
        except Exception:
            raise DocumentError(f"CSV decoding failed: {last_err}", status.HTTP_400_BAD_REQUEST)

    def _normalize_bulk_filename(self, value: str) -> str:
        if not value:
            return ""
        try:
            candidate = Path(value)
            name = candidate.name
            return name or value
        except Exception:
            return value

    def _build_bulk_file_lookup(self, files: List[UploadFile]) -> Dict[str, UploadFile]:
        mapping: Dict[str, UploadFile] = {}
        for upload in files:
            if not upload or not upload.filename:
                continue
            normalized = self._normalize_bulk_filename(upload.filename)
            candidates = {
                upload.filename,
                upload.filename.lower(),
                normalized,
                normalized.lower(),
            }
            for candidate in candidates:
                if candidate:
                    mapping[candidate] = upload
        return mapping
    async def _parse_and_validate_csv(self, csv_content: bytes) -> List[Dict[str, Any]]:
        """Parse and validate CSV content."""
        try:
            # Decode CSV content (robust multi-encoding support + NBSP normalization)
            csv_text = self._decode_csv_content(csv_content)

            # Parse CSV using pandas; fallback to semicolon if the file uses ';' as delimiter
            df = pd.read_csv(io.StringIO(csv_text))
            if df.shape[1] == 1 and ';' in csv_text:
                df = pd.read_csv(io.StringIO(csv_text), sep=';')

            # Normalize column names to avoid NBSP and case issues (common with Excel exports)
            df = self.bulk_upload_service.normalize_csv_dataframe(df)
            
            # Validate CSV structure
            required_columns = ['filename', 'upload_type', 'letter_no', 'date', 'ocr_enabled']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                raise DocumentError(
                    f"Missing required columns: {', '.join(missing_columns)}",
                    status.HTTP_400_BAD_REQUEST
                )

            # Convert to list of dictionaries
            csv_data = df.to_dict('records')
            
            # Validate each row
            validated_data = []
            for idx, row in enumerate(csv_data, 1):
                try:
                    validated_row = await self._validate_csv_row(row, idx)
                    validated_data.append(validated_row)
                except Exception as e:
                    logger.error(f"Row {idx} validation failed: {str(e)}")
                    # Continue processing other rows
                    validated_data.append({
                        **row,
                        '_validation_error': str(e),
                        '_row_number': idx
                    })
            
            return validated_data

        except pd.errors.EmptyDataError:
            raise DocumentError("CSV file is empty", status.HTTP_400_BAD_REQUEST)
        except pd.errors.ParserError as e:
            raise DocumentError(f"CSV parsing error: {str(e)}", status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            raise DocumentError(f"CSV processing failed: {str(e)}", status.HTTP_500_INTERNAL_SERVER_ERROR)

    async def _validate_csv_row(self, row: Dict[str, Any], row_number: int) -> Dict[str, Any]:
        """Validate individual CSV row."""
        # Clean and validate filename
        filename = str(row.get('filename', '')).strip()
        if not filename:
            raise ValueError(f"Row {row_number}: filename is required")
        
        # Validate upload type
        upload_type_value = (
            row.get('upload_type')
            or row.get('uploadtype')
            or row.get('uploadType')
        )
        upload_type = str(upload_type_value or '').strip().lower()
        if upload_type not in ['incoming', 'outgoing']:
            raise ValueError(f"Row {row_number}: upload_type must be 'incoming' or 'outgoing'")
        
        # Validate letter number
        letter_no = str(row.get('letter_no') or row.get('letterNo') or '').strip()
        if not letter_no:
            raise ValueError(f"Row {row_number}: letter_no is required")
        
        # Validate and parse date
        date_str = str(row.get('date', '')).strip()
        if not date_str:
            raise ValueError(f"Row {row_number}: date is required")
        
        parsed_date = parse_date_safely(date_str)
        if not parsed_date:
            raise ValueError(f"Row {row_number}: invalid date format '{date_str}'")
        
        # Subject is optional for bulk upload; default to empty string
        subject = str(row.get('subject', '') or '').strip()

        # Optional fields with defaults
        from_company = str(row.get('from_') or row.get('from') or '').strip() or None
        to_company = str(row.get('to', '')).strip() or None
        tags = self._parse_list_field(row.get('tags', ''))
        sub_tags = self._parse_list_field(row.get('sub_tags') or row.get('subTags') or '')
        status = str(row.get('status', 'draft')).strip()
        ocr_enabled = self._parse_boolean_field(row.get('ocr_enabled') or row.get('ocrEnabled') or 'true')
        compression_enabled = self._parse_boolean_field(
            row.get('compression_enabled') or row.get('compressionEnabled') or 'false'
        )
        path_structure = str(row.get('path_structure') or row.get('pathStructure') or '').strip() or None
        path_structure1 = str(row.get('path_structure1') or row.get('pathStructure1') or '').strip() or None

        return {
            'filename': filename,
            'upload_type': upload_type,
            'letter_no': letter_no,
            'date': parsed_date,
            'subject': subject,
            'from_': from_company,
            'to': to_company,
            'tags': tags,
            'sub_tags': sub_tags,
            'status': status,
            'ocr_enabled': ocr_enabled,
            'compression_enabled': compression_enabled,
            'path_structure': path_structure,
            'path_structure1': path_structure1,
            '_row_number': row_number
        }

    def _parse_list_field(self, value: Any) -> List[str]:
        """Parse comma-separated string into list."""
        if not value or pd.isna(value):
            return []
        
        if isinstance(value, str):
            return [item.strip() for item in value.split(',') if item.strip()]
        
        return []

    def _parse_boolean_field(self, value: Any) -> bool:
        """Parse boolean field from various formats."""
        if pd.isna(value):
            return False
        
        if isinstance(value, bool):
            return value
        
        if isinstance(value, str):
            return value.lower() in ['true', '1', 't', 'y', 'yes', 'on']
        
        return bool(value)

    async def _emit_bulk_upload_notification(
        self,
        *,
        job_id: str,
        organization_id: str,
        project_id: str,
        successful_uploads: int,
        failed_uploads: int,
        current_user: CurrentUser,
    ) -> None:
        notification_service = getattr(self.document_service, "notification_service", None)
        if not notification_service or successful_uploads <= 0 or not project_id:
            return

        message = (
            f"Bulk upload completed: {successful_uploads} document"
            f"{'' if successful_uploads == 1 else 's'} uploaded"
        )
        if failed_uploads:
            message = f"{message}, {failed_uploads} failed"

        await notification_service.emit(
            NotificationType.BULK_UPLOAD_COMPLETED,
            project_id,
            "project",
            context=NotificationContext.PROJECT,
            actor_id=current_user.id,
            data={
                "title": "Bulk upload completed",
                "message": message,
                "job_id": job_id,
                "successful_count": successful_uploads,
                "failed_count": failed_uploads,
                "organization_id": organization_id,
                "project_id": project_id,
            },
            actions=[
                {
                    "key": "view_documents",
                    "label": "View Documents",
                    "method": "navigate",
                    "href": "/documents",
                }
            ],
            resource_link="/documents",
            dedupe_key=f"bulk_upload_completed:{job_id}",
        )

    async def _process_bulk_upload(
        self,
        job_id: str,
        csv_data: List[Dict[str, Any]],
        stored_files: List[Dict[str, Any]],
        organization_id: str,
        project_id: str,
        current_user: CurrentUser
    ):
        """Process bulk upload in background."""
        try:
            temp_uploads: List[UploadFile] = []
            for stored in stored_files:
                try:
                    file_handle = open(stored["temp_path"], "rb")
                    temp_uploads.append(
                        UploadFile(
                            file=file_handle,
                            filename=stored["filename"],
                        )
                    )
                except Exception as exc:
                    logger.error(f"Failed to open temp file for {stored.get('filename')}: {exc}")

            # Create filename to file mapping
            file_mapping = self._build_bulk_file_lookup(temp_uploads)
            
            results = []
            successful_uploads = 0
            failed_uploads = 0

            # Process each CSV row
            for row_data in csv_data:
                try:
                    result = await self._process_single_file(
                        row_data, file_mapping, organization_id, project_id, current_user
                    )
                    
                    if result.success:
                        successful_uploads += 1
                    else:
                        failed_uploads += 1
                    
                    results.append(result)
                    
                    # Update progress
                    await self.bulk_upload_service.update_progress(
                        job_id, len(results), successful_uploads, failed_uploads, results
                    )

                except Exception as e:
                    logger.error(f"Failed to process file {row_data.get('filename')}: {str(e)}")
                    
                    failed_result = DocumentProcessingResult(
                        filename=row_data.get('filename', 'unknown'),
                        success=False,
                        error=str(e),
                        row_number=row_data.get('_row_number', 0)
                    )
                    
                    results.append(failed_result)
                    failed_uploads += 1
                    
                    # Update progress
                    await self.bulk_upload_service.update_progress(
                        job_id, len(results), successful_uploads, failed_uploads, results
                    )

            # Mark job as completed
            final_status = "completed" if failed_uploads == 0 else "completed_with_errors"
            await self.bulk_upload_service.complete_job(
                job_id, final_status, successful_uploads, failed_uploads, results
            )

            if successful_uploads > 0:
                try:
                    await self._emit_bulk_upload_notification(
                        job_id=job_id,
                        organization_id=organization_id,
                        project_id=project_id,
                        successful_uploads=successful_uploads,
                        failed_uploads=failed_uploads,
                        current_user=current_user,
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to emit bulk upload notification for %s: %s",
                        job_id,
                        exc,
                    )

            logger.info(f"Bulk upload {job_id} completed: {successful_uploads} success, {failed_uploads} failed")

        except Exception as e:
            logger.error(f"Bulk upload {job_id} failed: {str(e)}")
            
            # Mark job as failed
            await self.bulk_upload_service.fail_job(job_id, str(e))
        finally:
            # Close file handles and delete temp files
            for upload in temp_uploads:
                try:
                    await upload.close()
                except Exception:
                    pass

            for stored in stored_files:
                temp_path = stored.get("temp_path")
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

    async def _process_single_file(
        self,
        row_data: Dict[str, Any],
        file_mapping: Dict[str, UploadFile],
        organization_id: str,
        project_id: str,
        current_user: CurrentUser
    ) -> DocumentProcessingResult:
        """Process a single file from bulk upload."""
        filename = row_data['filename']
        row_number = row_data.get('_row_number', 0)
        
        try:
            # Check for validation errors
            if '_validation_error' in row_data:
                return DocumentProcessingResult(
                    filename=filename,
                    success=False,
                    error=row_data['_validation_error'],
                    row_number=row_number
                )
            
            # Find corresponding file
            lookup_key = self._normalize_bulk_filename(filename)
            file = (
                file_mapping.get(filename)
                or file_mapping.get(filename.lower())
                or file_mapping.get(lookup_key)
                or file_mapping.get(lookup_key.lower())
            )
            if not file:
                return DocumentProcessingResult(
                    filename=filename,
                    success=False,
                    error=f"File '{filename}' not found in uploaded files",
                    row_number=row_number
                )

            await file.seek(0)

            # Create document
            document = await self.create_document(
                background_tasks=None,
                file=file,
                organization_id=organization_id,
                project_id=project_id,
                upload_type=row_data['upload_type'],
                letter_no=row_data['letter_no'],
                date_str=row_data['date'].isoformat() if isinstance(row_data['date'], datetime) else str(row_data['date']),
                current_user=current_user,
                subject=row_data.get('subject', ''),
                from_=row_data.get('from_'),
                to=row_data.get('to'),
                tags=row_data.get('tags', []),
                sub_tags=row_data.get('sub_tags', []),
                status=row_data.get('status', 'draft'),
                ocr_enabled=row_data.get('ocr_enabled', False),
                compression_enabled=row_data.get('compression_enabled', False),
                pathStructure=row_data.get('path_structure'),
                pathStructure1=row_data.get('path_structure1'),
                emit_upload_notification=False,
            )
            
            return DocumentProcessingResult(
                filename=filename,
                success=True,
                document_id=document.id,
                row_number=row_number
            )

        except Exception as e:
            logger.error(f"Failed to process file {filename}: {str(e)}")
            return DocumentProcessingResult(
                filename=filename,
                success=False,
                error=str(e),
                row_number=row_number
            )

    async def process_document(
        self,
        document_id: str,
        current_user: Optional[CurrentUser],
        skip_authorization: bool = False,
    ) -> DocumentProcessingResult:
        """Trigger document reprocessing through the LangChain pipeline."""
        try:
            document = await self.document_service.get_document(document_id)
            if not document:
                raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

            if not skip_authorization:
                if current_user is None:
                    raise DocumentError("Unauthorized", status.HTTP_401_UNAUTHORIZED)
                await self.policy_service.authorize_document(
                    current_user, Permissions.DOCUMENT_EDIT_METADATA, document
                )

            file_path = await self._materialize_for_processing(document)

            await self.document_service.process_document_async(
                document_id=document_id,
                file_path=file_path,
                organization_id=document.organization_id,
                project_id=document.project_id,
                upload_type=document.uploadType,
            )

            return DocumentProcessingResult(
                filename=getattr(document, "filename", ""),
                document_id=document_id,
                success=True,
                row_number=0,
            )
        except (DocumentError, HTTPException):
            raise
        except Exception as exc:
            logger.error("Document reprocessing failed for %s: %s", document_id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Document processing service temporarily unavailable",
            )

    async def get_bulk_upload_status(
        self,
        job_id: str,
        current_user: CurrentUser
    ) -> BulkUploadStatus:
        """Get bulk upload job status."""
        try:
            # BUGFIX (H2): a local variable named `status` previously shadowed the
            # imported FastAPI `status` module, so the not-found branch raised an
            # AttributeError that surfaced as a 500 instead of a clean 404.
            job_status = await self.bulk_upload_service.get_job_status(job_id)

            if not job_status:
                raise DocumentError("Bulk upload job not found", status.HTTP_404_NOT_FOUND)

            # Verify user has access (basic security check)
            # In production, you might want more sophisticated access control

            return job_status

        except (DocumentError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Failed to get bulk upload status: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Bulk upload status service temporarily unavailable"
            )

    # ... (keep all existing methods from the original file)
    
async def controller_get_document(
    self, document_id: str, current_user: CurrentUser
) -> Document:
    """Get document with proper authorization and enrichment."""
    try:
        document = await self.document_service.get_document_by_id(document_id)
        if not document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.policy_service.authorize_document(
            current_user, Permissions.DOCUMENT_VIEW, document
        )

        return await self.document_service.enrich_document(document)

    except (DocumentError, HTTPException):
        raise
    except Exception as e:
        logger.error(f"Document retrieval failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document service temporarily unavailable",
        )

    
async def controller_list_documents(
    self,
    filters: Dict[str, Any],
    pagination: Dict[str, int],
    current_user: CurrentUser
) -> DocumentListResponse:
    """List documents with proper filtering and authorization."""
    try:
        authorized_query = await self.auth_service.build_document_query(
            current_user, filters
        )

        documents, total_count = await self.document_service.list_documents(
            authorized_query, pagination
        )

        enriched_documents: List[Document] = []
        for doc in documents:
            if not doc:
                continue
            try:
                enriched_documents.append(
                    await self.document_service.enrich_document(doc)
                )
            except Exception as enrich_err:
                logger.warning(
                    "Failed to enrich document %s: %s",
                    getattr(doc, 'id', 'unknown'),
                    enrich_err,
                )

        limit = pagination.get("limit", 0) or len(enriched_documents) or 1
        skip = pagination.get("skip", 0) or 0
        page = skip // limit + 1
        has_next = (skip + limit) < total_count
        has_previous = skip > 0

        return DocumentListResponse(
            documents=enriched_documents,
            total=total_count,
            page=page,
            size=limit,
            has_next=has_next,
            has_previous=has_previous,
        )

    except (DocumentError, HTTPException):
        raise
    except Exception as e:
        logger.error(f"Document listing failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document listing service temporarily unavailable",
        )

    
async def controller_update_document(
    self,
    document_id: str,
    update_data: DocumentUpdate,
    current_user: CurrentUser,
    expected_revision: Optional[int] = None,
) -> Document:
    """Update document with validation and authorization."""
    try:
        document = await self.document_service.get_document_by_id(document_id)
        if not document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.policy_service.authorize_document(
            current_user, Permissions.DOCUMENT_EDIT_METADATA, document
        )

        validated_update = await self.document_service.validate_update(
            update_data, current_user
        )
        update_payload = validated_update.model_dump(
            exclude_unset=True, exclude_none=True
        )
        updated_document_model = document.model_copy(update=update_payload)

        updated_document = await self.document_service.update_document(
            document_id,
            updated_document_model,
            expected_revision=expected_revision,
        )
        if not updated_document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.audit_service.emit(
            resource_type="document",
            resource_id=document_id,
            event_type="document.updated",
            actor_id=getattr(current_user, "id", None),
            organization_id=document.organization_id,
            project_id=document.project_id,
            metadata={
                "updated_fields": sorted(update_payload.keys()),
                "expected_revision": expected_revision,
                "current_revision": await self.document_service.get_document_revision(document_id),
            },
        )

        return await self.document_service.enrich_document(updated_document)

    except (DocumentError, HTTPException):
        raise
    except DocumentConflictError as exc:
        raise DocumentError(
            "Document was modified by another user",
            status.HTTP_409_CONFLICT,
            error="ConflictError",
            code="document_conflict",
            details={"current_revision": exc.current_revision},
        ) from exc
    except Exception as e:
        logger.error(f"Document update failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document update service temporarily unavailable",
        )

    
async def controller_delete_document(
    self,
    document_id: str,
    current_user: CurrentUser,
    expected_revision: Optional[int] = None,
):
    """Delete document with proper authorization and cleanup."""
    try:
        document = await self.document_service.get_document_by_id(document_id)
        if not document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.policy_service.authorize_document(
            current_user, Permissions.DOCUMENT_DELETE, document
        )

        deleted = await self.document_service.delete_document(
            document_id,
            expected_revision=expected_revision,
        )
        if not deleted:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)
        await self.audit_service.emit(
            resource_type="document",
            resource_id=document_id,
            event_type="document.deleted",
            actor_id=getattr(current_user, "id", None),
            organization_id=document.organization_id,
            project_id=document.project_id,
            metadata={
                "soft_delete": True,
                "expected_revision": expected_revision,
                "current_revision": await self.document_service.get_document_revision(document_id),
            },
        )

    except (DocumentError, HTTPException):
        raise
    except DocumentConflictError as exc:
        raise DocumentError(
            "Document was modified by another user",
            status.HTTP_409_CONFLICT,
            error="ConflictError",
            code="document_conflict",
            details={"current_revision": exc.current_revision},
        ) from exc
    except Exception as e:
        logger.error(f"Document deletion failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document deletion service temporarily unavailable",
        )

    
async def controller_add_enclosure(
    self,
    document_id: str,
    file: UploadFile,
    current_user: CurrentUser
) -> EnclosureResponse:
    """Add enclosure with security validation."""
    try:
        document = await self.document_service.get_document_by_id(document_id)
        if not document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.policy_service.authorize_document(
            current_user, Permissions.DOCUMENT_EDIT_METADATA, document
        )

        if not file.filename:
            raise DocumentError("No file provided", status.HTTP_400_BAD_REQUEST)

        max_size = max(1, int(settings.GENERAL_UPLOAD_MAX_FILE_SIZE_MB)) * 1024 * 1024
        async with upload_concurrency_limiter.slot(
            f"user:{current_user.id}",
            int(settings.UPLOAD_MAX_CONCURRENT_PER_USER),
        ), upload_concurrency_limiter.slot(
            f"org:{document.organization_id}",
            int(settings.UPLOAD_MAX_CONCURRENT_PER_ORG),
        ):
            spooled = await spool_upload_file(file, max_size_bytes=max_size)
            try:
                validation_result = validate_spooled_upload(
                    spooled, settings.ALLOWED_ENCLOSURE_MIMES
                )
                if not validation_result.is_valid:
                    raise DocumentError(
                        f"Invalid enclosure: {validation_result.error}",
                        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    )

                # --- ANTIVIRUS STREAM SCAN ---
                if settings.ANTIVIRUS_ENABLED:
                    antivirus = AntivirusService()
                    is_clean, scan_detail = await antivirus.scan_file(spooled.path)
                    if not is_clean:
                        raise DocumentError(
                            f"Antivirus scan rejected this enclosure: {scan_detail}",
                            status.HTTP_400_BAD_REQUEST
                        )
                # ------------------------------

                context = await self.file_object_service.resolve_storage_context(
                    document.organization_id,
                    document.project_id,
                )
                upload_id = str(ObjectId())
                storage_key = self.storage_key_builder.build_enclosure_key(
                    organization=context["organization"],
                    project=context["project"],
                    parent_document_id=document_id,
                    original_filename=spooled.filename,
                    upload_id=upload_id,
                )
                store_result = await self.file_object_service.store_path(
                    source_path=spooled.path,
                    size=spooled.size,
                    sha256=spooled.sha256,
                    mime_type=spooled.mime_type,
                    organization_id=document.organization_id,
                    project_id=document.project_id,
                    original_filename=spooled.filename,
                    storage_key=storage_key,
                    current_user=current_user,
                    document_type="enclosure",
                    upload_id=upload_id,
                    providers=context["providers"],
                    document_id=document_id,
                )
            finally:
                await spooled.cleanup()
        await self.audit_service.emit(
            resource_type="document",
            resource_id=document_id,
            event_type="document.enclosure_added",
            actor_id=getattr(current_user, "id", None),
            organization_id=document.organization_id,
            project_id=document.project_id,
            metadata={
                "filename": spooled.filename,
                "file_object_id": store_result.get("file_object_id"),
                "storage_key": store_result.get("storage_key"),
                "upload_streamed": True,
            },
        )

        return await self.document_service.add_enclosure(
            document_id,
            spooled.sample,
            spooled.filename,
            current_user,
            filepath_local=store_result.get("filepath_local"),
            filepath_s3=store_result.get("filepath_s3"),
            storage_key=store_result.get("storage_key"),
            file_object_id=store_result.get("file_object_id"),
            storage_locations=store_result.get("storage_locations"),
            filetype=spooled.mime_type,
            filesize=spooled.size,
        )

    except (DocumentError, HTTPException):
        raise
    except Exception as e:
        logger.error(f"Enclosure addition failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Enclosure service temporarily unavailable",
        )


# Additional enclosure helpers
async def controller_list_enclosures(
    self,
    document_id: str,
    current_user: CurrentUser,
) -> List[Enclosure]:
    try:
        document = await self.document_service.get_document_by_id(document_id)
        if not document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.policy_service.authorize_document(
            current_user, Permissions.DOCUMENT_VIEW, document
        )

        return await self.document_service.list_enclosures(document_id)
    except (DocumentError, HTTPException):
        raise
    except Exception as e:
        logger.error("Failed to load enclosures for %s: %s", document_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load enclosures",
        )


async def controller_remove_enclosure(
    self,
    document_id: str,
    enclosure_id: str,
    current_user: CurrentUser,
) -> None:
    try:
        document = await self.document_service.get_document_by_id(document_id)
        if not document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.policy_service.authorize_document(
            current_user, Permissions.DOCUMENT_EDIT_METADATA, document
        )

        await self.document_service.remove_enclosure(document_id, enclosure_id)
    except (DocumentError, HTTPException):
        raise
    except Exception as e:
        logger.error(
            "Failed to remove enclosure %s from %s: %s",
            enclosure_id,
            document_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to remove enclosure",
        )


async def controller_list_references(
    self,
    document_id: str,
    current_user: CurrentUser,
) -> Dict[str, Any]:
    try:
        document = await self.document_service.get_document_by_id(document_id)
        if not document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.policy_service.authorize_document(
            current_user, Permissions.DOCUMENT_VIEW, document
        )

        return await self.document_service.list_references(document_id)
    except (DocumentError, HTTPException):
        raise
    except Exception as e:
        logger.error("Failed to list references for %s: %s", document_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load references",
        )


async def controller_add_reference(
    self,
    document_id: str,
    reference_data: ReferenceCreate,
    current_user: CurrentUser,
) -> Document:
    try:
        document = await self.document_service.get_document_by_id(document_id)
        if not document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.policy_service.authorize_document(
            current_user, Permissions.DOCUMENT_LINK_REFERENCE, document
        )

        target = await self.document_service.get_document_by_id(
            reference_data.referenced_document_id
        )
        if not target:
            raise DocumentError("Referenced document not found", status.HTTP_404_NOT_FOUND)

        await self.policy_service.authorize_document(
            current_user, Permissions.DOCUMENT_VIEW, target
        )

        return await self.document_service.add_reference(
            document_id,
            reference_data,
            current_user=current_user,
        )
    except (DocumentError, HTTPException):
        raise
    except Exception as e:
        logger.error("Failed to add reference for %s: %s", document_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add reference",
        )


async def controller_remove_reference(
    self,
    document_id: str,
    reference_id: str,
    current_user: CurrentUser,
) -> Document:
    try:
        document = await self.document_service.get_document_by_id(document_id)
        if not document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.policy_service.authorize_document(
            current_user, Permissions.DOCUMENT_LINK_REFERENCE, document
        )

        return await self.document_service.remove_reference(document_id, reference_id)
    except (DocumentError, HTTPException):
        raise
    except Exception as e:
        logger.error("Failed to remove reference %s from %s: %s", reference_id, document_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to remove reference",
        )


async def controller_sync_references(
    self,
    document_id: str,
    current_user: CurrentUser,
) -> Dict[str, Any]:
    try:
        document = await self.document_service.get_document_by_id(document_id)
        if not document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.policy_service.authorize_document(
            current_user, Permissions.DOCUMENT_LINK_REFERENCE, document
        )

        references_payload: List[Dict[str, Any]] = []

        for item in document.reference or []:
            if hasattr(item, "model_dump"):
                try:
                    references_payload.append(item.model_dump(by_alias=True, exclude_none=True))
                except Exception:
                    continue
            elif isinstance(item, dict):
                references_payload.append(
                    {k: v for k, v in item.items() if v not in (None, "", [], {})}
                )

        sync_result = await self.document_service.reference_sync_service.sync_bidirectional(
            document_id=document_id,
            references=references_payload,
            source="parser",
            clear_existing=True,
        )

        # Reload inside the service so Falkor receives the relationships that
        # were just reconciled, not the stale pre-sync document snapshot.
        await self.document_service.sync_reference_graph(document_id)

        return {
            "message": (
                "Reference synchronisation completed"
                if references_payload
                else "No extracted references found; automatic links were cleared"
            ),
            "sync": sync_result,
        }
    except (DocumentError, HTTPException):
        raise
    except ReferenceSyncError as exc:
        logger.error("Reference sync error for %s: %s", document_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except DocumentServiceError as exc:
        logger.error("Reference graph sync error for %s: %s", document_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Unexpected reference sync failure for %s: %s", document_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to synchronise references",
        )


async def controller_link_documents(
    self,
    payload: LinkDocumentsRequest,
    current_user: CurrentUser,
) -> Dict[str, Any]:
    try:
        source = await self.document_service.get_document_by_id(
            payload.source_document_id
        )
        target = await self.document_service.get_document_by_id(
            payload.target_document_id
        )

        if not source or not target:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.policy_service.authorize_document(
            current_user, Permissions.DOCUMENT_LINK_REFERENCE, source
        )
        await self.policy_service.authorize_document(
            current_user, Permissions.DOCUMENT_VIEW, target
        )

        return await self.document_service.link_documents(
            payload.source_document_id,
            payload.target_document_id,
            payload.link_type,
            description=payload.description,
            current_user=current_user,
        )
    except (DocumentError, HTTPException):
        raise
    except Exception as e:
        logger.error(
            "Failed to link documents %s -> %s: %s",
            payload.source_document_id,
            payload.target_document_id,
            e,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to link documents",
        )


async def controller_list_linked_documents(
    self,
    document_id: str,
    current_user: CurrentUser,
) -> List[Dict[str, Any]]:
    try:
        document = await self.document_service.get_document_by_id(document_id)
        if not document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.policy_service.authorize_document(
            current_user, Permissions.DOCUMENT_VIEW, document
        )

        return await self.document_service.list_linked_documents(document_id)
    except (DocumentError, HTTPException):
        raise
    except Exception as e:
        logger.error("Failed to list linked documents for %s: %s", document_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load linked documents",
        )


# Dependency injection
async def get_document_controller() -> DocumentController:
    """Factory function for document controller."""
    notification_service = await get_notification_service()
    document_service = DocumentService(notification_service=notification_service)
    file_service = SecureFileService(base_dir=settings.SECURE_UPLOADS_DIR)
    export_service = ExportService()
    auth_service = AuthorizationService()
    bulk_upload_service = BulkUploadService()
    
    return DocumentController(
        document_service, file_service, export_service, auth_service, bulk_upload_service
    )



# API Endpoints

@router.get("/documents/vector-search")
@handle_exceptions
async def vector_search_documents_endpoint(
    query: str = Query(..., min_length=2, max_length=1024),
    limit: int = Query(5, ge=1, le=50),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    uploadType: Optional[str] = Query(None),
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    filters = {
        "organization_id": organization_id,
        "project_id": project_id,
        "uploadType": uploadType,
    }
    return await controller.vector_search_documents(
        query=query,
        limit=limit,
        filters=filters,
        current_user=current_user,
    )

@router.get("/documents/export")
@handle_exceptions
async def export_documents(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),
    subTags: Optional[List[str]] = Query(None),
    uploadType: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Export documents with filtering. Returns a downloadable file.
    Note: Uses enrichment so Tag/Sub-Tag names and project_name are included.
    """
    from fastapi.responses import StreamingResponse

    # Build filters consistent with list endpoint
    filters: Dict[str, Any] = {
        "organization_id": organization_id,
        "project_id": project_id,
        "tags": tags,
        "subTags": subTags,
        "uploadType": uploadType,
        "status": status,
        "search": search,
        "date_from": date_from,
        "date_to": date_to,
    }

    scope_org, scope_project = _current_user_policy_scope(current_user)
    await PolicyService().authorize(
        current_user,
        Permissions.DOCUMENT_DOWNLOAD,
        resource_type="documents_export",
        organization_id=organization_id or scope_org,
        project_id=project_id or scope_project,
    )

    # Authorization-aware query
    authorized_query = await controller.auth_service.build_document_query(
        current_user, filters
    )

    max_records = int(getattr(settings, "DOCUMENT_EXPORT_MAX_RECORDS", 100000) or 100000)
    batch_size = int(getattr(settings, "DOCUMENT_EXPORT_BATCH_SIZE", 1000) or 1000)
    max_service_page_limit = 1000  # validate_pagination default cap
    batch_size = max(1, min(batch_size, max_records, max_service_page_limit))

    documents: List[Document] = []
    skip = 0

    while len(documents) < max_records:
        limit = min(batch_size, max_records - len(documents))
        batch, total_count = await controller.document_service.list_documents(
            authorized_query,
            {"skip": skip, "limit": limit},
        )

        if not batch:
            break

        documents.extend(batch)
        skip += len(batch)

        if len(batch) < limit:
            break

        if total_count is not None and skip >= total_count:
            break

    # Enrich and convert to plain dicts compatible with the export service
    enriched_dicts: List[Dict[str, Any]] = []
    for doc in documents:
        try:
            enriched = await controller.document_service.enrich_document(doc)
            enriched_dicts.append(enriched.model_dump(by_alias=True))
        except Exception:
            # Fallback to raw doc if enrichment fails
            try:
                enriched_dicts.append(doc.model_dump(by_alias=True))
            except Exception:
                continue

    db = await get_database()

    def _safe_object_id(value: Any) -> Optional[ObjectId]:
        try:
            return ObjectId(str(value))
        except Exception:
            return None

    def _pick_display_name(record: Dict[str, Any], candidates: tuple[str, ...]) -> str:
        for key in candidates:
            if key in record and record[key] not in (None, ""):
                return str(record[key])
        return str(record.get("_id") or "")

    async def _build_lookup(
        collection: str, ids: set[str], name_fields: tuple[str, ...]
    ) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        if not ids:
            return mapping

        col = getattr(db, collection)
        object_ids: List[ObjectId] = []
        string_ids: List[str] = []
        for raw in ids:
            if raw in (None, ""):
                continue
            oid = _safe_object_id(raw)
            if oid:
                object_ids.append(oid)
            else:
                string_ids.append(str(raw))

        if object_ids:
            async for record in col.find({"_id": {"$in": object_ids}}):
                mapping[str(record.get("_id"))] = _pick_display_name(record, name_fields)

        if string_ids:
            async for record in col.find({"_id": {"$in": string_ids}}):
                mapping[str(record.get("_id"))] = _pick_display_name(record, name_fields)

        return mapping

    def _format_date(value: Any, include_time: bool = False) -> str:
        if value is None:
            return ""

        dt_value: Optional[datetime] = None
        if isinstance(value, datetime):
            dt_value = value
        elif isinstance(value, date):
            dt_value = datetime.combine(value, datetime.min.time())
        elif isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return ""
            try:
                dt_value = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
            except Exception:
                try:
                    dt_value = datetime.strptime(cleaned, "%Y-%m-%d")
                except Exception:
                    return cleaned

        if dt_value is None:
            return str(value)

        return dt_value.strftime("%d-%m-%Y %H:%M" if include_time else "%d-%m-%Y")

    def _resolve_display(raw_value: Any, lookup: Dict[str, str]) -> str:
        if raw_value in (None, ""):
            return ""
        key = str(raw_value)
        return lookup.get(key, key)

    organization_ids: set[str] = set()
    project_ids: set[str] = set()
    user_ids: set[str] = set()
    party_ids: set[str] = set()

    for doc in enriched_dicts:
        org_id_val = doc.get("organization_id") or doc.get("organizationId")
        if org_id_val:
            organization_ids.add(str(org_id_val))

        project_id_val = doc.get("project_id") or doc.get("projectId")
        if project_id_val:
            project_ids.add(str(project_id_val))

        creator_val = doc.get("createdBy") or doc.get("created_by")
        if creator_val:
            user_ids.add(str(creator_val))

        for key in ("from", "from_", "to"):
            candidate = doc.get(key)
            if candidate:
                party_ids.add(str(candidate))

    org_lookup = await _build_lookup("organizations", organization_ids, ("name", "title"))
    project_lookup = await _build_lookup("projects", project_ids, ("name", "title"))
    user_lookup = await _build_lookup(
        "users",
        user_ids,
        ("full_name", "fullName", "name", "username", "email"),
    )
    party_lookup = await _build_lookup("parties", party_ids, ("name", "title"))

    prepared_documents: List[Dict[str, Any]] = []
    for doc in enriched_dicts:
        org_id = str(doc.get("organization_id") or doc.get("organizationId") or "")
        project_id = str(doc.get("project_id") or doc.get("projectId") or "")
        created_by = doc.get("createdBy") or doc.get("created_by")
        upload_date_source = (
            doc.get("createdAt")
            or doc.get("created_at")
            or doc.get("uploadedAt")
            or doc.get("uploaded_at")
            or doc.get("updatedAt")
            or doc.get("updated_at")
            or doc.get("date")
        )

        tags_value = doc.get("tags") or []
        if not isinstance(tags_value, list):
            tags_value = [tags_value]
        tags_display = [str(t) for t in tags_value if t not in (None, "")]

        subtags_value = doc.get("subTags") or []
        if not isinstance(subtags_value, list):
            subtags_value = [subtags_value]
        subtags_display = [str(t) for t in subtags_value if t not in (None, "")]

        direction_value = (
            "Incoming" if str(doc.get("uploadType", "")).lower() == "incoming" else "Outgoing"
        )
        from_display = _resolve_display(doc.get("from") or doc.get("from_"), party_lookup)
        to_display = _resolve_display(doc.get("to"), party_lookup)
        if not from_display and direction_value == "Outgoing" and org_id:
            from_display = org_lookup.get(org_id, "")

        prepared_documents.append(
            {
                **doc,
                "date_display": _format_date(doc.get("date")),
                "direction": direction_value,
                "from_display": from_display or "N/A",
                "to_display": to_display,
                "tag_display": tags_display,
                "subTag_display": subtags_display,
                "organization_name": org_lookup.get(org_id, org_id),
                "project_name": project_lookup.get(
                    project_id, doc.get("project_name") or doc.get("projectName") or project_id
                ),
                "upload_date_display": _format_date(upload_date_source, include_time=True),
                "uploader_name": user_lookup.get(str(created_by), str(created_by or "")),
            }
        )

    filename = "documents_export.xlsx"
    # Generate Excel file bytes via export service
    data_bytes = await controller.export_service.export_documents_to_xlsx(
        prepared_documents,
        filename=filename,
    )
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    # Use the standard Excel MIME type for .xlsx responses
    return StreamingResponse(
        iter([data_bytes]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )

@router.post("/documents", response_model=Document)
@handle_exceptions
async def create_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    organization_id: str = Form(...),
    project_id: str = Form(...),
    pathStructure: Optional[str] = Form(None),
    pathStructure1: Optional[str] = Form(None),
    uploadType: str = Form(...),
    letterNo: str = Form(...),
    date: str = Form(...),
    subject: Optional[str] = Form(""),
    from_: Optional[str] = Form(None, alias="from"),
    to: Optional[str] = Form(None),
    tags: Optional[List[str]] = Form(None),
    subTags: Optional[List[str]] = Form(None),
    doc_status: Optional[str] = Form("draft"),
    ocrEnabled: str = Form("1"),
    compressionEnabled: str = Form("0"),

    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Create a new document."""
    await policy.authorize(
        current_user,
        "dms.document.upload",
        resource_type="project",
        resource_id=project_id,
        organization_id=organization_id,
        project_id=project_id,
    )
    return await controller.create_document(
        background_tasks=background_tasks,
        file=file,
        organization_id=organization_id,
        project_id=project_id,
        pathStructure=pathStructure,
        pathStructure1=pathStructure1,
        upload_type=uploadType,
        letter_no=letterNo,
        date_str=date,
        current_user=current_user,
        subject=subject,
        from_=from_,
        to=to,
        tags=tags,
        sub_tags=subTags,
        status=doc_status,
        ocr_enabled=ocrEnabled.lower() in ("true", "1", "t", "y", "yes"),
        compression_enabled=compressionEnabled.lower() in ("true", "1", "t", "y", "yes"),
    )


@router.get("/documents/{id}", response_model=Document)
@handle_exceptions
async def get_document(
    id: str,
    response: Response,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get a specific document."""
    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_VIEW)
    document = await controller_get_document(controller, id, current_user)
    await PolicyService().authorize_document(current_user, Permissions.DOCUMENT_VIEW, document)
    await _set_document_revision_headers(response, controller, id)
    return document


@router.post("/documents/{id}/process", response_model=DocumentProcessingResult)
@handle_exceptions
async def process_document_endpoint(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
) -> DocumentProcessingResult:
    """Trigger OCR/AI processing for an existing document."""
    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_EDIT_METADATA)
    return await controller.process_document(id, current_user)


@router.post("/internal/documents/{id}/process", response_model=DocumentProcessingResult)
@handle_exceptions
async def process_document_internal(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    _: None = Depends(verify_langgraph_token),
) -> DocumentProcessingResult:
    """Internal endpoint for orchestrators to process documents without user context."""
    return await controller.process_document(id, None, skip_authorization=True)


@router.get("/documents/download-all")
@handle_exceptions
async def download_all_project_documents(
    project_id: str = Query(..., min_length=1),
    type: str = Query("complete", pattern="^(letters|contracts|complete)$"),
    upload_type: Optional[str] = Query(None, pattern="^(incoming|outgoing|contract)$"),
    year: Optional[int] = Query(None, ge=1900, le=3000),
    month: Optional[int] = Query(None, ge=1, le=12),
    document_id: Optional[str] = Query(None, min_length=1),
    service: DocumentBulkDownloadService = Depends(get_document_bulk_download_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Download all project letters/documents, contract documents, or both as a zip.

    Authorization is enforced in the service:
    - superadmin: any project
    - orgadmin: projects in their organization
    - projectadmin: assigned projects
    - other users: assigned project plus dms.document.bulk_download
    """
    result = await service.create_project_archive(
        project_id=project_id,
        download_type=type,  # type: ignore[arg-type]
        current_user=current_user,
        upload_type=upload_type,
        year=year,
        month=month,
        document_id=document_id,
    )
    headers = {
        "X-Document-Count": str(result.file_count),
        "X-Archive-Size": str(result.total_size),
        "Access-Control-Expose-Headers": "Content-Disposition, X-Document-Count, X-Archive-Size",
    }
    return FileResponse(
        path=str(result.zip_path),
        media_type="application/zip",
        filename=result.filename,
        headers=headers,
        background=BackgroundTask(service.cleanup_paths, result.cleanup_paths),
    )


@router.get("/documents/{id}/download")
@handle_exceptions
async def download_document(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """
    Securely download/stream the original document bytes.

    - Authorizes the current user against the document's org/project
    - Prefers local file path (filepath_local) if available
    - Falls back to redirect to presigned_url if provided
    """
    # Load document
    document = await controller.document_service.get_document_by_id(id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    await policy.authorize_document(current_user, "dms.document.download", document)
    await _ensure_document_access(current_user, id, "dms.document.download")

    # Prefer local file if present
    try:
        path = getattr(document, "filepath_local", None)
        if path:
            p = Path(path)
            if p.exists() and p.is_file():
                controller.file_object_service.assert_local_path_allowed(str(p))
                await controller.audit_service.emit(
                    resource_type="document",
                    resource_id=id,
                    event_type="document.downloaded",
                    actor_id=getattr(current_user, "id", None),
                    organization_id=document.organization_id,
                    project_id=document.project_id,
                    metadata={"provider": "local"},
                )
                media = document.filetype or "application/octet-stream"
                return FileResponse(
                    path=str(p),
                    media_type=media,
                    filename=document.filename,
                )
    except Exception:
        # continue to fallback
        pass

    # Fallback to S3 presigned URL if key present
    s3_key = getattr(document, "filepath_s3", None)
    if s3_key:
        try:
            presigned = await controller.s3_service.generate_presigned_url(
                s3_key, current_user
            )
            await controller.audit_service.emit(
                resource_type="document",
                resource_id=id,
                event_type="document.downloaded",
                actor_id=getattr(current_user, "id", None),
                organization_id=document.organization_id,
                project_id=document.project_id,
                metadata={"provider": "s3"},
            )
            return Response(status_code=307, headers={"Location": presigned.get("url")})
        except Exception:
            # continue to presigned_url below
            pass

    # Stored public URLs are intentionally not used for downloads.
    if isinstance(document.presigned_url, str) and document.presigned_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stored public file URLs are not used for downloads; regenerate an authorized URL",
        )

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not available for download")


@router.get("/documents", response_model=DocumentListResponse)
@handle_exceptions
async def list_documents(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),
    subTags: Optional[List[str]] = Query(None),
    uploadType: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    letterNo: Optional[List[str]] = Query(None),
    subject: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List documents with filtering and pagination."""
    filters = {
        "organization_id": organization_id,
        "project_id": project_id,
        "tags": tags,
        "subTags": subTags,
        "uploadType": uploadType,
        "status": status,
        "search": search,
        "date_from": date_from,
        "date_to": date_to,
        "letterNo": letterNo,
        "subject": subject,
    }
    pagination = {"skip": skip, "limit": limit}

    scope_org, scope_project = _current_user_policy_scope(current_user)
    await PolicyService().authorize(
        current_user,
        Permissions.DOCUMENT_VIEW,
        resource_type="documents",
        organization_id=organization_id or scope_org,
        project_id=project_id or scope_project,
        audit=False,
    )
    return await controller_list_documents(controller, filters, pagination, current_user)


@router.put("/documents/{id}", response_model=Document)
@handle_exceptions
async def update_document(
    id: str,
    document_update: DocumentUpdate,
    response: Response,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    x_document_revision: Optional[str] = Header(default=None, alias="X-Document-Revision"),
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update a document."""
    existing = await controller.document_service.get_document_by_id(id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    await PolicyService().authorize_document(current_user, Permissions.DOCUMENT_EDIT_METADATA, existing)
    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_EDIT_METADATA)
    expected_revision = _parse_revision_header(if_match, x_document_revision)
    document = await controller_update_document(
        controller,
        id,
        document_update,
        current_user,
        expected_revision=expected_revision,
    )
    await _set_document_revision_headers(response, controller, id)
    return document


@router.patch("/documents/{id}/summary-metadata", response_model=Document)
@handle_exceptions
async def update_document_summary_metadata(
    id: str,
    payload: DocumentSummaryMetadataUpdate,
    response: Response,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Update manually curated Letter Summary metadata and keyword fields."""
    existing = await controller.document_service.get_document_by_id(id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    await PolicyService().authorize_document(current_user, Permissions.DOCUMENT_EDIT_METADATA, existing)
    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_EDIT_METADATA)

    update_fields = _build_summary_metadata_update(payload)
    updated_document = await controller.document_service.update_summary_metadata(
        id,
        update_fields,
        updated_by=getattr(current_user, "id", None) or getattr(current_user, "email", None),
    )
    if not updated_document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    await controller.audit_service.emit(
        resource_type="document",
        resource_id=id,
        event_type="document.summary_metadata_updated",
        actor_id=getattr(current_user, "id", None),
        organization_id=existing.organization_id,
        project_id=existing.project_id,
        metadata={"updated_fields": sorted(update_fields.keys())},
    )
    await _set_document_revision_headers(response, controller, id)
    return await controller.document_service.enrich_document(updated_document)


@router.delete("/documents/{id}", status_code=204)
@handle_exceptions
async def delete_document(
    id: str,
    request: Request,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    x_document_revision: Optional[str] = Header(default=None, alias="X-Document-Revision"),
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete a document."""
    await require_step_up(request, current_user, action="documents.delete")
    existing = await controller.document_service.get_document_by_id(id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    await PolicyService().authorize_document(current_user, Permissions.DOCUMENT_DELETE, existing)
    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_DELETE)
    expected_revision = _parse_revision_header(if_match, x_document_revision)
    await controller_delete_document(
        controller,
        id,
        current_user,
        expected_revision=expected_revision,
    )


@router.get("/documents/{id}/audit-events", response_model=List[Dict[str, Any]])
@handle_exceptions
async def list_document_audit_events(
    id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return immutable audit events for a document."""
    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_VIEW)
    document = await controller.document_service.get_document_by_id(id)
    if not document:
        raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)
    return await controller.audit_service.list_events(
        resource_type="document",
        resource_id=id,
        skip=skip,
        limit=limit,
    )


@router.get("/documents/{id}/enclosures", response_model=List[Enclosure])
@handle_exceptions
async def list_enclosures(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List all enclosures for a document."""
    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_VIEW)
    return await controller_list_enclosures(controller, id, current_user)


@router.post("/documents/{id}/enclosures", response_model=EnclosureResponse)
@handle_exceptions
async def add_enclosure(
    id: str,
    file: UploadFile = File(...),
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Add an enclosure to a document."""
    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_EDIT_METADATA)
    return await controller_add_enclosure(controller, id, file, current_user)


@router.delete("/documents/{id}/enclosures/{enclosure_id}", status_code=204)
@handle_exceptions
async def remove_enclosure(
    id: str,
    enclosure_id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Remove an enclosure from a document."""
    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_EDIT_METADATA)
    await controller_remove_enclosure(controller, id, enclosure_id, current_user)


@router.get("/documents/{id}/references", response_model=Dict[str, Any])
@handle_exceptions
async def get_document_references(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Retrieve parsed and linked references for a document."""
    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_VIEW)
    return await controller_list_references(controller, id, current_user)


@router.post("/documents/{id}/references", response_model=Document)
@handle_exceptions
async def add_document_reference(
    id: str,
    reference_data: ReferenceCreate,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Add or update a linked reference for a document."""
    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_LINK_REFERENCE)
    return await controller_add_reference(controller, id, reference_data, current_user)


@router.delete("/documents/{id}/references/{reference_id}", response_model=Document)
@handle_exceptions
async def delete_document_reference(
    id: str,
    reference_id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Remove a linked reference from a document."""
    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_LINK_REFERENCE)
    return await controller_remove_reference(controller, id, reference_id, current_user)


@router.post("/documents/{id}/sync-references", response_model=Dict[str, Any])
@handle_exceptions
async def trigger_reference_sync(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Manually trigger bidirectional reference synchronisation for a document."""
    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_LINK_REFERENCE)
    return await controller_sync_references(controller, id, current_user)


@router.post("/documents/link", response_model=Dict[str, Any])
@handle_exceptions
async def link_documents_endpoint(
    payload: LinkDocumentsRequest,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Link two documents together."""
    await _ensure_document_access(current_user, payload.source_document_id, Permissions.DOCUMENT_LINK_REFERENCE)
    await _ensure_document_access(current_user, payload.target_document_id, Permissions.DOCUMENT_VIEW)
    return await controller_link_documents(controller, payload, current_user)


@router.get("/documents/{id}/linked", response_model=List[Dict[str, Any]])
@handle_exceptions
async def get_linked_documents(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return documents linked to the given document."""
    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_VIEW)
    return await controller_list_linked_documents(controller, id, current_user)

# ---------------------------------------------
# Comments endpoints used by LetterSummaryPage
# ---------------------------------------------
@router.get("/documents/{id}/comments", response_model=List[Dict[str, Any]])
@handle_exceptions
async def get_document_comments(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Return comments for a document: [{ id, text, author, createdAt }, ...]
    """
    # Load document for auth context
    document = await controller.document_service.get_document_by_id(id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    await _ensure_document_access(current_user, id, Permissions.DOCUMENT_VIEW)

    return await controller.document_service.get_comments(id)

@router.post("/documents/{id}/comments", response_model=Dict[str, Any])
@handle_exceptions
async def add_document_comment(
    id: str,
    text: str = Body(..., embed=True),
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Add a comment to a document. Body shape: { "text": "..." }
    """
    document = await controller.document_service.get_document_by_id(id)
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    await _ensure_document_access(current_user, id, Permissions.COMMENT_ADD)

    comment = await controller.document_service.add_comment(id, text, current_user)
    await controller.audit_service.emit(
        resource_type="document",
        resource_id=id,
        event_type="document.comment_added",
        actor_id=getattr(current_user, "id", None),
        organization_id=document.organization_id,
        project_id=document.project_id,
        metadata={"comment_id": comment.get("id")},
    )
    return comment

@router.post("/documents/bulk-upload", response_model=BulkUploadResponse)
@handle_exceptions
async def bulk_upload_documents(
    background_tasks: BackgroundTasks,
    csv_file: UploadFile = File(..., description="CSV file with document metadata"),
    files: List[UploadFile] = File(..., description="Document files to upload"),
    organization_id: str = Form(...),
    project_id: str = Form(...),
    csv_encoding: Optional[str] = Form(None, description="Optional CSV encoding"),

    # Add validation for bulk upload limits
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Bulk upload documents with CSV metadata.
    
    The CSV file should contain metadata for each document file.
    File names in CSV must match the uploaded file names.
    """
    await PolicyService().authorize(
        current_user,
        Permissions.DOCUMENT_UPLOAD,
        resource_type="project",
        resource_id=project_id,
        organization_id=organization_id,
        project_id=project_id,
    )
    # Check bulk upload limits
    if len(files) > settings.BULK_UPLOAD_MAX_FILES:
        raise DocumentError(
            f"Bulk upload exceeds maximum file limit ({settings.BULK_UPLOAD_MAX_FILES})",
            status.HTTP_400_BAD_REQUEST
        )

    # SECURITY (H1): the previous check used `file.file._file.tell()` before the
    # stream had been read, so it evaluated to ~0 and enforced no cap at all.
    # Measure each file's real size by seeking to the end, then rewind so the
    # downstream read starts from the beginning.
    total_size = 0
    for file in files:
        file.file.seek(0, 2)  # 2 == os.SEEK_END
        total_size += file.file.tell()
        file.file.seek(0)
    if total_size > settings.BULK_UPLOAD_MAX_SIZE_MB * 1024 * 1024:
        raise DocumentError(
            f"Bulk upload exceeds maximum total size ({settings.BULK_UPLOAD_MAX_SIZE_MB}MB)",
            status.HTTP_400_BAD_REQUEST
        )

    return await controller.bulk_upload_documents(
        background_tasks=background_tasks,
        csv_file=csv_file,
        files=files,
        organization_id=organization_id,
        project_id=project_id,
        current_user=current_user,
        csv_encoding=csv_encoding,
    )

@router.get("/documents/bulk-upload/{job_id}/status", response_model=BulkUploadStatus)
@handle_exceptions
async def get_bulk_upload_status(
    job_id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get the status of a bulk upload job."""
    scope_org, scope_project = _current_user_policy_scope(current_user)
    await PolicyService().authorize(
        current_user,
        Permissions.DOCUMENT_UPLOAD,
        resource_type="documents_bulk_upload_status",
        organization_id=scope_org,
        project_id=scope_project,
    )
    return await controller.get_bulk_upload_status(job_id, current_user)

@router.get("/documents/bulk-upload/template")
async def download_bulk_upload_template(
    current_user: CurrentUser = Depends(get_current_user),
):
    """Download CSV template for bulk upload."""
    # Return CSV template file
    from fastapi.responses import Response
    scope_org, scope_project = _current_user_policy_scope(current_user)
    await PolicyService().authorize(
        current_user,
        Permissions.DOCUMENT_UPLOAD,
        resource_type="documents_bulk_upload_template",
        organization_id=scope_org,
        project_id=scope_project,
    )
    
    csv_template = """filename,uploadType,letterNo,date,ocrEnabled
sample-letter-001.pdf,incoming,LTR-2024-001,2024-01-15,true
sample-letter-002.pdf,outgoing,LTR-2024-002,2024-01-16,true
sample-invoice-001.pdf,incoming,INV-2024-001,2024-01-17,true"""

    return Response(
        content=csv_template,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=bulk-upload-template.csv"}
    )

@router.post("/documents/{id}/request-draft")
@handle_exceptions
async def request_draft_for_document(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """
    Initialize a draft letter request for a document.
    Returns document details to prefill the letter initiation form.
    """
    try:
        # Get the document
        document = await controller.document_service.get_document_by_id(id)
        if not document:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

        await _ensure_document_access(current_user, id, Permissions.DOCUMENT_VIEW)

        await policy.authorize(
            current_user,
            "drafting.request.create",
            resource_type="document",
            resource_id=id,
            organization_id=document.organization_id,
            project_id=document.project_id,
        )

        # Check if document already has a draft in progress
        if document.status == "Under Process":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Draft already in progress for this document"
            )

        # Prepare response with document details for prefilling the form
        response_data = {
            "document_id": id,
            "letter_no": document.letterNo or "",
            "subject": document.subject or "",
            "recipient": document.from_ if document.uploadType == "incoming" else document.to or "",
            "organization_id": document.organization_id,
            "project_id": document.project_id,
            "reference_document": {
                "id": id,
                "filename": document.filename,
                "letter_no": document.letterNo,
                "date": document.date,
                "subject": document.subject,
                "from_": document.from_,
                "to": document.to,
                "direction": document.uploadType,
            }
        }

        # Update document status to "Under Process" to prevent duplicate draft requests
        await controller.document_service.update_document(
            id,
            document.model_copy(update={"status": "Under Process"})
        )

        return response_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to initialize draft request for document {id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initialize draft request"
        )
