"""
Secure email group management API with comprehensive validation, rate limiting,
and proper authorization. Addresses all security and performance issues.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
import logging
from datetime import datetime

from ..core.security import get_current_user, CurrentUser
from ..services.email_group_service import EmailGroupService
from ..services.authorization_service import AuthorizationService
from ..models.email_group import (
    EmailGroup, EmailGroupCreate, EmailGroupUpdate, EmailGroupListResponse
)
from ..utils.validation import validate_email, validate_input, sanitize_text, validate_object_id
from ..utils.error_handler import handle_exceptions, EmailGroupError
from ..utils.rate_limiter import RateLimiter
from ..utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)
router = APIRouter()


class EmailGroupController:
    """Secure email group controller with comprehensive validation and authorization."""
    
    def __init__(
        self,
        email_group_service: EmailGroupService,
        auth_service: AuthorizationService,
        rate_limiter: RateLimiter,
        audit_logger: AuditLogger
    ):
        self.email_group_service = email_group_service
        self.auth_service = auth_service
        self.rate_limiter = rate_limiter
        self.audit_logger = audit_logger

    async def get_email_groups(
        self,
        pagination: dict,
        filters: dict,
        current_user: CurrentUser
    ) -> EmailGroupListResponse:
        """Get email groups with proper authorization and pagination."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "email_groups:read")
            
            # Build authorized query based on user scope
            authorized_query = await self.auth_service.build_email_group_query(
                current_user, filters
            )
            
            # Get email groups with pagination
            groups, total_count = await self.email_group_service.get_groups_paginated(
                authorized_query, pagination
            )
            
            return EmailGroupListResponse(
                groups=groups,
                total=total_count,
                page=pagination["skip"] // pagination["limit"] + 1,
                limit=pagination["limit"]
            )
            
        except Exception as e:
            logger.error(f"Failed to get email groups: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Email group service temporarily unavailable"
            )

    async def create_email_group(
        self, group_data: EmailGroupCreate, current_user: CurrentUser
    ) -> EmailGroup:
        """Create email group with comprehensive validation."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=3)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "email_groups:create")
            
            # Validate and sanitize input
            validated_data = await self._validate_group_input(group_data)
            
            # Check authorization for organization/project context
            if validated_data.organization_id:
                await self.auth_service.check_organization_access(
                    current_user, validated_data.organization_id, "create_email_group"
                )
            
            if validated_data.project_id:
                await self.auth_service.check_project_access(
                    current_user, validated_data.project_id, "create_email_group"
                )
            
            # Check for duplicate group name within scope
            if await self.email_group_service.group_exists_in_scope(
                validated_data.name, validated_data.organization_id, validated_data.project_id
            ):
                raise EmailGroupError(
                    f"Email group '{validated_data.name}' already exists in this scope",
                    status.HTTP_409_CONFLICT
                )
            
            # Create email group
            group = await self.email_group_service.create_group(
                validated_data, current_user
            )
            
            # Audit log
            await self.audit_logger.log_email_group_created(
                current_user.id, group.id, group.name, len(group.emails)
            )
            
            return group
            
        except EmailGroupError:
            raise
        except Exception as e:
            logger.error(f"Email group creation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Email group creation service temporarily unavailable"
            )

    async def get_email_group(
        self, group_id: str, current_user: CurrentUser
    ) -> EmailGroup:
        """Get single email group with authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "email_groups:read")
            
            # Validate group ID
            validated_group_id = validate_object_id(group_id)
            
            # Get group
            group = await self.email_group_service.get_group_by_id(validated_group_id)
            if not group:
                raise EmailGroupError("Email group not found", status.HTTP_404_NOT_FOUND)
            
            # Check authorization for this specific group
            await self.auth_service.check_email_group_access(
                current_user, group, "read"
            )
            
            return group
            
        except EmailGroupError:
            raise
        except Exception as e:
            logger.error(f"Failed to get email group: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Email group service temporarily unavailable"
            )

    async def update_email_group(
        self, group_id: str, update_data: EmailGroupUpdate, current_user: CurrentUser
    ) -> EmailGroup:
        """Update email group with validation and authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "email_groups:update")
            
            # Validate group ID
            validated_group_id = validate_object_id(group_id)
            
            # Get existing group
            existing_group = await self.email_group_service.get_group_by_id(validated_group_id)
            if not existing_group:
                raise EmailGroupError("Email group not found", status.HTTP_404_NOT_FOUND)
            
            # Check authorization for this specific group
            await self.auth_service.check_email_group_access(
                current_user, existing_group, "update"
            )
            
            # Validate update data
            validated_update = await self._validate_group_update(update_data)
            
            # Check for name conflicts if name is being updated
            if (validated_update.name and 
                validated_update.name != existing_group.name):
                if await self.email_group_service.group_exists_in_scope(
                    validated_update.name,
                    existing_group.organization_id,
                    existing_group.project_id
                ):
                    raise EmailGroupError(
                        f"Email group '{validated_update.name}' already exists in this scope",
                        status.HTTP_409_CONFLICT
                    )
            
            # Update group
            updated_group = await self.email_group_service.update_group(
                validated_group_id, validated_update, current_user
            )
            
            # Audit log
            changed_fields = list(validated_update.dict(exclude_unset=True).keys())
            await self.audit_logger.log_email_group_updated(
                current_user.id, validated_group_id, changed_fields
            )
            
            return updated_group
            
        except EmailGroupError:
            raise
        except Exception as e:
            logger.error(f"Failed to update email group: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Email group update failed"
            )

    async def delete_email_group(
        self, group_id: str, current_user: CurrentUser
    ) -> dict:
        """Delete email group with authorization checks."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=5)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "email_groups:delete")
            
            # Validate group ID
            validated_group_id = validate_object_id(group_id)
            
            # Get group for validation
            group = await self.email_group_service.get_group_by_id(validated_group_id)
            if not group:
                raise EmailGroupError("Email group not found", status.HTTP_404_NOT_FOUND)
            
            # Check authorization for this specific group
            await self.auth_service.check_email_group_access(
                current_user, group, "delete"
            )
            
            # Delete group
            await self.email_group_service.delete_group(validated_group_id, current_user)
            
            # Audit log
            await self.audit_logger.log_email_group_deleted(
                current_user.id, validated_group_id, group.name
            )
            
            return {"message": "Email group deleted successfully"}
            
        except EmailGroupError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete email group: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Email group deletion failed"
            )

    async def resolve_group_emails(
        self, group_id: str, current_user: CurrentUser
    ) -> List[str]:
        """Resolve and return unique emails from a group."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "email_groups:read")
            
            # Validate group ID
            validated_group_id = validate_object_id(group_id)
            
            # Get group
            group = await self.email_group_service.get_group_by_id(validated_group_id)
            if not group:
                raise EmailGroupError("Email group not found", status.HTTP_404_NOT_FOUND)
            
            # Check authorization for this specific group
            await self.auth_service.check_email_group_access(
                current_user, group, "read"
            )
            
            # Return deduplicated emails
            return await self.email_group_service.get_unique_emails(group.emails)
            
        except EmailGroupError:
            raise
        except Exception as e:
            logger.error(f"Failed to resolve group emails: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Email resolution service temporarily unavailable"
            )

    async def _validate_group_input(self, group_data: EmailGroupCreate) -> EmailGroupCreate:
        """Validate and sanitize email group input."""
        # Validate name
        validated_name = sanitize_text(
            validate_input(group_data.name, max_length=200, required=True)
        )
        
        # Validate and deduplicate emails
        validated_emails = []
        seen_emails = set()
        
        for email in (group_data.emails or []):
            try:
                validated_email = validate_email(email.strip())
                if validated_email not in seen_emails:
                    seen_emails.add(validated_email)
                    validated_emails.append(validated_email)
            except ValueError:
                # Skip invalid emails but log them
                logger.warning(f"Invalid email skipped: {email}")
                continue
        
        # Validate description
        validated_description = None
        if group_data.description:
            validated_description = sanitize_text(
                validate_input(group_data.description, max_length=1000)
            )
        
        return EmailGroupCreate(
            name=validated_name,
            description=validated_description,
            emails=validated_emails,
            organization_id=group_data.organization_id,
            project_id=group_data.project_id
        )

    async def _validate_group_update(self, update_data: EmailGroupUpdate) -> EmailGroupUpdate:
        """Validate email group update data."""
        validated_fields = {}
        
        if update_data.name is not None:
            validated_fields['name'] = sanitize_text(
                validate_input(update_data.name, max_length=200, required=True)
            )
        
        if update_data.description is not None:
            validated_fields['description'] = sanitize_text(
                validate_input(update_data.description, max_length=1000)
            )
        
        if update_data.emails is not None:
            validated_emails = []
            seen_emails = set()
            
            for email in update_data.emails:
                try:
                    validated_email = validate_email(email.strip())
                    if validated_email not in seen_emails:
                        seen_emails.add(validated_email)
                        validated_emails.append(validated_email)
                except ValueError:
                    logger.warning(f"Invalid email skipped in update: {email}")
                    continue
            
            validated_fields['emails'] = validated_emails
        
        return EmailGroupUpdate(**validated_fields)


# Dependency injection
async def get_email_group_controller() -> EmailGroupController:
    """Factory function for email group controller."""
    email_group_service = EmailGroupService()
    auth_service = AuthorizationService()
    rate_limiter = RateLimiter()
    audit_logger = AuditLogger()
    
    return EmailGroupController(
        email_group_service, auth_service, rate_limiter, audit_logger
    )


# API Endpoints
@router.get("/email/groups", response_model=EmailGroupListResponse)
@handle_exceptions
async def get_email_groups(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    controller: EmailGroupController = Depends(get_email_group_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get email groups with filtering and pagination."""
    filters = {
        "organization_id": organization_id,
        "project_id": project_id,
        "search": search
    }
    pagination = {"skip": skip, "limit": limit}
    
    return await controller.get_email_groups(pagination, filters, current_user)


