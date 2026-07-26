"""Contract upload, listing, search, and download routes."""

from __future__ import annotations

import shutil
import tempfile
import uuid
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse, Response

from ..config.document_processing_config import DocumentProcessingConfig
from ..core.config import settings
from ..core.permissions import Permissions
from ..core.security import CurrentUser, get_current_user
from ..models.contract_models import (
    ChunkUploadResponse,
    ContractListResponse,
    ContractSearchRequest,
    ContractSearchResponse,
    OCRRetryRequest,
    ContractUploadSessionRequest,
    ContractUploadSessionResponse,
    StatusResponse,
    UploadMultipartResponse,
    UploadResult,
)
from ..models.storage_settings import StorageProviderConfig
from ..services.contract_ingest_queue import ContractQueueEnqueueResult, get_contract_ingest_queue
from ..services.contract_service import ContractService
from ..services.document_audit_service import DocumentAuditService
from ..services.file_object_service import FileObjectService
from ..services.policy_service import PolicyService
from ..services.file_service import SecureFileService
from ..services.s3_service import S3Service
from ..services.storage_key_builder import StorageKeyBuilder
from ..services.storage_settings_service import StorageSettingsService
from ..services.usage_metering_service import UsageEventType
from ..services.upload_limits import upload_concurrency_limiter
from ..services.upload_streaming import (
    SpooledUpload,
    inspect_existing_file,
    spool_upload_file,
    validate_spooled_upload,
)
from ..utils.error_handler import ContractError, handle_exceptions
from ..utils.file_validation import sniff_mime_from_bytes
from ..utils.validation import sanitize_filename
from ..services.antivirus_service import AntivirusService

router = APIRouter()
logger = logging.getLogger(__name__)


def _resolve_uploads_dir() -> Path:
    candidate = Path(getattr(settings, "UPLOADS_DIR", "uploads/contracts") or "uploads/contracts").expanduser()
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        test_file = candidate / ".write_test"
        with test_file.open("wb") as handle:
            handle.write(b"test")
        test_file.unlink(missing_ok=True)
        return candidate
    except Exception:
        fallback = Path(tempfile.gettempdir()) / "contract_uploads"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


BASE_UPLOAD_PATH: Path = _resolve_uploads_dir()
BASE_UPLOAD_DIR: str = str(BASE_UPLOAD_PATH)


def _preprocess_pdf_with_ocr(pdf_path: str, language: str = "eng") -> tuple[str, str | None, bool]:
    source = Path(pdf_path)
    if not source.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    destination_dir = BASE_UPLOAD_PATH / "preprocessed"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{uuid.uuid4()}_{source.name}"
    shutil.copy2(source, destination)
    return str(destination), None, False


def _contract_limits() -> tuple[int, int]:
    max_file = max(1, int(settings.CONTRACT_UPLOAD_MAX_FILE_SIZE_MB)) * 1024 * 1024
    max_chunk = max(1, int(settings.CONTRACT_UPLOAD_MAX_CHUNK_SIZE_MB)) * 1024 * 1024
    return max_file, max_chunk


def _storage_path_prefix(organization_id: str, project_id: Optional[str], upload_id: str) -> str:
    now = datetime.utcnow()
    return f"contracts/{organization_id}/{project_id or 'default'}/{now:%Y}/{now:%m}/{upload_id}"


def _stored_filename(upload_id: str, safe_filename: str) -> str:
    return f"{upload_id}_{safe_filename}"


def _write_processing_copy(upload_id: str, safe_filename: str, content: bytes) -> Path:
    processing_dir = Path(tempfile.gettempdir()) / "contract_processing"
    processing_dir.mkdir(parents=True, exist_ok=True)
    path = processing_dir / f"{upload_id}_{safe_filename}"
    path.write_bytes(content)
    return path


