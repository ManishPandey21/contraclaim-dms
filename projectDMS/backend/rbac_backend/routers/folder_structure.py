# Improved folder_structure.py
"""
Secure folder structure management with proper separation of concerns,
comprehensive security measures, and performance optimizations.
"""

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Query, BackgroundTasks
from typing import List, Optional, Dict, Any, Union
from pathlib import Path
import asyncio
import logging
from datetime import datetime
import uuid

from ..core.security import get_current_user, CurrentUser, authorize_scope
from ..core.database import get_db
from ..core.config import settings
from ..services.folder_service import FolderService
from ..services.s3_service import S3Service
from ..services.authorization_service import AuthorizationService
from ..services.audit_event_service import AuditEventService
from ..services.policy_service import PolicyService
from ..models.folder_models import (
    FolderItem, CreateFolderRequest, FolderResponse, 
    UploadFileRequest, UploadFileResponse
)
from ..utils.validation import validate_input, sanitize_filename, secure_path_join
from ..utils.error_handler import BaseDomainError, handle_exceptions, FolderError
from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter()


class FolderController:
    """Secure folder management controller with proper separation of concerns."""
    
    def __init__(
        self,
        folder_service: FolderService,
        s3_service: S3Service,
        auth_service: AuthorizationService,
        rate_limiter: RateLimiter
    ):
        self.folder_service = folder_service
        self.s3_service = s3_service
        self.auth_service = auth_service
        self.rate_limiter = rate_limiter

    async def create_folder_structure(
        self,
        folder_data: CreateFolderRequest,
        current_user: CurrentUser
    ) -> FolderResponse:
        """Create folder structure with comprehensive security validation."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.check_folder_access(
                current_user, folder_data.organization_id, folder_data.project_id, "create"
            )
            
            # Input validation and sanitization
            safe_name = sanitize_filename(validate_input(folder_data.name, max_length=255))
            safe_path = await self._validate_and_sanitize_path(
                folder_data.path, folder_data.organization_id, folder_data.project_id
            )
            
            # Check for existing folder
            existing = await self.folder_service.get_folder_by_path(
                safe_path, folder_data.organization_id, folder_data.project_id
            )
            if existing:
                raise FolderError(
                    f"Folder already exists at '{safe_path}'",
                    status.HTTP_409_CONFLICT
                )
            
            # Create folder with atomic operation
            folder = await self.folder_service.create_folder(
                name=safe_name,
                path=safe_path,
                organization_id=folder_data.organization_id,
                project_id=folder_data.project_id,
                current_user=current_user
            )
            
            # Create S3 placeholder asynchronously (non-blocking)
            if folder_data.type == "folder":
                asyncio.create_task(
                    self.s3_service.create_folder_placeholder(safe_path)
                )
            
            return FolderResponse(
                message=f"Folder '{safe_name}' created successfully",
                folder_id=folder.id,
                path=safe_path
            )
            
        except (BaseDomainError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Folder creation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Folder creation service temporarily unavailable"
            )

    async def get_folder_structure(
        self,
        organization_id: Optional[str],
        project_id: Optional[str],
        current_user: CurrentUser
    ) -> List[FolderItem]:
        """Get root folder structure with efficient querying and caching."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Resolve effective IDs
            effective_org_id = organization_id or str(current_user.organization_id)
            
            # Authorization check
            await self.auth_service.check_folder_access(
                current_user, effective_org_id, project_id, "read"
            )
            
            # Get folder structure with caching
            folders = await self.folder_service.get_root_folders(
                effective_org_id, project_id, use_cache=True
            )
            
            # Build tree structure efficiently
            return await self._build_folder_tree(folders, None)
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get folder structure: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Folder service temporarily unavailable"
            )

    async def upload_file(
        self,
        background_tasks: BackgroundTasks,
        file: UploadFile,
        name: str,
        path: str,
        file_extension: str,
        organization_id: str,
        project_id: str,
        current_user: CurrentUser
    ) -> UploadFileResponse:
        """Secure file upload with comprehensive validation."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.check_folder_access(
                current_user, organization_id, project_id, "upload"
            )
            
            # Input validation
            if not file.filename:
                raise FolderError("No file uploaded", status.HTTP_400_BAD_REQUEST)
            
            safe_name = sanitize_filename(validate_input(name, max_length=200))
            safe_extension = validate_input(file_extension, pattern=r'^\.[a-zA-Z0-9]{1,10}$')
            safe_path = await self._validate_and_sanitize_path(path, organization_id, project_id)
            
            # Verify destination folder exists
            folder = await self.folder_service.get_folder_by_path(
                safe_path, organization_id, project_id
            )
            if not folder or folder.type != "folder":
                raise FolderError(
                    "Destination folder not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # File validation
            content = await file.read()
            validation_result = await self._validate_file_content(
                content, file.filename, safe_extension
            )
            if not validation_result.is_valid:
                raise FolderError(
                    f"Invalid file: {validation_result.error}",
                    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
                )
            
            # Generate unique file path
            full_filename = f"{safe_name}{safe_extension}"
            file_path = secure_path_join(safe_path, full_filename, base_dir="uploads")
            
            # Check for existing file
            existing = await self.folder_service.get_file_by_path(
                str(file_path), organization_id, project_id
            )
            if existing:
                raise FolderError(
                    f"File '{full_filename}' already exists",
                    status.HTTP_409_CONFLICT
                )
            
            # Upload to S3 asynchronously
            s3_key = await self.s3_service.upload_bytes(
                str(file_path),
                content,
                validation_result.mime_type,
            )
            
            # Create file record
            file_record = await self.folder_service.create_file(
                name=full_filename,
                path=str(file_path),
                parent_path=safe_path,
                organization_id=organization_id,
                project_id=project_id,
                size=len(content),
                file_extension=safe_extension,
                s3_key=s3_key,
                current_user=current_user
            )
            
            return UploadFileResponse(
                message=f"File '{full_filename}' uploaded successfully",
                file_id=file_record.id,
                path=str(file_path),
                size=len(content)
            )
            
        except (BaseDomainError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"File upload failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="File upload service temporarily unavailable"
            )

    async def delete_folder(
        self,
        path: str,
        current_user: CurrentUser
    ):
        """Secure folder/file deletion with proper cleanup."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Validate and sanitize path
            safe_path = await self._validate_and_sanitize_path(
                path, str(current_user.organization_id), None
            )
            
            # Get item to delete
            item = await self.folder_service.get_item_by_path(
                safe_path, str(current_user.organization_id)
            )
            if not item:
                raise FolderError("Item not found", status.HTTP_404_NOT_FOUND)
            
            # Authorization check
            await self.auth_service.check_folder_access(
                current_user, item.organization_id, item.project_id, "delete"
            )
            
            # Perform deletion with cleanup
            await self.folder_service.delete_item_recursive(item.id, current_user)
            
            # Schedule S3 cleanup asynchronously
            asyncio.create_task(
                self.s3_service.cleanup_deleted_items(safe_path)
            )
            
        except (BaseDomainError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Deletion failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Delete service temporarily unavailable"
            )

    async def _validate_and_sanitize_path(
        self, path: str, organization_id: str, project_id: Optional[str]
    ) -> str:
        """Validate and sanitize path to prevent traversal attacks."""
        if not path:
            raise FolderError("Path cannot be empty", status.HTTP_400_BAD_REQUEST)
        
        # Remove dangerous characters and sequences
        safe_path = validate_input(path, max_length=1000)
        
        # Ensure path starts with expected prefix
        expected_prefix = f"uploads/{organization_id}"
        if project_id:
            expected_prefix += f"/{project_id}"
        
        if not safe_path.startswith(expected_prefix):
            raise FolderError("Invalid path structure", status.HTTP_400_BAD_REQUEST)
        
        # Use secure path joining to prevent traversal
        try:
            validated_path = secure_path_join(safe_path, base_dir="uploads")
            return str(validated_path)
        except Exception:
            raise FolderError("Invalid path", status.HTTP_400_BAD_REQUEST)

    async def _validate_file_content(
        self, content: bytes, filename: str, extension: str
    ) -> Any:
        """Comprehensive file content validation."""
        from ..models.document import FileValidationResult
        from ..utils.validation import validate_file
        
        # Size check
        max_size = max(1, int(settings.GENERAL_UPLOAD_MAX_FILE_SIZE_MB)) * 1024 * 1024
        if len(content) > max_size:
            return FileValidationResult(
                is_valid=False,
                filename=sanitize_filename(filename),
                file_size=len(content),
                file_type=extension.lstrip("."),
                mime_type="application/octet-stream",
                error=f"File too large (max {max_size // (1024 * 1024)}MB)",
            )
        
        # MIME type validation
        return await validate_file(
            content,
            filename,
            settings.ALLOWED_DOCUMENT_MIMES,
        )

    async def _build_folder_tree(
        self, folders: List[Any], parent_path: Optional[str]
    ) -> List[FolderItem]:
        """Efficiently build folder tree structure."""
        tree = []
        current_level = [f for f in folders if f.parent_path == parent_path]
        
        # Sort: folders first, then files, then by name
        current_level.sort(key=lambda x: (x.type != "folder", x.name.lower()))
        
        for item in current_level:
            if item.type == "folder":
                children = await self._build_folder_tree(folders, item.path)
                folder_item = FolderItem(
                    id=str(item.id),
                    name=item.name,
                    path=item.path,
                    type=item.type,
                    children=children,
                    created_at=item.created_at.isoformat() if item.created_at else None
                )
            else:
                folder_item = FolderItem(
                    id=str(item.id),
                    name=item.name,
                    path=item.path,
                    type=item.type,
                    size=item.size,
                    file_extension=item.file_extension,
                    created_at=item.created_at.isoformat() if item.created_at else None
                )
            
            tree.append(folder_item)
        
        return tree


