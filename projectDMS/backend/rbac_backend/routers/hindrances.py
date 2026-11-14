"""Hindrance & Constraint Register API.

The canonical API for the register. It is backed by the `delay_events`
collection through `HindranceRegisterService`, the same service the
compatibility routes in `evidence_registers.py` (`/api/delay-events`) call.

Authorization: the gate answers membership (`PolicyService.authorize`), and
`build_scope_query` answers row visibility for listings. Per-record routes load
the row first and authorize against ITS organisation and project, inline in the
handler; a foreign record answers 403, matching the register contract since
its introduction. Relationship targets (key dates, activities, EOT submissions)
are authorized by the service, which is the only code that loads them.

Active project scope (owner decision 2026-09-22, `core/tenant_context.py`): the
navbar selection (`X-Org-Id` / `X-Proj-Id`) is a request boundary. With a project
selected, a record, create body or reverse-lookup target in another project is
403 `context_forbidden`; record-level and mutating routes with no selection are
400 `selection_required`. The list with no selection stays bounded by
`build_scope_query`, never global.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..core.tenant_context import ActiveScope, active_scope
from ..models.evidence_registers import (
    DelayEvent,
    DelayEventStatus,
    DelayResponsibility,
    HindranceAffectingItem,
    HindranceAffectingResponse,
    HindranceCategory,
    HindranceClaimStatus,
    HindranceCreate,
    HindranceEventType,
    HindranceHistoryEntry,
    HindranceHistoryResponse,
    HindranceLink,
    HindranceLinkCreate,
    HindranceLinkListResponse,
    HindranceListResponse,
    HindranceReason,
    HindranceUpdate,
    TimelineSyncStatus,
)
from ..services.evidence_register_service import EvidenceRegisterNotFound
from ..services.hindrance_register_service import LINK_TARGETS, HindranceError, HindranceRegisterService
from ..services.policy_service import PolicyService

router = APIRouter()

SortField = Literal[
    "start_date",
    "end_date",
    "hindrance_ref",
    "title",
    "status",
    "event_type",
    "category",
    "responsibility",
    "created_at",
    "updated_at",
]


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


def _fail(exc: HindranceError) -> NoReturn:
    raise exc.as_http() from exc


async def _load(service: HindranceRegisterService, item_id: str, scope: ActiveScope) -> dict:
    """Load a record and hold it to the selected project (400 when none is selected)."""
    scope.require_selection()
    item = await service.get(item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Register entry not found")
    await scope.require_record(item)
    return item


@router.get("/hindrances", response_model=HindranceListResponse, response_model_by_alias=False)
async def list_hindrances(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    event_type: Optional[HindranceEventType] = Query(None),
    status_filter: Optional[DelayEventStatus] = Query(None, alias="status"),
    claim_status: Optional[HindranceClaimStatus] = Query(None),
    category: Optional[HindranceCategory] = Query(None),
    responsibility: Optional[DelayResponsibility] = Query(None),
    critical_path_impact: Optional[bool] = Query(None),
    timeline_sync_status: Optional[TimelineSyncStatus] = Query(None),
    start_from: Optional[datetime] = Query(None),
    start_to: Optional[datetime] = Query(None),
    q: Optional[str] = Query(None, max_length=200),
    include_archived: bool = Query(False),
    sort: SortField = Query("start_date"),
    order: Literal["asc", "desc"] = Query("desc"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    if selection.has_project:
        # A filter may narrow the selection, never leave it.
        if project_id or organization_id:
            await selection.require_project(project_id or selection.project_id, organization_id)
        project_id = selection.project_id
        organization_id = selection.organization_id
    elif selection.organization_id:
        # An organisation-only selection still narrows.
        await selection.require_organization(organization_id)
        organization_id = selection.organization_id
    await policy.authorize(
        current_user,
        Permissions.HINDRANCE_VIEW,
        resource_type="delay_events",
        # Nothing selected: gate on the principal's own organisation (as Variation
        # does); rows stay bounded by build_scope_query. Without it a project-tier
        # member fails the subscription check on an organisation of None.
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id,
        audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    try:
        rows, total = await HindranceRegisterService(db).list_entries(
            scope,
            project_id=project_id,
            event_type=event_type.value if event_type else None,
            status=status_filter.value if status_filter else None,
            claim_status=claim_status.value if claim_status else None,
            category=category.value if category else None,
            responsibility=responsibility.value if responsibility else None,
            critical_path_impact=critical_path_impact,
            timeline_sync_status=timeline_sync_status.value if timeline_sync_status else None,
            start_from=start_from,
            start_to=start_to,
            q=q,
            include_archived=include_archived,
            sort=sort,
            order=order,
            skip=skip,
            limit=limit,
            with_total=True,
        )
    except HindranceError as exc:
        _fail(exc)
    return HindranceListResponse(items=[DelayEvent(**row) for row in rows], total=total, skip=skip, limit=limit)


@router.post(
    "/hindrances",
    response_model=DelayEvent,
    response_model_by_alias=False,
    status_code=status.HTTP_201_CREATED,
)
async def create_hindrance(
    payload: HindranceCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    # The body's project must BE the selection: refused, never rewritten.
    await selection.require_project(payload.project_id, payload.organization_id)
    selected_org = payload.organization_id or selection.organization_id
    # Authorize before resolving, so an unauthorised caller learns nothing about
    # whether a project exists.
    await policy.authorize(
        current_user,
        Permissions.HINDRANCE_CREATE,
        resource_type="delay_event",
        organization_id=selected_org,
        project_id=payload.project_id,
    )
    service = HindranceRegisterService(db)
    try:
        owner = await service.resolve_project_organization(payload.project_id, selected_org)
        created = await service.create(payload.model_copy(update={"organization_id": owner}), current_user)
    except HindranceError as exc:
        _fail(exc)
    return DelayEvent(**created)


@router.get(
    "/hindrances/affecting/{target_type}/{target_id}",
    response_model=HindranceAffectingResponse,
    response_model_by_alias=False,
)
async def list_hindrances_affecting(
    target_type: str,
    target_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    service = HindranceRegisterService(db)
    # Record-level: the target must be in the selected project, and there must be
    # one (400) - the same rule `_load` applies to a register entry.
    selection.require_selection()
    try:
        target = await service.load_link_target(target_type, target_id)
    except HindranceError as exc:
        _fail(exc)
    await selection.require_record(target)
    await policy.authorize_document(
        current_user, LINK_TARGETS[target_type].view_permission, target, resource_type=target_type
    )
    await policy.authorize(
        current_user,
        Permissions.HINDRANCE_VIEW,
        resource_type="delay_events",
        organization_id=str(target.get("organization_id") or "") or None,
        project_id=str(target.get("project_id") or "") or None,
        audit=False,
    )
    items = await service.affecting(current_user, target_type, target, policy)
    return HindranceAffectingResponse(items=[HindranceAffectingItem(**item) for item in items])


@router.get("/hindrances/{item_id}", response_model=DelayEvent, response_model_by_alias=False)
async def get_hindrance(
    item_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    item = await _load(HindranceRegisterService(db), item_id, selection)
    await policy.authorize_document(current_user, Permissions.HINDRANCE_VIEW, item, resource_type="delay_event")
    return DelayEvent(**item)


@router.patch("/hindrances/{item_id}", response_model=DelayEvent, response_model_by_alias=False)
async def update_hindrance(
    item_id: str,
    payload: HindranceUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    service = HindranceRegisterService(db)
    item = await _load(service, item_id, selection)
    await policy.authorize_document(current_user, Permissions.HINDRANCE_EDIT, item, resource_type="delay_event")
    try:
        updated = await service.update(item_id, payload.model_dump(exclude_unset=True), current_user)
    except EvidenceRegisterNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Register entry not found")
    except HindranceError as exc:
        _fail(exc)
    return DelayEvent(**updated)


@router.post("/hindrances/{item_id}/archive", response_model=DelayEvent, response_model_by_alias=False)
async def archive_hindrance(
    item_id: str,
    body: HindranceReason,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    service = HindranceRegisterService(db)
    item = await _load(service, item_id, selection)
    await policy.authorize_document(current_user, Permissions.HINDRANCE_ARCHIVE, item, resource_type="delay_event")
    try:
        return DelayEvent(**await service.archive(item_id, current_user, reason=body.reason))
    except HindranceError as exc:
        _fail(exc)


@router.post("/hindrances/{item_id}/restore", response_model=DelayEvent, response_model_by_alias=False)
async def restore_hindrance(
    item_id: str,
    body: HindranceReason,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    service = HindranceRegisterService(db)
    item = await _load(service, item_id, selection)
    await policy.authorize_document(current_user, Permissions.HINDRANCE_ARCHIVE, item, resource_type="delay_event")
    try:
        return DelayEvent(**await service.restore(item_id, current_user, reason=body.reason))
    except HindranceError as exc:
        _fail(exc)


@router.post("/hindrances/{item_id}/timeline-sync", response_model=DelayEvent, response_model_by_alias=False)
async def resync_hindrance_timeline(
    item_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    service = HindranceRegisterService(db)
    item = await _load(service, item_id, selection)
    await policy.authorize_document(current_user, Permissions.HINDRANCE_EDIT, item, resource_type="delay_event")
    return DelayEvent(**await service.sync_timeline(item, current_user))


@router.get("/hindrances/{item_id}/history", response_model=HindranceHistoryResponse, response_model_by_alias=False)
async def hindrance_history(
    item_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    service = HindranceRegisterService(db)
    item = await _load(service, item_id, selection)
    await policy.authorize_document(current_user, Permissions.HINDRANCE_VIEW, item, resource_type="delay_event")
    entries = await service.history(item_id)
    return HindranceHistoryResponse(entries=[HindranceHistoryEntry(**entry) for entry in entries])


@router.get("/hindrances/{item_id}/links", response_model=HindranceLinkListResponse, response_model_by_alias=False)
async def list_hindrance_links(
    item_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    service = HindranceRegisterService(db)
    item = await _load(service, item_id, selection)
    await policy.authorize_document(current_user, Permissions.HINDRANCE_VIEW, item, resource_type="delay_event")
    links = await service.list_links(current_user, item, policy)
    return HindranceLinkListResponse(links=[HindranceLink(**link) for link in links])


@router.post(
    "/hindrances/{item_id}/links",
    response_model=HindranceLink,
    response_model_by_alias=False,
    status_code=status.HTTP_201_CREATED,
)
async def link_hindrance(
    item_id: str,
    payload: HindranceLinkCreate,
    response: Response,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    service = HindranceRegisterService(db)
    item = await _load(service, item_id, selection)
    await policy.authorize_document(current_user, Permissions.HINDRANCE_EDIT, item, resource_type="delay_event")
    try:
        link, created = await service.link(current_user, item, payload, policy)
    except HindranceError as exc:
        _fail(exc)
    if not created:
        response.status_code = status.HTTP_200_OK
    return HindranceLink(**link)


@router.post(
    "/hindrances/{item_id}/links/{link_id}/remove",
    response_model=HindranceLink,
    response_model_by_alias=False,
)
async def unlink_hindrance(
    item_id: str,
    link_id: str,
    body: HindranceReason,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    service = HindranceRegisterService(db)
    item = await _load(service, item_id, selection)
    await policy.authorize_document(current_user, Permissions.HINDRANCE_EDIT, item, resource_type="delay_event")
    try:
        link = await service.remove_link(current_user, item, link_id, body.reason, policy)
    except HindranceError as exc:
        _fail(exc)
    return HindranceLink(**link)
