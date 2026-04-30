# Improved permissions.py
"""
Comprehensive permission management system with proper security, validation, and scalability.
Addresses the incomplete implementation with full RBAC functionality.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Body, status
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union
import logging
from datetime import datetime

from ..core.security import get_current_user, CurrentUser, authorize_scope
from ..core.database import get_db
from ..services.permission_service import PermissionService
from ..services.role_service import RoleService
from ..services.authorization_service import AuthorizationService

# Import permission models from permission module
from ..models.permission import (
    Permission, PermissionCreate, PermissionUpdate, PermissionResponse,
    PermissionGroup, PermissionCategory, DEFAULT_PERMISSIONS,
    PermissionMatrix, PermissionCheck, RolePermission, UserRole
)

# Import role models from role module
from ..models.role import Role, RoleCreate, RoleUpdate

from ..utils.validation import validate_input, sanitize_text
from ..utils.error_handler import handle_exceptions, PermissionError
from ..utils.rate_limiter import RateLimiter


logger = logging.getLogger(__name__)
router = APIRouter()


class PermissionController:
    """Comprehensive permission management controller with RBAC support."""
    
    def __init__(
        self,
        permission_service: PermissionService,
        role_service: RoleService,
        auth_service: AuthorizationService,
        rate_limiter: RateLimiter
    ):
        self.permission_service = permission_service
        self.role_service = role_service
        self.auth_service = auth_service
        self.rate_limiter = rate_limiter

    async def get_permissions(
        self,
        pagination: Dict[str, int],
        filters: Dict[str, Any],
        current_user: CurrentUser
    ) -> Dict[str, Any]:
        """Get permissions with filtering and pagination."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "permissions:read")
            
            # Get permissions with efficient querying
            permissions, total_count = await self.permission_service.get_permissions_paginated(
                filters, pagination
            )
            
            return {
                "permissions": permissions,
                "total": total_count,
                "page": pagination["skip"] // pagination["limit"] + 1,
                "limit": pagination["limit"]
            }
            
        except PermissionError:
            raise
        except Exception as e:
            logger.error(f"Failed to get permissions: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Permission service temporarily unavailable"
            )

    async def create_permission(
        self,
        permission_data: PermissionCreate,
        current_user: CurrentUser
    ) -> Permission:
        """Create new permission with validation."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "permissions:create")
            
            # Input validation
            validated_data = await self._validate_permission_input(permission_data)
            
            # Check for existing permission
            existing = await self.permission_service.get_permission_by_name(
                validated_data.name
            )
            if existing:
                raise PermissionError(
                    f"Permission '{validated_data.name}' already exists",
                    status.HTTP_409_CONFLICT
                )
            
            # Create permission
            permission = await self.permission_service.create_permission(
                validated_data, current_user
            )
            
            logger.info(f"Permission created: {permission.name} by {current_user.id}")
            return permission
            
        except PermissionError:
            raise
        except Exception as e:
            logger.error(f"Failed to create permission: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Permission creation service temporarily unavailable"
            )

    async def update_permission(
        self,
        permission_id: str,
        update_data: PermissionUpdate,
        current_user: CurrentUser
    ) -> Permission:
        """Update existing permission."""
        try:
            # Authorization check
            await self.auth_service.require_permission(current_user, "permissions:update")
            
            # Get existing permission
            permission = await self.permission_service.get_permission_by_id(permission_id)
            if not permission:
                raise PermissionError(
                    "Permission not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Validate update data
            validated_update = await self._validate_permission_update(update_data)
            
            # Update permission
            updated_permission = await self.permission_service.update_permission(
                permission_id, validated_update, current_user
            )
            
            logger.info(f"Permission updated: {permission_id} by {current_user.id}")
            return updated_permission
            
        except PermissionError:
            raise
        except Exception as e:
            logger.error(f"Failed to update permission {permission_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Permission update service temporarily unavailable"
            )

    async def delete_permission(
        self,
        permission_id: str,
        current_user: CurrentUser
    ):
        """Delete permission with dependency checks."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "permissions:delete")
            
            # Get permission
            permission = await self.permission_service.get_permission_by_id(permission_id)
            if not permission:
                raise PermissionError(
                    "Permission not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Check for dependencies (roles using this permission)
            dependent_roles = await self.role_service.get_roles_with_permission(permission_id)
            if dependent_roles:
                role_names = [role.name for role in dependent_roles]
                raise PermissionError(
                    f"Cannot delete permission. Used by roles: {', '.join(role_names)}",
                    status.HTTP_409_CONFLICT
                )
            
            # Delete permission
            await self.permission_service.delete_permission(permission_id, current_user)
            
            logger.info(f"Permission deleted: {permission_id} by {current_user.id}")
            
        except PermissionError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete permission {permission_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Permission deletion service temporarily unavailable"
            )

    async def check_user_permission(
        self,
        user_id: str,
        permission_name: str,
        resource_context: Optional[Dict[str, Any]],
        current_user: CurrentUser
    ) -> PermissionCheck:
        """Check if user has specific permission."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check (can check own permissions or admin)
            if user_id != current_user.id:
                await self.auth_service.require_permission(
                    current_user, "permissions:check_others"
                )
            
            # Perform permission check
            has_permission = await self.auth_service.check_user_permission(
                user_id, permission_name, resource_context
            )
            
            # Get permission details for response
            permission_details = await self.permission_service.get_permission_by_name(
                permission_name
            )
            
            return PermissionCheck(
                user_id=user_id,
                permission=permission_name,
                granted=has_permission,
                context=resource_context,
                checked_at=datetime.utcnow(),
                permission_details=permission_details
            )
            
        except Exception as e:
            logger.error(f"Permission check failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Permission check service temporarily unavailable"
            )

    async def get_user_permissions(
        self,
        user_id: str,
        current_user: CurrentUser
    ) -> List[Permission]:
        """Get all permissions for a user through their roles."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            if user_id != current_user.id:
                await self.auth_service.require_permission(
                    current_user, "permissions:read_others"
                )
            
            # Get user permissions
            permissions = await self.permission_service.get_user_permissions(user_id)
            
            return permissions
            
        except Exception as e:
            logger.error(f"Failed to get user permissions: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="User permissions service temporarily unavailable"
            )

    async def get_permission_matrix(
        self,
        organization_id: Optional[str],
        current_user: CurrentUser
    ) -> PermissionMatrix:
        """Get comprehensive permission matrix for organization."""
        try:
            # Rate limiting for expensive operation
            await self.rate_limiter.check_user_limit(current_user.id, cost=10)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "permissions:matrix")
            
            # Get permission matrix
            matrix = await self.permission_service.build_permission_matrix(
                organization_id, current_user
            )
            
            return matrix
            
        except Exception as e:
            logger.error(f"Failed to get permission matrix: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Permission matrix service temporarily unavailable"
            )

    async def _validate_permission_input(
        self, permission_data: PermissionCreate
    ) -> PermissionCreate:
        """Validate and sanitize permission input aligned with PermissionCreate model."""
        return PermissionCreate(
            name=validate_input(
                permission_data.name,
                pattern=r'^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$',
                max_length=100
            ),
            description=sanitize_text(
                validate_input(permission_data.description, max_length=500)
            ) if permission_data.description else None,
            category=permission_data.category,
            resource=validate_input(
                permission_data.resource,
                pattern=r'^[a-z][a-z0-9_]*$',
                max_length=50
            ),
            action=permission_data.action,
            is_system=permission_data.is_system or False
        )

    async def _validate_permission_update(
        self, update_data: PermissionUpdate
    ) -> PermissionUpdate:
        """Validate permission update data."""
        validated_fields = {}
        
        if update_data.description is not None:
            validated_fields['description'] = sanitize_text(
                validate_input(update_data.description, max_length=500)
            )
        
        if update_data.category is not None:
            validated_fields['category'] = update_data.category
        
        if update_data.is_active is not None:
            validated_fields['is_active'] = update_data.is_active
        
        return PermissionUpdate(**validated_fields)


