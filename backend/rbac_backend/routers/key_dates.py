"""Key Date / Milestone Tracker API.

Tenant-scoped CRUD for milestones plus the EOT lifecycle, extension history and
achievement recording. Every endpoint is gated by PolicyService and lists are
filtered with build_scope_query, so Org A never sees Org B's key dates.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.key_date import (
    AchievementRecord,
    EOTApplication,
    EOTApplicationCreate,
    EOTReview,
    ExtensionHistory,
    KeyDateDashboard,
    KeyDateMilestone,
    KeyDateMilestoneCreate,
    KeyDateMilestoneUpdate,
)
from ..services.key_date_service import KeyDateError, KeyDateService
from ..services.policy_service import PolicyService

router = APIRouter()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


def _bad_request(exc: KeyDateError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def _load(milestone_id: str, permission: str, db, current_user, policy) -> dict:
    svc = KeyDateService(db)
    m = await svc.get(milestone_id)
    if not m:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Milestone not found")
    await policy.authorize_document(current_user, permission, m, resource_type="key_date_milestone")
    return m


@router.get("/key-dates", response_model=List[KeyDateMilestone])
async def list_milestones(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    responsible_party_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.KEYDATE_VIEW, resource_type="key_dates",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    items = await KeyDateService(db).list(
        scope, project_id=project_id, status=status_filter,
        responsible_party_id=responsible_party_id, skip=skip, limit=limit,
    )
    return [KeyDateMilestone(**m) for m in items]


@router.get("/key-dates/dashboard", response_model=KeyDateDashboard)
async def key_date_dashboard(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.KEYDATE_VIEW, resource_type="key_dates",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return KeyDateDashboard(**await KeyDateService(db).dashboard(scope, project_id=project_id))


@router.post("/key-dates", response_model=KeyDateMilestone, status_code=status.HTTP_201_CREATED)
async def create_milestone(
    payload: KeyDateMilestoneCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user, Permissions.KEYDATE_CREATE, resource_type="key_date_milestone",
        organization_id=org, project_id=payload.project_id,
    )
    try:
        created = await KeyDateService(db).create_milestone(payload, current_user)
    except KeyDateError as exc:
        raise _bad_request(exc)
    return KeyDateMilestone(**created)


@router.get("/key-dates/{milestone_id}", response_model=KeyDateMilestone)
async def get_milestone(
    milestone_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return KeyDateMilestone(**await _load(milestone_id, Permissions.KEYDATE_VIEW, db, current_user, policy))


@router.put("/key-dates/{milestone_id}", response_model=KeyDateMilestone)
async def update_milestone(
    milestone_id: str,
    payload: KeyDateMilestoneUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    m = await _load(milestone_id, Permissions.KEYDATE_EDIT, db, current_user, policy)
    try:
        updated = await KeyDateService(db).update(m, payload.model_dump(exclude_unset=True), current_user)
    except KeyDateError as exc:
        raise _bad_request(exc)
    return KeyDateMilestone(**(updated or m))


@router.delete("/key-dates/{milestone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_milestone(
    milestone_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    m = await _load(milestone_id, Permissions.KEYDATE_DELETE, db, current_user, policy)
    await KeyDateService(db).delete(m, current_user)
    return None


# --- EOT ------------------------------------------------------------------


@router.post("/key-dates/{milestone_id}/eot", response_model=EOTApplication, status_code=status.HTTP_201_CREATED)
async def submit_eot(
    milestone_id: str,
    payload: EOTApplicationCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    m = await _load(milestone_id, Permissions.KEYDATE_EOT_SUBMIT, db, current_user, policy)
    try:
        eot = await KeyDateService(db).submit_eot(m, payload, current_user)
    except KeyDateError as exc:
        raise _bad_request(exc)
    return EOTApplication(**eot)


@router.get("/key-dates/{milestone_id}/eots", response_model=List[EOTApplication])
async def list_eots(
    milestone_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load(milestone_id, Permissions.KEYDATE_VIEW, db, current_user, policy)
    return [EOTApplication(**e) for e in await KeyDateService(db).list_eots(milestone_id)]


@router.post("/key-dates/{milestone_id}/eot/{eot_id}/review", response_model=EOTApplication)
async def review_eot(
    milestone_id: str,
    eot_id: str,
    review: EOTReview,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    m = await _load(milestone_id, Permissions.KEYDATE_EOT_APPROVE, db, current_user, policy)
    svc = KeyDateService(db)
    eot = await db.key_date_eot_applications.find_one({"_id": eot_id})
    if not eot or str(eot.get("milestone_id")) != str(milestone_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="EOT application not found")
    try:
        updated = await svc.review_eot(m, eot, review, current_user)
    except KeyDateError as exc:
        raise _bad_request(exc)
    return EOTApplication(**(updated or eot))


@router.get("/key-dates/{milestone_id}/history", response_model=List[ExtensionHistory])
async def extension_history(
    milestone_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load(milestone_id, Permissions.KEYDATE_VIEW, db, current_user, policy)
    return [ExtensionHistory(**h) for h in await KeyDateService(db).list_extension_history(milestone_id)]


# --- achievement ----------------------------------------------------------


@router.post("/key-dates/{milestone_id}/achievement", response_model=KeyDateMilestone)
async def record_achievement(
    milestone_id: str,
    rec: AchievementRecord,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    m = await _load(milestone_id, Permissions.KEYDATE_ACHIEVEMENT, db, current_user, policy)
    try:
        updated = await KeyDateService(db).record_achievement(m, rec, current_user)
    except KeyDateError as exc:
        raise _bad_request(exc)
    return KeyDateMilestone(**updated)