@router.post("/email/groups", response_model=EmailGroup)
@handle_exceptions
async def create_email_group(
    group_data: EmailGroupCreate,
    controller: EmailGroupController = Depends(get_email_group_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Create new email group with validation."""
    return await controller.create_email_group(group_data, current_user)


@router.get("/email/groups/{group_id}", response_model=EmailGroup)
@handle_exceptions
async def get_email_group(
    group_id: str,
    controller: EmailGroupController = Depends(get_email_group_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get specific email group by ID."""
    return await controller.get_email_group(group_id, current_user)


@router.put("/email/groups/{group_id}", response_model=EmailGroup)
@handle_exceptions
async def update_email_group(
    group_id: str,
    update_data: EmailGroupUpdate,
    controller: EmailGroupController = Depends(get_email_group_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update email group with validation."""
    return await controller.update_email_group(group_id, update_data, current_user)


@router.delete("/email/groups/{group_id}")
@handle_exceptions
async def delete_email_group(
    group_id: str,
    controller: EmailGroupController = Depends(get_email_group_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Delete email group with authorization checks."""
    return await controller.delete_email_group(group_id, current_user)


@router.post("/email/groups/{group_id}/resolve", response_model=List[str])
@handle_exceptions
async def resolve_group_emails(
    group_id: str,
    controller: EmailGroupController = Depends(get_email_group_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Resolve unique emails from group."""
    return await controller.resolve_group_emails(group_id, current_user)
