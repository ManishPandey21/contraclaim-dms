"""Insurance Register API. PolicyService-gated, tenant-scoped, audited.

Mirrors the Bank Guarantee router: list / summary / alerts / export, full CRUD,
file replace, and the admin-managed Insurance Type master.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.insurance import (
    Insurance,
    InsuranceCreate,
    InsuranceSummary,
    InsuranceType,
    InsuranceTypeCreate,
    InsuranceTypeUpdate,
    InsuranceUpdate,
)
from ..services.insurance_service import InsuranceService, InsuranceTypeService
from ..services.policy_service import PolicyService

router = APIRouter()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


async def _load(insurance_id: str, permission: str, db, current_user, policy) -> dict:
    ins = await InsuranceService(db).get(insurance_id)
    if not ins:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance policy not found")
    await policy.authorize_document(current_user, permission, ins, resource_type="insurance")
    return ins


# --- register --------------------------------------------------------------


@router.get("/insurance", response_model=List[Insurance])
async def list_insurance(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    insurance_type: Optional[str] = Query(None, alias="type"),
    insurance_company: Optional[str] = Query(None, alias="company"),
    status_filter: Optional[str] = Query(None, alias="status"),
    uploaded_by: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_VIEW, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    items = await InsuranceService(db).list(
        scope, project_id=project_id, contract_id=contract_id, insurance_type=insurance_type,
        insurance_company=insurance_company, status=status_filter, uploaded_by=uploaded_by,
        q=q, skip=skip, limit=limit,
    )
    return [Insurance(**i) for i in items]


@router.get("/insurance/summary", response_model=InsuranceSummary)
async def insurance_summary(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_VIEW, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return InsuranceSummary(**await InsuranceService(db).summary(scope, project_id=project_id))


@router.get("/insurance/alerts", response_model=List[Insurance])
async def insurance_alerts(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_VIEW, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return [Insurance(**i) for i in await InsuranceService(db).alerts(scope)]


@router.get("/insurance/export")
async def export_insurance(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_EXPORT, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    from ..services import contract_controls_export as cx

    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await InsuranceService(db).list(scope, project_id=project_id, limit=5000)
    return cx.export_response("insurance-register", cx.INSURANCE_COLUMNS, rows, format)


# --- insurance type master (admin-managed) ---------------------------------


@router.get("/insurance/types", response_model=List[InsuranceType])
async def list_insurance_types(
    organization_id: Optional[str] = Query(None),
    include_inactive: bool = Query(False),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_VIEW, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        audit=False,
    )
    org = organization_id or getattr(current_user, "organization_id", None)
    return [InsuranceType(**t) for t in await InsuranceTypeService(db).list(org, include_inactive=include_inactive)]


@router.post("/insurance/types", response_model=InsuranceType, status_code=status.HTTP_201_CREATED)
async def create_insurance_type(
    payload: InsuranceTypeCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user, Permissions.INSURANCE_MANAGE_TYPES, resource_type="insurance",
        organization_id=org,
    )
    return InsuranceType(**await InsuranceTypeService(db).create(payload, current_user))


@router.put("/insurance/types/{type_id}", response_model=InsuranceType)
async def update_insurance_type(
    type_id: str,
    payload: InsuranceTypeUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    service = InsuranceTypeService(db)
    existing = await service.get(type_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance type not found")
    await policy.authorize(
        current_user, Permissions.INSURANCE_MANAGE_TYPES, resource_type="insurance",
        organization_id=existing.get("organization_id"),
    )
    return InsuranceType(**(await service.update(existing, payload.model_dump(exclude_unset=True), current_user) or existing))


@router.delete("/insurance/types/{type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_insurance_type(
    type_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    service = InsuranceTypeService(db)
    existing = await service.get(type_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance type not found")
    await policy.authorize(
        current_user, Permissions.INSURANCE_MANAGE_TYPES, resource_type="insurance",
        organization_id=existing.get("organization_id"),
    )
    await service.delete(existing, current_user)
    return None


# --- CRUD ------------------------------------------------------------------


@router.post("/insurance", response_model=Insurance, status_code=status.HTTP_201_CREATED)
async def create_insurance(
    payload: InsuranceCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user, Permissions.INSURANCE_CREATE, resource_type="insurance",
        organization_id=org, project_id=payload.project_id,
    )
    return Insurance(**await InsuranceService(db).create(payload, current_user))


@router.get("/insurance/{insurance_id}", response_model=Insurance)
async def get_insurance(
    insurance_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return Insurance(**await _load(insurance_id, Permissions.INSURANCE_VIEW, db, current_user, policy))


@router.put("/insurance/{insurance_id}", response_model=Insurance)
async def update_insurance(
    insurance_id: str,
    payload: InsuranceUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    ins = await _load(insurance_id, Permissions.INSURANCE_EDIT, db, current_user, policy)
    updated = await InsuranceService(db).update(ins, payload.model_dump(exclude_unset=True), current_user)
    return Insurance(**(updated or ins))


@router.post("/insurance/{insurance_id}/replace-file", response_model=Insurance)
async def replace_insurance_file(
    insurance_id: str,
    document_id: str = Body(..., embed=True),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    ins = await _load(insurance_id, Permissions.INSURANCE_EDIT, db, current_user, policy)
    return Insurance(**await InsuranceService(db).replace_file(ins, document_id, current_user))


@router.delete("/insurance/{insurance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_insurance(
    insurance_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    ins = await _load(insurance_id, Permissions.INSURANCE_DELETE, db, current_user, policy)
    await InsuranceService(db).delete(ins, current_user)
    return None
