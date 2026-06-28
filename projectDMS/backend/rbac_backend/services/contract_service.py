from __future__ import annotations

import logging
import re
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bson import ObjectId
from fastapi import status

from ..config.document_processing_config import DocumentProcessingConfig
from ..core.config import settings
from ..core.database import get_database
from ..core.security import CurrentUser, build_scope_query
from ..models.contract_models import (
    ContractClauseChunk,
    ContractListResponse,
    ContractSearchRequest,
    ContractSearchResponse,
    ContractSource,
    ContractUploadRecord,
    ContractUploadSessionResponse,
    StatusResponse,
)
from ..models.document import Document
from ..models.storage_architecture import ContractAggregate, ContractVersion
from ..retrieval.embeddings import EmbeddingClient
from ..retrieval.vector_client import VectorClient
from ..utils.error_handler import ContractError
from ..utils.file_validation import sniff_mime_from_bytes
from ..utils.validation import sanitize_filename
from .document_audit_service import DocumentAuditService
from .document_service import DocumentService, DocumentServiceError
from .file_object_service import FileObjectService
from .contract_graph_service import ContractGraphService
from .ocr_service import OCRService

logger = logging.getLogger(__name__)

CONTRACT_ALLOWED_EXTENSIONS = [".pdf", ".docx"]


