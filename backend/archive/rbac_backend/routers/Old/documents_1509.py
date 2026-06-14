# Improved documents.py
"""
Document management module with secure file handling, proper authorization, and clean architecture.
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query, BackgroundTasks
from typing import List, Optional, Dict, Any, Union
from datetime import datetime
import logging
from pathlib import Path
import uuid

from ..core.database import get_db
from ..core.security import get_current_user, CurrentUser, authorize_scope
from ..core.config import settings
from ..services.document_service import DocumentService
from ..services.file_service import SecureFileService
from ..services.export_service import ExportService
from ..services.authorization_service import AuthorizationService
from ..models.document import (
    Document, DocumentUpdate, DocumentListResponse, EnclosureResponse,
    DocumentReference, ReferenceCreate, LinkDocumentsRequest, LinkDocumentsResponse
)
from ..utils.validation import validate_file, sanitize_filename
from ..utils.error_handler import handle_exceptions, DocumentError
from ..utils.date_parser import parse_date_safely

logger = logging.getLogger(__name__)
router = APIRouter()


class DocumentController:
    """Document controller with clean separation of concerns."""
    
    def __init__(
        self,
        document_service: DocumentService,
        file_service: SecureFileService,
        export_service: ExportService,
        auth_service: AuthorizationService
    ):
        self.document_service = document_service
        self.file_service = file_service
        self.export_service = export_service
        self.auth_service = auth_service

    async def create_document(
        self,
        background_tasks: BackgroundTasks,
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
                background_tasks.add_task(
                    self.document_service.process_document_async,
                    document.id, file_path
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

    async def get_document(
        self, document_id: str, current_user: CurrentUser
    ) -> Document:
        """Get document with proper authorization and enrichment."""
        try:
            # Fetch document
            document = await self.document_service.get_document_by_id(document_id)
            if not document:
                raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)
            
            # Authorization check
            await self.auth_service.check_document_access(
                current_user, document.organization_id, document.project_id, "read"
            )
            
            # Enrich document with additional data
            enriched_document = await self.document_service.enrich_document(document)
            
            return enriched_document
            
        except DocumentError:
            raise
        except Exception as e:
            logger.error(f"Document retrieval failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Document service temporarily unavailable"
            )

    async def list_documents(
        self,
        filters: Dict[str, Any],
        pagination: Dict[str, int],
        current_user: CurrentUser
    ) -> DocumentListResponse:
        """List documents with proper filtering and authorization."""
        try:
            # Build authorization-aware query
            authorized_query = await self.auth_service.build_authorized_query(
                current_user, filters
            )
            
            # Get documents with efficient querying
            documents, total_count = await self.document_service.list_documents(
                authorized_query, pagination
            )
            
            # Enrich documents for display
            enriched_documents = []
            for doc in documents:
                try:
                    enriched = await self.document_service.enrich_document(doc)
                    enriched_documents.append(enriched)
                except Exception as e:
                    logger.warning(f"Failed to enrich document {doc.id}: {str(e)}")
                    # Continue with other documents
                    continue
            
            return DocumentListResponse(
                documents=enriched_documents,
                total=total_count
            )
            
        except Exception as e:
            logger.error(f"Document listing failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Document listing service temporarily unavailable"
            )

    async def update_document(
        self,
        document_id: str,
        update_data: DocumentUpdate,
        current_user: CurrentUser
    ) -> Document:
        """Update document with validation and authorization."""
        try:
            # Get existing document
            document = await self.document_service.get_document_by_id(document_id)
            if not document:
                raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)
            
            # Authorization check
            await self.auth_service.check_document_access(
                current_user, document.organization_id, document.project_id, "update"
            )
            
            # Validate update data
            validated_update = await self.document_service.validate_update(
                update_data, current_user
            )
            
            # Perform update
            updated_document = await self.document_service.update_document(
                document_id, validated_update
            )
            
            return await self.document_service.enrich_document(updated_document)
            
        except DocumentError:
            raise
        except Exception as e:
            logger.error(f"Document update failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Document update service temporarily unavailable"
            )

    async def delete_document(
        self, document_id: str, current_user: CurrentUser
    ):
        """Delete document with proper authorization and cleanup."""
        try:
            # Get document
            document = await self.document_service.get_document_by_id(document_id)
            if not document:
                raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)
            
            # Authorization check
            await self.auth_service.check_document_access(
                current_user, document.organization_id, document.project_id, "delete"
            )
            
            # Perform soft delete with cleanup
            await self.document_service.delete_document(document_id, current_user)
            
        except DocumentError:
            raise
        except Exception as e:
            logger.error(f"Document deletion failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Document deletion service temporarily unavailable"
            )

    async def add_enclosure(
        self,
        document_id: str,
        file: UploadFile,
        current_user: CurrentUser
    ) -> EnclosureResponse:
        """Add enclosure with security validation."""
        try:
            # Get document and check authorization
            document = await self.document_service.get_document_by_id(document_id)
            if not document:
                raise DocumentError("Document not found", status.HTTP_404_NOT_FOUND)
            
            await self.auth_service.check_document_access(
                current_user, document.organization_id, document.project_id, "update"
            )
            
            # Validate enclosure file
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
                    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
                )
            
            # Store and create enclosure
            enclosure = await self.document_service.add_enclosure(
                document_id, content, safe_filename, current_user
            )
            
            return enclosure
            
        except DocumentError:
            raise
        except Exception as e:
            logger.error(f"Enclosure addition failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Enclosure service temporarily unavailable"
            )


# Dependency injection
async def get_document_controller() -> DocumentController:
    """Factory function for document controller."""
    document_service = DocumentService()
    file_service = SecureFileService(base_dir=settings.SECURE_UPLOADS_DIR)
    export_service = ExportService()
    auth_service = AuthorizationService()
    return DocumentController(document_service, file_service, export_service, auth_service)


# API Endpoints with minimal logic
@router.post("/documents", response_model=Document)
@handle_exceptions
async def create_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    organization_id: str = Form(...),
    project_id: str = Form(...),
    uploadType: str = Form(...),
    letterNo: str = Form(...),
    date: str = Form(...),
    subject: Optional[str] = Form(""),
    from_: Optional[str] = Form(None, alias="from"),
    to: Optional[str] = Form(None),
    tags: Optional[List[str]] = Form(None),
    subTags: Optional[List[str]] = Form(None),
    status: Optional[str] = Form("draft"),
    ocrEnabled: str = Form("0"),
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Create a new document."""
    return await controller.create_document(
        background_tasks=background_tasks,
        file=file,
        organization_id=organization_id,
        project_id=project_id,
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
        ocr_enabled=ocrEnabled.lower() in ('true', '1', 't', 'y', 'yes')
    )


@router.get("/documents/{id}", response_model=Document)
@handle_exceptions
async def get_document(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get a specific document."""
    return await controller.get_document(id, current_user)


@router.get("/documents", response_model=DocumentListResponse)
@handle_exceptions
async def list_documents(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),
    subTags: Optional[List[str]] = Query(None),
    uploadType: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
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
        "status": status
    }
    pagination = {"skip": skip, "limit": limit}
    
    return await controller.list_documents(filters, pagination, current_user)


@router.put("/documents/{id}", response_model=Document)
@handle_exceptions
async def update_document(
    id: str,
    document_update: DocumentUpdate,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update a document."""
    return await controller.update_document(id, document_update, current_user)


@router.delete("/documents/{id}", status_code=204)
@handle_exceptions
async def delete_document(
    id: str,
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Delete a document."""
    await controller.delete_document(id, current_user)


@router.post("/documents/{id}/enclosures", response_model=EnclosureResponse)
@handle_exceptions
async def add_enclosure(
    id: str,
    file: UploadFile = File(...),
    controller: DocumentController = Depends(get_document_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Add an enclosure to a document."""
    return await controller.add_enclosure(id, file, current_user)