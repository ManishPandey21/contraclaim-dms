# Improved tags.py
"""
Secure tag and subtag management with comprehensive validation, proper authorization,
and clean architecture. Addresses ObjectId handling and performance issues.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Optional, Dict, Any
import logging
from datetime import datetime

from ..core.security import get_current_user, CurrentUser
from ..core.database import get_db
from ..services.tag_service import TagService
from ..services.authorization_service import AuthorizationService
from ..services.audit_event_service import AuditEventService
from ..services.policy_service import PolicyService
from ..models.tag import (
    Tag, TagCreate, TagUpdate, TagResponse, TagListResponse,
    Subtag, SubtagCreate, SubtagUpdate, SubtagListResponse
)
from ..utils.validation import validate_input, sanitize_text, validate_object_id
from ..utils.error_handler import handle_exceptions, TagError, AuthorizationError
from ..utils.rate_limiter import RateLimiter
from ..utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)
router = APIRouter()


class TagController:
    """Secure tag controller with comprehensive validation and authorization."""

    def __init__(
        self,
        tag_service: TagService,
        auth_service: AuthorizationService,
        rate_limiter: RateLimiter,
        audit_logger: AuditLogger
    ):
        self.tag_service = tag_service
        self.auth_service = auth_service
        self.rate_limiter = rate_limiter
        self.audit_logger = audit_logger

    async def create_tag(
        self,
        tag_data: TagCreate,
        current_user: CurrentUser
    ) -> Tag:
        """Create tag with comprehensive validation."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)

            # Authorization check
            await self.auth_service.require_permission(current_user, "tags:create")

            # Validate and sanitize input
            validated_data = await self._validate_tag_input(tag_data)

            # Determine organization context
            organization_id = await self._resolve_organization_context(
                validated_data.organization_id, current_user
            )

            # Check for duplicate tag name within organization
            existing_tag = await self.tag_service.get_tag_by_name_and_org(
                validated_data.name, organization_id
            )
            if existing_tag:
                raise TagError(
                    f"Tag '{validated_data.name}' already exists in this organization",
                    status.HTTP_409_CONFLICT
                )

            # Create tag with resolved organization context
            tag = await self.tag_service.create_tag(
                validated_data, organization_id, current_user
            )

            # Audit log
            await self.audit_logger.log_tag_created(
                current_user.id, tag.id, tag.name, organization_id
            )

            return tag

        except TagError:
            raise
        except AuthorizationError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Tag creation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Tag creation service temporarily unavailable"
            )

    async def get_tags(
        self,
        pagination: Dict[str, int],
        filters: Dict[str, Any],
        current_user: CurrentUser
    ) -> TagListResponse:
        """Get tags with filtering and authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)

            # Authorization check
            await self.auth_service.require_permission(current_user, "tags:read")

            # Build authorized query based on user scope
            authorized_query = await self.auth_service.build_tag_query(
                current_user, filters
            )

            # Get tags with pagination
            tags, total_count = await self.tag_service.get_tags_paginated(
                authorized_query, pagination
            )

            return TagListResponse(
                tags=tags,
                total=total_count,
                page=pagination["skip"] // pagination["limit"] + 1,
                limit=pagination["limit"]
            )

        except AuthorizationError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get tags: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Tag service temporarily unavailable"
            )

    async def get_tag(
        self,
        tag_id: str,
        current_user: CurrentUser
    ) -> Tag:
        """Get single tag with authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)

            # Authorization check
            await self.auth_service.require_permission(current_user, "tags:read")

            # Validate tag ID
            try:
                validated_tag_id = validate_object_id(tag_id)
            except ValueError as ve:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(ve)
                )

            # Get tag
            tag = await self.tag_service.get_tag_by_id(validated_tag_id)
            if not tag:
                raise TagError("Tag not found", status.HTTP_404_NOT_FOUND)

            # Check authorization for this specific tag
            await self.auth_service.check_tag_access(current_user, tag, "read")

            return tag

        except TagError:
            raise
        except AuthorizationError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get tag {tag_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Tag service temporarily unavailable"
            )

    async def update_tag(
        self,
        tag_id: str,
        update_data: TagUpdate,
        current_user: CurrentUser
    ) -> Tag:
        """Update tag with validation and authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)

            # Authorization check
            await self.auth_service.require_permission(current_user, "tags:update")

            # Validate tag ID
            try:
                validated_tag_id = validate_object_id(tag_id)
            except ValueError as ve:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(ve)
                )

            # Get existing tag
            existing_tag = await self.tag_service.get_tag_by_id(validated_tag_id)
            if not existing_tag:
                raise TagError("Tag not found", status.HTTP_404_NOT_FOUND)

            # Check authorization for this specific tag
            await self.auth_service.check_tag_access(current_user, existing_tag, "update")

            # Validate update data
            validated_update = await self._validate_tag_update(update_data)

            # Check for name conflicts if name is being updated
            if validated_update.name and validated_update.name != existing_tag.name:
                existing_with_name = await self.tag_service.get_tag_by_name_and_org(
                    validated_update.name, existing_tag.organization_id
                )
                if existing_with_name:
                    raise TagError(
                        f"Tag '{validated_update.name}' already exists in this organization",
                        status.HTTP_409_CONFLICT
                    )

            # Update tag
            updated_tag = await self.tag_service.update_tag(
                validated_tag_id, validated_update, current_user
            )

            # Audit log
            changed_fields = self._get_changed_fields(existing_tag, validated_update)
            await self.audit_logger.log_tag_updated(
                current_user.id, validated_tag_id, changed_fields
            )

            return updated_tag

        except TagError:
            raise
        except AuthorizationError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to update tag {tag_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Tag update failed"
            )

    async def delete_tag(
        self,
        tag_id: str,
        current_user: CurrentUser
    ) -> Dict[str, str]:
        """Delete tag with cascade deletion of subtags."""
        try:
            # Rate limiting for destructive operations
            await self.rate_limiter.check_user_limit(current_user.id, cost=5)

            # Authorization check
            await self.auth_service.require_permission(current_user, "tags:delete")

            # Validate tag ID
            try:
                validated_tag_id = validate_object_id(tag_id)
            except ValueError as ve:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(ve)
                )

            # Get tag for validation
            tag = await self.tag_service.get_tag_by_id(validated_tag_id)
            if not tag:
                raise TagError("Tag not found", status.HTTP_404_NOT_FOUND)

            # Check authorization for this specific tag
            await self.auth_service.check_tag_access(current_user, tag, "delete")

            # Check for dependencies (usage in documents, etc.)
            usage_count = await self.tag_service.count_tag_usage(validated_tag_id)
            if usage_count > 0:
                raise TagError(
                    f"Cannot delete tag. It is used in {usage_count} documents",
                    status.HTTP_409_CONFLICT
                )

            # Delete tag and associated subtags
            deleted_subtags_count = await self.tag_service.delete_tag_with_subtags(
                validated_tag_id, current_user
            )

            # Audit log
            await self.audit_logger.log_tag_deleted(
                current_user.id, validated_tag_id, tag.name, deleted_subtags_count
            )

            message = f"Tag deleted successfully"
            if deleted_subtags_count > 0:
                message += f" along with {deleted_subtags_count} subtags"

            return {"message": message}

        except TagError:
            raise
        except AuthorizationError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to delete tag {tag_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Tag deletion failed"
            )

    async def create_subtag(
        self,
        tag_id: str,
        subtag_data: SubtagCreate,
        current_user: CurrentUser
    ) -> Subtag:
        """Create subtag with validation."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)

            # Authorization check
            await self.auth_service.require_permission(current_user, "tags:create")

            # Validate tag ID
            try:
                validated_tag_id = validate_object_id(tag_id)
            except ValueError as ve:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(ve)
                )

            # Get parent tag
            parent_tag = await self.tag_service.get_tag_by_id(validated_tag_id)
            if not parent_tag:
                raise TagError("Parent tag not found", status.HTTP_404_NOT_FOUND)

            # Check authorization for parent tag
            await self.auth_service.check_tag_access(current_user, parent_tag, "create_subtag")

            # Validate subtag input
            validated_data = await self._validate_subtag_input(subtag_data)

            # Check for duplicate subtag name within tag
            existing_subtag = await self.tag_service.get_subtag_by_name_and_tag(
                validated_data.name, validated_tag_id
            )
            if existing_subtag:
                raise TagError(
                    f"Subtag '{validated_data.name}' already exists in this tag",
                    status.HTTP_409_CONFLICT
                )

            # Create subtag
            subtag = await self.tag_service.create_subtag(
                validated_tag_id, validated_data, current_user
            )

            # Audit log
            await self.audit_logger.log_subtag_created(
                current_user.id, subtag.id, subtag.name, validated_tag_id
            )

            return subtag

        except TagError:
            raise
        except AuthorizationError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Subtag creation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Subtag creation service temporarily unavailable"
            )

    async def get_subtags(
        self,
        tag_id: str,
        pagination: Dict[str, int],
        current_user: CurrentUser
    ) -> SubtagListResponse:
        """Get subtags for a tag with pagination."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)

            # Authorization check
            await self.auth_service.require_permission(current_user, "tags:read")

            # Validate tag ID
            try:
                validated_tag_id = validate_object_id(tag_id)
            except ValueError as ve:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(ve)
                )

            # Get parent tag for authorization
            parent_tag = await self.tag_service.get_tag_by_id(validated_tag_id)
            if not parent_tag:
                raise TagError("Parent tag not found", status.HTTP_404_NOT_FOUND)

            # Check authorization for parent tag
            await self.auth_service.check_tag_access(current_user, parent_tag, "read")

            # Get subtags with pagination
            subtags, total_count = await self.tag_service.get_subtags_paginated(
                validated_tag_id, pagination
            )

            return SubtagListResponse(
                subtags=subtags,
                total=total_count,
                page=pagination["skip"] // pagination["limit"] + 1,
                limit=pagination["limit"]
            )

        except TagError:
            raise
        except AuthorizationError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get subtags for tag {tag_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Subtag service temporarily unavailable"
            )

    async def update_subtag(
        self,
        subtag_id: str,
        update_data: SubtagUpdate,
        current_user: CurrentUser
    ) -> Subtag:
        """Update subtag with validation."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)

            # Authorization check
            await self.auth_service.require_permission(current_user, "tags:update")

            # Validate subtag ID
            try:
                validated_subtag_id = validate_object_id(subtag_id)
            except ValueError as ve:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(ve)
                )

            # Get existing subtag and parent tag
            existing_subtag = await self.tag_service.get_subtag_by_id(validated_subtag_id)
            if not existing_subtag:
                raise TagError("Subtag not found", status.HTTP_404_NOT_FOUND)

            parent_tag = await self.tag_service.get_tag_by_id(existing_subtag.tag_id)
            if not parent_tag:
                raise TagError("Parent tag not found", status.HTTP_404_NOT_FOUND)

            # Check authorization
            await self.auth_service.check_tag_access(current_user, parent_tag, "update")

            # Validate update data
            validated_update = await self._validate_subtag_update(update_data)

            # Check for name conflicts if name is being updated
            if validated_update.name and validated_update.name != existing_subtag.name:
                existing_with_name = await self.tag_service.get_subtag_by_name_and_tag(
                    validated_update.name, existing_subtag.tag_id
                )
                if existing_with_name:
                    raise TagError(
                        f"Subtag '{validated_update.name}' already exists in this tag",
                        status.HTTP_409_CONFLICT
                    )

            # Update subtag
            updated_subtag = await self.tag_service.update_subtag(
                validated_subtag_id, validated_update, current_user
            )

            # Audit log
            changed_fields = self._get_changed_subtag_fields(existing_subtag, validated_update)
            await self.audit_logger.log_subtag_updated(
                current_user.id, validated_subtag_id, changed_fields
            )

            return updated_subtag

        except TagError:
            raise
        except AuthorizationError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to update subtag {subtag_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Subtag update failed"
            )

    async def delete_subtag(
        self,
        subtag_id: str,
        current_user: CurrentUser
    ) -> Dict[str, str]:
        """Delete subtag with validation."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=3)

            # Authorization check
            await self.auth_service.require_permission(current_user, "tags:delete")

            # Validate subtag ID
            try:
                validated_subtag_id = validate_object_id(subtag_id)
            except ValueError as ve:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(ve)
                )

            # Get subtag and parent tag
            subtag = await self.tag_service.get_subtag_by_id(validated_subtag_id)
            if not subtag:
                raise TagError("Subtag not found", status.HTTP_404_NOT_FOUND)

            parent_tag = await self.tag_service.get_tag_by_id(subtag.tag_id)
            if not parent_tag:
                raise TagError("Parent tag not found", status.HTTP_404_NOT_FOUND)

            # Check authorization
            await self.auth_service.check_tag_access(current_user, parent_tag, "delete")

            # Check for usage dependencies
            usage_count = await self.tag_service.count_subtag_usage(validated_subtag_id)
            if usage_count > 0:
                raise TagError(
                    f"Cannot delete subtag. It is used in {usage_count} documents",
                    status.HTTP_409_CONFLICT
                )

            # Delete subtag
            await self.tag_service.delete_subtag(validated_subtag_id, current_user)

            # Audit log
            await self.audit_logger.log_subtag_deleted(
                current_user.id, validated_subtag_id, subtag.name
            )

            return {"message": "Subtag deleted successfully"}

        except TagError:
            raise
        except AuthorizationError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to delete subtag {subtag_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Subtag deletion failed"
            )

    async def _validate_tag_input(self, tag_data: TagCreate) -> TagCreate:
        """Validate and sanitize tag input."""
        return TagCreate(
            name=sanitize_text(
                validate_input(tag_data.name.strip(), max_length=100, required=True)
            ),
            description=sanitize_text(
                validate_input(tag_data.description, max_length=500)
            ) if tag_data.description else None,
            color=validate_input(
                tag_data.color, pattern=r'^#[0-9A-Fa-f]{6}$'
            ) if tag_data.color else None,
            organization_id=tag_data.organization_id
        )

    async def _validate_tag_update(self, update_data: TagUpdate) -> TagUpdate:
        """Validate tag update data."""
        validated_fields = {}

        if update_data.name is not None:
            validated_fields['name'] = sanitize_text(
                validate_input(update_data.name.strip(), max_length=100, required=True)
            )

        if update_data.description is not None:
            validated_fields['description'] = sanitize_text(
                validate_input(update_data.description, max_length=500)
            )

        if update_data.color is not None:
            validated_fields['color'] = validate_input(
                update_data.color, pattern=r'^#[0-9A-Fa-f]{6}$'
            )

        return TagUpdate(**validated_fields)

    async def _validate_subtag_input(self, subtag_data: SubtagCreate) -> SubtagCreate:
        """Validate and sanitize subtag input."""
        return SubtagCreate(
            name=sanitize_text(
                validate_input(subtag_data.name.strip(), max_length=100, required=True)
            ),
            description=sanitize_text(
                validate_input(subtag_data.description, max_length=500)
            ) if subtag_data.description else None
        )

    async def _validate_subtag_update(self, update_data: SubtagUpdate) -> SubtagUpdate:
        """Validate subtag update data."""
        validated_fields = {}

        if update_data.name is not None:
            validated_fields['name'] = sanitize_text(
                validate_input(update_data.name.strip(), max_length=100, required=True)
            )

        if update_data.description is not None:
            validated_fields['description'] = sanitize_text(
                validate_input(update_data.description, max_length=500)
            )

        return SubtagUpdate(**validated_fields)

    async def _resolve_organization_context(
        self, requested_org_id: Optional[str], current_user: CurrentUser
    ) -> str:
        """Resolve the organization context for tag creation."""
        user_roles = set(current_user.roles or [])

        if "superadmin" in user_roles:
            # Superadmin can create global or org-specific tags
            return requested_org_id or "global"

        elif "orgadmin" in user_roles or "orguser" in user_roles:
            # Org users must use their organization
            if requested_org_id:
                await self.auth_service.check_organization_access(
                    current_user, requested_org_id, "create_tag"
                )
                return requested_org_id
            else:
                return str(current_user.organization_id)

        elif "projectadmin" in user_roles or "projectuser" in user_roles:
            # Project-scoped roles can create tags; organization must be their own
            org_id = requested_org_id or getattr(current_user, "organization_id", None)
            if not org_id:
                raise TagError(
                    "Organization context required for project-level tag creation",
                    status.HTTP_403_FORBIDDEN
                )
            if requested_org_id:
                await self.auth_service.check_organization_access(
                    current_user, requested_org_id, "create_tag"
                )
                return requested_org_id
            return str(org_id)

        else:
            raise TagError(
                "Not authorized to create tags",
                status.HTTP_403_FORBIDDEN
            )

    def _get_changed_fields(self, original: Tag, update: TagUpdate) -> List[str]:
        """Get list of fields that were changed for tags."""
        changed_fields = []

        for field_name in update.__fields_set__:
            if hasattr(original, field_name):
                old_value = getattr(original, field_name)
                new_value = getattr(update, field_name)
                if old_value != new_value:
                    changed_fields.append(field_name)

        return changed_fields

    def _get_changed_subtag_fields(self, original: Subtag, update: SubtagUpdate) -> List[str]:
        """Get list of fields that were changed for subtags."""
        changed_fields = []

        for field_name in update.__fields_set__:
            if hasattr(original, field_name):
                old_value = getattr(original, field_name)
                new_value = getattr(update, field_name)
                if old_value != new_value:
                    changed_fields.append(field_name)

        return changed_fields


# Dependency injection
async def get_tag_controller() -> TagController:
    """Factory function for tag controller."""
    tag_service = TagService()
    auth_service = AuthorizationService()
    rate_limiter = RateLimiter(
        max_requests=120,
        window_seconds=3600,
        scope="tags",
    )
    audit_logger = AuditLogger()

    return TagController(tag_service, auth_service, rate_limiter, audit_logger)


# API Endpoints - Tags
@router.get("/tags", response_model=TagListResponse)
@handle_exceptions
async def get_tags(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    organization_id: Optional[str] = Query(None),
    controller: TagController = Depends(get_tag_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get tags with filtering and pagination."""
    await PolicyService().authorize(
        current_user,
        "dms.document.view",
        resource_type="tag",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        audit=False,
    )
    filters = {
        'search': search,
        'organization_id': organization_id
    }
    pagination = {'skip': skip, 'limit': limit}

    return await controller.get_tags(pagination, filters, current_user)


