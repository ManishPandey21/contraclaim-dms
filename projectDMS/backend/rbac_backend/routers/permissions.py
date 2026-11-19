# routers/permissions_simple.py
"""
Simplified permissions router with basic functionality to get the UI working.
This removes complex dependencies and focuses on core CRUD operations.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from typing import List, Optional, Dict, Any
import logging

from ..core.security import (
    CurrentUser,
    _normalize_roles_list,
    _without_revoked_roles,
    get_current_user,
    require_permission,
)
from ..services.permission_service import PermissionService
from ..services.role_service import RoleService, RoleServiceError
from ..services.audit_event_service import AuditEventService
from ..services.policy_service import PolicyService
from ..services.step_up_service import require_step_up
from ..models.permission import Permission, PermissionCreate, PermissionUpdate
from ..models.role import Role, RoleCreate, RoleUpdate
from ..utils.error_handler import BaseDomainError
from ..utils.rate_limiter import RateLimiter
from ..services.account_status import account_is_active
from ..services.authorization_service import AuthorizationService
from ..services.role_grants import user_memberships
from ..services.scope_service import ScopeService
from bson import ObjectId
from types import SimpleNamespace

logger = logging.getLogger(__name__)
router = APIRouter()

# Simple dependency injection
def get_permission_service() -> PermissionService:
    return PermissionService()

def get_role_service() -> RoleService:
    return RoleService()

def get_policy_service() -> PolicyService:
    return PolicyService()

#: Another user's permissions are an administrative read (held by Super Admin and the
#: organisation and project administrators, not by ordinary members).
PERMISSION_CHECK_ADMIN_PERMISSION = "roles:read"
permission_check_limiter = RateLimiter(max_requests=60, window_seconds=60, scope="permission_check_other_user")


def _text_field(payload: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


async def _find_user(db: Any, user_id: str) -> Optional[Dict[str, Any]]:
    """The one user stored under ``user_id`` (string or ObjectId form); ``None`` if none or
    if both forms exist (two different users: never pick one, review finding 6)."""
    candidates: List[Any] = [user_id]
    try:
        candidates.append(ObjectId(user_id))
    except Exception:
        pass
    found = await db.users.find({"_id": {"$in": candidates}}).to_list(length=2)
    return found[0] if len(found) == 1 else None


#: Same status and body whether the caller lacks the authority or the scope does not
#: resolve, so the route answers nothing about another tenant's projects (review finding 1).
_SCOPE_REFUSED = "Not authorized: scope_denied"


@router.post("/permissions/check", response_model=Dict[str, Any])
async def check_permission_endpoint(
    payload: Dict[str, Any],
    permission_service: PermissionService = Depends(get_permission_service),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Whether a user holds a permission.

    - **Your own** (no ``user_id``, or your own): unchanged; any signed-in user. An
      optional ``organization_id`` / ``project_id`` narrows the answer to that scope.
    - **Another user's** is an administrative read: ``roles:read`` in a named
      ``organization_id`` or ``project_id`` (Super Admin may omit the scope), and the
      user must be a member of that scope and visible to you under the user-read rules.
      A user who does not exist and one you may not see get the same 404, so the route
      cannot be used to enumerate another tenant's users. Every such check is audited.
    """
    permission_name = _text_field(payload, "permission", "permission_name")
    if not permission_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="permission is required",
        )
    organization_id = _text_field(payload, "organization_id")
    project_id = _text_field(payload, "project_id")
    target_user_id = _text_field(payload, "user_id") or str(current_user.id)
    if target_user_id == str(current_user.id):
        # The permission-level rule the gates use (PolicyService.has_permission): the
        # explicit-grant permissions need an explicit grant (no wildcard, no Super Admin),
        # and a role counts only where it applies.
        granted = await policy.has_permission(
            current_user, permission_name, organization_id=organization_id, project_id=project_id
        )
        return {"granted": granted, "permission": permission_name, "user_id": target_user_id}

    db = await permission_service._get_db()
    audit = AuditEventService(db)

    async def record(result: str, reason: str, granted: Optional[bool] = None) -> None:
        await audit.emit(
            action="permission.check.other_user",
            actor_id=str(current_user.id),
            resource_type="user_permissions",
            resource_id=target_user_id,
            organization_id=organization_id,
            project_id=project_id,
            result=result,
            reason=reason,
            metadata={"permission": permission_name, "granted": granted},
        )

    try:
        await permission_check_limiter.check_user_limit(str(current_user.id))
    except HTTPException:
        await record("deny", "rate_limited")
        raise

    scope_service = ScopeService()
    is_superadmin = scope_service.is_superadmin(current_user)
    try:
        # Authority first, before anything about the named scope is read: a caller
        # without roles:read anywhere learns nothing (review finding 1).
        if not is_superadmin and not await policy.has_permission(current_user, PERMISSION_CHECK_ADMIN_PERMISSION):
            await record("deny", "not_authorized")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_SCOPE_REFUSED)
        if not (organization_id or project_id) and not is_superadmin:
            await record("deny", "scope_required")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Checking another user's permissions needs an organization_id or project_id",
            )
        if project_id:
            resolved_org, _ = await permission_service.resolve_request_scope(db, organization_id, project_id)
            if resolved_org == PermissionService.UNRESOLVED_ORGANISATION:
                await record("deny", "scope_unresolved")
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_SCOPE_REFUSED)
            organization_id = resolved_org
        try:
            await policy.authorize(
                current_user,
                PERMISSION_CHECK_ADMIN_PERMISSION,
                resource_type="user_permissions",
                resource_id=target_user_id,
                organization_id=organization_id,
                project_id=project_id,
            )
        except HTTPException:
            await record("deny", "not_authorized")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_SCOPE_REFUSED)
        # Organisation-wide answers need organisation-wide authority: a project
        # administrator names their project (review finding 3; membership rows do not
        # reach CurrentUser.projects, so check_user_access alone cannot confine them).
        if (
            not project_id
            and organization_id
            and not await scope_service.has_organization_wide_scope(current_user, organization_id=organization_id)
        ):
            await record("deny", "organization_wide_scope_required")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_SCOPE_REFUSED)

        # The same reads whether or not the user exists, so a missing and a hidden user
        # cost about the same (review finding 2).
        target = await _find_user(db, target_user_id)
        probe: Dict[str, Any] = target or {"_id": "\x00no-such-user", "roles": [], "projects": []}
        organisations, projects = await user_memberships(db, probe)
        visible = target is not None
        if organization_id and organization_id not in organisations:
            visible = False
        if project_id and project_id not in projects:
            visible = False
        if not is_superadmin:
            try:
                await AuthorizationService().check_user_access(current_user, probe, "read")
            except (BaseDomainError, HTTPException):
                visible = False
        if not visible or target is None:
            await record("deny", "target_not_visible")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

        # The target as a request would see them: revoked role references dropped, role
        # spellings normalised (review finding 4).
        # The roles a request would carry: revoked references dropped, spellings
        # normalised. Not a validated CurrentUser: a stored email the model rejects
        # (none, or a local domain) must not turn the answer into a 500 (re-review).
        as_request = await _without_revoked_roles(db, dict(target))
        principal = SimpleNamespace(
            id=str(target["_id"]),
            roles=_normalize_roles_list(as_request.get("roles", [])),
            organization_id=as_request.get("organization_id"),
            organizations=list(as_request.get("organizations") or []),
            projects=list(as_request.get("projects") or []),
            account_type=as_request.get("account_type", "client_user"),
        )
        account_active = account_is_active(target)
        granted = account_active and await policy.has_permission(
            principal, permission_name, organization_id=organization_id, project_id=project_id
        )
    except HTTPException:
        raise
    except Exception as exc:
        await record("error", type(exc).__name__)
        raise
    await record("allow", "checked", granted)
    return {
        "granted": granted,
        "permission": permission_name,
        "user_id": target_user_id,
        "organization_id": organization_id,
        "project_id": project_id,
        "account_active": account_active,
    }

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

    except (BaseDomainError, HTTPException):
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

    except (BaseDomainError, HTTPException):
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

    except (BaseDomainError, HTTPException):
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

    except (BaseDomainError, HTTPException):
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

    except (BaseDomainError, HTTPException):
        raise
    except Exception as e:
        logger.error(f"Failed to delete permission {permission_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Permission deletion error: {str(e)}"
        )
