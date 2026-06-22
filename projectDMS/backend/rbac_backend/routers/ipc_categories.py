"""IPC category master API (advance + deduction type catalogs).

PolicyService-gated and tenant-scoped. Read uses IPC view; create/update/delete
reuse IPC edit (managing the catalog is part of running the register).
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, get_current_user
from ..models.ipc_category import IPCCategory, IPCCategoryCreate, IPCCategoryUpdate
from ..services.ipc_category_service import IPCCategoryService
from ..services.policy_service import PolicyService

router = APIRouter()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


def _org_of(current_user: CurrentUser, organization_id: Optional[str]) -> Optional[str]:
    return organization_id or getattr(current_user, "organization_id", None)


@router.get("/ipc-categories", response_model=List[IPCCategory])
async def list_categories(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    kind: Optional[str] = Query(None, pattern="^(advance|deduction)$"),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    """Merged catalog (org-wide overlaid by project overrides) for the editor."""
    org = _org_of(current_user, organization_id)
    await policy.authorize(
        current_user, Permissions.IPC_VIEW, resource_type="ipc_categories",
        organization_id=org, project_id=project_id, audit=False,
    )
    rows = await IPCCategoryService(db).resolve(org, project_id=project_id, kind=kind)
    return [IPCCategory(**c) for c in rows]


@router.get("/ipc-categories/manage", response_model=List[IPCCategory])
async def manage_categories(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    """Raw entries (org + project, incl. inactive) for the management dialog."""
    org = _org_of(current_user, organization_id)
    await policy.authorize(
        current_user, Permissions.IPC_VIEW, resource_type="ipc_categories",
        organization_id=org, project_id=project_id, audit=False,
    )
    rows = await IPCCategoryService(db).list_manage(org, project_id=project_id)
    return [IPCCategory(**c) for c in rows]


@router.post("/ipc-categories", response_model=IPCCategory, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: IPCCategoryCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = _org_of(current_user, payload.organization_id)
    await policy.authorize(
        current_user, Permissions.IPC_EDIT, resource_type="ipc_category",
        organization_id=org, project_id=payload.project_id,
    )
    payload.organization_id = org
    try:
        created = await IPCCategoryService(db).create(payload, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return IPCCategory(**created)


async def _load(category_id: str, permission: str, db, current_user, policy) -> dict:
    c = await IPCCategoryService(db).get(category_id)
    if not c:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    await policy.authorize(
        current_user, permission, resource_type="ipc_category",
        organization_id=c.get("organization_id"), project_id=c.get("project_id"),
    )
    return c


@router.put("/ipc-categories/{category_id}", response_model=IPCCategory)
async def update_category(
    category_id: str,
    payload: IPCCategoryUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    c = await _load(category_id, Permissions.IPC_EDIT, db, current_user, policy)
    updated = await IPCCategoryService(db).update(c, payload.model_dump(exclude_unset=True), current_user)
    return IPCCategory(**(updated or c))


@router.delete("/ipc-categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    c = await _load(category_id, Permissions.IPC_EDIT, db, current_user, policy)
    await IPCCategoryService(db).delete(c, current_user)
    return None
