# Improved roles.py
"""
Secure role management with comprehensive validation, proper authorization,
and clean architecture. Addresses security vulnerabilities and performance issues.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Optional, Dict, Any
import logging
from datetime import datetime

from ..core.security import get_current_user, CurrentUser
from ..core.database import get_db
from ..services.role_service import RoleService
from ..services.permission_service import PermissionService
from ..services.authorization_service import AuthorizationService
from ..models.role import (
    Role, RoleCreate, RoleUpdate, RoleResponse, RoleListResponse,
    RolePermissionAssignment
)
from ..models.permission import Permission
from ..utils.validation import validate_input, sanitize_text, validate_object_id
from ..utils.error_handler import handle_exceptions, RoleError
from ..utils.rate_limiter import RateLimiter
from ..utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)
router = APIRouter()


class RoleController:
    """Secure role controller with comprehensive validation and authorization."""
    
    def __init__(
        self,
        role_service: RoleService,
        permission_service: PermissionService,
        auth_service: AuthorizationService,
        rate_limiter: RateLimiter,
        audit_logger: AuditLogger
    ):
        self.role_service = role_service
        self.permission_service = permission_service
        self.auth_service = auth_service
        self.rate_limiter = rate_limiter
        self.audit_logger = audit_logger

    async def create_role(
        self,
        role_data: RoleCreate,
        current_user: CurrentUser
    ) -> Role:
        """Create role with comprehensive validation."""
        try:
            # Rate limiting for role creation
            await self.rate_limiter.check_user_limit(current_user.id, cost=5)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "roles:create")
            
            # Validate and sanitize input
            validated_data = await self._validate_role_input(role_data)
            
            # Check for duplicate role name
            existing_role = await self.role_service.get_role_by_name(validated_data.name)
            if existing_role:
                raise RoleError(
                    f"Role '{validated_data.name}' already exists",
                    status.HTTP_409_CONFLICT
                )
            
            # Validate permissions if provided
            if validated_data.permissions:
                await self._validate_permissions_exist(validated_data.permissions)
            
            # Create role
            role = await self.role_service.create_role(validated_data, current_user)
            
            # Audit log
            await self.audit_logger.log_role_created(
                current_user.id, role.id, role.name, validated_data.permissions or []
            )
            
            return role
            
        except RoleError:
            raise
        except Exception as e:
            logger.error(f"Role creation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Role creation service temporarily unavailable"
            )

    async def get_roles(
        self,
        pagination: Dict[str, int],
        filters: Dict[str, Any],
        current_user: CurrentUser
    ) -> RoleListResponse:
        """Get roles with filtering and pagination."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "roles:read")
            
            # Get roles with pagination
            roles, total_count = await self.role_service.get_roles_paginated(
                filters, pagination
            )
            
            return RoleListResponse(
                roles=roles,
                total=total_count,
                page=pagination["skip"] // pagination["limit"] + 1,
                limit=pagination["limit"]
            )
            
        except Exception as e:
            logger.error(f"Failed to get roles: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Role service temporarily unavailable"
            )

    async def get_role(
        self,
        role_id: str,
        current_user: CurrentUser
    ) -> Role:
        """Get single role with authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "roles:read")
            
            # Validate role ID
            validated_role_id = validate_object_id(role_id)
            
            # Get role
            role = await self.role_service.get_role_by_id(validated_role_id)
            if not role:
                raise RoleError("Role not found", status.HTTP_404_NOT_FOUND)
            
            return role
            
        except RoleError:
            raise
        except Exception as e:
            logger.error(f"Failed to get role {role_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Role service temporarily unavailable"
            )

    async def update_role(
        self,
        role_id: str,
        update_data: RoleUpdate,
        current_user: CurrentUser
    ) -> Role:
        """Update role with validation and authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=3)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "roles:update")
            
            # Validate role ID
            validated_role_id = validate_object_id(role_id)
            
            # Get existing role
            existing_role = await self.role_service.get_role_by_id(validated_role_id)
            if not existing_role:
                raise RoleError("Role not found", status.HTTP_404_NOT_FOUND)
            
            # Validate update data
            validated_update = await self._validate_role_update(update_data)
            
            # Check for name conflicts if name is being updated
            if validated_update.name and validated_update.name != existing_role.name:
                existing_with_name = await self.role_service.get_role_by_name(validated_update.name)
                if existing_with_name:
                    raise RoleError(
                        f"Role '{validated_update.name}' already exists",
                        status.HTTP_409_CONFLICT
                    )
            
            # Update role
            updated_role = await self.role_service.update_role(
                validated_role_id, validated_update, current_user
            )
            
            # Audit log
            changed_fields = self._get_changed_fields(existing_role, validated_update)
            await self.audit_logger.log_role_updated(
                current_user.id, validated_role_id, changed_fields
            )
            
            return updated_role
            
        except RoleError:
            raise
        except Exception as e:
            logger.error(f"Failed to update role {role_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Role update failed"
            )

    async def delete_role(
        self,
        role_id: str,
        current_user: CurrentUser
    ) -> Dict[str, str]:
        """Delete role with dependency checks."""
        try:
            # Rate limiting for destructive operations
            await self.rate_limiter.check_user_limit(current_user.id, cost=10)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "roles:delete")
            
            # Validate role ID
            validated_role_id = validate_object_id(role_id)
            
            # Get role for validation
            role = await self.role_service.get_role_by_id(validated_role_id)
            if not role:
                raise RoleError("Role not found", status.HTTP_404_NOT_FOUND)
            
            # Check for dependencies (users with this role)
            user_count = await self.role_service.count_users_with_role(validated_role_id)
            if user_count > 0:
                raise RoleError(
                    f"Cannot delete role. {user_count} users are assigned to this role",
                    status.HTTP_409_CONFLICT
                )
            
            # Delete role
            await self.role_service.delete_role(validated_role_id, current_user)
            
            # Audit log
            await self.audit_logger.log_role_deleted(
                current_user.id, validated_role_id, role.name
            )
            
            return {"message": "Role deleted successfully"}
            
        except RoleError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete role {role_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Role deletion failed"
            )

    async def get_role_permissions(
        self,
        role_id: str,
        current_user: CurrentUser
    ) -> List[Permission]:
        """Get permissions for a role."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "roles:read")
            
            # Validate role ID
            validated_role_id = validate_object_id(role_id)
            
            # Get role permissions
            permissions = await self.role_service.get_role_permissions(validated_role_id)
            
            return permissions
            
        except Exception as e:
            logger.error(f"Failed to get role permissions for {role_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Role permissions service temporarily unavailable"
            )

    async def update_role_permissions(
        self,
        role_id: str,
        permission_ids: List[str],
        current_user: CurrentUser
    ) -> Role:
        """Update role permissions with validation."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=5)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "roles:update")
            
            # Validate role ID
            validated_role_id = validate_object_id(role_id)
            
            # Validate permission IDs
            validated_permission_ids = [validate_object_id(pid) for pid in permission_ids]
            
            # Verify all permissions exist
            await self._validate_permissions_exist(validated_permission_ids)
            
            # Get existing role
            existing_role = await self.role_service.get_role_by_id(validated_role_id)
            if not existing_role:
                raise RoleError("Role not found", status.HTTP_404_NOT_FOUND)
            
            # Update role permissions
            updated_role = await self.role_service.update_role_permissions(
                validated_role_id, validated_permission_ids, current_user
            )
            
            # Audit log
            await self.audit_logger.log_role_permissions_updated(
                current_user.id, validated_role_id, 
                existing_role.permissions or [], validated_permission_ids
            )
            
            return updated_role
            
        except RoleError:
            raise
        except Exception as e:
            logger.error(f"Failed to update role permissions for {role_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Role permission update failed"
            )

    async def add_role_permission(
        self,
        role_id: str,
        permission_id: str,
        current_user: CurrentUser
    ) -> Role:
        """Add single permission to role."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "roles:update")
            
            # Validate IDs
            validated_role_id = validate_object_id(role_id)
            validated_permission_id = validate_object_id(permission_id)
            
            # Verify role and permission exist
            role = await self.role_service.get_role_by_id(validated_role_id)
            if not role:
                raise RoleError("Role not found", status.HTTP_404_NOT_FOUND)
            
            permission = await self.permission_service.get_permission_by_id(validated_permission_id)
            if not permission:
                raise RoleError("Permission not found", status.HTTP_404_NOT_FOUND)
            
            # Add permission to role
            updated_role = await self.role_service.add_permission_to_role(
                validated_role_id, validated_permission_id, current_user
            )
            
            # Audit log
            await self.audit_logger.log_permission_added_to_role(
                current_user.id, validated_role_id, validated_permission_id
            )
            
            return updated_role
            
        except RoleError:
            raise
        except Exception as e:
            logger.error(f"Failed to add permission to role: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Role permission addition failed"
            )

    async def remove_role_permission(
        self,
        role_id: str,
        permission_id: str,
        current_user: CurrentUser
    ) -> Role:
        """Remove permission from role."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "roles:update")
            
            # Validate IDs
            validated_role_id = validate_object_id(role_id)
            validated_permission_id = validate_object_id(permission_id)
            
            # Verify role exists
            role = await self.role_service.get_role_by_id(validated_role_id)
            if not role:
                raise RoleError("Role not found", status.HTTP_404_NOT_FOUND)
            
            # Remove permission from role
            updated_role = await self.role_service.remove_permission_from_role(
                validated_role_id, validated_permission_id, current_user
            )
            
            # Audit log
            await self.audit_logger.log_permission_removed_from_role(
                current_user.id, validated_role_id, validated_permission_id
            )
            
            return updated_role
            
        except RoleError:
            raise
        except Exception as e:
            logger.error(f"Failed to remove permission from role: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Role permission removal failed"
            )

    async def _validate_role_input(self, role_data: RoleCreate) -> RoleCreate:
        """Validate and sanitize role input."""
        return RoleCreate(
            name=sanitize_text(
                validate_input(role_data.name, max_length=100, required=True)
            ),
            description=sanitize_text(
                validate_input(role_data.description, max_length=500)
            ) if role_data.description else None,
            permissions=[
                validate_object_id(pid) for pid in (role_data.permissions or [])
            ],
            is_system=role_data.is_system or False
        )

    async def _validate_role_update(self, update_data: RoleUpdate) -> RoleUpdate:
        """Validate role update data."""
        validated_fields = {}
        
        if update_data.name is not None:
            validated_fields['name'] = sanitize_text(
                validate_input(update_data.name, max_length=100, required=True)
            )
        
        if update_data.description is not None:
            validated_fields['description'] = sanitize_text(
                validate_input(update_data.description, max_length=500)
            )
        
        if update_data.permissions is not None:
            validated_fields['permissions'] = [
                validate_object_id(pid) for pid in update_data.permissions
            ]
        
        # Prevent modification of system roles by non-superadmin
        if update_data.is_system is not None:
            validated_fields['is_system'] = update_data.is_system
        
        return RoleUpdate(**validated_fields)

    async def _validate_permissions_exist(self, permission_ids: List[str]):
        """Validate that all permissions exist."""
        for permission_id in permission_ids:
            permission = await self.permission_service.get_permission_by_id(permission_id)
            if not permission:
                raise RoleError(
                    f"Permission '{permission_id}' not found",
                    status.HTTP_400_BAD_REQUEST
                )

    def _get_changed_fields(self, original: Role, update: RoleUpdate) -> List[str]:
        """Get list of fields that were changed."""
        changed_fields = []
        
        for field_name in update.__fields_set__:
            if hasattr(original, field_name):
                old_value = getattr(original, field_name)
                new_value = getattr(update, field_name)
                if old_value != new_value:
                    changed_fields.append(field_name)
        
        return changed_fields


