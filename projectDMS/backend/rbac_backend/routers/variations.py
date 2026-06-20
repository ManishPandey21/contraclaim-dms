"""Variation Register API. PolicyService-gated, tenant-scoped, audited."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.variation import (
    Variation,
    VariationCreate,
    VariationSummary,
    VariationUpdate,
)
from ..services.policy_service import PolicyService
from ..services.variation_service import VariationService

router = APIRouter()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


async def _load(variation_id: str, permission: str, db, current_user, policy) -> dict:
    v = await VariationService(db).get(variation_id)
    if not v:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variation not found")
    await policy.authorize_document(current_user, permission, v, resource_type="variation")
    return v


@router.get("/variations", response_model=List[Variation])
async def list_variations(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    variation_type: Optional[str] = Query(None, alias="type"),
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.VARIATION_VIEW, resource_type="variations",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    items = await VariationService(db).list(
        scope, project_id=project_id, contract_id=contract_id,
        status=status_filter, variation_type=variation_type, skip=skip, limit=limit,
    )
    return [Variation(**v) for v in items]


@router.get("/variations/summary", response_model=VariationSummary)
async def variation_summary(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    original_contract_value: Optional[float] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.VARIATION_VIEW, resource_type="variations",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return VariationSummary(**await VariationService(db).summary(
        scope, project_id=project_id, contract_id=contract_id, original_contract_value=original_contract_value,
    ))


@router.get("/variations/export")
async def export_variations(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.VARIATION_EXPORT, resource_type="variations",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    from ..services import contract_controls_export as cx

    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await VariationService(db).list(scope, project_id=project_id, contract_id=contract_id, limit=5000)
    return cx.export_response("variation-register", cx.VARIATION_COLUMNS, rows, format)


@router.post("/variations", response_model=Variation, status_code=status.HTTP_201_CREATED)
async def create_variation(
    payload: VariationCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user, Permissions.VARIATION_CREATE, resource_type="variation",
        organization_id=org, project_id=payload.project_id,
    )
    created = await VariationService(db).create(payload, current_user)
    return Variation(**created)


@router.get("/variations/{variation_id}", response_model=Variation)
async def get_variation(
    variation_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return Variation(**await _load(variation_id, Permissions.VARIATION_VIEW, db, current_user, policy))


@router.put("/variations/{variation_id}", response_model=Variation)
async def update_variation(
    variation_id: str,
    payload: VariationUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    # Status moves into approved/rejected require the approve permission.
    perm = Permissions.VARIATION_EDIT
    if payload.status in {"approved", "rejected"}:
        perm = Permissions.VARIATION_APPROVE
    v = await _load(variation_id, perm, db, current_user, policy)
    updated = await VariationService(db).update(v, payload.model_dump(exclude_unset=True), current_user)
    return Variation(**(updated or v))


@router.delete("/variations/{variation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_variation(
    variation_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    v = await _load(variation_id, Permissions.VARIATION_DELETE, db, current_user, policy)
    await VariationService(db).delete(v, current_user)
    return None
