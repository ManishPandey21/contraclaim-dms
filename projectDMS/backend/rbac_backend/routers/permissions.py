# routers/permissions_simple.py
"""
Simplified permissions router with basic functionality to get the UI working.
This removes complex dependencies and focuses on core CRUD operations.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Optional, Dict, Any
import logging

from ..core.security import get_current_user, CurrentUser, require_permission
from ..services.permission_service import PermissionService
from ..services.role_service import RoleService, RoleServiceError
from ..models.permission import Permission, PermissionCreate, PermissionUpdate
from ..models.role import Role, RoleCreate, RoleUpdate

logger = logging.getLogger(__name__)
router = APIRouter()

# Simple dependency injection
def get_permission_service() -> PermissionService:
    return PermissionService()

def get_role_service() -> RoleService:
    return RoleService()

@router.post("/permissions/check", response_model=Dict[str, Any])
async def check_permission_endpoint(
    payload: Dict[str, Any],
    permission_service: PermissionService = Depends(get_permission_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Check whether the current user (or provided user_id) has a given permission.
    """
    permission_name = payload.get("permission") or payload.get("permission_name")
    target_user_id = payload.get("user_id") or current_user.id
    if not permission_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="permission is required",
        )
    granted = await permission_service.user_has_permission(target_user_id, permission_name)
    return {"granted": granted, "permission": permission_name, "user_id": target_user_id}

# Permissions endpoints
@router.get("/permissions", response_model=Dict[str, Any])
async def get_permissions(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None),
    permission_service: PermissionService = Depends(get_permission_service),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("permissions:read")),
):
    """Get permissions with basic filtering and pagination."""
    try:
        filters = {'search': search}
        pagination = {'skip': skip, 'limit': limit}
        
        permissions, total_count = await permission_service.get_permissions_paginated(
            filters, pagination
        )
        
        return {
            "permissions": permissions,
            "total": total_count,
            "page": pagination["skip"] // pagination["limit"] + 1,
            "limit": pagination["limit"]
        }
        
    except Exception as e:
        logger.error(f"Failed to get permissions: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Permission service error: {str(e)}"
        )

@router.get("/permissions/{permission_id}", response_model=Permission)
async def get_permission(
    permission_id: str,
    permission_service: PermissionService = Depends(get_permission_service),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("permissions:read")),
):
    """Get specific permission by ID."""
    try:
        permission = await permission_service.get_permission_by_id(permission_id)
        if not permission:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Permission not found"
            )
        return permission
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get permission {permission_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Permission service error: {str(e)}"
        )

@router.post("/permissions", response_model=Permission)
async def create_permission(
    permission_data: PermissionCreate,
    permission_service: PermissionService = Depends(get_permission_service),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("roles:superuser")),
):
    """Create new permission."""
    try:
        permission = await permission_service.create_permission(permission_data, current_user)
        return permission
        
    except Exception as e:
        logger.error(f"Failed to create permission: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Permission creation error: {str(e)}"
        )

@router.put("/permissions/{permission_id}", response_model=Permission)
async def update_permission(
    permission_id: str,
    update_data: PermissionUpdate,
    permission_service: PermissionService = Depends(get_permission_service),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("roles:superuser")),
):
    """Update existing permission."""
    try:
        permission = await permission_service.update_permission(permission_id, update_data, current_user)
        return permission
        
    except Exception as e:
        logger.error(f"Failed to update permission {permission_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Permission update error: {str(e)}"
        )

@router.delete("/permissions/{permission_id}")
async def delete_permission(
    permission_id: str,
    permission_service: PermissionService = Depends(get_permission_service),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("roles:superuser")),
):
    """Delete permission."""
    try:
        success = await permission_service.delete_permission(permission_id, current_user)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Permission not found"
            )
        return {"message": "Permission deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete permission {permission_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Permission deletion error: {str(e)}"
        )

# Roles endpoints
@router.get("/roles", response_model=List[Role])
async def get_roles(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("roles:read")),
):
    """Get roles with basic filtering and pagination."""
    try:
        if not role_service.can_view_roles(current_user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view roles")

        filters = {'search': search}
        role_names = {str(r).lower() for r in (current_user.roles or [])}
        if "orgadmin" in role_names:
            filters["scope_in"] = {"organization", "project"}
        elif "projectadmin" in role_names:
            filters["scope"] = "project"
        pagination = {'skip': skip, 'limit': limit}
        
        roles, total_count = await role_service.get_roles_paginated(filters, pagination)
        return await role_service.filter_roles_for_user(current_user, roles)
        
    except Exception as e:
        logger.error(f"Failed to get roles: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Role service error: {str(e)}"
        )

@router.get("/roles/{role_id}", response_model=Role)
async def get_role(
    role_id: str,
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("roles:read")),
):
    """Get specific role by ID."""
    try:
        role = await role_service.get_role_by_id(role_id)
        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Role not found"
            )
        if not await role_service.can_view_role(current_user, role):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this role")
        return role
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get role {role_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Role service error: {str(e)}"
        )

@router.post("/roles", response_model=Role)
async def create_role(
    role_data: RoleCreate,
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("roles:create")),
):
    """Create new role."""
    try:
        role = await role_service.create_role(role_data, current_user)
        return role
        
    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create role: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Role creation error: {str(e)}"
        )

@router.put("/roles/{role_id}", response_model=Role)
async def update_role(
    role_id: str,
    update_data: RoleUpdate,
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("roles:update")),
):
    """Update role."""
    try:
        role = await role_service.update_role(role_id, update_data, current_user)
        return role
        
    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update role {role_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Role update error: {str(e)}"
        )

@router.delete("/roles/{role_id}")
async def delete_role(
    role_id: str,
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("roles:delete")),
):
    """Delete role."""
    try:
        success = await role_service.delete_role(role_id, current_user)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Role not found"
            )
        return {"message": "Role deleted successfully"}
        
    except HTTPException:
        raise
    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete role {role_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Role deletion error: {str(e)}"
        )

# Role-Permission management
@router.get("/roles/{role_id}/permissions", response_model=List[Permission])
async def get_role_permissions(
    role_id: str,
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("roles:read")),
):
    """Get permissions for a role."""
    try:
        role = await role_service.get_role_by_id(role_id)
        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Role not found"
            )
        if not await role_service.can_view_role(current_user, role):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view this role")
        permissions = await role_service.get_role_permissions(role_id)
        return permissions
        
    except Exception as e:
        logger.error(f"Failed to get role permissions for {role_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Role permissions error: {str(e)}"
        )

@router.post("/roles/{role_id}/permissions/{permission_id}", response_model=Role)
async def add_role_permission(
    role_id: str,
    permission_id: str,
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("roles:assign")),
):
    """Add permission to role."""
    try:
        role = await role_service.add_permission_to_role(role_id, permission_id, current_user)
        return role
        
    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to add permission to role: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Add permission error: {str(e)}"
        )

@router.delete("/roles/{role_id}/permissions/{permission_id}", response_model=Role)
async def remove_role_permission(
    role_id: str,
    permission_id: str,
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("roles:assign")),
):
    """Remove permission from role."""
    try:
        role = await role_service.remove_permission_from_role(role_id, permission_id, current_user)
        return role
        
    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to remove permission from role: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Remove permission error: {str(e)}"
        )
