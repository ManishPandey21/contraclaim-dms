"""Reviewer role assignments (plan 4B-07, D-06).

Who may call what is decided in ``services.reviewer_assignments``: platform
scope needs a Super Admin, organisation and project scope need an administrator
of that organisation, and nobody grants themselves a role.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, status

from ..core.database import get_db
from ..core.security import CurrentUser, get_current_user
from ..models.reviewer_assignment import (
    LegacyReviewerRoleReport,
    MyReviewerRoles,
    ReviewerAssignmentCreate,
    ReviewerAssignmentList,
    ReviewerAssignmentRevoke,
    ReviewerAssignmentView,
    ReviewerScopeType,
)
from ..services.policy_service import PolicyService
from ..services.reviewer_assignments import ReviewerAssignmentService
from ..utils.error_handler import handle_exceptions
from ..utils.rate_limiter import RateLimiter

router = APIRouter(prefix="/reviewer-assignments", tags=["reviewer-assignments"])
reviewer_assignment_limiter = RateLimiter(max_requests=60, window_seconds=60, scope="reviewer_assignments")


async def get_service(db=Depends(get_db)) -> ReviewerAssignmentService:
    return ReviewerAssignmentService(db, PolicyService(db=db))


@router.get("/mine", response_model=MyReviewerRoles)
@handle_exceptions
async def my_reviewer_roles(
    current_user: CurrentUser = Depends(get_current_user),
    service: ReviewerAssignmentService = Depends(get_service),
) -> MyReviewerRoles:
    """The caller's own live assignments. Everyone may read their own."""
    return await service.mine(current_user)


@router.get("/legacy-report", response_model=LegacyReviewerRoleReport)
@handle_exceptions
async def legacy_reviewer_roles(
    organization_id: Optional[str] = Query(None, max_length=64),
    current_user: CurrentUser = Depends(get_current_user),
    service: ReviewerAssignmentService = Depends(get_service),
) -> LegacyReviewerRoleReport:
    return await service.legacy_report(current_user, organization_id=organization_id)


@router.get("", response_model=ReviewerAssignmentList)
@handle_exceptions
async def list_reviewer_assignments(
    scope_type: ReviewerScopeType = Query(...),
    organization_id: Optional[str] = Query(None, max_length=64),
    project_id: Optional[str] = Query(None, max_length=64),
    include_inactive: bool = Query(False),
    current_user: CurrentUser = Depends(get_current_user),
    service: ReviewerAssignmentService = Depends(get_service),
) -> ReviewerAssignmentList:
    items = await service.list(
        current_user,
        scope_type=scope_type,
        organization_id=organization_id,
        project_id=project_id,
        include_inactive=include_inactive,
    )
    return ReviewerAssignmentList(items=items)


@router.post("", response_model=ReviewerAssignmentView, status_code=status.HTTP_201_CREATED)
@handle_exceptions
async def grant_reviewer_assignment(
    payload: ReviewerAssignmentCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: ReviewerAssignmentService = Depends(get_service),
) -> ReviewerAssignmentView:
    await reviewer_assignment_limiter.check_user_limit(str(getattr(current_user, "id", "") or ""))
    return await service.grant(payload, current_user)


@router.post("/{assignment_id}/revoke", response_model=ReviewerAssignmentView)
@handle_exceptions
async def revoke_reviewer_assignment(
    assignment_id: str,
    payload: ReviewerAssignmentRevoke,
    current_user: CurrentUser = Depends(get_current_user),
    service: ReviewerAssignmentService = Depends(get_service),
) -> ReviewerAssignmentView:
    await reviewer_assignment_limiter.check_user_limit(str(getattr(current_user, "id", "") or ""))
    return await service.revoke(assignment_id, payload.reason, current_user)