async def _enqueue_or_start_contract_ingest(payload: Dict[str, Any]) -> ContractQueueEnqueueResult:
    try:
        job_id = await get_contract_ingest_queue().enqueue(payload)
        return ContractQueueEnqueueResult(job_id=job_id)
    except Exception as exc:
        logger.warning("Contract queue unavailable; marking ingest degraded: %s", exc)
        upload_id = str(payload.get("upload_id") or "unknown")
        return ContractQueueEnqueueResult(
            job_id=f"queue-unavailable:{upload_id}",
            status="failed",
            degraded=True,
            error=f"Contract ingestion queue unavailable: {exc}",
        )


async def _write_to_providers(
    *,
    content: bytes,
    organization_id: str,
    project_id: Optional[str],
    safe_filename: str,
    upload_id: str,
    file_service: SecureFileService,
    storage_settings: StorageSettingsService,
    s3_service: S3Service,
    current_user: Optional[CurrentUser] = None,
) -> Dict[str, Any]:
    file_object_service = FileObjectService(
        file_service=file_service,
        storage_settings=storage_settings,
        s3_service=s3_service,
    )
    key_builder = StorageKeyBuilder()
    try:
        context = await file_object_service.resolve_storage_context(organization_id, project_id)
    except Exception:
        context = {
            "organization": organization_id,
            "project": project_id or "default",
            "providers": [StorageProviderConfig(id="local", enabled=True, primary=True)],
        }
    storage_key = key_builder.build_contract_key(
        organization=context["organization"],
        project=context["project"],
        safe_filename=safe_filename,
        upload_id=upload_id,
    )
    try:
        return await file_object_service.store_bytes(
            content=content,
            organization_id=organization_id,
            project_id=project_id,
            original_filename=safe_filename,
            storage_key=storage_key,
            current_user=current_user,
            document_type="contract",
            upload_id=upload_id,
            content_type=sniff_mime_from_bytes(content, safe_filename),
            providers=context["providers"],
        )
    except Exception as exc:
        raise ContractError(
            "Failed to store contract to any provider",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc


async def _write_spooled_to_providers(
    *,
    spooled: SpooledUpload,
    organization_id: str,
    project_id: Optional[str],
    upload_id: str,
    file_service: SecureFileService,
    storage_settings: StorageSettingsService,
    s3_service: S3Service,
    current_user: Optional[CurrentUser] = None,
) -> Dict[str, Any]:
    file_object_service = FileObjectService(
        file_service=file_service,
        storage_settings=storage_settings,
        s3_service=s3_service,
    )
    key_builder = StorageKeyBuilder()
    try:
        context = await file_object_service.resolve_storage_context(organization_id, project_id)
    except Exception:
        context = {
            "organization": organization_id,
            "project": project_id or "default",
            "providers": [StorageProviderConfig(id="local", enabled=True, primary=True)],
        }
    storage_key = key_builder.build_contract_key(
        organization=context["organization"],
        project=context["project"],
        safe_filename=spooled.filename,
        upload_id=upload_id,
    )
    try:
        return await file_object_service.store_path(
            source_path=spooled.path,
            size=spooled.size,
            sha256=spooled.sha256,
            mime_type=spooled.mime_type,
            organization_id=organization_id,
            project_id=project_id,
            original_filename=spooled.filename,
            storage_key=storage_key,
            current_user=current_user,
            document_type="contract",
            upload_id=upload_id,
            providers=context["providers"],
        )
    except Exception as exc:
        raise ContractError(
            "Failed to store contract to any provider",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from exc


async def get_contract_service() -> ContractService:
    return ContractService()


def get_policy_service() -> PolicyService:
    """Construct policy enforcement without exposing constructor internals to FastAPI."""
    return PolicyService()


async def get_file_service() -> SecureFileService:
    config = DocumentProcessingConfig()
    if settings.SECURE_UPLOADS_DIR:
        config.uploads_dir = settings.SECURE_UPLOADS_DIR
    return SecureFileService(config=config)


async def _authorize_contract_scope(
    policy: PolicyService,
    current_user: CurrentUser,
    permission: str,
    *,
    organization_id: Optional[str],
    project_id: Optional[str],
    resource_type: str,
    meter_event_type: Optional[str] = None,
    meter_quantity: int = 1,
    meter_metadata: Optional[Dict[str, Any]] = None,
    audit: bool = True,
) -> None:
    await policy.authorize(
        current_user,
        permission,
        resource_type=resource_type,
        organization_id=organization_id,
        project_id=project_id,
        meter_event_type=meter_event_type,
        meter_quantity=meter_quantity,
        meter_metadata=meter_metadata,
        audit=audit,
    )


@router.post("/contracts/upload-session", response_model=ContractUploadSessionResponse)
@handle_exceptions
async def create_contract_upload_session(
    payload: ContractUploadSessionRequest,
    contract_service: ContractService = Depends(get_contract_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await _authorize_contract_scope(
        policy,
        current_user,
        Permissions.DOCUMENT_UPLOAD,
        resource_type="project",
        organization_id=payload.organization_id,
        project_id=payload.project_id,
    )
    return await contract_service.create_upload_session(
        filename=payload.filename,
        organization_id=payload.organization_id,
        project_id=payload.project_id,
        current_user=current_user,
    )


@router.post("/contracts/upload-multipart", response_model=UploadMultipartResponse)
@handle_exceptions
async def upload_contracts_multipart(
    files: List[UploadFile] = File(...),
    organization_id: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    upload_ids: Optional[List[str]] = Form(None),
    tags: Optional[List[str]] = Form(None),
    contract_service: ContractService = Depends(get_contract_service),
    file_service: SecureFileService = Depends(get_file_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    if not files:
        raise ContractError("At least one contract file is required", status.HTTP_422_UNPROCESSABLE_ENTITY)
    if upload_ids and len(upload_ids) not in (0, len(files)):
        raise ContractError("upload_ids must align with the number of files", status.HTTP_422_UNPROCESSABLE_ENTITY)

    max_file_size_bytes, _ = _contract_limits()
    storage_settings = StorageSettingsService()
    s3_service = S3Service()
    results: List[UploadResult] = []
    effective_org: Optional[str] = None
    effective_project: Optional[str] = None

    for index, file in enumerate(files):
        if not file.filename:
            raise ContractError("File missing filename", status.HTTP_400_BAD_REQUEST)
        upload_id = upload_ids[index] if upload_ids else None
        if upload_id:
            session_doc = await contract_service.validate_upload_session(
                upload_id,
                current_user,
                organization_id,
                project_id,
                file.filename,
            )
            await _authorize_contract_scope(
                policy,
                current_user,
                Permissions.DOCUMENT_UPLOAD,
                organization_id=str(session_doc.get("organization_id") or ""),
                project_id=str(session_doc.get("project_id") or "") or None,
                resource_type="contract_upload",
                audit=index == 0,
            )
        else:
            await _authorize_contract_scope(
                policy,
                current_user,
                Permissions.DOCUMENT_UPLOAD,
                organization_id=organization_id,
                project_id=project_id,
                resource_type="contract_upload",
                audit=index == 0,
            )
        async with upload_concurrency_limiter.slot(
            f"user:{current_user.id}",
            int(settings.UPLOAD_MAX_CONCURRENT_PER_USER),
        ), upload_concurrency_limiter.slot(
            f"org:{organization_id or 'unscoped'}",
            int(settings.UPLOAD_MAX_CONCURRENT_PER_ORG),
        ):
            spooled = await spool_upload_file(file, max_size_bytes=max_file_size_bytes)
            try:
                validation = validate_spooled_upload(spooled, settings.ALLOWED_CONTRACT_MIMES)
                if not validation.is_valid:
                    raise ContractError(validation.error or "Invalid contract file", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

                # --- ANTIVIRUS STREAM SCAN ---
                if settings.ANTIVIRUS_ENABLED:
                    antivirus = AntivirusService()
                    is_clean, scan_detail = await antivirus.scan_file(spooled.path)
                    if not is_clean:
                        raise ContractError(
                            f"Antivirus scan rejected this file: {scan_detail}",
                            status.HTTP_400_BAD_REQUEST
                        )
                # ------------------------------

                if not upload_id:
                    session = await contract_service.create_upload_session(file.filename, organization_id, project_id, current_user)
                    upload_id = session.upload_id
                session_doc = await contract_service.validate_upload_session(
                    upload_id,
                    current_user,
                    organization_id,
                    project_id,
                    file.filename,
                    file_size=spooled.size,
                )
                effective_org = str(session_doc.get("organization_id") or organization_id or "")
                effective_project = str(session_doc.get("project_id") or "") or None
                await _authorize_contract_scope(
                    policy,
                    current_user,
                    Permissions.DOCUMENT_UPLOAD,
                    organization_id=effective_org,
                    project_id=effective_project,
                    resource_type="contract_upload",
                    meter_event_type=UsageEventType.DOCUMENT_UPLOAD,
                    meter_metadata={
                        "operation": "upload_contracts_multipart",
                        "filename": file.filename,
                        "upload_id": upload_id,
                    },
                    audit=True,
                )
                store_result = await _write_spooled_to_providers(
                    spooled=spooled,
                    organization_id=effective_org,
                    project_id=effective_project,
                    upload_id=upload_id,
                    file_service=file_service,
                    storage_settings=storage_settings,
                    s3_service=s3_service,
                    current_user=current_user,
                )
                document_id = await contract_service.create_contract_document(
                    upload_id=upload_id,
                    filename=file.filename,
                    organization_id=effective_org,
                    project_id=effective_project,
                    tags=tags or [],
                    file_size=spooled.size,
                    file_bytes=spooled.sample,
                    filepath_local=store_result.get("filepath_local"),
                    filepath_s3=store_result.get("filepath_s3"),
                    storage_locations=store_result.get("storage_locations") or [],
                    current_user=current_user,
                    file_object_id=store_result.get("file_object_id"),
                    storage_key=store_result.get("storage_key"),
                    sha256=store_result.get("sha256"),
                )
                await contract_service.complete_upload_session(upload_id, document_id)
                ingest_payload = {
                    "upload_id": upload_id,
                    "document_id": document_id,
                    "organization_id": effective_org,
                    "project_id": effective_project,
                    "filename": file.filename,
                    "tags": tags or [],
                    "file_object_id": store_result.get("file_object_id"),
                }
                queue_result = await _enqueue_or_start_contract_ingest(ingest_payload)
                job_status = queue_result.status
                if queue_result.degraded:
                    await contract_service.update_contract_document(
                        document_id,
                        status_value="failed",
                        error=queue_result.error,
                        tags=tags or [],
                    )
                await contract_service.update_job_status(
                    upload_id,
                    job_status,
                    error=queue_result.error,
                    metadata={
                        "document_id": document_id,
                        "filename": file.filename,
                        "organization_id": effective_org,
                        "project_id": effective_project,
                        "tags": tags or [],
                        "size": spooled.size,
                        "queue_job_id": queue_result.job_id,
                        "queue_degraded": queue_result.degraded,
                        "processing_stage": "queue_failed" if queue_result.degraded else "queued",
                        "stage_label": "Contract ingestion queue unavailable" if queue_result.degraded else "Queued for contract ingestion",
                        "progress": 0 if queue_result.degraded else 10,
                        "file_object_id": store_result.get("file_object_id"),
                        "storage_key": store_result.get("storage_key"),
                        "sha256": store_result.get("sha256"),
                        "upload_streamed": True,
                    },
                )
                results.append(UploadResult(upload_id=upload_id, document_id=document_id, filename=file.filename, status=job_status))
            finally:
                await spooled.cleanup()

    return UploadMultipartResponse(organization_id=effective_org or "", project_id=effective_project, results=results)


@router.post("/contracts/upload-chunk", response_model=ChunkUploadResponse)
@handle_exceptions
async def upload_contract_chunk(
    chunk: UploadFile = File(...),
    upload_id: str = Form(...),
    filename: str = Form(...),
    chunkIndex: int = Form(..., ge=0),
    totalChunks: int = Form(..., ge=1),
    organization_id: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    tags: Optional[List[str]] = Form(None),
    contract_service: ContractService = Depends(get_contract_service),
    file_service: SecureFileService = Depends(get_file_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    max_file_size_bytes, max_chunk_size_bytes = _contract_limits()
    session_doc = await contract_service.validate_upload_session(
        upload_id,
        current_user,
        organization_id,
        project_id,
        filename,
        total_chunks=totalChunks,
    )
    effective_org = str(session_doc.get("organization_id") or "")
    effective_project = str(session_doc.get("project_id") or "") or None
    await _authorize_contract_scope(
        policy,
        current_user,
        Permissions.DOCUMENT_UPLOAD,
        organization_id=effective_org,
        project_id=effective_project,
        resource_type="contract_upload",
        audit=False,
    )
    chunk_bytes = await chunk.read()
    await chunk.seek(0)
    chunk_sha256 = hashlib.sha256(chunk_bytes).hexdigest()
    if len(chunk_bytes) > max_chunk_size_bytes:
        raise ContractError("Chunk exceeds the maximum allowed chunk size", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    safe_filename = sanitize_filename(filename)
    if session_doc.get("document_id"):
        received_chunks = sorted(
            int(idx)
            for idx in (session_doc.get("received_chunks") or [])
            if str(idx).isdigit()
        )
        return ChunkUploadResponse(
            upload_id=upload_id,
            document_id=str(session_doc.get("document_id")),
            filename=filename,
            chunk_index=chunkIndex,
            total_chunks=totalChunks,
            received=True,
            merged=True,
            scheduled=True,
            received_chunks=received_chunks,
            missing_chunks=[],
            upload_complete=True,
        )
    sessions = await contract_service._get_sessions()
    await file_service.store_chunk_bytes(
        chunk_bytes,
        upload_id,
        chunkIndex,
        organization_id=effective_org,
        user_id=current_user.id,
    )
    await sessions.update_one(
        {"upload_id": upload_id},
        {
            "$addToSet": {"received_chunks": chunkIndex},
            "$set": {
                "total_chunks": totalChunks,
                f"chunk_checksums.{chunkIndex}": chunk_sha256,
                "updatedAt": datetime.utcnow(),
            }
        },
    )
    refreshed_session = await sessions.find_one({"upload_id": upload_id}) or {}
    current_chunks = {
        int(idx)
        for idx in (refreshed_session.get("received_chunks") or [])
        if str(idx).isdigit()
    }
    received_chunks = sorted(current_chunks)
    missing_chunks = [idx for idx in range(totalChunks) if idx not in current_chunks]
    await sessions.update_one(
        {"upload_id": upload_id},
        {"$set": {"missing_chunks": missing_chunks, "updatedAt": datetime.utcnow()}},
    )
    merged = False
    scheduled = False
    document_id: Optional[str] = None
    upload_complete = not missing_chunks
    if upload_complete:
        async with upload_concurrency_limiter.slot(
            f"user:{current_user.id}",
            int(settings.UPLOAD_MAX_CONCURRENT_PER_USER),
        ), upload_concurrency_limiter.slot(
            f"org:{effective_org}",
            int(settings.UPLOAD_MAX_CONCURRENT_PER_ORG),
        ):
            final_path = await file_service.merge_chunks(
                upload_id,
                totalChunks,
                effective_org,
                effective_project,
                safe_filename,
                stored_filename=_stored_filename(upload_id, safe_filename),
                user_id=current_user.id,
            )
            spooled = await inspect_existing_file(final_path, filename=safe_filename)
            if spooled.size > max_file_size_bytes:
                await file_service.cleanup_upload(upload_id, organization_id=effective_org, user_id=current_user.id)
                raise ContractError("Upload exceeds the maximum allowed file size", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
            validation = validate_spooled_upload(spooled, settings.ALLOWED_CONTRACT_MIMES)
            if not validation.is_valid:
                await file_service.cleanup_upload(upload_id, organization_id=effective_org, user_id=current_user.id)
                raise ContractError(validation.error or "Invalid contract file", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

            # --- ANTIVIRUS STREAM SCAN ---
            if settings.ANTIVIRUS_ENABLED:
                antivirus = AntivirusService()
                is_clean, scan_detail = await antivirus.scan_file(spooled.path)
                if not is_clean:
                    await file_service.cleanup_upload(upload_id, organization_id=effective_org, user_id=current_user.id)
                    try:
                        final_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    raise ContractError(
                        f"Antivirus scan rejected this file: {scan_detail}",
                        status.HTTP_400_BAD_REQUEST
                    )
            # ------------------------------

            await _authorize_contract_scope(
                policy,
                current_user,
                Permissions.DOCUMENT_UPLOAD,
                organization_id=effective_org,
                project_id=effective_project,
                resource_type="contract_upload",
                meter_event_type=UsageEventType.DOCUMENT_UPLOAD,
                meter_metadata={
                    "operation": "upload_contract_chunk_merge",
                    "filename": filename,
                    "upload_id": upload_id,
                    "total_chunks": totalChunks,
                },
                audit=True,
            )

            store_result = await _write_spooled_to_providers(
                spooled=spooled,
                organization_id=effective_org,
                project_id=effective_project,
                upload_id=upload_id,
                file_service=file_service,
                storage_settings=StorageSettingsService(),
                s3_service=S3Service(),
                current_user=current_user,
            )
            document_id = await contract_service.create_contract_document(
                upload_id=upload_id,
                filename=filename,
                organization_id=effective_org,
                project_id=effective_project,
                tags=tags or [],
                file_size=spooled.size,
                file_bytes=spooled.sample,
                filepath_local=store_result.get("filepath_local"),
                filepath_s3=store_result.get("filepath_s3"),
                storage_locations=store_result.get("storage_locations") or [],
                current_user=current_user,
                file_object_id=store_result.get("file_object_id"),
                storage_key=store_result.get("storage_key"),
                sha256=store_result.get("sha256"),
            )
            await contract_service.complete_upload_session(upload_id, document_id)
            ingest_payload = {
                "upload_id": upload_id,
                "document_id": document_id,
                "organization_id": effective_org,
                "project_id": effective_project,
                "filename": filename,
                "tags": tags or [],
                "file_object_id": store_result.get("file_object_id"),
            }
            queue_result = await _enqueue_or_start_contract_ingest(ingest_payload)
            job_status = queue_result.status
            if queue_result.degraded:
                await contract_service.update_contract_document(
                    document_id,
                    status_value="failed",
                    error=queue_result.error,
                    tags=tags or [],
                )
            await contract_service.update_job_status(
                upload_id,
                job_status,
                error=queue_result.error,
                metadata={
                    "document_id": document_id,
                    "filename": filename,
                    "organization_id": effective_org,
                    "project_id": effective_project,
                    "tags": tags or [],
                    "size": spooled.size,
                    "queue_job_id": queue_result.job_id,
                    "queue_degraded": queue_result.degraded,
                    "processing_stage": "queue_failed" if queue_result.degraded else "queued",
                    "stage_label": "Contract ingestion queue unavailable" if queue_result.degraded else "Queued for contract ingestion",
                    "progress": 0 if queue_result.degraded else 10,
                    "file_object_id": store_result.get("file_object_id"),
                    "storage_key": store_result.get("storage_key"),
                    "sha256": store_result.get("sha256"),
                    "upload_streamed": True,
                },
            )
            await file_service.cleanup_upload(upload_id, organization_id=effective_org, user_id=current_user.id)
            try:
                final_path.unlink(missing_ok=True)
            except Exception:
                pass
            merged = True
            scheduled = True
            missing_chunks = []

    return ChunkUploadResponse(
        upload_id=upload_id,
        document_id=document_id,
        filename=filename,
        chunk_index=chunkIndex,
        total_chunks=totalChunks,
        received=True,
        merged=merged,
        scheduled=scheduled,
        received_chunks=received_chunks,
        missing_chunks=missing_chunks,
        upload_complete=upload_complete,
    )


@router.get("/contracts/status", response_model=StatusResponse)
@handle_exceptions
async def get_contract_status(
    upload_id: str = Query(...),
    contract_service: ContractService = Depends(get_contract_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    status_response = await contract_service.get_job_status(upload_id, current_user)
    await _authorize_contract_scope(
        policy,
        current_user,
        Permissions.DOCUMENT_VIEW,
        organization_id=status_response.organization_id,
        project_id=status_response.project_id,
        resource_type="contract_upload_status",
        audit=False,
    )
    return status_response


@router.post("/contracts/{document_id}/ocr/retry", response_model=StatusResponse)
@handle_exceptions
async def retry_contract_ocr_pages(
    document_id: str,
    request: OCRRetryRequest,
    contract_service: ContractService = Depends(get_contract_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    document = await contract_service.get_contract_document(document_id, current_user)
    organization_id = str(document.get("organization_id") or "")
    project_id = str(document.get("project_id") or "") or None
    await _authorize_contract_scope(
        policy,
        current_user,
        Permissions.DOCUMENT_EDIT_METADATA,
        organization_id=organization_id,
        project_id=project_id,
        resource_type="contract_ocr",
        audit=True,
    )
    page_numbers = sorted({int(page) for page in request.page_numbers if int(page) > 0})
    if not page_numbers:
        page_numbers = await contract_service.get_failed_ocr_pages(document_id)
    if not page_numbers:
        raise ContractError("No failed OCR pages found for this contract", status.HTTP_422_UNPROCESSABLE_ENTITY)

    upload_id = str(document.get("contract_upload_id") or document.get("upload_id") or document_id)
    local_path = document.get("filepath_local") or document.get("file_path")
    if not local_path and not document.get("file_object_id"):
        raise ContractError("Original contract file is not available for OCR retry", status.HTTP_404_NOT_FOUND)

    payload = {
        "upload_id": upload_id,
        "document_id": document_id,
        "organization_id": organization_id,
        "project_id": project_id,
        "filename": document.get("filename") or "contract.pdf",
        "tags": document.get("tags") or [],
        "file_object_id": document.get("file_object_id"),
        "processing_path": local_path,
        "retry_ocr_pages": page_numbers,
    }
    queue_result = await _enqueue_or_start_contract_ingest(payload)
    job_status = queue_result.status
    if queue_result.degraded:
        await contract_service.update_contract_document(
            document_id,
            status_value="failed",
            error=queue_result.error,
            tags=document.get("tags") or [],
        )
    await contract_service.update_job_status(
        upload_id,
        job_status,
        error=queue_result.error,
        metadata={
            "document_id": document_id,
            "filename": payload["filename"],
            "organization_id": organization_id,
            "project_id": project_id,
            "queue_job_id": queue_result.job_id,
            "queue_degraded": queue_result.degraded,
            "retry_ocr_pages": page_numbers,
            "processing_stage": "queue_failed" if queue_result.degraded else "ocr_retry_queued",
            "stage_label": "Contract ingestion queue unavailable" if queue_result.degraded else f"Queued OCR retry for {len(page_numbers)} page(s)",
            "progress": 0 if queue_result.degraded else 10,
        },
    )
    return await contract_service.get_job_status(upload_id, current_user)


@router.post("/contracts/{document_id}/reindex", response_model=StatusResponse)
@handle_exceptions
async def reindex_contract(
    document_id: str,
    contract_service: ContractService = Depends(get_contract_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Re-run ingestion for an existing contract to rebuild its vector index
    without re-uploading. Intended for documents whose vectors were never
    written (e.g. a Qdrant outage during the original ingest left the contract
    "completed" but unsearchable). The stored source file is re-processed
    through the normal ingest queue, so it runs on the backend's own OpenAI
    connection and produces production-identical chunks."""
    document = await contract_service.get_contract_document(document_id, current_user)
    organization_id = str(document.get("organization_id") or "")
    project_id = str(document.get("project_id") or "") or None
    await _authorize_contract_scope(
        policy,
        current_user,
        Permissions.DOCUMENT_EDIT_METADATA,
        organization_id=organization_id,
        project_id=project_id,
        resource_type="contract_reindex",
        audit=True,
    )

    upload_id = str(document.get("contract_upload_id") or document.get("upload_id") or document_id)
    local_path = document.get("filepath_local") or document.get("file_path")
    if not local_path and not document.get("file_object_id"):
        raise ContractError(
            "Original contract file is not available for reindex",
            status.HTTP_404_NOT_FOUND,
        )

    payload = {
        "upload_id": upload_id,
        "document_id": document_id,
        "organization_id": organization_id,
        "project_id": project_id,
        "filename": document.get("filename") or "contract.pdf",
        "tags": document.get("tags") or [],
        "file_object_id": document.get("file_object_id"),
        "processing_path": local_path,
    }
    queue_result = await _enqueue_or_start_contract_ingest(payload)
    job_status = queue_result.status
    if queue_result.degraded:
        await contract_service.update_contract_document(
            document_id,
            status_value="failed",
            error=queue_result.error,
            tags=document.get("tags") or [],
        )
    await contract_service.update_job_status(
        upload_id,
        job_status,
        error=queue_result.error,
        metadata={
            "document_id": document_id,
            "filename": payload["filename"],
            "organization_id": organization_id,
            "project_id": project_id,
            "queue_job_id": queue_result.job_id,
            "queue_degraded": queue_result.degraded,
            "processing_stage": "queue_failed" if queue_result.degraded else "reindex_queued",
            "stage_label": (
                "Contract ingestion queue unavailable"
                if queue_result.degraded
                else "Queued reindex (rebuild vector index)"
            ),
            "progress": 0 if queue_result.degraded else 10,
        },
    )
    return await contract_service.get_job_status(upload_id, current_user)


@router.get("/contracts/list", response_model=ContractListResponse)
@handle_exceptions
async def list_contract_uploads(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    skip: int = Query(0, ge=0),
    contract_service: ContractService = Depends(get_contract_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await _authorize_contract_scope(
        policy,
        current_user,
        Permissions.DOCUMENT_VIEW,
        resource_type="contracts",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    return await contract_service.list_uploads(current_user, organization_id, project_id, limit, skip)


@router.post("/contracts/search", response_model=ContractSearchResponse)
@handle_exceptions
async def search_contracts(
    request: ContractSearchRequest,
    contract_service: ContractService = Depends(get_contract_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await _authorize_contract_scope(
        policy,
        current_user,
        Permissions.DOCUMENT_VIEW,
        resource_type="contracts",
        organization_id=request.organization_id,
        project_id=request.project_id,
        audit=False,
    )
    return await contract_service.search_contracts(request, current_user)


@router.get("/contracts/{document_id}/download")
@handle_exceptions
async def download_contract(
    document_id: str,
    contract_service: ContractService = Depends(get_contract_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    document = await contract_service.get_contract_document(document_id, current_user)
    await PolicyService().authorize_document(
        current_user,
        "dms.document.download",
        document,
        resource_type="contract",
    )
    file_object_service = FileObjectService()
    audit_service = DocumentAuditService()
    local_path = document.get("filepath_local")
    if local_path:
        path = Path(str(local_path))
        if path.exists() and path.is_file():
            file_object_service.assert_local_path_allowed(str(path))
            await audit_service.emit(
                resource_type="contract",
                resource_id=document_id,
                event_type="contract.downloaded",
                actor_id=getattr(current_user, "id", None),
                organization_id=document.get("organization_id"),
                project_id=document.get("project_id"),
                metadata={"provider": "local"},
            )
            return FileResponse(
                path=str(path),
                media_type=document.get("filetype") or "application/octet-stream",
                filename=document.get("filename") or "contract",
            )
    s3_key = document.get("filepath_s3")
    if s3_key:
        presigned = await S3Service().generate_presigned_url(str(s3_key), current_user)
        await audit_service.emit(
            resource_type="contract",
            resource_id=document_id,
            event_type="contract.downloaded",
            actor_id=getattr(current_user, "id", None),
            organization_id=document.get("organization_id"),
            project_id=document.get("project_id"),
            metadata={"provider": "s3"},
        )
        return Response(status_code=307, headers={"Location": presigned.get("url")})
    raise ContractError("File not available for download", status.HTTP_404_NOT_FOUND)
