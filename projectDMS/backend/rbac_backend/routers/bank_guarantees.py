"""Bank Guarantee Register API. PolicyService-gated, tenant-scoped, audited."""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.bank_guarantee import (
    BankGuarantee,
    BankGuaranteeCreate,
    BankGuaranteeUpdate,
    BGExtendRequest,
    BGExtensionHistory,
    BGReleaseRequest,
    BGSummary,
)
from ..services.bank_guarantee_service import BankGuaranteeService
from ..services.policy_service import PolicyService

router = APIRouter()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


async def _load(bg_id: str, permission: str, db, current_user, policy) -> dict:
    bg = await BankGuaranteeService(db).get(bg_id)
    if not bg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank guarantee not found")
    await policy.authorize_document(current_user, permission, bg, resource_type="bank_guarantee")
    return bg


@router.get("/bank-guarantees", response_model=List[BankGuarantee])
async def list_bgs(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    bg_type: Optional[str] = Query(None, alias="type"),
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.BG_VIEW, resource_type="bank_guarantees",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    items = await BankGuaranteeService(db).list(
        scope, project_id=project_id, contract_id=contract_id, status=status_filter, bg_type=bg_type, skip=skip, limit=limit,
    )
    return [BankGuarantee(**b) for b in items]


@router.get("/bank-guarantees/summary", response_model=BGSummary)
async def bg_summary(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.BG_VIEW, resource_type="bank_guarantees",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return BGSummary(**await BankGuaranteeService(db).summary(scope, project_id=project_id))


@router.get("/bank-guarantees/alerts", response_model=List[BankGuarantee])
async def bg_alerts(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.BG_VIEW, resource_type="bank_guarantees",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return [BankGuarantee(**b) for b in await BankGuaranteeService(db).alerts(scope)]


@router.get("/bank-guarantees/export")
async def export_bgs(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.BG_EXPORT, resource_type="bank_guarantees",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    from ..services import contract_controls_export as cx

    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await BankGuaranteeService(db).list(scope, project_id=project_id, limit=5000)
    return cx.export_response("bank-guarantee-register", cx.BG_COLUMNS, rows, format)


@router.post("/bank-guarantees", response_model=BankGuarantee, status_code=status.HTTP_201_CREATED)
async def create_bg(
    payload: BankGuaranteeCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user, Permissions.BG_CREATE, resource_type="bank_guarantee",
        organization_id=org, project_id=payload.project_id,
    )
    created = await BankGuaranteeService(db).create(payload, current_user)
    return BankGuarantee(**created)


@router.get("/bank-guarantees/{bg_id}", response_model=BankGuarantee)
async def get_bg(
    bg_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return BankGuarantee(**await _load(bg_id, Permissions.BG_VIEW, db, current_user, policy))


@router.put("/bank-guarantees/{bg_id}", response_model=BankGuarantee)
async def update_bg(
    bg_id: str,
    payload: BankGuaranteeUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    bg = await _load(bg_id, Permissions.BG_EDIT, db, current_user, policy)
    updated = await BankGuaranteeService(db).update(bg, payload.model_dump(exclude_unset=True), current_user)
    return BankGuarantee(**(updated or bg))


@router.delete("/bank-guarantees/{bg_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bg(
    bg_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    bg = await _load(bg_id, Permissions.BG_DELETE, db, current_user, policy)
    await BankGuaranteeService(db).delete(bg, current_user)
    return None


@router.post("/bank-guarantees/{bg_id}/extend", response_model=BankGuarantee)
async def extend_bg(
    bg_id: str,
    req: BGExtendRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    bg = await _load(bg_id, Permissions.BG_EXTEND, db, current_user, policy)
    return BankGuarantee(**await BankGuaranteeService(db).extend(bg, req, current_user))


@router.post("/bank-guarantees/{bg_id}/release", response_model=BankGuarantee)
async def release_bg(
    bg_id: str,
    req: BGReleaseRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    bg = await _load(bg_id, Permissions.BG_RELEASE, db, current_user, policy)
    return BankGuarantee(**await BankGuaranteeService(db).release(bg, current_user, remarks=req.remarks))


@router.get("/bank-guarantees/{bg_id}/history", response_model=List[BGExtensionHistory])
async def bg_history(
    bg_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load(bg_id, Permissions.BG_VIEW, db, current_user, policy)
    return [BGExtensionHistory(**h) for h in await BankGuaranteeService(db).list_history(bg_id)]