# Dependency injection
async def get_permission_controller() -> PermissionController:
    """Factory function for permission controller."""
    permission_service = PermissionService()
    role_service = RoleService()
    auth_service = AuthorizationService()
    rate_limiter = RateLimiter(
        requests_per_minute=200,
        window_seconds=3600
    )
    return PermissionController(permission_service, role_service, auth_service, rate_limiter)


# API Endpoints
@router.get("/permissions", response_model=Dict[str, Any])
@handle_exceptions
async def get_permissions(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    resource_type: Optional[str] = Query(None),
    scope: Optional[str] = Query(None),
    is_system: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    controller: PermissionController = Depends(get_permission_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get permissions with filtering and pagination."""
    filters = {
        'category': resource_type,
        'is_system': is_system,
        'search': search
    }
    pagination = {'skip': skip, 'limit': limit}
    
    return await controller.get_permissions(pagination, filters, current_user)


@router.post("/permissions", response_model=Permission)
@handle_exceptions
async def create_permission(
    permission_data: PermissionCreate,
    controller: PermissionController = Depends(get_permission_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Create new permission."""
    return await controller.create_permission(permission_data, current_user)


@router.get("/permissions/{permission_id}", response_model=Permission)
@handle_exceptions
async def get_permission(
    permission_id: str,
    controller: PermissionController = Depends(get_permission_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get specific permission by ID."""
    await controller.auth_service.require_permission(current_user, "permissions:read")
    return await controller.permission_service.get_permission_by_id(permission_id)


@router.put("/permissions/{permission_id}", response_model=Permission)
@handle_exceptions
async def update_permission(
    permission_id: str,
    update_data: PermissionUpdate,
    controller: PermissionController = Depends(get_permission_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update existing permission."""
    return await controller.update_permission(permission_id, update_data, current_user)


@router.delete("/permissions/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_exceptions
async def delete_permission(
    permission_id: str,
    controller: PermissionController = Depends(get_permission_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Delete permission."""
    await controller.delete_permission(permission_id, current_user)


@router.post("/permissions/check", response_model=PermissionCheck)
@handle_exceptions
async def check_permission(
    check_data: Dict[str, Any] = Body(...),
    controller: PermissionController = Depends(get_permission_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Check if user has specific permission."""
    user_id = check_data.get("user_id", current_user.id)
    permission_name = check_data.get("permission")
    resource_context = check_data.get("context")
    
    if not permission_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="permission is required"
        )
    
    return await controller.check_user_permission(
        user_id, permission_name, resource_context, current_user
    )


@router.get("/permissions/users/{user_id}", response_model=List[Permission])
@handle_exceptions
async def get_user_permissions(
    user_id: str,
    controller: PermissionController = Depends(get_permission_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get all permissions for a user."""
    return await controller.get_user_permissions(user_id, current_user)


@router.get("/permissions/matrix", response_model=PermissionMatrix)
@handle_exceptions
async def get_permission_matrix(
    organization_id: Optional[str] = Query(None),
    controller: PermissionController = Depends(get_permission_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get comprehensive permission matrix."""
    return await controller.get_permission_matrix(organization_id, current_user)


# Role management endpoints
@router.get("/roles", response_model=List[Role])
@handle_exceptions
async def get_roles(
    controller: PermissionController = Depends(get_permission_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get all roles."""
    await controller.auth_service.require_permission(current_user, "roles:read")
    return await controller.role_service.get_all_roles()


@router.post("/roles", response_model=Role)
@handle_exceptions
async def create_role(
    role_data: RoleCreate,
    controller: PermissionController = Depends(get_permission_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Create new role."""
    await controller.auth_service.require_permission(current_user, "roles:create")
    return await controller.role_service.create_role(role_data, current_user)


@router.post("/roles/{role_id}/permissions/{permission_id}")
@handle_exceptions
async def assign_permission_to_role(
    role_id: str,
    permission_id: str,
    controller: PermissionController = Depends(get_permission_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Assign permission to role."""
    await controller.auth_service.require_permission(current_user, "roles:manage_permissions")
    return await controller.role_service.assign_permission(role_id, permission_id, current_user)