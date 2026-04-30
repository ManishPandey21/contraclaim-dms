"""Contract upload, listing, search, and download routes."""

from __future__ import annotations

import shutil
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse, Response

from ..config.document_processing_config import DocumentProcessingConfig
from ..core.config import settings
from ..core.security import CurrentUser, get_current_user
from ..models.contract_models import (
    ChunkUploadResponse,
    ContractListResponse,
    ContractSearchRequest,
    ContractSearchResponse,
    ContractUploadSessionRequest,
    ContractUploadSessionResponse,
    StatusResponse,
    UploadMultipartResponse,
    UploadResult,
)
from ..models.storage_settings import StorageProviderConfig
from ..services.contract_ingest_queue import get_contract_ingest_queue
from ..services.contract_service import ContractService
from ..services.file_service import SecureFileService
from ..services.s3_service import S3Service
from ..services.storage_settings_service import StorageSettingsService
from ..utils.error_handler import ContractError, handle_exceptions
from ..utils.file_validation import sniff_mime_from_bytes
from ..utils.validation import sanitize_filename, validate_file

router = APIRouter()


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
) -> Dict[str, Any]:
    filepath_local = None
    filepath_s3 = None
    storage_locations: List[Dict[str, Any]] = []
    stored_name = _stored_filename(upload_id, safe_filename)
    path_prefix = _storage_path_prefix(organization_id, project_id, upload_id)
    try:
        resolved = await storage_settings.resolve_settings(organization_id, project_id)
    except Exception:
        resolved = None
    providers = (resolved.providers if resolved else None) or [StorageProviderConfig(id="local", enabled=True, primary=True)]
    providers = sorted(providers, key=lambda provider: (not provider.primary, provider.id))
    for provider in providers:
        if not provider.enabled:
            continue
        if provider.id == "local":
            local_path = await file_service.store_document(
                content,
                organization_id,
                project_id or "default",
                safe_filename,
                stored_filename=stored_name,
                path_structure=path_prefix,
            )
            filepath_local = filepath_local or local_path
            storage_locations.append({"provider": "local", "status": "ok"})
            continue
        if provider.id == "s3":
            key_prefix = provider.prefix or path_prefix
            object_key = f"{key_prefix.rstrip('/')}/{stored_name}"
            saved_key = await s3_service.upload_bytes(
                object_key,
                content,
                content_type=sniff_mime_from_bytes(content, safe_filename),
            )
            filepath_s3 = filepath_s3 or saved_key
            storage_locations.append({"provider": "s3", "status": "ok"})
            continue
        storage_locations.append({"provider": provider.id, "status": "skipped"})
    if not filepath_local and not filepath_s3:
        raise ContractError("Failed to store contract to any provider", status.HTTP_500_INTERNAL_SERVER_ERROR)
    return {
        "filepath_local": filepath_local,
        "filepath_s3": filepath_s3,
        "storage_locations": storage_locations,
    }


async def get_contract_service() -> ContractService:
    return ContractService()


async def get_file_service() -> SecureFileService:
    config = DocumentProcessingConfig()
    if settings.SECURE_UPLOADS_DIR:
        config.uploads_dir = settings.SECURE_UPLOADS_DIR
    return SecureFileService(config=config)


