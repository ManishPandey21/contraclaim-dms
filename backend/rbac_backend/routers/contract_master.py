"""Contract Master API. PolicyService-gated, tenant-scoped, audited.

Lives on the existing /api/contracts surface (/contracts/master). The
revise-completion endpoint is the EOT hook the Bank Guarantee register reads.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.contract_master import (
    BGRequiredDate,
    ContractMaster,
    ContractMasterCreate,
    ContractMasterUpdate,
    ReviseCompletionRequest,
)
from ..services.contract_master_service import ContractMasterService
from ..services.policy_service import PolicyService

router = APIRouter()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


async def _load(master_id: str, permission: str, db, current_user, policy) -> dict:
    cm = await ContractMasterService(db).get(master_id)
    if not cm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contract master not found")
    await policy.authorize_document(current_user, permission, cm, resource_type="contract_master")
    return cm


@router.get("/contracts/master", response_model=List[ContractMaster])
async def list_contract_master(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.CONTRACT_MASTER_VIEW, resource_type="contract_master",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return [ContractMaster(**c) for c in await ContractMasterService(db).list(scope, project_id=project_id)]


@router.post("/contracts/master", response_model=ContractMaster, status_code=status.HTTP_201_CREATED)
async def create_contract_master(
    payload: ContractMasterCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user, Permissions.CONTRACT_MASTER_MANAGE, resource_type="contract_master",
        organization_id=org, project_id=payload.project_id,
    )
    return ContractMaster(**await ContractMasterService(db).create(payload, current_user))


@router.get("/contracts/master/{master_id}", response_model=ContractMaster)
async def get_contract_master(
    master_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return ContractMaster(**await _load(master_id, Permissions.CONTRACT_MASTER_VIEW, db, current_user, policy))


@router.put("/contracts/master/{master_id}", response_model=ContractMaster)
async def update_contract_master(
    master_id: str,
    payload: ContractMasterUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    cm = await _load(master_id, Permissions.CONTRACT_MASTER_MANAGE, db, current_user, policy)
    updated = await ContractMasterService(db).update(cm, payload.model_dump(exclude_unset=True), current_user)
    return ContractMaster(**(updated or cm))


@router.post("/contracts/master/{master_id}/revise-completion", response_model=ContractMaster)
async def revise_completion(
    master_id: str,
    req: ReviseCompletionRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    cm = await _load(master_id, Permissions.CONTRACT_MASTER_MANAGE, db, current_user, policy)
    return ContractMaster(**await ContractMasterService(db).revise_completion(
        cm, req.revised_completion_date, current_user, remarks=req.remarks,
    ))


@router.get("/contracts/master/{master_id}/bg-required-dates", response_model=List[BGRequiredDate])
async def bg_required_dates(
    master_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    cm = await _load(master_id, Permissions.CONTRACT_MASTER_VIEW, db, current_user, policy)
    return [BGRequiredDate(**d) for d in ContractMasterService(db).bg_required_dates(cm)]
