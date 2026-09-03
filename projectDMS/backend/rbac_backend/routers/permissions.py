# routers/permissions_simple.py
"""
Simplified permissions router with basic functionality to get the UI working.
This removes complex dependencies and focuses on core CRUD operations.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from typing import List, Optional, Dict, Any
import logging

from ..core.security import get_current_user, CurrentUser, require_permission
from ..services.permission_service import PermissionService
from ..services.role_service import RoleService, RoleServiceError
from ..services.audit_event_service import AuditEventService
from ..services.policy_service import PolicyService
from ..services.step_up_service import require_step_up
from ..models.permission import Permission, PermissionCreate, PermissionUpdate
from ..models.role import Role, RoleCreate, RoleUpdate

logger = logging.getLogger(__name__)
router = APIRouter()

# Simple dependency injection
def get_permission_service() -> PermissionService:
    return PermissionService()

def get_role_service() -> RoleService:
    return RoleService()

def get_policy_service() -> PolicyService:
    return PolicyService()

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

    except HTTPException:
        raise
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
    request: Request,
    permission_service: PermissionService = Depends(get_permission_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
    _: None = Depends(require_permission("roles:superuser")),
):
    """Create new permission."""
    await require_step_up(request, current_user, action="platform.permission.manage")
    await policy.authorize(current_user, "platform.permission.manage", resource_type="permission")
    try:
        permission = await permission_service.create_permission(permission_data, current_user)
        await AuditEventService().emit(
            action="permission.created",
            actor_id=current_user.id,
            resource_type="permission",
            resource_id=str(getattr(permission, "id", None) or getattr(permission, "name", "")),
            after=permission.model_dump(mode="json") if hasattr(permission, "model_dump") else None,
        )
        return permission

    except HTTPException:
        raise
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
    request: Request,
    permission_service: PermissionService = Depends(get_permission_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
    _: None = Depends(require_permission("roles:superuser")),
):
    """Update existing permission."""
    await require_step_up(request, current_user, action="platform.permission.manage")
    await policy.authorize(current_user, "platform.permission.manage", resource_type="permission", resource_id=permission_id)
    try:
        before = await permission_service.get_permission_by_id(permission_id)
        permission = await permission_service.update_permission(permission_id, update_data, current_user)
        await AuditEventService().emit(
            action="permission.updated",
            actor_id=current_user.id,
            resource_type="permission",
            resource_id=permission_id,
            before=before.model_dump(mode="json") if hasattr(before, "model_dump") else None,
            after=permission.model_dump(mode="json") if hasattr(permission, "model_dump") else None,
        )
        return permission

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update permission {permission_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Permission update error: {str(e)}"
        )

@router.delete("/permissions/{permission_id}")
async def delete_permission(
    permission_id: str,
    request: Request,
    permission_service: PermissionService = Depends(get_permission_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
    _: None = Depends(require_permission("roles:superuser")),
):
    """Delete permission."""
    await require_step_up(request, current_user, action="platform.permission.manage")
    await policy.authorize(current_user, "platform.permission.manage", resource_type="permission", resource_id=permission_id)
    try:
        before = await permission_service.get_permission_by_id(permission_id)
        success = await permission_service.delete_permission(permission_id, current_user)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Permission not found"
            )
        await AuditEventService().emit(
            action="permission.deleted",
            actor_id=current_user.id,
            resource_type="permission",
            resource_id=permission_id,
            before=before.model_dump(mode="json") if hasattr(before, "model_dump") else None,
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
