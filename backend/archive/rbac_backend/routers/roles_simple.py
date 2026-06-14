# routers/roles_simple.py
"""
Simplified roles router with basic functionality to get the UI working.
This removes complex dependencies and focuses on core CRUD operations.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import List, Optional, Dict, Any
import logging

from ..core.security import get_current_user, CurrentUser
from ..services.role_service import RoleService
from ..services.permission_service import PermissionService
from ..models.role import Role, RoleCreate, RoleUpdate
from ..models.permission import Permission

logger = logging.getLogger(__name__)
router = APIRouter()

# Simple dependency injection
def get_role_service() -> RoleService:
    return RoleService()

def get_permission_service() -> PermissionService:
    return PermissionService()

@router.get("/roles", response_model=List[Role])
async def get_roles(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    is_system: Optional[bool] = Query(None),
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get roles with basic filtering and pagination."""
    try:
        filters = {
            'search': search,
            'is_system': is_system
        }
        pagination = {'skip': skip, 'limit': limit}
        
        roles, total_count = await role_service.get_roles_paginated(filters, pagination)
        return roles
        
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
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get specific role by ID."""
    try:
        role = await role_service.get_role_by_id(role_id)
        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Role not found"
            )
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
    current_user: CurrentUser = Depends(get_current_user)
):
    """Create new role."""
    try:
        role = await role_service.create_role(role_data, current_user)
        return role
        
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
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update role."""
    try:
        role = await role_service.update_role(role_id, update_data, current_user)
        return role
        
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
    current_user: CurrentUser = Depends(get_current_user)
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
    except Exception as e:
        logger.error(f"Failed to delete role {role_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Role deletion error: {str(e)}"
        )

@router.get("/roles/{role_id}/permissions", response_model=List[Permission])
async def get_role_permissions(
    role_id: str,
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get permissions for a role."""
    try:
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
    current_user: CurrentUser = Depends(get_current_user)
):
    """Add permission to role."""
    try:
        role = await role_service.add_permission_to_role(role_id, permission_id, current_user)
        return role
        
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
    current_user: CurrentUser = Depends(get_current_user)
):
    """Remove permission from role."""
    try:
        role = await role_service.remove_permission_from_role(role_id, permission_id, current_user)
        return role
        
    except Exception as e:
        logger.error(f"Failed to remove permission from role: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Remove permission error: {str(e)}"
        )
