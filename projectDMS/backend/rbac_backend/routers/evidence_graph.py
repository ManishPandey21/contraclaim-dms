"""Evidence graph and contract intelligence timeline API."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.evidence_graph import (
    EventLink,
    EventLinkCreate,
    EventLinkDecision,
    ProjectEvent,
    ProjectEventCreate,
    TimelineResponse,
)
from ..services.evidence_graph_backfill_service import EvidenceGraphBackfillService
from ..services.evidence_graph_reconciliation_service import EvidenceGraphReconciliationService
from ..services.evidence_graph_service import EvidenceGraphError, EvidenceGraphNotFound, EvidenceGraphService
from ..services.policy_service import PolicyService

router = APIRouter()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid datetime: {value}") from exc


@router.get("/project-events", response_model=List[ProjectEvent])
async def list_project_events(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    package: Optional[str] = Query(None),
    party: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.EVIDENCE_GRAPH_VIEW,
        resource_type="project_events",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await EvidenceGraphService(db).list_project_events(
        scope,
        event_type=event_type,
        status=status_filter,
        package=package,
        party=party,
        date_from=_parse_dt(date_from),
        date_to=_parse_dt(date_to),
        skip=skip,
        limit=limit,
    )
    return [ProjectEvent(**row) for row in rows]


@router.post("/project-events", response_model=ProjectEvent, status_code=status.HTTP_201_CREATED)
async def create_project_event(
    payload: ProjectEventCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user,
        Permissions.EVIDENCE_GRAPH_MANAGE,
        resource_type="project_event",
        organization_id=org,
        project_id=payload.project_id,
    )
    created = await EvidenceGraphService(db).create_project_event(payload, current_user)
    return ProjectEvent(**created)


@router.get("/project-events/{event_id}", response_model=ProjectEvent)
async def get_project_event(
    event_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    event = await EvidenceGraphService(db).get_project_event(event_id)
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project event not found")
    await policy.authorize_document(current_user, Permissions.EVIDENCE_GRAPH_VIEW, event, resource_type="project_event")
    return ProjectEvent(**event)


@router.get("/event-links", response_model=List[EventLink])
async def list_event_links(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    source_type: Optional[str] = Query(None),
    source_id: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    target_id: Optional[str] = Query(None),
    latest_only: bool = Query(True),
    skip: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=5000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.EVIDENCE_GRAPH_VIEW,
        resource_type="event_links",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await EvidenceGraphService(db).list_links(
        scope,
        status=status_filter,
        source_type=source_type,
        source_id=source_id,
        target_type=target_type,
        target_id=target_id,
        latest_only=latest_only,
        skip=skip,
        limit=limit,
    )
    return [EventLink(**row) for row in rows]


@router.get("/evidence-graph/downstream-links", response_model=List[EventLink])
async def downstream_event_links(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    source_id: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    target_id: Optional[str] = Query(None),
    include_ai_suggested: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=5000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.EVIDENCE_GRAPH_VIEW,
        resource_type="evidence_graph_downstream_links",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await EvidenceGraphService(db).downstream_links(
        scope,
        source_type=source_type,
        source_id=source_id,
        target_type=target_type,
        target_id=target_id,
        include_ai_suggested=include_ai_suggested,
        skip=skip,
        limit=limit,
    )
    return [EventLink(**row) for row in rows]


@router.post("/evidence-graph/backfill/dry-run")
async def evidence_graph_backfill_dry_run(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    limit_per_collection: int = Query(500, ge=1, le=5000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.EVIDENCE_GRAPH_MANAGE,
        resource_type="evidence_graph_backfill",
        organization_id=organization_id,
        project_id=project_id,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return await EvidenceGraphBackfillService(db).run(
        scope,
        current_user=current_user,
        dry_run=True,
        project_id=project_id,
        limit_per_collection=limit_per_collection,
    )


@router.post("/evidence-graph/backfill")
async def evidence_graph_backfill(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    dry_run: bool = Query(True),
    limit_per_collection: int = Query(500, ge=1, le=5000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.EVIDENCE_GRAPH_MANAGE,
        resource_type="evidence_graph_backfill",
        organization_id=organization_id,
        project_id=project_id,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return await EvidenceGraphBackfillService(db).run(
        scope,
        current_user=current_user,
        dry_run=dry_run,
        project_id=project_id,
        limit_per_collection=limit_per_collection,
    )


@router.post("/evidence-graph/reconcile")
async def evidence_graph_reconcile(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.EVIDENCE_GRAPH_VIEW,
        resource_type="evidence_graph_reconciliation",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return await EvidenceGraphReconciliationService(db).report(scope, project_id=project_id)


@router.get("/event-links/{link_group_id}/history", response_model=List[EventLink])
async def event_link_history(
    link_group_id: str,
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.EVIDENCE_GRAPH_VIEW,
        resource_type="event_link_history",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await EvidenceGraphService(db).get_link_history(scope, link_group_id)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event link not found")
    return [EventLink(**row) for row in rows]


@router.post("/event-links/suggest", response_model=EventLink, status_code=status.HTTP_201_CREATED)
async def suggest_event_link(
    payload: EventLinkCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.EVIDENCE_GRAPH_MANAGE,
        resource_type="event_link",
        organization_id=payload.organization_id,
        project_id=payload.project_id,
    )
    try:
        return EventLink(**await EvidenceGraphService(db).suggest_link(payload, current_user=current_user))
    except EvidenceGraphError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/event-links/{link_group_id}/verify", response_model=EventLink)
async def verify_event_link(
    link_group_id: str,
    body: EventLinkDecision | None = None,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    svc = EvidenceGraphService(db)
    latest = await svc.get_latest_link(link_group_id)
    if not latest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event link not found")
    await policy.authorize_document(current_user, Permissions.EVIDENCE_GRAPH_VERIFY, latest, resource_type="event_link")
    try:
        return EventLink(**await svc.verify_link(link_group_id, current_user, note=(body.note if body else None)))
    except EvidenceGraphNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event link not found")
    except EvidenceGraphError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/event-links/{link_group_id}/reject", response_model=EventLink)
async def reject_event_link(
    link_group_id: str,
    body: EventLinkDecision | None = None,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    svc = EvidenceGraphService(db)
    latest = await svc.get_latest_link(link_group_id)
    if not latest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event link not found")
    await policy.authorize_document(current_user, Permissions.EVIDENCE_GRAPH_VERIFY, latest, resource_type="event_link")
    try:
        return EventLink(**await svc.reject_link(link_group_id, current_user, note=(body.note if body else None)))
    except EvidenceGraphNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event link not found")
    except EvidenceGraphError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/event-links/{link_group_id}/approve", response_model=EventLink)
async def approve_event_link(
    link_group_id: str,
    body: EventLinkDecision | None = None,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    svc = EvidenceGraphService(db)
    latest = await svc.get_latest_link(link_group_id)
    if not latest:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event link not found")
    await policy.authorize_document(current_user, Permissions.EVIDENCE_GRAPH_VERIFY, latest, resource_type="event_link")
    try:
        return EventLink(**await svc.approve_link(link_group_id, current_user, note=(body.note if body else None)))
    except EvidenceGraphNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event link not found")
    except EvidenceGraphError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/contracts/timeline", response_model=TimelineResponse)
async def contract_timeline(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    link_status: Optional[str] = Query(None),
    claim_type: Optional[str] = Query(None),
    clause: Optional[str] = Query(None),
    drawing: Optional[str] = Query(None),
    key_date: Optional[str] = Query(None),
    delay_responsibility: Optional[str] = Query(None),
    payment_status: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    package: Optional[str] = Query(None),
    party: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_TIMELINE_VIEW,
        resource_type="contract_timeline",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return await EvidenceGraphService(db).timeline(
        scope,
        event_type=event_type,
        link_status=link_status,
        claim_type=claim_type,
        clause=clause,
        drawing=drawing,
        key_date=key_date,
        delay_responsibility=delay_responsibility,
        payment_status=payment_status,
        location=location,
        date_from=_parse_dt(date_from),
        date_to=_parse_dt(date_to),
        package=package,
        party=party,
        skip=skip,
        limit=limit,
    )