# Dependency injection
async def get_folder_controller() -> FolderController:
    """Factory function for folder controller."""
    folder_service = FolderService()
    s3_service = S3Service()
    auth_service = AuthorizationService()
    rate_limiter = RateLimiter(
        max_requests=settings.USER_RATE_LIMIT_REQUESTS,
        window_seconds=settings.USER_RATE_LIMIT_WINDOW,
        scope="folders",
    )
    return FolderController(folder_service, s3_service, auth_service, rate_limiter)


# API Endpoints with minimal logic
@router.post("/folder-structure", status_code=status.HTTP_201_CREATED)
@handle_exceptions
async def create_folder_structure(
    folder_data: CreateFolderRequest,
    controller: FolderController = Depends(get_folder_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Create folder structure with security validation."""
    await PolicyService().authorize(
        current_user,
        "dms.document.upload",
        resource_type="folder",
        organization_id=folder_data.organization_id,
        project_id=folder_data.project_id,
    )
    result = await controller.create_folder_structure(folder_data, current_user)
    await AuditEventService().emit(
        action="folder.created",
        actor_id=current_user.id,
        resource_type="folder",
        organization_id=folder_data.organization_id,
        project_id=folder_data.project_id,
        after=result.model_dump(mode="json") if hasattr(result, "model_dump") else None,
    )
    return result


@router.get("/folder-structure", response_model=List[FolderItem])
@handle_exceptions
async def get_root_folder_structure(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    controller: FolderController = Depends(get_folder_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get root folder structure with efficient caching."""
    await PolicyService().authorize(
        current_user,
        "dms.document.view",
        resource_type="folder",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id,
        audit=False,
    )
    return await controller.get_folder_structure(organization_id, project_id, current_user)


@router.get("/folder-structure/{path:path}", response_model=FolderItem)
@handle_exceptions
async def get_folder_by_path(
    path: str,
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    controller: FolderController = Depends(get_folder_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get specific folder by path with security validation."""
    await PolicyService().authorize(
        current_user,
        "dms.document.view",
        resource_type="folder",
        resource_id=path,
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id,
        audit=False,
    )
    return await controller.folder_service.get_folder_with_children(
        path, organization_id or str(current_user.organization_id), project_id
    )


@router.post("/upload-file", status_code=status.HTTP_201_CREATED)
@handle_exceptions
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    name: str = Query(...),
    path: str = Query(...),
    file_extension: str = Query(...),
    organization_id: str = Query(...),
    project_id: str = Query(...),
    controller: FolderController = Depends(get_folder_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Secure file upload with validation."""
    await PolicyService().authorize(
        current_user,
        "dms.document.upload",
        resource_type="folder_file",
        organization_id=organization_id,
        project_id=project_id,
    )
    return await controller.upload_file(
        background_tasks, file, name, path, file_extension,
        organization_id, project_id, current_user
    )


@router.delete("/folder-structure/{path:path}", status_code=status.HTTP_204_NO_CONTENT)
@handle_exceptions
async def delete_folder(
    path: str,
    controller: FolderController = Depends(get_folder_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Secure folder/file deletion."""
    await PolicyService().authorize(
        current_user,
        "dms.document.delete",
        resource_type="folder",
        resource_id=path,
        organization_id=getattr(current_user, "organization_id", None),
    )
    await controller.delete_folder(path, current_user)
    await AuditEventService().emit(
        action="folder.deleted",
        actor_id=current_user.id,
        resource_type="folder",
        resource_id=path,
        organization_id=getattr(current_user, "organization_id", None),
    )


@router.get("/folder-structure/open-file")
@handle_exceptions
async def open_file(
    file_path: str = Query(...),
    controller: FolderController = Depends(get_folder_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Generate secure presigned URL for file access."""
    await PolicyService().authorize(
        current_user,
        "dms.document.download",
        resource_type="folder_file",
        resource_id=file_path,
        organization_id=getattr(current_user, "organization_id", None),
        audit=False,
    )
    return await controller.s3_service.generate_presigned_url(
        file_path, current_user, expire_minutes=60
    )
