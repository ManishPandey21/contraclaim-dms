# routers/roles_simple.py
"""
Simplified roles router with basic functionality to get the UI working.
This removes complex dependencies and focuses on core CRUD operations.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from typing import List, Optional, Dict, Any
import logging

from ..core.security import get_current_user, CurrentUser, require_permission
from ..services.role_service import RoleService, RoleServiceError
from ..services.permission_service import PermissionService
from ..services.audit_event_service import AuditEventService
from ..services.policy_service import PolicyService
from ..services.step_up_service import require_step_up
from ..models.role import Role, RoleCreate, RoleUpdate
from ..models.permission import Permission
from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter()

# Rate limiter for role mutations (strict limits for security)
role_mutation_limiter = RateLimiter(max_requests=20, window_seconds=60, scope="roles")

async def check_role_mutation_rate_limit(current_user: CurrentUser = Depends(get_current_user)):
    await role_mutation_limiter.check_user_limit(current_user.id)

# Simple dependency injection
def get_role_service() -> RoleService:
    return RoleService()

def get_permission_service() -> PermissionService:
    return PermissionService()

def get_policy_service() -> PolicyService:
    return PolicyService()

def _role_field(role: Any, key: str) -> Any:
    if isinstance(role, dict):
        return role.get(key) or role.get(key.replace("_", ""))
    return getattr(role, key, None)

def _role_policy_scope(role: Any) -> tuple[Optional[str], Optional[str]]:
    organization_id = (
        _role_field(role, "organization_id")
        or _role_field(role, "organizationId")
    )
    project_id = (
        _role_field(role, "project_id")
        or _role_field(role, "projectId")
    )
    return (
        str(organization_id) if organization_id else None,
        str(project_id) if project_id else None,
    )

def _create_role_policy_scope(role_data: RoleCreate, current_user: CurrentUser) -> tuple[Optional[str], Optional[str]]:
    roles = {str(role).lower() for role in (current_user.roles or [])}
    if "superadmin" in roles:
        return (
            str(role_data.organization_id) if role_data.organization_id else None,
            str(role_data.project_id) if role_data.project_id else None,
        )
    if "orgadmin" in roles:
        return (str(current_user.organization_id) if current_user.organization_id else None, None)
    if "projectadmin" in roles:
        projects = [str(project) for project in (current_user.projects or []) if project]
        requested_project = str(role_data.project_id) if role_data.project_id else None
        project_id = requested_project or (projects[0] if projects else None)
        return (str(current_user.organization_id) if current_user.organization_id else None, project_id)
    return (
        str(role_data.organization_id) if role_data.organization_id else None,
        str(role_data.project_id) if role_data.project_id else None,
    )

@router.get("/roles", response_model=List[Role])
async def get_roles(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    is_system: Optional[bool] = Query(None),
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("roles:read")),
):
    """Get roles with basic filtering and pagination."""
    try:
        if not role_service.can_view_roles(current_user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view roles")

        filters = {
            'search': search,
            'is_system': is_system
        }
        role_names = {str(r).lower() for r in (current_user.roles or [])}
        if "orgadmin" in role_names:
            filters["scope_in"] = {"organization", "project"}
        elif "projectadmin" in role_names:
            filters["scope"] = "project"
        pagination = {'skip': skip, 'limit': limit}

        roles, total_count = await role_service.get_roles_paginated(filters, pagination)
        return await role_service.filter_roles_for_user(current_user, roles)

    except HTTPException:
        raise
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
    request: Request,
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
    _: None = Depends(require_permission("roles:create")),
    __: None = Depends(check_role_mutation_rate_limit),
):
    """Create new role."""
    await require_step_up(request, current_user, action="platform.role.manage")
    organization_id, project_id = _create_role_policy_scope(role_data, current_user)
    await policy.authorize(
        current_user,
        "roles:create",
        resource_type="role",
        organization_id=organization_id,
        project_id=project_id,
    )
    try:
        role = await role_service.create_role(role_data, current_user)
        await AuditEventService().emit(
            action="role.created",
            actor_id=current_user.id,
            resource_type="role",
            resource_id=str(getattr(role, "id", None) or getattr(role, "_id", "") or role_data.name),
            after=role.model_dump(mode="json") if hasattr(role, "model_dump") else None,
        )
        return role

    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except HTTPException:
        raise
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
    request: Request,
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
    _: None = Depends(require_permission("roles:update")),
    __: None = Depends(check_role_mutation_rate_limit),
):
    """Update role."""
    await require_step_up(request, current_user, action="platform.role.manage")
    try:
        before = await role_service.get_role_by_id(role_id)
        if not before:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
        organization_id, project_id = _role_policy_scope(before)
        await policy.authorize(
            current_user,
            "roles:update",
            resource_type="role",
            resource_id=role_id,
            organization_id=organization_id,
            project_id=project_id,
        )
        role = await role_service.update_role(role_id, update_data, current_user)
        await AuditEventService().emit(
            action="role.updated",
            actor_id=current_user.id,
            resource_type="role",
            resource_id=role_id,
            before=before.model_dump(mode="json") if hasattr(before, "model_dump") else None,
            after=role.model_dump(mode="json") if hasattr(role, "model_dump") else None,
        )
        return role

    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update role {role_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Role update error: {str(e)}"
        )

@router.delete("/roles/{role_id}")
async def delete_role(
    role_id: str,
    request: Request,
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
    _: None = Depends(require_permission("roles:delete")),
    __: None = Depends(check_role_mutation_rate_limit),
):
    """Delete role."""
    await require_step_up(request, current_user, action="platform.role.manage")
    try:
        before = await role_service.get_role_by_id(role_id)
        if not before:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
        organization_id, project_id = _role_policy_scope(before)
        await policy.authorize(
            current_user,
            "roles:delete",
            resource_type="role",
            resource_id=role_id,
            organization_id=organization_id,
            project_id=project_id,
        )
        success = await role_service.delete_role(role_id, current_user)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Role not found"
            )
        await AuditEventService().emit(
            action="role.deleted",
            actor_id=current_user.id,
            resource_type="role",
            resource_id=role_id,
            before=before.model_dump(mode="json") if hasattr(before, "model_dump") else None,
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

    except HTTPException:
        raise
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
    request: Request,
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
    _: None = Depends(require_permission("roles:assign")),
    __: None = Depends(check_role_mutation_rate_limit),
):
    """Add permission to role."""
    await require_step_up(request, current_user, action="platform.role.manage")
    try:
        before = await role_service.get_role_by_id(role_id)
        if not before:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
        organization_id, project_id = _role_policy_scope(before)
        await policy.authorize(
            current_user,
            "roles:assign",
            resource_type="role",
            resource_id=role_id,
            organization_id=organization_id,
            project_id=project_id,
        )
        role = await role_service.add_permission_to_role(role_id, permission_id, current_user)
        await AuditEventService().emit(
            action="role.permission_added",
            actor_id=current_user.id,
            resource_type="role",
            resource_id=role_id,
            before=before.model_dump(mode="json") if hasattr(before, "model_dump") else None,
            after=role.model_dump(mode="json") if hasattr(role, "model_dump") else None,
            metadata={"permission_id": permission_id},
        )
        return role

    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except HTTPException:
        raise
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
    request: Request,
    role_service: RoleService = Depends(get_role_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
    _: None = Depends(require_permission("roles:assign")),
    __: None = Depends(check_role_mutation_rate_limit),
):
    """Remove permission from role."""
    await require_step_up(request, current_user, action="platform.role.manage")
    try:
        before = await role_service.get_role_by_id(role_id)
        if not before:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
        organization_id, project_id = _role_policy_scope(before)
        await policy.authorize(
            current_user,
            "roles:assign",
            resource_type="role",
            resource_id=role_id,
            organization_id=organization_id,
            project_id=project_id,
        )
        role = await role_service.remove_permission_from_role(role_id, permission_id, current_user)
        await AuditEventService().emit(
            action="role.permission_removed",
            actor_id=current_user.id,
            resource_type="role",
            resource_id=role_id,
            before=before.model_dump(mode="json") if hasattr(before, "model_dump") else None,
            after=role.model_dump(mode="json") if hasattr(role, "model_dump") else None,
            metadata={"permission_id": permission_id},
        )
        return role

    except RoleServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to remove permission from role: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Remove permission error: {str(e)}"
        )
