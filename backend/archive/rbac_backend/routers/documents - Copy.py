# Enhanced documents.py with Bulk Upload Support

"""
Document management module with secure file handling, proper authorization, and bulk upload capabilities.
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query, BackgroundTasks, Body, Header
from typing import List, Optional, Dict, Any, Union
from datetime import datetime
import logging
from pathlib import Path
import uuid
import csv
import io
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
import asyncio
import os
from tempfile import NamedTemporaryFile

from ..core.database import get_db, get_database
from ..core.security import get_current_user, CurrentUser, authorize_scope
from ..core.config import settings
from ..config.document_processing_config import DocumentProcessingConfig
from ..services.document_service import DocumentService
from ..services.file_service import SecureFileService
from ..services.export_service import ExportService
from ..services.authorization_service import AuthorizationService
from ..services.bulk_upload_service import BulkUploadService
from ..services.langchain_vector_service import LangChainVectorService
from ..dependencies import get_notification_service
from ..services.reference_sync_service import ReferenceSyncError
from ..models.document import (
    Document,
    DocumentUpdate,
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
from ..utils.validation import validate_file, sanitize_filename
from ..utils.error_handler import handle_exceptions, DocumentError
from ..utils.date_parser import parse_date_safely
from ..utils.csv_validator import validate_csv_structure, parse_csv_row
from fastapi.responses import FileResponse, Response

logger = logging.getLogger(__name__)
router = APIRouter()


def verify_langgraph_token(x_api_token: str = Header(...)) -> None:
    expected = getattr(settings, "LANGGRAPH_API_TOKEN", None)
    if not expected or x_api_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service token",
        )

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
                await self.auth_service.check_document_access(
                    current_user,
                    str(document.organization_id),
                    str(document.project_id) if document.project_id else None,
                    "read",
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
        **kwargs
    ) -> Document:
        """Create document with comprehensive validation and security."""
        try:
            # Authorization check
            await self.auth_service.check_document_access(
                current_user, organization_id, project_id, "create"
            )

            # Validate required fields
            if not file.filename or not letter_no:
                raise DocumentError("Missing required fields", status.HTTP_400_BAD_REQUEST)

            # Secure file handling
            safe_filename = sanitize_filename(file.filename)
            content = await file.read()

            # Validate file
            validation_result = await validate_file(
                content, safe_filename, settings.ALLOWED_DOCUMENT_MIMES
            )

            if not validation_result.is_valid:
                raise DocumentError(
                    f"Invalid file: {validation_result.error}",
                    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
                )

            # Parse date safely
            parsed_date = parse_date_safely(date_str)

            # Store file securely
            file_path = await self.file_service.store_document(
                content, organization_id, project_id, safe_filename
            )

            # Create document record
            document = await self.document_service.create_document(
                file_path=file_path,
                filename=file.filename,
                organization_id=organization_id,
                project_id=project_id,
                upload_type=upload_type,
                letter_no=letter_no,
                date=parsed_date,
                current_user=current_user,
                **kwargs
            )

            # Schedule background processing if needed
            if kwargs.get('ocr_enabled', False):
                await self.document_service.queue_document_processing(
                    document,
                    file_path,
                )

            return document

        except DocumentError:
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
        current_user: CurrentUser
    ) -> BulkUploadResponse:
        """Handle bulk document upload with CSV metadata."""
        try:
            # Authorization check
            await self.auth_service.check_document_access(
                current_user, organization_id, project_id, "create"
            )

            # Validate CSV file
            if not csv_file.filename or not csv_file.filename.endswith('.csv'):
                raise DocumentError("Invalid CSV file", status.HTTP_400_BAD_REQUEST)

            # Read and validate CSV content
            csv_content = await csv_file.read()
            csv_data = await self._parse_and_validate_csv(csv_content)

            # Persist uploaded files so background task can safely read them
            persisted_files = await self._persist_uploaded_files(files)

            # Create bulk upload job
            job_id = str(uuid.uuid4())
            
            # Initialize bulk upload tracking
            bulk_status = BulkUploadStatus(
                job_id=job_id,
                total_files=len(persisted_files),
                processed_files=0,
                successful_uploads=0,
                failed_uploads=0,
                status="processing",
                created_at=datetime.utcnow(),
                results=[]
            )

            # Store job status
            await self.bulk_upload_service.create_bulk_job(bulk_status)

            # Schedule bulk processing directly on the event loop since BackgroundTasks
            # does not await async coroutines
            asyncio.create_task(
                self._process_bulk_upload(
                    job_id,
                    csv_data,
                    persisted_files,
                    organization_id,
                    project_id,
                    current_user,
                )
            )

            return BulkUploadResponse(
                job_id=job_id,
                message="Bulk upload initiated successfully",
                total_files=len(persisted_files),
                status="processing"
            )

        except DocumentError:
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

    async def _persist_uploaded_files(self, files: List[UploadFile]) -> List[Dict[str, Any]]:
        """Copy uploaded files to named temporary files for background processing."""
        stored_files: List[Dict[str, Any]] = []
        for upload in files:
            if not upload or not upload.filename:
                continue

            await upload.seek(0)
            temp_file = NamedTemporaryFile(delete=False, suffix=Path(upload.filename).suffix)
            try:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    temp_file.write(chunk)
            finally:
                temp_file.close()

            stored_files.append(
                {
                    "filename": upload.filename,
                    "temp_path": temp_file.name,
                }
            )

        return stored_files
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
            df.columns = [str(c).strip().lower().replace('\u00a0', ' ') for c in df.columns]

            # Validate CSV structure. Older templates didn't include "subject" so we
            # treat it as optional and generate a fallback from other columns.
            required_columns = ['filename', 'upload_type', 'letter_no', 'date']
            missing_columns = [col for col in required_columns if col not in df.columns]

            if missing_columns:
                raise DocumentError(
                    f"Missing required columns: {', '.join(missing_columns)}",
                    status.HTTP_400_BAD_REQUEST
                )

            if 'subject' not in df.columns:
                logger.warning(
                    "CSV missing 'subject' column. Falling back to letter number for subject."
                )
                df['subject'] = df['letter_no'].fillna('').astype(str)

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
        upload_type = str(row.get('upload_type', '')).strip().lower()
        if upload_type not in ['incoming', 'outgoing']:
            raise ValueError(f"Row {row_number}: upload_type must be 'incoming' or 'outgoing'")
        
        # Validate letter number
        letter_no = str(row.get('letter_no', '')).strip()
        if not letter_no:
            raise ValueError(f"Row {row_number}: letter_no is required")
        
        # Validate and parse date
        date_str = str(row.get('date', '')).strip()
        if not date_str:
            raise ValueError(f"Row {row_number}: date is required")
        
        parsed_date = parse_date_safely(date_str)
        if not parsed_date:
            raise ValueError(f"Row {row_number}: invalid date format '{date_str}'")
        
        # Validate subject with fallbacks for legacy CSV templates
        subject = str(row.get('subject', '')).strip()
        if not subject:
            fallback_candidates = [
                row.get('letter_no', ''),
                row.get('filename', ''),
                f"Document {row_number}",
            ]
            subject = next(
                (str(candidate).strip() for candidate in fallback_candidates if candidate),
                ''
            )
        if not subject:
            raise ValueError(f"Row {row_number}: subject is required")

        # Optional fields with defaults
        from_company = str(row.get('from_', '')).strip() or None
        to_company = str(row.get('to', '')).strip() or None
        tags = self._parse_list_field(row.get('tags', ''))
        sub_tags = self._parse_list_field(row.get('sub_tags', ''))
        status = str(row.get('status', 'draft')).strip()
        ocr_enabled = self._parse_boolean_field(row.get('ocr_enabled', 'true'))

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
        temp_uploads: List[UploadFile] = []
        try:
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
                subject=row_data['subject'],
                from_=row_data.get('from_'),
                to=row_data.get('to'),
                tags=row_data.get('tags', []),
                sub_tags=row_data.get('sub_tags', []),
                status=row_data.get('status', 'draft'),
                ocr_enabled=row_data.get('ocr_enabled', False)
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
                await self.auth_service.check_document_access(
                    current_user,
                    document.organization_id,
                    document.project_id,
                    "update",
                )

            file_path = getattr(document, "filepath_local", None) or getattr(
                document, "filepath_s3", None
            )
            if not file_path:
                raise DocumentError(
                    "Document file path unavailable for processing",
                    status.HTTP_400_BAD_REQUEST,
                )

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
        except DocumentError:
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
            status = await self.bulk_upload_service.get_job_status(job_id)
            
            if not status:
                raise DocumentError("Bulk upload job not found", status.HTTP_404_NOT_FOUND)
            
            # Verify user has access (basic security check)
            # In production, you might want more sophisticated access control
            
            return status

        except DocumentError:
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

        await self.auth_service.check_document_access(
            current_user, document.organization_id, document.project_id, "read"
        )

        return await self.document_service.enrich_document(document)

    except DocumentError:
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

    except DocumentError:
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
    current_user: CurrentUser
) -> Document:
    """Update document with validation and authorization."""
    try:
        document = await self.document_service.get_document_by_id(document_id)
        if not document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.auth_service.check_document_access(
            current_user, document.organization_id, document.project_id, "update"
        )

        validated_update = await self.document_service.validate_update(
            update_data, current_user
        )
        update_payload = validated_update.model_dump(
            exclude_unset=True, exclude_none=True
        )
        updated_document_model = document.model_copy(update=update_payload)

        updated_document = await self.document_service.update_document(
            document_id, updated_document_model
        )
        if not updated_document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        return await self.document_service.enrich_document(updated_document)

    except DocumentError:
        raise
    except Exception as e:
        logger.error(f"Document update failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document update service temporarily unavailable",
        )

    
async def controller_delete_document(
    self,
    document_id: str,
    current_user: CurrentUser
):
    """Delete document with proper authorization and cleanup."""
    try:
        document = await self.document_service.get_document_by_id(document_id)
        if not document:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

        await self.auth_service.check_document_access(
            current_user, document.organization_id, document.project_id, "delete"
        )

        deleted = await self.document_service.delete_document(document_id)
        if not deleted:
            raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)

    except DocumentError:
        raise
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

        await self.auth_service.check_document_access(
            current_user, document.organization_id, document.project_id, "update"
        )

        if not file.filename:
            raise DocumentError("No file provided", status.HTTP_400_BAD_REQUEST)

        safe_filename = sanitize_filename(file.filename)
        content = await file.read()

        validation_result = await validate_file(
            content, safe_filename, settings.ALLOWED_ENCLOSURE_MIMES
        )
        if not validation_result.is_valid:
            raise DocumentError(
                f"Invalid enclosure: {validation_result.error}",
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            )

        return await self.document_service.add_enclosure(
            document_id, content, safe_filename, current_user
        )

    except DocumentError:
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

        await self.auth_service.check_document_access(
            current_user, document.organization_id, document.project_id, "read"
        )

        return await self.document_service.list_enclosures(document_id)
    except DocumentError:
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

        await self.auth_service.check_document_access(
            current_user, document.organization_id, document.project_id, "update"
        )

        await self.document_service.remove_enclosure(document_id, enclosure_id)
    except DocumentError:
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

        await self.auth_service.check_document_access(
            current_user, document.organization_id, document.project_id, "read"
        )

        return await self.document_service.list_references(document_id)
    except DocumentError:
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

        await self.auth_service.check_document_access(
            current_user, document.organization_id, document.project_id, "update"
        )

        target = await self.document_service.get_document_by_id(
            reference_data.referenced_document_id
        )
        if not target:
            raise DocumentError("Referenced document not found", status.HTTP_404_NOT_FOUND)

        await self.auth_service.check_document_access(
            current_user, target.organization_id, target.project_id, "read"
        )

        return await self.document_service.add_reference(
            document_id,
            reference_data,
            current_user=current_user,
        )
    except DocumentError:
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

        await self.auth_service.check_document_access(
            current_user, document.organization_id, document.project_id, "update"
        )

        return await self.document_service.remove_reference(document_id, reference_id)
    except DocumentError:
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

        await self.auth_service.check_document_access(
            current_user, document.organization_id, document.project_id, "update"
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

        for entry in document.references or []:
            source_label = getattr(entry, "source", None)
            if not isinstance(source_label, str) or source_label.lower() != "parser":
                continue
            if hasattr(entry, "model_dump"):
                try:
                    references_payload.append(entry.model_dump(by_alias=True, exclude_none=True))
                except Exception:
                    continue
            elif isinstance(entry, dict):
                entry_source = str(entry.get("source", "")).lower()
                if entry_source != "parser":
                    continue
                references_payload.append(
                    {k: v for k, v in entry.items() if v not in (None, "", [], {})}
                )

        if not references_payload:
            return {
                "message": "No references available for synchronisation",
                "sync": {"resolved": 0, "missing": [], "updated_targets": 0, "removed_targets": 0},
            }

        sync_result = await self.document_service.reference_sync_service.sync_bidirectional(
            document_id=document_id,
            references=references_payload,
            source="parser",
        )

        try:
            document_payload = document.model_dump(by_alias=True, exclude_none=True)
            self.document_service.graph_ingestion.sync_document_to_falkor(
                document_id=document.id,
                document=document_payload,
                metadata=None,
                upload_type=document.uploadType,
            )
        except Exception as exc:
            logger.warning(
                "FalkorDB synchronisation failed for document %s during reference sync: %s",
                document_id,
                exc,
            )

        return {
            "message": "Reference synchronisation completed",
            "sync": sync_result,
        }
    except DocumentError:
        raise
    except ReferenceSyncError as exc:
        logger.error("Reference sync error for %s: %s", document_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
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

        await self.auth_service.check_document_access(
            current_user, source.organization_id, source.project_id, "update"
        )
        await self.auth_service.check_document_access(
            current_user, target.organization_id, target.project_id, "read"
        )

        return await self.document_service.link_documents(
            payload.source_document_id,
            payload.target_document_id,
            payload.link_type,
            description=payload.description,
            current_user=current_user,
        )
    except DocumentError:
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

        await self.auth_service.check_document_access(
            current_user, document.organization_id, document.project_id, "read"
        )

        return await self.document_service.list_linked_documents(document_id)
    except DocumentError:
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
    }

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

    filename = "documents_export.xlsx"
    # Generate Excel file bytes via export service
    data_bytes = await controller.export_service.export_documents_to_xlsx(
        enriched_dicts,
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
    status: Optional[str] = Form("draft"),
    ocrEnabled: str = Form("1"),
    compressionEnabled: str = Form("0"),

    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Create a new document."""
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
        status=status,
        ocr_enabled=ocrEnabled.lower() in ("true", "1", "t", "y", "yes"),
        compression_enabled=compressionEnabled.lower() in ("true", "1", "t", "y", "yes"),
    )