class ContractService:
    def __init__(self) -> None:
        self._db = None
        self._jobs = None
        self._documents = None
        self._vectors = None
        self._sessions = None
        self._ingestor = None
        self._session_indexes_ready = False
        self._processing_config = DocumentProcessingConfig()
        self._embedding_client = EmbeddingClient(self._processing_config)
        self._vector_client = VectorClient(self._processing_config)
        self._document_service = DocumentService()
        self._file_object_service = FileObjectService()
        self._audit_service = DocumentAuditService()

    async def _get_db(self):
        if self._db is None:
            self._db = await get_database()
        return self._db

    async def _get_jobs(self):
        if self._jobs is None:
            self._jobs = (await self._get_db()).contract_ingest_jobs
        return self._jobs

    async def _get_documents(self):
        if self._documents is None:
            self._documents = (await self._get_db()).documents
        return self._documents

    async def _get_vectors(self):
        if self._vectors is None:
            self._vectors = (await self._get_db()).document_vectors
        return self._vectors

    async def _get_sessions(self):
        if self._sessions is None:
            self._sessions = (await self._get_db()).contract_upload_sessions
        if not self._session_indexes_ready:
            await self._sessions.create_index("upload_id", unique=True, background=True)
            await self._sessions.create_index("expiresAt", expireAfterSeconds=0, background=True)
            self._session_indexes_ready = True
        return self._sessions

    async def _get_ingestor(self):
        if self._ingestor is None:
            from .contracts_ingest import create_contract_ingestor

            self._ingestor = create_contract_ingestor(await self._get_db())
        return self._ingestor

    @staticmethod
    def _roles(current_user: CurrentUser) -> set[str]:
        return {str(role).lower() for role in (getattr(current_user, "roles", []) or [])}

    @staticmethod
    def _allowed_projects(current_user: CurrentUser) -> List[str]:
        return [str(project) for project in (getattr(current_user, "projects", []) or []) if project]

    def _resolve_scope(
        self,
        current_user: CurrentUser,
        organization_id: Optional[str],
        project_id: Optional[str],
        *,
        require_project_for_project_roles: bool = False,
    ) -> Tuple[Optional[str], Optional[str], Optional[List[str]]]:
        roles = self._roles(current_user)
        requested_org = str(organization_id) if organization_id else None
        requested_project = str(project_id) if project_id else None
        allowed_projects = self._allowed_projects(current_user)
        if "superadmin" in roles:
            return requested_org, requested_project, None

        user_org = str(getattr(current_user, "organization_id", "") or "")
        if not user_org:
            raise ContractError("No organization scope available for this user", status.HTTP_403_FORBIDDEN)
        if requested_org and requested_org != user_org:
            raise ContractError("Not authorized for this organization", status.HTTP_403_FORBIDDEN)

        is_org_role = bool({"orgadmin", "orguser"} & roles)
        is_project_role = bool({"projectadmin", "projectuser"} & roles)
        if requested_project:
            if allowed_projects and requested_project not in allowed_projects:
                raise ContractError("Not authorized for this project", status.HTTP_403_FORBIDDEN)
            return user_org, requested_project, None
        if allowed_projects:
            if require_project_for_project_roles and is_project_role:
                raise ContractError("Project selection is required", status.HTTP_422_UNPROCESSABLE_ENTITY)
            return user_org, None, allowed_projects
        if require_project_for_project_roles and is_project_role:
            raise ContractError("Project selection is required", status.HTTP_422_UNPROCESSABLE_ENTITY)
        if is_project_role:
            raise ContractError("No project scope available for this user", status.HTTP_403_FORBIDDEN)
        return user_org, None, None

    async def create_upload_session(
        self,
        filename: str,
        organization_id: Optional[str],
        project_id: Optional[str],
        current_user: CurrentUser,
    ) -> ContractUploadSessionResponse:
        safe_filename = sanitize_filename(filename)
        extension = Path(safe_filename).suffix.lower()
        if extension not in CONTRACT_ALLOWED_EXTENSIONS:
            raise ContractError(
                "Unsupported contract format. Only PDF and DOCX are allowed.",
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )
        effective_org, effective_project, _ = self._resolve_scope(
            current_user, organization_id, project_id, require_project_for_project_roles=True
        )
        if not effective_org:
            raise ContractError("Organization selection is required", status.HTTP_422_UNPROCESSABLE_ENTITY)

        sessions = await self._get_sessions()
        active_count = await sessions.count_documents(
            {
                "user_id": current_user.id,
                "status": {"$in": ["open", "uploading"]},
                "expiresAt": {"$gt": datetime.utcnow()},
            }
        )
        if active_count >= max(1, int(settings.CONTRACT_UPLOAD_MAX_CONCURRENT_SESSIONS)):
            raise ContractError("Too many active contract upload sessions", status.HTTP_429_TOO_MANY_REQUESTS)

        upload_id = str(ObjectId())
        max_file_size_bytes = max(1, int(settings.CONTRACT_UPLOAD_MAX_FILE_SIZE_MB)) * 1024 * 1024
        max_chunk_size_bytes = max(1, int(settings.CONTRACT_UPLOAD_MAX_CHUNK_SIZE_MB)) * 1024 * 1024
        max_chunks = max(1, (max_file_size_bytes + max_chunk_size_bytes - 1) // max_chunk_size_bytes)
        expires_at = datetime.utcnow() + timedelta(seconds=max(60, int(settings.CONTRACT_UPLOAD_SESSION_TTL_SECONDS)))
        await sessions.insert_one(
            {
                "upload_id": upload_id,
                "filename": filename,
                "safe_filename": safe_filename,
                "organization_id": effective_org,
                "project_id": effective_project or "",
                "user_id": current_user.id,
                "status": "open",
                "createdAt": datetime.utcnow(),
                "updatedAt": datetime.utcnow(),
                "expiresAt": expires_at,
                "max_file_size_bytes": max_file_size_bytes,
                "max_chunk_size_bytes": max_chunk_size_bytes,
                "max_chunks": max_chunks,
                "received_chunks": [],
                "missing_chunks": [],
                "chunk_checksums": {},
            }
        )
        return ContractUploadSessionResponse(
            upload_id=upload_id,
            organization_id=effective_org,
            project_id=effective_project or None,
            expires_at=expires_at,
            max_file_size_bytes=max_file_size_bytes,
            max_chunk_size_bytes=max_chunk_size_bytes,
            max_chunks=max_chunks,
            allowed_extensions=CONTRACT_ALLOWED_EXTENSIONS,
            allowed_mime_types=sorted(settings.ALLOWED_CONTRACT_MIMES),
        )

    async def validate_upload_session(
        self,
        upload_id: str,
        current_user: CurrentUser,
        organization_id: Optional[str],
        project_id: Optional[str],
        filename: str,
        *,
        total_chunks: Optional[int] = None,
        file_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        sessions = await self._get_sessions()
        session = await sessions.find_one({"upload_id": upload_id})
        if not session:
            raise ContractError("Upload session not found or expired", status.HTTP_404_NOT_FOUND)
        if session.get("user_id") != current_user.id and "superadmin" not in self._roles(current_user):
            raise ContractError("Not authorized for this upload session", status.HTTP_403_FORBIDDEN)

        effective_org, effective_project, _ = self._resolve_scope(
            current_user,
            organization_id or session.get("organization_id"),
            project_id if project_id is not None else session.get("project_id") or None,
            require_project_for_project_roles=True,
        )
        if effective_org != session.get("organization_id") or (effective_project or "") != str(session.get("project_id") or ""):
            raise ContractError("Upload session scope mismatch", status.HTTP_403_FORBIDDEN)
        if sanitize_filename(filename) != session.get("safe_filename"):
            raise ContractError("Upload filename does not match the session", status.HTTP_400_BAD_REQUEST)
        if session.get("expiresAt") and session["expiresAt"] < datetime.utcnow():
            raise ContractError("Upload session expired", status.HTTP_410_GONE)
        if total_chunks is not None and total_chunks > int(session.get("max_chunks") or 0):
            raise ContractError("Upload exceeds the maximum allowed chunk count", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        if file_size is not None and file_size > int(session.get("max_file_size_bytes") or 0):
            raise ContractError("Upload exceeds the maximum allowed file size", status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

        if not session.get("document_id"):
            await sessions.update_one({"upload_id": upload_id}, {"$set": {"status": "uploading", "updatedAt": datetime.utcnow()}})
        session["organization_id"] = effective_org
        session["project_id"] = effective_project or ""
        return session

    async def complete_upload_session(self, upload_id: str, document_id: str) -> None:
        await (await self._get_sessions()).update_one(
            {"upload_id": upload_id},
            {"$set": {"status": "queued", "document_id": document_id, "updatedAt": datetime.utcnow()}},
        )

    async def create_contract_document(
        self,
        *,
        upload_id: str,
        filename: str,
        organization_id: str,
        project_id: Optional[str],
        tags: List[str],
        file_size: int,
        file_bytes: bytes,
        filepath_local: Optional[str],
        filepath_s3: Optional[str],
        storage_locations: List[Dict[str, Any]],
        current_user: CurrentUser,
        file_object_id: Optional[str] = None,
        storage_key: Optional[str] = None,
        sha256: Optional[str] = None,
    ) -> str:
        try:
            document = Document(
                _id=str(ObjectId()),
                organization_id=organization_id,
                project_id=project_id or "",
                filename=filename,
                filepath_local=filepath_local,
                filepath_s3=filepath_s3,
                presigned_url=None,
                filetype=sniff_mime_from_bytes(file_bytes, filename),
                filesize=file_size,
                uploadType="contract",
                date=datetime.utcnow(),
                subject=filename,
                tags=tags,
                subTags=[],
                status="queued",
                ocrEnabled=Path(filename).suffix.lower() == ".pdf",
                compressionEnabled=False,
                createdBy=current_user.id,
                storage_locations=storage_locations,
                contract_upload_id=upload_id,
                contract_categories=[],
                contract_error=None,
            )
            created = await self._document_service.create_document(document=document)
            document_id = str(created.id)
            await self._attach_contract_storage_records(
                document_id=document_id,
                upload_id=upload_id,
                filename=filename,
                organization_id=organization_id,
                project_id=project_id,
                current_user=current_user,
                file_object_id=file_object_id,
                storage_key=storage_key,
                sha256=sha256,
                metadata_snapshot=document.model_dump(by_alias=True, exclude_none=True),
            )
            await self._audit_service.emit(
                resource_type="contract",
                resource_id=document_id,
                event_type="contract.created",
                actor_id=getattr(current_user, "id", None),
                organization_id=organization_id,
                project_id=project_id,
                metadata={
                    "upload_id": upload_id,
                    "file_object_id": file_object_id,
                    "storage_key": storage_key,
                    "sha256": sha256,
                },
            )
            return str(created.id)
        except DocumentServiceError as exc:
            raise ContractError(str(exc), status.HTTP_500_INTERNAL_SERVER_ERROR) from exc

    async def _attach_contract_storage_records(
        self,
        *,
        document_id: str,
        upload_id: str,
        filename: str,
        organization_id: str,
        project_id: Optional[str],
        current_user: CurrentUser,
        file_object_id: Optional[str],
        storage_key: Optional[str],
        sha256: Optional[str],
        metadata_snapshot: Dict[str, Any],
    ) -> None:
        db = await self._get_db()
        contract = ContractAggregate(
            organization_id=organization_id,
            project_id=project_id or "",
            title=filename,
            document_ids=[document_id],
            created_by=getattr(current_user, "id", None),
        )
        contract_result = await db.contracts.insert_one(contract.model_dump(by_alias=True))
        contract_id = str(contract_result.inserted_id)
        version = ContractVersion(
            contract_id=contract_id,
            document_id=document_id,
            upload_id=upload_id,
            version_number=1,
            file_object_id=file_object_id,
            sha256=sha256,
            filename=filename,
            created_by=getattr(current_user, "id", None),
        )
        version_result = await db.contract_versions.insert_one(version.model_dump(by_alias=True))
        contract_version_id = str(version_result.inserted_id)
        await db.contracts.update_one(
            {"_id": contract_id},
            {"$set": {"current_version_id": contract_version_id, "updatedAt": datetime.utcnow()}},
        )
        document_version_id = await self._file_object_service.attach_document_version(
            document_id=document_id,
            file_object_id=file_object_id,
            metadata_snapshot=metadata_snapshot,
            current_user=current_user,
            reason="contract_upload",
        )
        await db.documents.update_one(
            {"_id": self._as_lookup_id(document_id)},
            {
                "$set": {
                    "contract_id": contract_id,
                    "contract_version_id": contract_version_id,
                    "file_object_id": file_object_id,
                    "current_version_id": document_version_id,
                    "storage_key": storage_key,
                    "sha256": sha256,
                    "lifecycle_state": "active",
                }
            },
        )

    async def update_contract_document(
        self,
        document_id: str,
        *,
        status_value: str,
        categories: Optional[List[str]] = None,
        error: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        update_fields: Dict[str, Any] = {
            "status": status_value,
            "updatedAt": datetime.utcnow(),
            "contract_error": self._sanitize_error(error),
        }
        if categories is not None:
            update_fields["contract_categories"] = categories
        if tags is not None:
            update_fields["tags"] = tags
        await (await self._get_documents()).update_one(
            {"_id": self._as_lookup_id(document_id)},
            {"$set": update_fields},
        )
        try:
            await (await self._get_db()).contract_versions.update_one(
                {"document_id": document_id},
                {
                    "$set": {
                        "status": status_value,
                        "ingestion_status": status_value,
                        "updatedAt": datetime.utcnow(),
                    }
                },
            )
        except Exception:
            logger.warning("Failed to update contract version status for %s", document_id, exc_info=True)

    async def update_job_status(
        self,
        upload_id: str,
        status_value: str,
        *,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = datetime.utcnow()
        set_fields: Dict[str, Any] = {
            "status": status_value,
            "updatedAt": now,
            "error": self._sanitize_error(error),
        }
        if metadata:
            set_fields.update({key: value for key, value in metadata.items() if value is not None and key != "upload_id"})
        await (await self._get_jobs()).update_one(
            {"upload_id": upload_id},
            {"$set": set_fields, "$setOnInsert": {"upload_id": upload_id, "createdAt": now}},
            upsert=True,
        )

    async def ingest_contract(
        self,
        file_path: Path,
        organization_id: str,
        project_id: Optional[str],
        filename: str,
        tags: List[str],
        upload_id: str,
        document_id: str,
    ) -> Dict[str, Any]:
        return await (await self._get_ingestor()).ingest_file(
            organization_id=organization_id,
            project_id=project_id,
            file_path=str(file_path),
            filename=filename,
            tags=tags,
            upload_id=upload_id,
            document_id=document_id,
        )

    async def process_ingest_job(self, payload: Dict[str, Any]) -> None:
        upload_id = str(payload.get("upload_id") or "")
        document_id = str(payload.get("document_id") or "")
        organization_id = str(payload.get("organization_id") or "")
        project_id = str(payload.get("project_id") or "") or None
        filename = str(payload.get("filename") or "")
        tags = [str(tag) for tag in (payload.get("tags") or []) if tag]
        file_object_id = str(payload.get("file_object_id") or "")
        processing_path_value = str(payload.get("processing_path") or "")
        if not upload_id or not document_id or not organization_id or not filename:
            raise ContractError("Invalid contract ingest payload", status.HTTP_500_INTERNAL_SERVER_ERROR)
        materialized_temp = False
        if file_object_id:
            processing_path = await self._file_object_service.materialize_to_temp(
                file_object_id,
                suffix=Path(filename).suffix,
            )
            materialized_temp = True
        else:
            processing_path = Path(processing_path_value).resolve()
        if not processing_path.exists():
            raise ContractError("Contract processing file is missing", status.HTTP_404_NOT_FOUND)

        await self.update_job_status(
            upload_id,
            "processing",
            metadata={
                "document_id": document_id,
                "filename": filename,
                "processing_stage": "materializing",
                "stage_label": "Preparing file for processing",
                "progress": 10,
            },
        )
        await self.update_contract_document(document_id, status_value="processing", tags=tags)
        processed_path = processing_path
        try:
            await self.update_job_status(
                upload_id,
                "processing",
                metadata={
                    "document_id": document_id,
                    "filename": filename,
                    "processing_stage": "ocr",
                    "stage_label": "Running OCR / text preparation",
                    "progress": 20,
                },
            )
            processed_path = await OCRService(self._processing_config).process_document(processing_path, language="eng")
            await self.update_job_status(
                upload_id,
                "processing",
                metadata={
                    "document_id": document_id,
                    "filename": filename,
                    "processing_stage": "ingestion",
                    "stage_label": "Extracting clauses and indexing vectors",
                    "progress": 35,
                },
            )
            result = await self.ingest_contract(processed_path, organization_id, project_id, filename, tags, upload_id, document_id)
            categories = result.get("categories") or []
            await self.update_job_status(
                upload_id,
                "completed",
                metadata={
                    "document_id": document_id,
                    "filename": filename,
                    "categories": categories,
                    "processing_stage": "completed",
                    "stage_label": "Processing complete",
                    "progress": 100,
                },
            )
            await self.update_contract_document(document_id, status_value="completed", categories=categories, tags=tags)
        except Exception as exc:
            await self.update_job_status(
                upload_id,
                "failed",
                error=str(exc),
                metadata={
                    "document_id": document_id,
                    "filename": filename,
                    "processing_stage": "failed",
                    "stage_label": "Processing failed",
                },
            )
            await self.update_contract_document(document_id, status_value="failed", error=str(exc), tags=tags)
            raise
        finally:
            if materialized_temp or processing_path_value:
                self._cleanup_tmp_file(processing_path)
            if processed_path != processing_path:
                self._cleanup_tmp_file(processed_path)

    async def get_job_status(self, upload_id: str, current_user: CurrentUser) -> StatusResponse:
        doc = await (await self._get_jobs()).find_one({"upload_id": upload_id})
        if not doc:
            raise ContractError("Upload job not found", status.HTTP_404_NOT_FOUND)
        self._authorize_scope(doc, current_user)
        return StatusResponse(**self._normalize_job(doc))

    async def list_uploads(
        self,
        current_user: CurrentUser,
        organization_id: Optional[str],
        project_id: Optional[str],
        limit: int,
        skip: int,
    ) -> ContractListResponse:
        query = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
        query["uploadType"] = "contract"
        query["lifecycle_state"] = {"$ne": "deleted"}
        rows = await (
            (await self._get_documents())
            .find(query)
            .sort([("createdAt", -1), ("updatedAt", -1)])
            .skip(skip)
            .limit(limit)
            .to_list(length=limit)
        )
        total = await (await self._get_documents()).count_documents(query)
        return ContractListResponse(uploads=[self._normalize_contract_record(row) for row in rows], count=total)

    async def get_contract_document(self, document_id: str, current_user: CurrentUser) -> Dict[str, Any]:
        document = await (await self._get_documents()).find_one({"_id": self._as_lookup_id(document_id)})
        if (
            not document
            or str(document.get("uploadType") or "").lower() != "contract"
            or document.get("lifecycle_state") == "deleted"
        ):
            raise ContractError("Contract document not found", status.HTTP_404_NOT_FOUND)
        self._authorize_scope(document, current_user)
        return document

    def _hybrid_enabled(self, request: ContractSearchRequest) -> bool:
        return bool((request.query or "").strip() and self._processing_config.qdrant_enabled and self._vector_client.enabled)

    def _build_structured_search_filters(self, match_stage: Dict[str, Any], request: ContractSearchRequest) -> None:
        def add_regex(field: str, value: Optional[str]) -> None:
            cleaned = (value or "").strip()
            if cleaned:
                match_stage[field] = {"$regex": re.escape(cleaned), "$options": "i"}

        add_regex("clause_number", request.clause_number)
        add_regex("clause_title", request.clause_title)
        add_regex("section_heading", request.section_heading)
        if request.clause_tags:
            match_stage["clause_tags"] = {"$all": [tag for tag in request.clause_tags if tag]}

        page_bounds: Dict[str, int] = {}
        if request.page_from:
            page_bounds["$gte"] = request.page_from
        if request.page_to:
            page_bounds["$lte"] = request.page_to
        if page_bounds:
            match_stage.setdefault("$and", []).append(
                {
                    "$or": [
                        {"page_numbers": {"$elemMatch": page_bounds}},
                        {"page_number": page_bounds},
                        {"page": page_bounds},
                    ]
                }
            )

    def _build_regex(self, match_stage: Dict[str, Any], text_query: str, exact_phrase: bool = False) -> Optional[str]:
        quoted_phrases = re.findall(r'"([^"]+)"', text_query)
        unquoted_query = re.sub(r'"[^"]+"', " ", text_query).strip()
        if exact_phrase and not quoted_phrases and len(text_query.split()) > 1:
            quoted_phrases = [text_query]
            unquoted_query = ""

        def term_pattern(term: str) -> str:
            escaped = re.escape(term)
            return rf"\b{escaped}\b" if re.fullmatch(r"\w+", term) else escaped

        phrase_filters: List[Dict[str, Any]] = []
        phrase_regexes: List[str] = []
        for phrase in quoted_phrases:
            parts = [part.strip(",.;:!?()[]{}\"") for part in re.split(r"\s+", phrase.strip()) if part]
            if not parts:
                continue
            pattern = r"\W+".join(term_pattern(part) for part in parts if part)
            if not pattern:
                continue
            phrase_regexes.append(pattern)
            phrase_filters.append({"text": {"$regex": pattern, "$options": "i"}})
        if phrase_filters:
            match_stage.setdefault("$and", []).extend(phrase_filters)
            return "|".join(f"(?:{pattern})" for pattern in phrase_regexes)

        stopwords = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it", "of", "on", "or", "per", "the", "this", "that", "these", "those", "to", "upon", "was", "were", "with", "without"}
        escaped_terms: List[str] = []
        seen: set[str] = set()
        for term in [term for term in re.split(r"\s+", unquoted_query) if term and term.strip()]:
            cleaned = term.strip().strip(",.;:!?()[]{}\"")
            lowered = cleaned.lower()
            if not cleaned or len(lowered) < 3 or lowered in stopwords or lowered in seen:
                continue
            seen.add(lowered)
            escaped_terms.append(re.escape(cleaned))
        regex = "|".join(escaped_terms) if escaped_terms else re.escape(unquoted_query.strip() or text_query)
        if regex:
            match_stage.setdefault("$and", []).append(
                {
                    "$or": [
                        {"text_enriched": {"$regex": regex, "$options": "i"}},
                        {"text": {"$regex": regex, "$options": "i"}},
                    ]
                }
            )
        return regex

    async def search_contracts(self, request: ContractSearchRequest, current_user: CurrentUser) -> ContractSearchResponse:
        query_text = (request.query or "").strip()
        if not query_text:
            raise ContractError("Search query is required", status.HTTP_422_UNPROCESSABLE_ENTITY)
        effective_org, effective_project, project_filters = self._resolve_scope(current_user, request.organization_id, request.project_id)
        collection = await self._get_vectors()
        start_time = time.perf_counter()
        match_stage: Dict[str, Any] = {"uploadType": "contract"}
        if effective_org:
            match_stage["organization_id"] = effective_org
        if effective_project:
            match_stage["project_id"] = effective_project
        elif project_filters:
            match_stage["project_id"] = {"$in": project_filters}
        if request.document_id:
            match_stage["$or"] = [{"document_id": request.document_id}, {"upload_id": request.document_id}]
        if request.tags:
            match_stage["tags"] = {"$all": request.tags}
        self._build_structured_search_filters(match_stage, request)

        regex = self._build_regex(match_stage, query_text, exact_phrase=request.exact_phrase)
        page_size, skip_count = request.limit, request.skip
        candidate_limit = max(page_size * 5, page_size + skip_count + 10)
        lexical_candidates = await self._lexical_candidates(collection, match_stage, regex, candidate_limit, request.category_terms)
        vector_candidates = await self._vector_candidates(request, effective_org, effective_project, project_filters, candidate_limit)
        graph_candidates = await self._graph_candidates(request, effective_org, effective_project, candidate_limit)
        normalized, total_count, has_more = await self._assemble_results(collection, lexical_candidates, vector_candidates, graph_candidates, page_size, skip_count)
        normalized = self._rerank_contract_chunks(normalized, request)
        summary, summary_title = self._build_summary(normalized) if request.summarize else (None, None)
        return ContractSearchResponse(
            results=[ContractClauseChunk(**item) for item in normalized],
            summary=summary,
            ai_summary_title=summary_title,
            took_ms=int((time.perf_counter() - start_time) * 1000),
            total_count=total_count,
            has_more=has_more,
            current_page=skip_count // page_size + 1,
            page_size=page_size,
            sources=[ContractSource(**item) for item in self._build_sources(normalized)],
        )

    async def _lexical_candidates(self, collection, match_stage: Dict[str, Any], regex: Optional[str], candidate_limit: int, category_terms: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        pipeline: List[Dict[str, Any]] = [{"$match": match_stage}]
        category_regex = self._build_category_regex(category_terms or [])
        text_input = {"$ifNull": ["$text_enriched", {"$ifNull": ["$text", ""]}]}
        if regex:
            add_fields: Dict[str, Any] = {
                "match_score": {"$size": {"$regexFindAll": {"input": text_input, "regex": regex, "options": "i"}}},
            }
            if category_regex:
                add_fields["category_score"] = {"$size": {"$regexFindAll": {"input": text_input, "regex": category_regex, "options": "i"}}}
            else:
                add_fields["category_score"] = 0
            pipeline.extend(
                [
                    {"$addFields": add_fields},
                    {"$match": {"match_score": {"$gt": 0}}},
                    {"$addFields": {"combined_match_score": {"$add": ["$match_score", {"$multiply": ["$category_score", 0.35]}]}}},
                ]
            )
        else:
            pipeline.append({"$addFields": {"match_score": 0.0, "combined_match_score": 0.0}})
        pipeline.extend(
            [
                {"$group": {"_id": {"document_id": "$document_id", "clause_number": "$clause_number", "clause_start": "$clause_start_position"}, "best_score": {"$max": "$combined_match_score"}, "document_id": {"$first": "$document_id"}, "upload_id": {"$first": "$upload_id"}, "clause_number": {"$first": "$clause_number"}, "clause_start_position": {"$first": "$clause_start_position"}, "createdAt": {"$first": "$createdAt"}}},
                {"$sort": {"best_score": -1, "clause_start_position": 1, "createdAt": -1}},
                {"$limit": candidate_limit},
            ]
        )
        try:
            return await collection.aggregate(pipeline).to_list(length=candidate_limit)
        except Exception as exc:
            logger.warning("Contract lexical search failed: %s", exc)
            return []

    async def _vector_candidates(self, request: ContractSearchRequest, effective_org: Optional[str], effective_project: Optional[str], project_filters: Optional[List[str]], candidate_limit: int) -> List[Dict[str, Any]]:
        if not self._hybrid_enabled(request):
            return []
        try:
            vector_query = " ".join([request.query.strip(), *[term.strip() for term in request.category_terms[:20] if term.strip()]])
            embeddings = await self._embedding_client.embed([vector_query])
            query_vector = embeddings[0] if embeddings else []
            if not query_vector:
                return []
            filters: Dict[str, Any] = {
                "org_id": effective_org,
                "project_id": [effective_project] if effective_project else project_filters,
                "document_id": request.document_id,
                "tags": request.tags or None,
                "uploadType": "contract",
            }
            results = await self._vector_client.search(
                query_vector,
                filters=filters,
                limit=candidate_limit,
            )
            return [item for item in results if self._payload_matches_structured_filters(item.get("payload") or {}, request)]
        except Exception as exc:
            logger.warning("Contract vector search failed: %s", exc)
            return []

    async def _graph_candidates(
        self,
        request: ContractSearchRequest,
        effective_org: Optional[str],
        effective_project: Optional[str],
        candidate_limit: int,
    ) -> List[Dict[str, Any]]:
        if not effective_org or not effective_project:
            return []
        clause_numbers = self._extract_clause_numbers_for_graph(request)
        if not clause_numbers:
            return []
        try:
            rows = ContractGraphService().find_related_clauses(
                organization_id=effective_org,
                project_id=effective_project,
                seed_clause_numbers=clause_numbers,
                seed_document_ids=[request.document_id] if request.document_id else None,
                limit=candidate_limit,
            )
        except Exception as exc:
            logger.debug("Contract graph candidate expansion failed: %s", exc)
            return []
        return [
            {
                "document_id": self._coerce_id(row.get("document_id")),
                "clause_number": row.get("clause_number"),
                "clause_start_position": row.get("clause_start_position"),
                "score": 0.75 if row.get("graph_relation") == "same_clause_number" else 0.45,
                "graph_relation": row.get("graph_relation"),
            }
            for row in rows
            if row.get("document_id") and row.get("clause_number")
        ]

    def _extract_clause_numbers_for_graph(self, request: ContractSearchRequest) -> List[str]:
        values: List[str] = []
        if request.clause_number:
            values.append(request.clause_number)
        for match in re.finditer(r"\b(?:GCC|SCC|Clause)\s*([0-9A-Za-z._-]+)", request.query or "", flags=re.I):
            values.append(match.group(1))
        # For exact searches like "8.4 extension of time", use the leading
        # clause-like token as a graph seed without treating every number as one.
        leading = re.match(r"^\s*([0-9]+(?:\.[0-9A-Za-z]+)+)\b", request.query or "")
        if leading:
            values.append(leading.group(1))
        seen: set[str] = set()
        out: List[str] = []
        for value in values:
            cleaned = str(value or "").strip()
            key = cleaned.lower()
            if cleaned and key not in seen:
                seen.add(key)
                out.append(cleaned)
        return out[:10]

    def _payload_matches_structured_filters(self, payload: Dict[str, Any], request: ContractSearchRequest) -> bool:
        def contains(field: str, value: Optional[str]) -> bool:
            needle = (value or "").strip().lower()
            if not needle:
                return True
            return needle in str(payload.get(field) or "").lower()

        if not contains("clause_number", request.clause_number):
            return False
        if not contains("clause_title", request.clause_title):
            return False
        if not contains("section_heading", request.section_heading):
            return False
        if request.clause_tags:
            actual_tags = payload.get("clause_tags") or []
            if not isinstance(actual_tags, list):
                actual_tags = [actual_tags]
            actual = {str(tag).lower() for tag in actual_tags}
            expected = {tag.lower() for tag in request.clause_tags if tag}
            if expected and not expected.issubset(actual):
                return False
        if request.page_from or request.page_to:
            pages = payload.get("page_numbers") or payload.get("page") or []
            if not isinstance(pages, list):
                pages = [pages]
            low = request.page_from or 1
            high = request.page_to or 10**9
            page_values = [int(page) for page in pages if isinstance(page, (int, float)) or str(page).isdigit()]
            if not any(low <= page <= high for page in page_values):
                return False
        return True

    async def _assemble_results(
        self,
        collection,
        lexical_candidates: List[Dict[str, Any]],
        vector_candidates: List[Dict[str, Any]],
        graph_candidates: Optional[List[Dict[str, Any]]],
        page_size: int,
        skip_count: int,
    ) -> Tuple[List[Dict[str, Any]], int, bool]:
        def clause_key(document_id: Optional[str], clause_number: Optional[str], clause_start: Optional[Any]) -> str:
            return f"{document_id or ''}::{clause_number or ''}::{clause_start or 0}"

        key_meta: Dict[str, Dict[str, Any]] = {}
        lexical_ranked: List[Dict[str, Any]] = []
        for item in lexical_candidates:
            key = clause_key(self._coerce_id(item.get("document_id")), item.get("clause_number"), item.get("clause_start_position"))
            lexical_ranked.append({"key": key, "score": item.get("best_score") or 0.0})
            key_meta.setdefault(key, {"document_id": self._coerce_id(item.get("document_id")), "upload_id": self._coerce_id(item.get("upload_id")), "clause_number": item.get("clause_number"), "clause_start_position": item.get("clause_start_position")})

        vector_ranked: List[Dict[str, Any]] = []
        for item in vector_candidates:
            payload = item.get("payload") or {}
            document_id = self._coerce_id(payload.get("document_id"))
            clause_number = payload.get("clause_number")
            clause_start = payload.get("clause_start_position")
            if not document_id or not clause_number:
                continue
            key = clause_key(document_id, clause_number, clause_start)
            vector_ranked.append({"key": key, "score": item.get("score") or 0.0})
            key_meta.setdefault(key, {"document_id": document_id, "upload_id": self._coerce_id(payload.get("upload_id")), "clause_number": clause_number, "clause_start_position": clause_start})

        graph_ranked: List[Dict[str, Any]] = []
        for item in graph_candidates or []:
            document_id = self._coerce_id(item.get("document_id"))
            clause_number = item.get("clause_number")
            clause_start = item.get("clause_start_position")
            if not document_id or not clause_number:
                continue
            key = clause_key(document_id, clause_number, clause_start)
            graph_ranked.append({"key": key, "score": item.get("score") or 0.0})
            key_meta.setdefault(
                key,
                {
                    "document_id": document_id,
                    "upload_id": self._coerce_id(item.get("upload_id")),
                    "clause_number": clause_number,
                    "clause_start_position": clause_start,
                    "graph_relation": item.get("graph_relation"),
                },
            )

        def rrf(items: Sequence[Dict[str, Any]]) -> Dict[str, float]:
            scores: Dict[str, float] = {}
            for rank, item in enumerate(sorted(items, key=lambda value: value.get("score", 0), reverse=True), start=1):
                scores[item["key"]] = scores.get(item["key"], 0.0) + 1.0 / (60 + rank)
            return scores

        combined_scores: Dict[str, float] = {}
        for key, score in rrf(lexical_ranked).items():
            combined_scores[key] = combined_scores.get(key, 0.0) + score
        for key, score in rrf(vector_ranked).items():
            combined_scores[key] = combined_scores.get(key, 0.0) + score
        for key, score in rrf(graph_ranked).items():
            combined_scores[key] = combined_scores.get(key, 0.0) + (score * 0.8)
        ranked_keys = sorted(combined_scores.keys(), key=lambda key: (-combined_scores.get(key, 0.0), key_meta.get(key, {}).get("clause_start_position") or 0))
        total_count = len(ranked_keys)
        page_keys = ranked_keys[skip_count : skip_count + page_size]
        has_more = total_count > skip_count + page_size
        if not page_keys:
            return [], total_count, has_more

        clause_filters = []
        for key in page_keys:
            meta = key_meta.get(key) or {}
            if not meta.get("document_id") or not meta.get("clause_number"):
                continue
            clause_filter = {"uploadType": "contract", "document_id": meta["document_id"], "clause_number": meta["clause_number"]}
            if meta.get("clause_start_position") is not None:
                clause_filter["clause_start_position"] = meta["clause_start_position"]
            clause_filters.append(clause_filter)

        docs = await collection.find({"$or": clause_filters}).sort([("chunk_index", 1)]).to_list(length=None)
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for doc in docs:
            doc_id = self._coerce_id(doc.get("document_id"))
            clause_number = doc.get("clause_number")
            grouped.setdefault(clause_key(doc_id, clause_number, doc.get("clause_start_position")), []).append(doc)
            grouped.setdefault(clause_key(doc_id, clause_number, None), []).append(doc)

        normalized: List[Dict[str, Any]] = []
        for key in page_keys:
            chunks = grouped.get(key, [])
            chunks.sort(key=lambda chunk: chunk.get("chunk_index") or 0)
            for chunk in chunks:
                chunk["score"] = combined_scores.get(key, 0.0)
                normalized.append(self._normalize_vector_chunk(chunk))
        return normalized, total_count, has_more

    def _build_category_regex(self, category_terms: Sequence[str]) -> Optional[str]:
        terms: List[str] = []
        seen: set[str] = set()
        for term in category_terms:
            cleaned = re.sub(r"\s+", " ", str(term or "").strip())
            lowered = cleaned.lower()
            if not cleaned or len(cleaned) < 3 or lowered in seen:
                continue
            seen.add(lowered)
            terms.append(re.escape(cleaned))
            if len(terms) >= 30:
                break
        return "|".join(terms) if terms else None

    def _rerank_contract_chunks(self, normalized: Sequence[Dict[str, Any]], request: ContractSearchRequest) -> List[Dict[str, Any]]:
        if not normalized:
            return []

        query_terms = self._extract_query_terms(request.query)
        phrase = request.query.strip().strip('"').lower() if request.exact_phrase else ""
        category_terms = [str(term).lower() for term in request.category_terms if str(term).strip()]
        clause_number = (request.clause_number or "").lower()
        clause_title = (request.clause_title or "").lower()
        section_heading = (request.section_heading or "").lower()

        def key_for(chunk: Dict[str, Any]) -> str:
            return f"{chunk.get('document_id') or ''}::{chunk.get('clause_number') or ''}::{chunk.get('clause_start_position') or 0}"

        def chunk_score(chunk: Dict[str, Any]) -> float:
            base = float(chunk.get("score") or 0.0) * 100.0
            text = " ".join(
                str(chunk.get(field) or "")
                for field in ("text_enriched", "text", "clause_title", "section_heading")
            ).lower()
            for term in query_terms:
                if term in text:
                    base += min(text.count(term), 5) * 0.35
            if phrase and phrase in text:
                base += 2.5
            for term in category_terms:
                if term in text:
                    base += 0.12
            if clause_number and clause_number in str(chunk.get("clause_number") or "").lower():
                base += 3.0
            if clause_title and clause_title in str(chunk.get("clause_title") or "").lower():
                base += 2.0
            if section_heading and section_heading in str(chunk.get("section_heading") or "").lower():
                base += 1.5
            if request.page_from or request.page_to:
                pages = chunk.get("page_numbers") or []
                if not isinstance(pages, list):
                    pages = [pages]
                low = request.page_from or 1
                high = request.page_to or 10**9
                if any(isinstance(page, int) and low <= page <= high for page in pages):
                    base += 0.75
            return base

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        scores: Dict[str, float] = {}
        for item in normalized:
            key = key_for(item)
            grouped.setdefault(key, []).append(dict(item))
            scores[key] = max(scores.get(key, 0.0), chunk_score(item))

        ordered_keys = sorted(
            grouped.keys(),
            key=lambda key: (
                -scores.get(key, 0.0),
                grouped[key][0].get("clause_start_position") or 0,
                grouped[key][0].get("chunk_index") or 0,
            ),
        )
        reranked: List[Dict[str, Any]] = []
        for key in ordered_keys:
            for chunk in sorted(grouped[key], key=lambda item: item.get("chunk_index") or 0):
                chunk["score"] = scores.get(key, float(chunk.get("score") or 0.0))
                reranked.append(chunk)
        return reranked

    def _extract_query_terms(self, query: str) -> List[str]:
        stopwords = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it", "of", "on", "or", "per", "the", "this", "that", "these", "those", "to", "upon", "was", "were", "with", "without"}
        terms: List[str] = []
        seen: set[str] = set()
        for term in re.split(r"\W+", query.lower()):
            if len(term) < 3 or term in stopwords or term in seen:
                continue
            seen.add(term)
            terms.append(term)
        return terms

    def _build_sources(self, normalized: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sources_map: Dict[str, Dict[str, Any]] = {}
        for item in normalized:
            source_key = f"{item.get('document_id')}::{item.get('clause_number')}::{item.get('file_name') or item.get('filename')}"
            current = sources_map.get(source_key) or {"document_id": item.get("document_id"), "upload_id": item.get("upload_id"), "file_name": item.get("file_name") or item.get("filename"), "clause_number": item.get("clause_number"), "clause_title": item.get("clause_title"), "section_heading": item.get("section_heading") or item.get("clause_title"), "clause_tags": [], "page_numbers": [], "page_number": item.get("page_number") or item.get("page"), "page": item.get("page")}
            current["clause_tags"] = sorted(set(current.get("clause_tags", []) + (item.get("clause_tags") or [])))
            merged_pages = sorted(set(current.get("page_numbers", []) + (item.get("page_numbers") or [])))
            current["page_numbers"] = merged_pages
            if merged_pages:
                current["page_number"] = merged_pages[0]
                current["page"] = merged_pages[0]
            sources_map[source_key] = current
        return list(sources_map.values())

    def _build_summary(self, normalized: Sequence[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
        top_chunks = sorted(normalized, key=lambda chunk: chunk.get("score") if isinstance(chunk.get("score"), (int, float)) else 0, reverse=True)[:3]
        if not top_chunks:
            return None, None
        sections = []
        for chunk in top_chunks:
            clause_label = chunk.get("clause_number") or chunk.get("clause_title") or "Clause match"
            snippet = re.sub(r"\s+", " ", (chunk.get("text") or "").strip())
            if len(snippet) > 180:
                snippet = f"{snippet[:180].rstrip()}..."
            source_bits = [bit for bit in [chunk.get("file_name"), f"p.{chunk['page_number']}" if chunk.get("page_number") else None, chunk.get("section_heading")] if bit]
            source_suffix = f" [{' | '.join(source_bits)}]" if source_bits else ""
            sections.append(f"{clause_label}{source_suffix}: {snippet}")
        return "\n\n".join(sections), "Grounded Match Highlights"

    def _normalize_job(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(doc)
        for key in ("_id", "file_path", "saved_path", "filepath_local", "filepath_s3", "storage_locations"):
            data.pop(key, None)
        data["upload_id"] = str(data.get("upload_id") or "")
        data["document_id"] = self._coerce_id(data.get("document_id"))
        data["status"] = data.get("status") or "unknown"
        data["tags"] = data.get("tags") or []
        data["error"] = self._sanitize_error(data.get("error"))
        return data

    def _normalize_contract_record(self, doc: Dict[str, Any]) -> ContractUploadRecord:
        return ContractUploadRecord(document_id=self._coerce_id(doc.get("_id")) or "", upload_id=str(doc.get("contract_upload_id") or ""), filename=doc.get("filename"), status=doc.get("status") or "unknown", categories=doc.get("contract_categories") or [], error=self._sanitize_error(doc.get("contract_error")), size=doc.get("filesize"), tags=doc.get("tags") or [], createdAt=doc.get("createdAt"), updatedAt=doc.get("updatedAt"))

    def _normalize_vector_chunk(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(doc)
        for key in ("_id", "file_path", "source_file"):
            data.pop(key, None)
        data["document_id"] = self._coerce_id(data.get("document_id"))
        data["upload_id"] = self._coerce_id(data.get("upload_id"))
        data["tags"] = data.get("tags") or []
        data["clause_tags"] = data.get("clause_tags") or []
        data["toc_path"] = data.get("toc_path") or []
        pages_raw = data.get("page_numbers") if data.get("page_numbers") is not None else []
        if not isinstance(pages_raw, list):
            pages_raw = [pages_raw]
        data["page_numbers"] = [int(page) for page in pages_raw if isinstance(page, (int, float)) or str(page).isdigit()]
        if data["page_numbers"]:
            data.setdefault("page_number", data["page_numbers"][0])
            data.setdefault("page", data["page_numbers"][0])
        if not data.get("section_heading"):
            data["section_heading"] = data.get("clause_title")
        if not data.get("file_name"):
            data["file_name"] = data.get("filename") or data.get("source_filename")
        try:
            data["score"] = float(data.get("score") or 0.0)
        except (TypeError, ValueError):
            data["score"] = 0.0
        return data

    def _authorize_scope(self, scoped_doc: Dict[str, Any], current_user: CurrentUser) -> None:
        if "superadmin" in self._roles(current_user):
            return
        organization_id = str(scoped_doc.get("organization_id") or "")
        project_id = str(scoped_doc.get("project_id") or "") or None
        effective_org, _, project_filters = self._resolve_scope(current_user, organization_id, project_id)
        if effective_org and organization_id and effective_org != organization_id:
            raise ContractError("Not authorized for this organization", status.HTTP_403_FORBIDDEN)
        if project_filters and project_id and project_id not in project_filters:
            raise ContractError("Not authorized for this project", status.HTTP_403_FORBIDDEN)

    @staticmethod
    def _coerce_id(value: Any) -> Optional[str]:
        return None if value is None else str(value)

    @staticmethod
    def _sanitize_error(value: Optional[Any]) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text[:500] if text else None

    @staticmethod
    def _as_lookup_id(document_id: str) -> Any:
        try:
            return ObjectId(str(document_id))
        except Exception:
            return str(document_id)

    @staticmethod
    def _cleanup_tmp_file(path: Path) -> None:
        try:
            resolved = path.resolve()
            resolved.relative_to(Path(tempfile.gettempdir()).resolve())
            resolved.unlink(missing_ok=True)
        except Exception:
            return


async def process_contract_ingest_job(payload: Dict[str, Any]) -> None:
    await ContractService().process_ingest_job(payload)
