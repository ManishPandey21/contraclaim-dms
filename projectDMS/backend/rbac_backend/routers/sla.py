"""Correspondence SLA & time-bar API (Phase 4 / Module 2).

Read-only deadline views derived from claims. Both endpoints are gated by the
claim-view permission and scoped with build_scope_query, so a tenant only ever
sees its own deadlines.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.claim import SlaItem
from ..services.policy_service import PolicyService
from ..services.sla_service import DEFAULT_APPROACHING_DAYS, SlaService

router = APIRouter()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


async def _scoped_items(
    *,
    only_breached: bool,
    days: int,
    organization_id: Optional[str],
    project_id: Optional[str],
    db,
    current_user: CurrentUser,
    policy: PolicyService,
) -> List[SlaItem]:
    # C-27: the pages send no organisation. The subscription check needs one, and
    # the organisation's own notice windows are looked up by it, so fall back to the
    # active selection, as the other register routes do. Row scope is unchanged.
    active_org = organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user,
        Permissions.CLAIM_VIEW,
        resource_type="claims",
        organization_id=active_org,
        project_id=project_id,
        audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    items = await SlaService(db).list_sla(
        scope,
        days=days,
        only_breached=only_breached,
        organization_id=active_org,
    )
    return [SlaItem(**i) for i in items]


@router.get("/sla/upcoming", response_model=List[SlaItem])
async def upcoming_deadlines(
    days: int = Query(DEFAULT_APPROACHING_DAYS, ge=1, le=365),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return await _scoped_items(
        only_breached=False,
        days=days,
        organization_id=organization_id,
        project_id=project_id,
        db=db,
        current_user=current_user,
        policy=policy,
    )


@router.get("/sla/breached", response_model=List[SlaItem])
async def breached_deadlines(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return await _scoped_items(
        only_breached=True,
        days=DEFAULT_APPROACHING_DAYS,
        organization_id=organization_id,
        project_id=project_id,
        db=db,
        current_user=current_user,
        policy=policy,
    )