@router.get("/documents/{id}", response_model=Document)
@handle_exceptions
async def get_document(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get a specific document."""
    return await controller_get_document(controller, id, current_user)


@router.post("/documents/{id}/process", response_model=DocumentProcessingResult)
@handle_exceptions
async def process_document_endpoint(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
) -> DocumentProcessingResult:
    """Trigger OCR/AI processing for an existing document."""
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


@router.get("/documents/{id}/download")
@handle_exceptions
async def download_document(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
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

    # Authorization check
    await controller.auth_service.check_document_access(
        current_user, document.organization_id, document.project_id, "read"
    )

    # Prefer local file if present
    try:
        path = getattr(document, "filepath_local", None)
        if path:
            p = Path(path)
            if p.exists() and p.is_file():
                media = document.filetype or "application/octet-stream"
                return FileResponse(
                    path=str(p),
                    media_type=media,
                    filename=document.filename,
                )
    except Exception:
        # continue to fallback
        pass

    # If we have a presigned/public URL, redirect to it (temporary)
    if isinstance(document.presigned_url, str) and document.presigned_url:
        return Response(status_code=307, headers={"Location": document.presigned_url})

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
    letterNo: Optional[List[str]] = Query(None),
    subject: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """List documents with filtering and pagination."""
    filters = {
        "organization_id": organization_id,
        "project_id": project_id,
        "tags": tags,
        "subTags": subTags,
        "uploadType": uploadType,
        "status": status,
        "letterNo": letterNo,
        "subject": subject,
    }
    pagination = {"skip": skip, "limit": limit}

    return await controller_list_documents(controller, filters, pagination, current_user)


@router.put("/documents/{id}", response_model=Document)
@handle_exceptions
async def update_document(
    id: str,
    document_update: DocumentUpdate,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update a document."""
    return await controller_update_document(controller, id, document_update, current_user)


@router.delete("/documents/{id}", status_code=204)
@handle_exceptions
async def delete_document(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Delete a document."""
    await controller_delete_document(controller, id, current_user)


@router.get("/documents/{id}/enclosures", response_model=List[Enclosure])
@handle_exceptions
async def list_enclosures(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List all enclosures for a document."""
    return await controller_list_enclosures(controller, id, current_user)


@router.post("/documents/{id}/enclosures", response_model=EnclosureResponse)
@handle_exceptions
async def add_enclosure(
    id: str,
    file: UploadFile = File(...),
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Add an enclosure to a document."""
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
    await controller_remove_enclosure(controller, id, enclosure_id, current_user)


@router.get("/documents/{id}/references", response_model=Dict[str, Any])
@handle_exceptions
async def get_document_references(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Retrieve parsed and linked references for a document."""
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
    return await controller_remove_reference(controller, id, reference_id, current_user)


@router.post("/documents/{id}/sync-references", response_model=Dict[str, Any])
@handle_exceptions
async def trigger_reference_sync(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Manually trigger bidirectional reference synchronisation for a document."""
    return await controller_sync_references(controller, id, current_user)


@router.post("/documents/link", response_model=Dict[str, Any])
@handle_exceptions
async def link_documents_endpoint(
    payload: LinkDocumentsRequest,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Link two documents together."""
    return await controller_link_documents(controller, payload, current_user)


@router.get("/documents/{id}/linked", response_model=List[Dict[str, Any]])
@handle_exceptions
async def get_linked_documents(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return documents linked to the given document."""
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

    # Authorization
    await controller.auth_service.check_document_access(
        current_user, document.organization_id, document.project_id, "read"
    )

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

    # Require update/write privileges to add comments
    await controller.auth_service.check_document_access(
        current_user, document.organization_id, document.project_id, "update"
    )

    return await controller.document_service.add_comment(id, text, current_user)

@router.post("/documents/bulk-upload", response_model=BulkUploadResponse)
@handle_exceptions
async def bulk_upload_documents(
    background_tasks: BackgroundTasks,
    csv_file: UploadFile = File(..., description="CSV file with document metadata"),
    files: List[UploadFile] = File(..., description="Document files to upload"),
    organization_id: str = Form(...),
    project_id: str = Form(...),

    # Add validation for bulk upload limits
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Bulk upload documents with CSV metadata.
    
    The CSV file should contain metadata for each document file.
    File names in CSV must match the uploaded file names.
    """
    # Check bulk upload limits
    if len(files) > settings.BULK_UPLOAD_MAX_FILES:
        raise DocumentError(
            f"Bulk upload exceeds maximum file limit ({settings.BULK_UPLOAD_MAX_FILES})",
            status.HTTP_400_BAD_REQUEST
        )

    total_size = 0
    for upload in files:
        upload.file.seek(0, os.SEEK_END)
        total_size += upload.file.tell()
        upload.file.seek(0)
    if total_size > settings.BULK_UPLOAD_MAX_SIZE_MB * 1024 * 1024:
        raise DocumentError(
            f"Bulk upload exceeds maximum total size ({settings.BULK_UPLOAD_MAX_SIZE_MB}MB)",
            status.HTTP_400_BAD_REQUEST
        )

    # Reset file pointers
    for file in files:
        file.file.seek(0)

    return await controller.bulk_upload_documents(
        background_tasks=background_tasks,
        csv_file=csv_file,
        files=files,
        organization_id=organization_id,
        project_id=project_id,
        current_user=current_user
    )

@router.get("/documents/bulk-upload/{job_id}/status", response_model=BulkUploadStatus)
@handle_exceptions
async def get_bulk_upload_status(
    job_id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get the status of a bulk upload job."""
    return await controller.get_bulk_upload_status(job_id, current_user)

@router.get("/documents/bulk-upload/template")
async def download_bulk_upload_template():
    """Download CSV template for bulk upload."""
    # Return CSV template file
    from fastapi.responses import Response
    
    csv_template = """filename,upload_type,letter_no,date,subject,from_,to,tags,sub_tags,status,ocr_enabled
sample-letter-001.pdf,incoming,LTR-2024-001,2024-01-15,Contract Amendment Request,Contractor Corp,Our Company,Claims,Delay Claims,Received,true
sample-letter-002.pdf,outgoing,LTR-2024-002,2024-01-16,Response to Amendment Request,Our Company,Contractor Corp,Claims,Delay Claims,Draft,true
sample-invoice-001.pdf,incoming,INV-2024-001,2024-01-17,Monthly Progress Invoice,Contractor Corp,Our Company,Payment,Invoice,Received,true"""

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
    current_user: CurrentUser = Depends(get_current_user)
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

        # Authorization check
        await controller.auth_service.check_document_access(
            current_user, document.organization_id, document.project_id, "read"
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