@router.post("/tags", response_model=Tag)
@handle_exceptions
async def create_tag(
    tag_data: TagCreate,
    controller: TagController = Depends(get_tag_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Create new tag with validation."""
    await PolicyService().authorize(
        current_user,
        "dms.document.edit_metadata",
        resource_type="tag",
        organization_id=tag_data.organization_id or getattr(current_user, "organization_id", None),
    )
    tag = await controller.create_tag(tag_data, current_user)
    await AuditEventService().emit(
        action="tag.created",
        actor_id=current_user.id,
        resource_type="tag",
        resource_id=str(getattr(tag, "id", "") or ""),
        organization_id=getattr(tag, "organization_id", None),
        project_id=getattr(tag, "project_id", None),
        after=tag.model_dump(mode="json") if hasattr(tag, "model_dump") else None,
    )
    return tag


@router.get("/tags/{tag_id}", response_model=Tag)
@handle_exceptions
async def get_tag(
    tag_id: str,
    controller: TagController = Depends(get_tag_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get specific tag by ID."""
    tag = await controller.get_tag(tag_id, current_user)
    await PolicyService().authorize(
        current_user,
        "dms.document.view",
        resource_type="tag",
        resource_id=tag_id,
        organization_id=getattr(tag, "organization_id", None),
        project_id=getattr(tag, "project_id", None),
        audit=False,
    )
    return tag


@router.put("/tags/{tag_id}", response_model=Tag)
@handle_exceptions
async def update_tag(
    tag_id: str,
    update_data: TagUpdate,
    controller: TagController = Depends(get_tag_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update tag with validation."""
    before = await controller.tag_service.get_tag_by_id(tag_id)
    await PolicyService().authorize(
        current_user,
        "dms.document.edit_metadata",
        resource_type="tag",
        resource_id=tag_id,
        organization_id=getattr(before, "organization_id", None),
        project_id=getattr(before, "project_id", None),
    )
    tag = await controller.update_tag(tag_id, update_data, current_user)
    await AuditEventService().emit(
        action="tag.updated",
        actor_id=current_user.id,
        resource_type="tag",
        resource_id=tag_id,
        organization_id=getattr(tag, "organization_id", None),
        project_id=getattr(tag, "project_id", None),
        before=before.model_dump(mode="json") if hasattr(before, "model_dump") else None,
        after=tag.model_dump(mode="json") if hasattr(tag, "model_dump") else None,
    )
    return tag


@router.delete("/tags/{tag_id}")
@handle_exceptions
async def delete_tag(
    tag_id: str,
    controller: TagController = Depends(get_tag_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Delete tag with cascade deletion of subtags."""
    before = await controller.tag_service.get_tag_by_id(tag_id)
    await PolicyService().authorize(
        current_user,
        "dms.document.edit_metadata",
        resource_type="tag",
        resource_id=tag_id,
        organization_id=getattr(before, "organization_id", None),
        project_id=getattr(before, "project_id", None),
    )
    result = await controller.delete_tag(tag_id, current_user)
    await AuditEventService().emit(
        action="tag.deleted",
        actor_id=current_user.id,
        resource_type="tag",
        resource_id=tag_id,
        organization_id=getattr(before, "organization_id", None),
        project_id=getattr(before, "project_id", None),
        before=before.model_dump(mode="json") if hasattr(before, "model_dump") else None,
    )
    return result


# API Endpoints - Subtags
@router.get("/tags/{tag_id}/subtags", response_model=SubtagListResponse)
@handle_exceptions
async def get_subtags(
    tag_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    controller: TagController = Depends(get_tag_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get subtags for a tag with pagination."""
    pagination = {'skip': skip, 'limit': limit}
    return await controller.get_subtags(tag_id, pagination, current_user)


@router.post("/tags/{tag_id}/subtags", response_model=Subtag)
@handle_exceptions
async def create_subtag(
    tag_id: str,
    subtag_data: SubtagCreate,
    controller: TagController = Depends(get_tag_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Create subtag for a tag."""
    return await controller.create_subtag(tag_id, subtag_data, current_user)


@router.put("/subtags/{subtag_id}", response_model=Subtag)
@handle_exceptions
async def update_subtag(
    subtag_id: str,
    update_data: SubtagUpdate,
    controller: TagController = Depends(get_tag_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update subtag with validation."""
    return await controller.update_subtag(subtag_id, update_data, current_user)


@router.delete("/subtags/{subtag_id}")
@handle_exceptions
async def delete_subtag(
    subtag_id: str,
    controller: TagController = Depends(get_tag_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Delete subtag with validation."""
    return await controller.delete_subtag(subtag_id, current_user)
