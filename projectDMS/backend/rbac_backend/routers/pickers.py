"""Scoped pickers (plan 4B-11, C-21): people for a role on one project.

``GET /pickers/users?project_id=&permission=`` lists the active members of the
project who hold ``permission``. The project's organisation is read from the
project, never from the caller. The caller must be able to see that project
with the domain's view permission; otherwise the answer is the same refusal
any other project route gives.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.database import get_db
from ..core.security import CurrentUser, get_current_user
from ..services.picker_service import PICKER_PERMISSIONS, PickerService, PickerUsersResponse
from ..services.policy_service import PolicyService
from ..utils.error_handler import handle_exceptions
from ..utils.rate_limiter import RateLimiter

router = APIRouter(prefix="/pickers", tags=["pickers"])
picker_limiter = RateLimiter(max_requests=120, window_seconds=60, scope="pickers_users")


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


def _refusal(code: int, error_code: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": error_code, "user_message": message, "detail": message})


@router.get("/users", response_model=PickerUsersResponse)
@handle_exceptions
async def pick_users(
    project_id: str = Query(..., min_length=1),
    permission: str = Query(..., min_length=1),
    q: Optional[str] = Query(None, max_length=80),
    limit: int = Query(25, ge=1, le=100),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
) -> PickerUsersResponse:
    await picker_limiter.check_user_limit(str(getattr(current_user, "id", "") or ""))
    caller_permission = PICKER_PERMISSIONS.get(permission)
    if caller_permission is None:
        raise _refusal(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "picker_permission_not_supported",
            "This list cannot be filtered by that permission.",
        )
    project = await db.projects.find_one({"_id": project_id}, {"organization_id": 1})
    organization_id = (project or {}).get("organization_id")
    if not organization_id:
        raise _refusal(status.HTTP_404_NOT_FOUND, "project_not_found", "Project not found.")
    await policy.authorize(
        current_user,
        caller_permission,
        resource_type="picker",
        organization_id=str(organization_id),
        project_id=project_id,
        audit=False,
    )
    return await PickerService(db, policy).eligible_users(
        organization_id=str(organization_id), project_id=project_id, permission=permission, q=q, limit=limit
    )
