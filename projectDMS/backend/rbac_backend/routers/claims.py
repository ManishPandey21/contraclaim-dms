"""Claim register API (Phase 4 / Module 1).

Tenant-scoped CRUD + status transitions for claims. Every endpoint is gated by
PolicyService (permission + entitlement + org/project scope) and lists are
filtered with build_scope_query, so Org A can never see Org B's claims.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.approval import ApprovalRecord, AssignBody, DecisionBody
from ..models.claim import Claim, ClaimAssessment, ClaimCreate, ClaimStatusUpdate, ClaimUpdate
from ..services.approval_service import ApprovalError, ApprovalService
from ..services.audit_event_service import AuditEventService
from ..services.claim_assessment_service import ClaimAssessmentService
from ..services.claim_service import ClaimService
from ..services.evidence_bundle_service import EvidenceBundleService
from ..services.document_relationship_service import (
    DocumentRelationshipError,
    DocumentRelationshipService,
)
from ..services.policy_service import PolicyService
from ..utils.error_handler import handle_exceptions

router = APIRouter()


async def _present_claim(claim: dict, db, current_user: CurrentUser) -> Claim:
    presented = dict(claim)
    presented["linked_document_ids"] = await DocumentRelationshipService(
        db
    ).authorized_document_ids(
        current_user,
        "claim",
        str(claim.get("_id") or ""),
        legacy_document_ids=claim.get("linked_document_ids") or [],
    )
    return Claim(**presented)


async def _present_claims(claims: list[dict], db, current_user: CurrentUser) -> list[Claim]:
    ids_by_claim = await DocumentRelationshipService(
        db
    ).authorized_document_ids_for_claims(current_user, claims)
    return [
        Claim(
            **{
                **claim,
                "linked_document_ids": ids_by_claim.get(str(claim.get("_id") or ""), []),
            }
        )
        for claim in claims
    ]


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


@router.get("/claims", response_model=List[Claim])
@handle_exceptions
async def list_claims(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    claim_type: Optional[str] = Query(None, alias="type"),
    status_filter: Optional[str] = Query(None, alias="status"),
    responsible_party_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.CLAIM_VIEW,
        resource_type="claims",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    claims = await ClaimService(db).list(
        scope,
        claim_type=claim_type,
        status=status_filter,
        responsible_party_id=responsible_party_id,
        skip=skip,
        limit=limit,
    )
    return await _present_claims(claims, db, current_user)


@router.post("/claims", response_model=Claim, status_code=status.HTTP_201_CREATED)
@handle_exceptions
async def create_claim(
    payload: ClaimCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user,
        Permissions.CLAIM_CREATE,
        resource_type="claim",
        organization_id=org,
        project_id=payload.project_id,
    )
    legacy_ids = list(payload.linked_document_ids)
    claim_service = ClaimService(db)
    created = await claim_service.create(payload, current_user)
    if legacy_ids:
        try:
            await DocumentRelationshipService(db).replace_legacy_document_ids(
                current_user, "claim", str(created.get("_id") or ""), legacy_ids
            )
        except DocumentRelationshipError:
            await claim_service.delete(
                str(created.get("_id") or ""), current_user, before=created
            )
            raise
    return await _present_claim(created, db, current_user)


async def _load_authorized(claim_id: str, permission: str, db, current_user, policy) -> dict:
    claim = await ClaimService(db).get(claim_id)
    if not claim:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found")
    await policy.authorize_document(current_user, permission, claim, resource_type="claim")
    return claim


@router.get("/claims/{claim_id}", response_model=Claim)
@handle_exceptions
async def get_claim(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy)
    return await _present_claim(claim, db, current_user)


@router.put("/claims/{claim_id}", response_model=Claim)
@handle_exceptions
async def update_claim(
    claim_id: str,
    payload: ClaimUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy)
    changes = payload.model_dump(exclude_unset=True)
    legacy_ids = changes.pop("linked_document_ids", None)
    claim_service = ClaimService(db)
    updated = await claim_service.update(claim_id, changes, current_user, before=claim)
    if legacy_ids is not None:
        try:
            await DocumentRelationshipService(db).replace_legacy_document_ids(
                current_user, "claim", claim_id, legacy_ids
            )
        except DocumentRelationshipError:
            raise
        await claim_service.clear_legacy_document_ids(claim_id)
        updated = await claim_service.get(claim_id)
    return await _present_claim(updated or claim, db, current_user)


@router.post("/claims/{claim_id}/status", response_model=Claim)
async def set_claim_status(
    claim_id: str,
    body: ClaimStatusUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy)
    updated = await ClaimService(db).update(
        claim_id, {"status": body.status.value}, current_user, before=claim
    )
    return Claim(**(updated or claim))


@router.delete("/claims/{claim_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_exceptions
async def delete_claim(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_authorized(claim_id, Permissions.CLAIM_DELETE, db, current_user, policy)
    try:
        await DocumentRelationshipService(db, policy=policy).delete_target(
            current_user,
            "claim",
            claim_id,
            reason="Claim deleted",
        )
    except DocumentRelationshipError:
        raise
    return None


# --- approval workflow (Phase 4 / Module 3) -------------------------------
#
# Generic review/approval state machine bound to the claim. The drafter (claim
# creator) can assign + submit; a manager approves or returns and can never be
# the drafter (no self-approval — enforced in ApprovalService).


async def _approval_for(claim: dict, db) -> dict:
    return await ApprovalService(db).get_or_create(
        "claim",
        str(claim["_id"]),
        organization_id=claim.get("organization_id"),
        project_id=claim.get("project_id"),
        drafter_id=claim.get("created_by"),
    )


@router.post("/claims/{claim_id}/assess", response_model=ClaimAssessment)
async def assess_claim(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    """Run a clause-grounded AI assessment of the claim against the project's
    contract (reuses the citation-enforced contract-QA engine). Gated by
    claims.assess; the assessment + its citations + trace are persisted."""
    claim = await _load_authorized(claim_id, Permissions.CLAIM_ASSESS, db, current_user, policy)
    # Imported lazily — keeps the retrieval stack out of this router's import path.
    from .retrieval_engine import get_observability, get_retrieval_service

    observability = await get_observability(db=db)
    retrieval_service = await get_retrieval_service(db=db, observability=observability)
    record = await ClaimAssessmentService(db).assess(claim, current_user, retrieval_service)
    return ClaimAssessment(**record)


@router.get("/claims/{claim_id}/assessments", response_model=List[ClaimAssessment])
async def list_claim_assessments(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy)
    records = await ClaimAssessmentService(db).list(str(claim["_id"]))
    return [ClaimAssessment(**r) for r in records]


@router.get("/claims/{claim_id}/evidence-bundle")
@handle_exceptions
async def export_evidence_bundle(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    """Stream a ZIP evidence bundle: claim record + in-scope linked correspondence
    + the claim's audit trail (CSV) + a manifest. Requires claim-view to read the
    claim and audit-view for the export; the export action is itself audited."""
    claim = await _load_authorized(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy)
    await policy.authorize(
        current_user,
        Permissions.AUDIT_VIEW,
        resource_type="claim",
        organization_id=claim.get("organization_id"),
        project_id=claim.get("project_id"),
    )
    audit = AuditEventService(db)
    events = await audit.query_events(
        organization_id=claim.get("organization_id"),
        project_id=claim.get("project_id"),
        limit=50000,
    )
    events = [e for e in events if str(e.get("resource_id")) == str(claim_id)]
    try:
        document_sources = await DocumentRelationshipService(db).authorized_document_sources(
            current_user,
            "claim",
            claim_id,
            legacy_document_ids=claim.get("linked_document_ids") or [],
        )
    except DocumentRelationshipError:
        raise
    document_ids = [source["document_id"] for source in document_sources]
    export_claim = {**claim, "linked_document_ids": document_ids}
    content = await EvidenceBundleService(db).build(
        export_claim,
        events,
        document_sources=document_sources,
        generated_by=getattr(current_user, "id", None),
    )
    await audit.emit(
        action="claim.evidence_exported",
        actor_id=getattr(current_user, "id", None),
        resource_type="claim",
        resource_id=str(claim_id),
        organization_id=claim.get("organization_id"),
        project_id=claim.get("project_id"),
        after={"audit_event_count": len(events), "bytes": len(content)},
    )
    ref = claim.get("claim_ref") or claim_id
    filename = f"evidence-{ref}-{datetime.utcnow().strftime('%Y%m%d')}.zip"
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/claims/{claim_id}/approval", response_model=ApprovalRecord)
async def get_claim_approval(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy)
    return ApprovalRecord(**await _approval_for(claim, db))


@router.post("/claims/{claim_id}/assign", response_model=ApprovalRecord)
async def assign_claim_reviewer(
    claim_id: str,
    body: AssignBody,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_MANAGE, db, current_user, policy)
    record = await _approval_for(claim, db)
    try:
        updated = await ApprovalService(db).assign(
            record, body.reviewer_id, getattr(current_user, "id", None),
            due_at=body.due_at, note=body.note,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return ApprovalRecord(**updated)


@router.post("/claims/{claim_id}/submit-for-review", response_model=ApprovalRecord)
async def submit_claim_for_review(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy)
    record = await _approval_for(claim, db)
    try:
        updated = await ApprovalService(db).submit_for_review(record, getattr(current_user, "id", None))
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return ApprovalRecord(**updated)


@router.post("/claims/{claim_id}/approve", response_model=ApprovalRecord)
async def approve_claim(
    claim_id: str,
    body: DecisionBody,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_MANAGE, db, current_user, policy)
    record = await _approval_for(claim, db)
    try:
        updated = await ApprovalService(db).approve(record, getattr(current_user, "id", None), comment=body.comment)
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return ApprovalRecord(**updated)


@router.post("/claims/{claim_id}/return", response_model=ApprovalRecord)
async def return_claim(
    claim_id: str,
    body: DecisionBody,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_MANAGE, db, current_user, policy)
    record = await _approval_for(claim, db)
    try:
        updated = await ApprovalService(db).return_for_changes(record, getattr(current_user, "id", None), comment=body.comment)
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return ApprovalRecord(**updated)
