"""Matter chronology API.

CL-3B: the chronology's own routes follow the selected navbar project
(``core/tenant_context.py``) - lists are bounded by it, a record or a write needs
a selected project that the chronology is in (400 ``selection_required`` / 403
``context_forbidden``). Chronology events are canonical relationship targets
(``chronology_event``), so a Document is linked to an event only through
``/entities/chronology_event/{id}/document-links``; the raw
``related_document_ids`` write paths are closed. ``source_document_id`` stays the
event's single extraction provenance and is held to the chronology's scope.

The arbitration routes at the end authorize the chronology for the ARBITRATION
register and stay selection-blind with it (active-scope debt, contraclaim-dms#24).
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..core.tenant_context import ActiveScope, active_scope
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
from ..services.document_relationship_service import DocumentRelationshipService
from ..services.policy_service import PolicyService
from ..services.publication_policy import resolve_canonical_document

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


#: A Document reaches a chronology event through the canonical relationship API only.
LEGACY_WRITE_REFUSAL = (
    "related_document_ids is read-only: link Documents to a chronology event through "
    "/api/entities/chronology_event/{event_id}/document-links"
)


async def _load_and_authorize_chronology(
    chronology_id: str,
    permission: str,
    db,
    current_user,
    policy,
    selection: Optional[ActiveScope] = None,
) -> dict:
    """Load and authorize a chronology; with ``selection``, hold it to the navbar project first."""
    if selection is not None:
        selection.require_selection()
    chronology = await ChronologyService(db).get_chronology(chronology_id)
    if selection is not None:
        await selection.require_record(chronology)
    await policy.authorize_document(current_user, permission, chronology, resource_type="matter_chronology")
    return chronology


async def _load_chronology_in_selection(chronology_id: str, db, selection: ActiveScope) -> dict:
    """Load a chronology held to the navbar selection, leaving authorization to the service.

    Review decisions (verify, reject) check ``dms.chronology.verify`` inside
    ``ChronologyService`` so a direct service caller cannot skip it; checking it
    here as well would audit every decision twice.
    """
    selection.require_selection()
    chronology = await ChronologyService(db).get_chronology(chronology_id)
    await selection.require_record(chronology)
    return chronology


def _refuse_related_document_write(requested, stored=None) -> None:
    """Refuse a raw ``related_document_ids`` write that ADDS a reference (409).

    New relationships go through the canonical API. Removing a stored legacy
    reference (a wrong, foreign or duplicate id written before CL-3B) stays
    possible - a subset of the stored array, including an unchanged echo - and is
    recorded by the event's revision trail. ``null`` is refused (422): it would
    erase the array without saying which references went.
    """
    if requested is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="related_document_ids cannot be null; send the references to keep",
        )
    if set(map(str, requested)) <= set(map(str, stored or [])):
        return
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LEGACY_WRITE_REFUSAL)


async def _require_source_document_in_scope(db, chronology: dict, document_id: Optional[str]) -> None:
    """``source_document_id`` must name a Document of the chronology's own organisation and project.

    Provenance is one Document; naming one from another tenant or project would
    attach its identity (and, on verification, its authority) to this chronology.
    The response does not say whether the foreign Document exists. (Whether the
    writer may VIEW that Document is not asked: the G31 suite pins the exact
    authorization sequence of event creation - recorded as CL-3B debt.)
    """
    if not document_id:
        return
    document = await resolve_canonical_document(db, document_id)
    organization_id = str((document or {}).get("organization_id") or (document or {}).get("organizationId") or "")
    project_id = str((document or {}).get("project_id") or (document or {}).get("projectId") or "")
    if (
        not document
        or organization_id != str(chronology.get("organization_id") or "")
        or project_id != str(chronology.get("project_id") or "")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Source document is not in this chronology's project",
        )


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
    selection: ActiveScope = Depends(active_scope),
):
    # A filter may narrow the selection, never leave it; nothing selected keeps
    # the membership-bounded list (CL-3A rule).
    if selection.has_project:
        if project_id or organization_id:
            await selection.require_project(project_id or selection.project_id, organization_id)
        project_id = selection.project_id
        organization_id = selection.organization_id
    elif selection.organization_id:
        await selection.require_organization(organization_id)
        organization_id = selection.organization_id
    await policy.authorize(
        current_user,
        Permissions.CHRONOLOGY_VIEW,
        resource_type="matter_chronology",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
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
    selection: ActiveScope = Depends(active_scope),
):
    await selection.require_project(payload.project_id, payload.organization_id)
    org = payload.organization_id or selection.organization_id
    payload = payload.model_copy(update={"organization_id": org})
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
    selection: ActiveScope = Depends(active_scope),
):
    chronology = await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_VIEW, db, current_user, policy, selection)
    return MatterChronology(**chronology)


@router.patch("/chronologies/{chronology_id}", response_model=MatterChronology)
async def update_chronology(
    chronology_id: str,
    payload: MatterChronologyUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EDIT, db, current_user, policy, selection)
    return MatterChronology(**await ChronologyService(db).update_chronology(chronology_id, payload, current_user))


@router.delete("/chronologies/{chronology_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chronology(
    chronology_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_ADMIN, db, current_user, policy, selection)
    # The canonical Document links of its events are retired with it, in one
    # transaction and audited; the events and the Documents stay.
    await ChronologyService(db).delete_chronology(
        chronology_id, current_user, relationships=DocumentRelationshipService(db, policy=policy)
    )
    return None


@router.post("/chronologies/{chronology_id}/extract")
async def extract_chronology_events(
    chronology_id: str,
    payload: ChronologyExtractRequest | None = None,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EDIT, db, current_user, policy, selection)
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
    selection: ActiveScope = Depends(active_scope),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_VIEW, db, current_user, policy, selection)
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
    selection: ActiveScope = Depends(active_scope),
):
    chronology = await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EDIT, db, current_user, policy, selection)
    if "related_document_ids" in payload.model_fields_set:
        _refuse_related_document_write(payload.related_document_ids)
    await _require_source_document_in_scope(db, chronology, payload.source_document_id)
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
    selection: ActiveScope = Depends(active_scope),
):
    chronology = await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EDIT, db, current_user, policy, selection)
    service = ChronologyService(db)
    before = await service.get_event(chronology_id, event_id)
    if "related_document_ids" in payload.model_fields_set:
        # An unchanged echo or a removal passes; adding a reference is a
        # relationship write and belongs to the canonical API.
        _refuse_related_document_write(payload.related_document_ids, before.get("related_document_ids") or [])
    if "source_document_id" in payload.model_fields_set and str(payload.source_document_id or "") != str(
        before.get("source_document_id") or ""
    ):
        await _require_source_document_in_scope(db, chronology, payload.source_document_id)
        if payload.source_document_id:
            # The same write-boundary authority check event creation applies.
            await service._require_current_document_authority(payload.source_document_id)
    return MatterChronologyEvent(**await service.update_event(chronology_id, event_id, payload, current_user))


@router.post("/chronologies/{chronology_id}/events/{event_id}/verify", response_model=MatterChronologyEvent)
async def verify_chronology_event(
    chronology_id: str,
    event_id: str,
    payload: ChronologyDecisionRequest | None = None,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    await _load_chronology_in_selection(chronology_id, db, selection)
    return MatterChronologyEvent(
        **await ChronologyService(db).verify_event(chronology_id, event_id, current_user, payload, policy=policy)
    )


@router.post("/chronologies/{chronology_id}/events/{event_id}/reject", response_model=MatterChronologyEvent)
async def reject_chronology_event(
    chronology_id: str,
    event_id: str,
    payload: ChronologyDecisionRequest | None = None,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    await _load_chronology_in_selection(chronology_id, db, selection)
    return MatterChronologyEvent(
        **await ChronologyService(db).reject_event(chronology_id, event_id, current_user, payload, policy=policy)
    )


@router.post("/chronologies/{chronology_id}/events/{event_id}/mark-duplicate", response_model=MatterChronologyEvent)
async def mark_chronology_event_duplicate(
    chronology_id: str,
    event_id: str,
    payload: ChronologyDuplicateRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EDIT, db, current_user, policy, selection)
    # Edit is the floor; a verified target also needs verify, checked in the service.
    return MatterChronologyEvent(
        **await ChronologyService(db).mark_duplicate(chronology_id, event_id, payload, current_user, policy=policy)
    )


@router.post("/chronologies/{chronology_id}/events/{event_id}/link", response_model=MatterChronologyEvent)
async def link_chronology_event(
    chronology_id: str,
    event_id: str,
    payload: ChronologyLinkRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_EDIT, db, current_user, policy, selection)
    _refuse_related_document_write(payload.related_document_ids or [])
    return MatterChronologyEvent(**await ChronologyService(db).link_event(chronology_id, event_id, payload, current_user))


@router.get("/chronologies/{chronology_id}/events/{event_id}/revisions", response_model=List[MatterChronologyEventRevision])
async def chronology_event_revisions(
    chronology_id: str,
    event_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_VIEW, db, current_user, policy, selection)
    return [MatterChronologyEventRevision(**row) for row in await ChronologyService(db).event_revisions(chronology_id, event_id)]


@router.get("/chronologies/{chronology_id}/pleading-context", response_model=ChronologyPleadingContext)
async def chronology_pleading_context(
    chronology_id: str,
    include_unverified: bool = Query(False),
    limit: int = Query(250, ge=1, le=500),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    await _load_and_authorize_chronology(chronology_id, Permissions.CHRONOLOGY_VIEW, db, current_user, policy, selection)
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


# Exports are opened as plain browser downloads (`<a href>`), which cannot carry
# the selection headers, so they stay selection-blind: membership and the export
# permission still decide (active-scope debt, contraclaim-dms#24).


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
