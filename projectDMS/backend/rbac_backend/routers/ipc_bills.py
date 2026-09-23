"""IPC / Contractor Bill Register API. PolicyService-gated, tenant-scoped, audited."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..core.tenant_context import ActiveScope, active_scope
from ..models.ipc_bill import (
    IPCBill,
    IPCBillCreate,
    IPCBillSummary,
    IPCBillUpdate,
)
from ..services.ipc_bill_service import IPCBillService
from ..services.document_relationship_service import (
    DocumentRelationshipError,
    DocumentRelationshipService,
)
from ..services.policy_service import PolicyService

router = APIRouter()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {value}")


async def _load(ipc_id: str, permission: str, db, current_user, policy, selection: ActiveScope) -> dict:
    selection.require_selection()
    i = await IPCBillService(db).get(ipc_id)
    if not i:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="IPC bill not found")
    await selection.require_record(i, allow_unscoped=True)
    await policy.authorize_document(current_user, permission, i, resource_type="ipc_bill")
    return i


async def _present_ipc(ipc: dict, db, current_user: CurrentUser) -> IPCBill:
    presented = dict(ipc)
    presented["linked_document_ids"] = await DocumentRelationshipService(
        db
    ).authorized_document_ids(
        current_user,
        "ipc_bill",
        str(ipc.get("_id") or ""),
        legacy_document_ids=ipc.get("linked_document_ids") or [],
    )
    return IPCBill(**presented)


async def _present_ipcs(ipcs: list[dict], db, current_user: CurrentUser) -> list[IPCBill]:
    ids_by_ipc = await DocumentRelationshipService(db).authorized_document_ids_for_targets(
        current_user, "ipc_bill", ipcs
    )
    return [
        IPCBill(
            **{
                **ipc,
                "linked_document_ids": ids_by_ipc.get(str(ipc.get("_id") or ""), []),
            }
        )
        for ipc in ipcs
    ]


@router.get("/ipc-bills", response_model=List[IPCBill])
async def list_ipc_bills(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    payment_status: Optional[str] = Query(None),
    currency: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    organization_id, project_id = await selection.list_filters(organization_id, project_id)
    await policy.authorize(
        current_user, Permissions.IPC_VIEW, resource_type="ipc_bills",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    items = await IPCBillService(db).list(
        scope, project_id=project_id, contract_id=contract_id, status=status_filter,
        payment_status=payment_status, currency=currency,
        date_from=_parse_date(date_from), date_to=_parse_date(date_to), skip=skip, limit=limit,
    )
    return await _present_ipcs(items, db, current_user)


@router.get("/ipc-bills/summary", response_model=IPCBillSummary)
async def ipc_summary(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    original_contract_value: Optional[float] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    organization_id, project_id = await selection.list_filters(organization_id, project_id)
    await policy.authorize(
        current_user, Permissions.IPC_VIEW, resource_type="ipc_bills",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return IPCBillSummary(**await IPCBillService(db).summary(
        scope, project_id=project_id, contract_id=contract_id, original_contract_value=original_contract_value,
    ))


@router.get("/ipc-bills/export")
async def export_ipc_bills(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    ipc_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    if ipc_id:
        # A single-IPC export is record-level: held to the selection in _load.
        selection.require_selection()
    else:
        organization_id, project_id = await selection.list_filters(organization_id, project_id)
    await policy.authorize(
        current_user, Permissions.IPC_EXPORT, resource_type="ipc_bills",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    from ..services import contract_controls_export as cx

    svc = IPCBillService(db)
    if ipc_id:
        # Single-IPC export.
        one = await _load(ipc_id, Permissions.IPC_EXPORT, db, current_user, policy, selection)
        rows = [(await _present_ipc(one, db, current_user)).model_dump(by_alias=True)]
        name = f"ipc-{one.get('ipc_number') or ipc_id}"
    else:
        scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
        raw_rows = await svc.list(scope, project_id=project_id, contract_id=contract_id, limit=5000)
        rows = [row.model_dump(by_alias=True) for row in await _present_ipcs(raw_rows, db, current_user)]
        name = "ipc-register"
    return cx.export_response(name, cx.IPC_COLUMNS, rows, format)


@router.post("/ipc-bills", response_model=IPCBill, status_code=status.HTTP_201_CREATED)
async def create_ipc_bill(
    payload: IPCBillCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    # The body's project must BE the selection: refused, never rewritten.
    await selection.require_project(payload.project_id, payload.organization_id)
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user, Permissions.IPC_CREATE, resource_type="ipc_bill",
        organization_id=org, project_id=payload.project_id,
    )
    if "linked_document_ids" in payload.model_fields_set and payload.linked_document_ids:
        try:
            await DocumentRelationshipService(db, policy=policy).reject_ambiguous_legacy_write(
                current_user,
                "ipc_bill",
                organization_id=org,
                project_id=payload.project_id,
            )
        except DocumentRelationshipError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    created = await IPCBillService(db).create(payload, current_user)
    return await _present_ipc(created, db, current_user)


@router.get("/ipc-bills/{ipc_id}", response_model=IPCBill)
async def get_ipc_bill(
    ipc_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    return await _present_ipc(
        await _load(ipc_id, Permissions.IPC_VIEW, db, current_user, policy, selection),
        db,
        current_user,
    )


@router.put("/ipc-bills/{ipc_id}", response_model=IPCBill)
async def update_ipc_bill(
    ipc_id: str,
    payload: IPCBillUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    # Moving to approved/paid/rejected requires the approve permission.
    perm = Permissions.IPC_EDIT
    if payload.status in {"approved", "paid", "partially_paid", "rejected"}:
        perm = Permissions.IPC_APPROVE
    i = await _load(ipc_id, perm, db, current_user, policy, selection)
    changes = payload.model_dump(exclude_unset=True)
    legacy_ids = changes.pop("linked_document_ids", None)
    if "linked_document_ids" in payload.model_fields_set:
        try:
            await DocumentRelationshipService(db, policy=policy).replace_legacy_document_ids(
                current_user, "ipc_bill", ipc_id, legacy_ids or []
            )
        except DocumentRelationshipError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    updated = await IPCBillService(db).update(i, changes, current_user)
    return await _present_ipc(updated or i, db, current_user)


@router.delete("/ipc-bills/{ipc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ipc_bill(
    ipc_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    i = await _load(ipc_id, Permissions.IPC_DELETE, db, current_user, policy, selection)
    await DocumentRelationshipService(db, policy=policy).delete_target(
        current_user,
        "ipc_bill",
        ipc_id,
        reason="IPC deleted",
    )
    return None