@router.post("/contracts/upload-session", response_model=ContractUploadSessionResponse)
@handle_exceptions
async def create_contract_upload_session(
    payload: ContractUploadSessionRequest,
    contract_service: ContractService = Depends(get_contract_service),
    current_user: CurrentUser = Depends(get_current_user),
):
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
):
    if not files:
        raise ContractError("At least one contract file is required", status.HTTP_422_UNPROCESSABLE_ENTITY)
    if upload_ids and len(upload_ids) not in (0, len(files)):
        raise ContractError("upload_ids must align with the number of files", status.HTTP_422_UNPROCESSABLE_ENTITY)

    max_file_size_bytes, _ = _contract_limits()
    storage_settings = StorageSettingsService()
    s3_service = S3Service()
    queue = get_contract_ingest_queue()
    results: List[UploadResult] = []
    effective_org: Optional[str] = None
    effective_project: Optional[str] = None

    for index, file in enumerate(files):
        if not file.filename:
            raise ContractError("File missing filename", status.HTTP_400_BAD_REQUEST)
        safe_filename = sanitize_filename(file.filename)
        content = await file.read()
        await file.seek(0)
        if len(content) > max_file_size_bytes:
            raise ContractError("Upload exceeds the maximum allowed file size", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        validation = await validate_file(content, safe_filename, settings.ALLOWED_CONTRACT_MIMES)
        if not validation.is_valid:
            raise ContractError(validation.error or "Invalid contract file", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

        upload_id = upload_ids[index] if upload_ids else None
        if not upload_id:
            session = await contract_service.create_upload_session(file.filename, organization_id, project_id, current_user)
            upload_id = session.upload_id
        session_doc = await contract_service.validate_upload_session(
            upload_id,
            current_user,
            organization_id,
            project_id,
            file.filename,
            file_size=len(content),
        )
        effective_org = str(session_doc.get("organization_id") or organization_id or "")
        effective_project = str(session_doc.get("project_id") or "") or None
        store_result = await _write_to_providers(
            content=content,
            organization_id=effective_org,
            project_id=effective_project,
            safe_filename=safe_filename,
            upload_id=upload_id,
            file_service=file_service,
            storage_settings=storage_settings,
            s3_service=s3_service,
        )
        document_id = await contract_service.create_contract_document(
            upload_id=upload_id,
            filename=file.filename,
            organization_id=effective_org,
            project_id=effective_project,
            tags=tags or [],
            file_size=len(content),
            file_bytes=content,
            filepath_local=store_result.get("filepath_local"),
            filepath_s3=store_result.get("filepath_s3"),
            storage_locations=store_result.get("storage_locations") or [],
            current_user=current_user,
        )
        await contract_service.complete_upload_session(upload_id, document_id)
        processing_path = _write_processing_copy(upload_id, safe_filename, content)
        queue_job_id = await queue.enqueue(
            {
                "upload_id": upload_id,
                "document_id": document_id,
                "organization_id": effective_org,
                "project_id": effective_project,
                "filename": file.filename,
                "tags": tags or [],
                "processing_path": str(processing_path),
            }
        )
        await contract_service.update_job_status(
            upload_id,
            "queued",
            metadata={
                "document_id": document_id,
                "filename": file.filename,
                "organization_id": effective_org,
                "project_id": effective_project,
                "tags": tags or [],
                "size": len(content),
                "queue_job_id": queue_job_id,
            },
        )
        results.append(UploadResult(upload_id=upload_id, document_id=document_id, filename=file.filename, status="queued"))

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
):
    max_file_size_bytes, max_chunk_size_bytes = _contract_limits()
    chunk_bytes = await chunk.read()
    await chunk.seek(0)
    if len(chunk_bytes) > max_chunk_size_bytes:
        raise ContractError("Chunk exceeds the maximum allowed chunk size", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

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
    safe_filename = sanitize_filename(filename)
    await file_service.store_chunk(
        chunk,
        upload_id,
        chunkIndex,
        safe_filename,
        organization_id=effective_org,
        user_id=current_user.id,
    )
    merged = False
    scheduled = False
    document_id: Optional[str] = None
    if chunkIndex == totalChunks - 1:
        final_path = await file_service.merge_chunks(
            upload_id,
            totalChunks,
            effective_org,
            effective_project,
            safe_filename,
            stored_filename=_stored_filename(upload_id, safe_filename),
            user_id=current_user.id,
        )
        merged_bytes = final_path.read_bytes()
        if len(merged_bytes) > max_file_size_bytes:
            await file_service.cleanup_upload(upload_id, organization_id=effective_org, user_id=current_user.id)
            raise ContractError("Upload exceeds the maximum allowed file size", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        validation = await validate_file(merged_bytes, safe_filename, settings.ALLOWED_CONTRACT_MIMES)
        if not validation.is_valid:
            await file_service.cleanup_upload(upload_id, organization_id=effective_org, user_id=current_user.id)
            raise ContractError(validation.error or "Invalid contract file", status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

        store_result = await _write_to_providers(
            content=merged_bytes,
            organization_id=effective_org,
            project_id=effective_project,
            safe_filename=safe_filename,
            upload_id=upload_id,
            file_service=file_service,
            storage_settings=StorageSettingsService(),
            s3_service=S3Service(),
        )
        document_id = await contract_service.create_contract_document(
            upload_id=upload_id,
            filename=filename,
            organization_id=effective_org,
            project_id=effective_project,
            tags=tags or [],
            file_size=len(merged_bytes),
            file_bytes=merged_bytes,
            filepath_local=store_result.get("filepath_local"),
            filepath_s3=store_result.get("filepath_s3"),
            storage_locations=store_result.get("storage_locations") or [],
            current_user=current_user,
        )
        await contract_service.complete_upload_session(upload_id, document_id)
        processing_path = _write_processing_copy(upload_id, safe_filename, merged_bytes)
        queue_job_id = await get_contract_ingest_queue().enqueue(
            {
                "upload_id": upload_id,
                "document_id": document_id,
                "organization_id": effective_org,
                "project_id": effective_project,
                "filename": filename,
                "tags": tags or [],
                "processing_path": str(processing_path),
            }
        )
        await contract_service.update_job_status(
            upload_id,
            "queued",
            metadata={
                "document_id": document_id,
                "filename": filename,
                "organization_id": effective_org,
                "project_id": effective_project,
                "tags": tags or [],
                "size": len(merged_bytes),
                "queue_job_id": queue_job_id,
            },
        )
        await file_service.cleanup_upload(upload_id, organization_id=effective_org, user_id=current_user.id)
        merged = True
        scheduled = True

    return ChunkUploadResponse(
        upload_id=upload_id,
        document_id=document_id,
        filename=filename,
        chunk_index=chunkIndex,
        total_chunks=totalChunks,
        received=True,
        merged=merged,
        scheduled=scheduled,
    )


@router.get("/contracts/status", response_model=StatusResponse)
@handle_exceptions
async def get_contract_status(
    upload_id: str = Query(...),
    contract_service: ContractService = Depends(get_contract_service),
    current_user: CurrentUser = Depends(get_current_user),
):
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
):
    return await contract_service.list_uploads(current_user, organization_id, project_id, limit, skip)


@router.post("/contracts/search", response_model=ContractSearchResponse)
@handle_exceptions
async def search_contracts(
    request: ContractSearchRequest,
    contract_service: ContractService = Depends(get_contract_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await contract_service.search_contracts(request, current_user)


@router.get("/contracts/{document_id}/download")
@handle_exceptions
async def download_contract(
    document_id: str,
    contract_service: ContractService = Depends(get_contract_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    document = await contract_service.get_contract_document(document_id, current_user)
    local_path = document.get("filepath_local")
    if local_path:
        path = Path(str(local_path))
        if path.exists() and path.is_file():
            return FileResponse(
                path=str(path),
                media_type=document.get("filetype") or "application/octet-stream",
                filename=document.get("filename") or "contract",
            )
    s3_key = document.get("filepath_s3")
    if s3_key:
        presigned = await S3Service().generate_presigned_url(str(s3_key), current_user)
        return Response(status_code=307, headers={"Location": presigned.get("url")})
    raise ContractError("File not available for download", status.HTTP_404_NOT_FOUND)