# Dependency injection
async def get_role_controller() -> RoleController:
    """Factory function for role controller."""
    role_service = RoleService()
    permission_service = PermissionService()
    auth_service = AuthorizationService()
    rate_limiter = RateLimiter(
        requests_per_minute=100,
        window_seconds=3600
    )
    audit_logger = AuditLogger()
    
    return RoleController(
        role_service, permission_service, auth_service,
        rate_limiter, audit_logger
    )


# API Endpoints
@router.post("/roles", response_model=Role)
@handle_exceptions
async def create_role(
    role_data: RoleCreate,
    controller: RoleController = Depends(get_role_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Create new role with validation."""
    return await controller.create_role(role_data, current_user)


@router.get("/roles", response_model=RoleListResponse)
@handle_exceptions
async def get_roles(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    is_system: Optional[bool] = Query(None),
    controller: RoleController = Depends(get_role_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get roles with filtering and pagination."""
    filters = {
        'search': search,
        'is_system': is_system
    }
    pagination = {'skip': skip, 'limit': limit}
    
    return await controller.get_roles(pagination, filters, current_user)


@router.get("/roles/{role_id}", response_model=Role)
@handle_exceptions
async def get_role(
    role_id: str,
    controller: RoleController = Depends(get_role_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get specific role by ID."""
    return await controller.get_role(role_id, current_user)


@router.put("/roles/{role_id}", response_model=Role)
@handle_exceptions
async def update_role(
    role_id: str,
    update_data: RoleUpdate,
    controller: RoleController = Depends(get_role_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update role with validation."""
    return await controller.update_role(role_id, update_data, current_user)


@router.delete("/roles/{role_id}")
@handle_exceptions
async def delete_role(
    role_id: str,
    controller: RoleController = Depends(get_role_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Delete role with dependency checks."""
    return await controller.delete_role(role_id, current_user)


@router.get("/roles/{role_id}/permissions", response_model=List[Permission])
@handle_exceptions
async def get_role_permissions(
    role_id: str,
    controller: RoleController = Depends(get_role_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get permissions for a role."""
    return await controller.get_role_permissions(role_id, current_user)


@router.put("/roles/{role_id}/permissions", response_model=Role)
@handle_exceptions
async def update_role_permissions(
    role_id: str,
    permission_assignment: RolePermissionAssignment,
    controller: RoleController = Depends(get_role_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update role permissions."""
    return await controller.update_role_permissions(
        role_id, permission_assignment.permission_ids, current_user
    )


@router.put("/roles/{role_id}/permissions/{permission_id}", response_model=Role)
@handle_exceptions
async def add_role_permission(
    role_id: str,
    permission_id: str,
    controller: RoleController = Depends(get_role_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Add permission to role."""
    return await controller.add_role_permission(role_id, permission_id, current_user)


@router.delete("/roles/{role_id}/permissions/{permission_id}", response_model=Role)
@handle_exceptions
async def remove_role_permission(
    role_id: str,
    permission_id: str,
    controller: RoleController = Depends(get_role_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Remove permission from role."""
    return await controller.remove_role_permission(role_id, permission_id, current_user)