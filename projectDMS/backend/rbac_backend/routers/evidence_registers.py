"""Evidence graph domain register API."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..core.tenant_context import ActiveScope, active_scope
from ..models.evidence_registers import (
    DelayEvent,
    DelayEventCreate,
    DelayEventUpdate,
    DrawingReference,
    DrawingReferenceCreate,
    DrawingReferenceUpdate,
    ProgrammeMilestone,
    ProgrammeMilestoneCreate,
    ProgrammeMilestoneUpdate,
)
from ..services.evidence_register_service import EvidenceRegisterNotFound, EvidenceRegisterService
from ..services.hindrance_register_service import HindranceError, HindranceRegisterService
from ..services.policy_service import PolicyService

router = APIRouter()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


@router.get("/drawing-references", response_model=List[DrawingReference])
async def list_drawing_references(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    location: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(current_user, Permissions.EVIDENCE_GRAPH_VIEW, resource_type="drawing_references", organization_id=organization_id, project_id=project_id, audit=False)
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await EvidenceRegisterService(db).list_drawing_references(scope, project_id=project_id, status=status_filter, location=location, skip=skip, limit=limit)
    return [DrawingReference(**row) for row in rows]


@router.post("/drawing-references", response_model=DrawingReference, status_code=status.HTTP_201_CREATED)
async def create_drawing_reference(
    payload: DrawingReferenceCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(current_user, Permissions.EVIDENCE_GRAPH_MANAGE, resource_type="drawing_reference", organization_id=org, project_id=payload.project_id)
    return DrawingReference(**await EvidenceRegisterService(db).create_drawing_reference(payload, current_user))


@router.get("/drawing-references/{item_id}", response_model=DrawingReference)
async def get_drawing_reference(
    item_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    item = await EvidenceRegisterService(db).get_drawing_reference(item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drawing reference not found")
    await policy.authorize_document(current_user, Permissions.EVIDENCE_GRAPH_VIEW, item, resource_type="drawing_reference")
    return DrawingReference(**item)


@router.patch("/drawing-references/{item_id}", response_model=DrawingReference)
async def update_drawing_reference(
    item_id: str,
    payload: DrawingReferenceUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    svc = EvidenceRegisterService(db)
    item = await svc.get_drawing_reference(item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drawing reference not found")
    await policy.authorize_document(current_user, Permissions.EVIDENCE_GRAPH_MANAGE, item, resource_type="drawing_reference")
    try:
        return DrawingReference(**await svc.update_drawing_reference(item_id, payload, current_user))
    except EvidenceRegisterNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Drawing reference not found")


# ---------------------------------------------------------------------------
# /delay-events is the compatibility API of the Hindrance & Constraint Register
# (canonical API: routers/hindrances.py). Both call HindranceRegisterService and
# both are gated by the register's own `dms.hindrance.*` permissions: one
# resource, one authorization model - and by the same active project scope
# (`core/tenant_context.py`), so the compatibility routes are never a scope
# bypass. Deprecation: new clients use
# /api/hindrances; these four routes stay until no caller remains.
# ---------------------------------------------------------------------------


@router.get("/delay-events", response_model=List[DelayEvent])
async def list_delay_events(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    responsibility: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    if selection.has_project:
        if project_id or organization_id:
            await selection.require_project(project_id or selection.project_id, organization_id)
        project_id = selection.project_id
        organization_id = selection.organization_id
    await policy.authorize(current_user, Permissions.HINDRANCE_VIEW, resource_type="delay_events", organization_id=organization_id or getattr(current_user, "organization_id", None), project_id=project_id, audit=False)
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await EvidenceRegisterService(db).list_delay_events(scope, project_id=project_id, status=status_filter, responsibility=responsibility, location=location, skip=skip, limit=limit)
    return [DelayEvent(**row) for row in rows]


@router.post("/delay-events", response_model=DelayEvent, status_code=status.HTTP_201_CREATED)
async def create_delay_event(
    payload: DelayEventCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    await selection.require_project(payload.project_id, payload.organization_id)
    org = payload.organization_id or selection.organization_id
    await policy.authorize(current_user, Permissions.HINDRANCE_CREATE, resource_type="delay_event", organization_id=org, project_id=payload.project_id)
    try:
        owner = await HindranceRegisterService(db).resolve_project_organization(payload.project_id, org)
        created = await EvidenceRegisterService(db).create_delay_event(payload.model_copy(update={"organization_id": owner}), current_user)
    except HindranceError as exc:
        raise exc.as_http() from exc
    return DelayEvent(**created)


@router.get("/delay-events/{item_id}", response_model=DelayEvent)
async def get_delay_event(
    item_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    selection.require_selection()
    item = await EvidenceRegisterService(db).get_delay_event(item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delay event not found")
    await selection.require_record(item)
    await policy.authorize_document(current_user, Permissions.HINDRANCE_VIEW, item, resource_type="delay_event")
    return DelayEvent(**item)


@router.patch("/delay-events/{item_id}", response_model=DelayEvent)
async def update_delay_event(
    item_id: str,
    payload: DelayEventUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    selection.require_selection()
    svc = EvidenceRegisterService(db)
    item = await svc.get_delay_event(item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delay event not found")
    await selection.require_record(item)
    await policy.authorize_document(current_user, Permissions.HINDRANCE_EDIT, item, resource_type="delay_event")
    try:
        return DelayEvent(**await svc.update_delay_event(item_id, payload, current_user))
    except EvidenceRegisterNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delay event not found")
    except HindranceError as exc:
        raise exc.as_http() from exc


@router.get("/programme-milestones", response_model=List[ProgrammeMilestone])
async def list_programme_milestones(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    milestone_type: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(current_user, Permissions.EVIDENCE_GRAPH_VIEW, resource_type="programme_milestones", organization_id=organization_id, project_id=project_id, audit=False)
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await EvidenceRegisterService(db).list_programme_milestones(scope, project_id=project_id, status=status_filter, milestone_type=milestone_type, location=location, skip=skip, limit=limit)
    return [ProgrammeMilestone(**row) for row in rows]


@router.post("/programme-milestones", response_model=ProgrammeMilestone, status_code=status.HTTP_201_CREATED)
async def create_programme_milestone(
    payload: ProgrammeMilestoneCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(current_user, Permissions.EVIDENCE_GRAPH_MANAGE, resource_type="programme_milestone", organization_id=org, project_id=payload.project_id)
    return ProgrammeMilestone(**await EvidenceRegisterService(db).create_programme_milestone(payload, current_user))


@router.get("/programme-milestones/{item_id}", response_model=ProgrammeMilestone)
async def get_programme_milestone(
    item_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    item = await EvidenceRegisterService(db).get_programme_milestone(item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Programme milestone not found")
    await policy.authorize_document(current_user, Permissions.EVIDENCE_GRAPH_VIEW, item, resource_type="programme_milestone")
    return ProgrammeMilestone(**item)


@router.patch("/programme-milestones/{item_id}", response_model=ProgrammeMilestone)
async def update_programme_milestone(
    item_id: str,
    payload: ProgrammeMilestoneUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    svc = EvidenceRegisterService(db)
    item = await svc.get_programme_milestone(item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Programme milestone not found")
    await policy.authorize_document(current_user, Permissions.EVIDENCE_GRAPH_MANAGE, item, resource_type="programme_milestone")
    try:
        return ProgrammeMilestone(**await svc.update_programme_milestone(item_id, payload, current_user))
    except EvidenceRegisterNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Programme milestone not found")
