"""Matter chronology API."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.chronology import (
    AttachChronologyRequest,
    ChronologyDecisionRequest,
    ChronologyDuplicateRequest,
    ChronologyExtractRequest,
    ChronologyLinkRequest,
    ChronologyPleadingContext,
    MatterChronology,
    MatterChronologyCreate,
    MatterChronologyEvent,
    MatterChronologyEventCreate,
    MatterChronologyEventRevision,
    MatterChronologyEventUpdate,
    MatterChronologyUpdate,
)
from ..services.arbitration_drafting import ArbitrationDraftingService
from ..services.chronology import ChronologyService
from ..services.policy_service import PolicyService

router = APIRouter(tags=["chronology"])


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid datetime: {value}") from exc


async def _load_and_authorize_chronology(chronology_id: str, permission: str, db, current_user, policy) -> dict:
    chronology = await ChronologyService(db).get_chronology(chronology_id)
    await policy.authorize_document(current_user, permission, chronology, resource_type="matter_chronology")
    return chronology


@router.get("/chronologies", response_model=List[MatterChronology])
async def list_chronologies(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    matter_id: Optional[str] = Query(None),
    claim_id: Optional[str] = Query(None),
    chronology_type: Optional[str] = Query(None),
    party_perspective: Optional[str] = Query(None),
    status_value: Optional[str] = Query(None, alias="status"),
    q: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=250),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.CHRONOLOGY_VIEW,
        resource_type="matter_chronology",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await ChronologyService(db).list_chronologies(
        scope,
        {
            "project_id": project_id,
            "contract_id": contract_id,
            "matter_id": matter_id,
            "claim_id": claim_id,
            "chronology_type": chronology_type,
            "party_perspective": party_perspective,
            "status": status_value,
            "q": q,
        },
        skip=skip,
        limit=limit,
    )
    return [MatterChronology(**row) for row in rows]


@router.post("/chronologies", response_model=MatterChronology, status_code=status.HTTP_201_CREATED)
async def create_chronology(
    payload: MatterChronologyCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user,
        Permissions.CHRONOLOGY_CREATE,
        resource_type="matter_chronology",
        organization_id=org,
        project_id=payload.project_id,
    )
    return MatterChronology(**await ChronologyService(db).create_chronology(payload, current_user))


@router.get("/chronologies/{chronology_id}", response_model=MatterChronology)
async def get_chronology(
    chronology_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    chronology = await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_VIEW, db, current_user, policy)
    return MatterChronology(**chronology)


@router.patch("/chronologies/{chronology_id}", response_model=MatterChronology)
async def update_chronology(
    chronology_id: str,
    payload: MatterChronologyUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EDIT, db, current_user, policy)
    return MatterChronology(**await ChronologyService(db).update_chronology(chronology_id, payload, current_user))


@router.delete("/chronologies/{chronology_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chronology(
    chronology_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_ADMIN, db, current_user, policy)
    await ChronologyService(db).delete_chronology(chronology_id, current_user)
    return None


@router.post("/chronologies/{chronology_id}/extract")
async def extract_chronology_events(
    chronology_id: str,
    payload: ChronologyExtractRequest | None = None,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EDIT, db, current_user, policy)
    return await ChronologyService(db).extract_events(chronology_id, payload or ChronologyExtractRequest(), current_user)


@router.get("/chronologies/{chronology_id}/events", response_model=List[MatterChronologyEvent])
async def list_chronology_events(
    chronology_id: str,
    verification_status: Optional[str] = Query(None),
    supports_party: Optional[str] = Query(None),
    event_classification: Optional[str] = Query(None),
    impact_type: Optional[str] = Query(None),
    responsible_party: Optional[str] = Query(None),
    pleading_use: Optional[str] = Query(None),
    issue_tag: Optional[str] = Query(None),
    claim_head: Optional[str] = Query(None),
    clause: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(250, ge=1, le=1000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_VIEW, db, current_user, policy)
    rows = await ChronologyService(db).list_events(
        chronology_id,
        {
            "verification_status": verification_status,
            "supports_party": supports_party,
            "event_classification": event_classification,
            "impact_type": impact_type,
            "responsible_party": responsible_party,
            "pleading_use": pleading_use,
            "issue_tag": issue_tag,
            "claim_head": claim_head,
            "clause": clause,
            "source_type": source_type,
            "date_from": _parse_dt(date_from),
            "date_to": _parse_dt(date_to),
        },
        skip=skip,
        limit=limit,
    )
    return [MatterChronologyEvent(**row) for row in rows]


@router.post("/chronologies/{chronology_id}/events", response_model=MatterChronologyEvent, status_code=status.HTTP_201_CREATED)
async def create_chronology_event(
    chronology_id: str,
    payload: MatterChronologyEventCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EDIT, db, current_user, policy)
    payload.chronology_id = chronology_id
    return MatterChronologyEvent(**await ChronologyService(db).create_event(payload, current_user))


@router.patch("/chronologies/{chronology_id}/events/{event_id}", response_model=MatterChronologyEvent)
async def update_chronology_event(
    chronology_id: str,
    event_id: str,
    payload: MatterChronologyEventUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EDIT, db, current_user, policy)
    return MatterChronologyEvent(**await ChronologyService(db).update_event(chronology_id, event_id, payload, current_user))


@router.post("/chronologies/{chronology_id}/events/{event_id}/verify", response_model=MatterChronologyEvent)
async def verify_chronology_event(
    chronology_id: str,
    event_id: str,
    payload: ChronologyDecisionRequest | None = None,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_VERIFY, db, current_user, policy)
    return MatterChronologyEvent(**await ChronologyService(db).verify_event(chronology_id, event_id, current_user, payload))


@router.post("/chronologies/{chronology_id}/events/{event_id}/reject", response_model=MatterChronologyEvent)
async def reject_chronology_event(
    chronology_id: str,
    event_id: str,
    payload: ChronologyDecisionRequest | None = None,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_VERIFY, db, current_user, policy)
    return MatterChronologyEvent(**await ChronologyService(db).reject_event(chronology_id, event_id, current_user, payload))


@router.post("/chronologies/{chronology_id}/events/{event_id}/mark-duplicate", response_model=MatterChronologyEvent)
async def mark_chronology_event_duplicate(
    chronology_id: str,
    event_id: str,
    payload: ChronologyDuplicateRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EDIT, db, current_user, policy)
    return MatterChronologyEvent(**await ChronologyService(db).mark_duplicate(chronology_id, event_id, payload, current_user))


@router.post("/chronologies/{chronology_id}/events/{event_id}/link", response_model=MatterChronologyEvent)
async def link_chronology_event(
    chronology_id: str,
    event_id: str,
    payload: ChronologyLinkRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EDIT, db, current_user, policy)
    return MatterChronologyEvent(**await ChronologyService(db).link_event(chronology_id, event_id, payload, current_user))


@router.get("/chronologies/{chronology_id}/events/{event_id}/revisions", response_model=List[MatterChronologyEventRevision])
async def chronology_event_revisions(
    chronology_id: str,
    event_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_VIEW, db, current_user, policy)
    return [MatterChronologyEventRevision(**row) for row in await ChronologyService(db).event_revisions(chronology_id, event_id)]


@router.get("/chronologies/{chronology_id}/pleading-context", response_model=ChronologyPleadingContext)
async def chronology_pleading_context(
    chronology_id: str,
    include_unverified: bool = Query(False),
    limit: int = Query(250, ge=1, le=500),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_VIEW, db, current_user, policy)
    return await ChronologyService(db).pleading_context(chronology_id, include_unverified=include_unverified, limit=limit)


@router.post("/arbitration/drafts/{draft_id}/attach-chronology")
async def attach_chronology_to_arbitration_draft(
    draft_id: str,
    payload: AttachChronologyRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    draft = await ArbitrationDraftingService(db).repo.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arbitration draft not found")
    await policy.authorize_document(current_user, Permissions.ARBITRATION_EDIT, draft, resource_type="arbitration_draft")
    await _load_and_authorize_chronology(payload.chronology_id, Permissions.CHRONOLOGY_VIEW, db, current_user, policy)
    return await ChronologyService(db).attach_to_arbitration_draft(draft_id, payload, current_user)


@router.get("/arbitration/drafts/{draft_id}/chronology-context")
async def arbitration_draft_chronology_context(
    draft_id: str,
    chronology_id: str = Query(...),
    include_unverified: bool = Query(False),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    draft = await ArbitrationDraftingService(db).repo.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arbitration draft not found")
    await policy.authorize_document(current_user, Permissions.ARBITRATION_VIEW, draft, resource_type="arbitration_draft")
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_VIEW, db, current_user, policy)
    return await ChronologyService(db).pleading_context(chronology_id, include_unverified=include_unverified)


@router.get("/chronologies/{chronology_id}/export/docx")
async def export_chronology_docx(
    chronology_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EXPORT, db, current_user, policy)
    content = await ChronologyService(db).export(chronology_id, "docx", current_user)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="chronology.docx"'},
    )


@router.get("/chronologies/{chronology_id}/export/xlsx")
async def export_chronology_xlsx(
    chronology_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EXPORT, db, current_user, policy)
    content = await ChronologyService(db).export(chronology_id, "xlsx", current_user)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="chronology.csv"'},
    )


@router.get("/chronologies/{chronology_id}/export/pdf")
async def export_chronology_pdf(
    chronology_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EXPORT, db, current_user, policy)
    content = await ChronologyService(db).export(chronology_id, "pdf", current_user)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="chronology.pdf"'},
    )


@router.get("/chronologies/{chronology_id}/export/evidence-index")
async def export_chronology_evidence_index(
    chronology_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EXPORT, db, current_user, policy)
    content = await ChronologyService(db).export(chronology_id, "xlsx", current_user)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="chronology-evidence-index.csv"'},
    )
