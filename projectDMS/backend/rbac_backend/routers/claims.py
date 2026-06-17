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
from ..services.policy_service import PolicyService

router = APIRouter()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


@router.get("/claims", response_model=List[Claim])
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
    return [Claim(**c) for c in claims]


@router.post("/claims", response_model=Claim, status_code=status.HTTP_201_CREATED)
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
    created = await ClaimService(db).create(payload, current_user)
    return Claim(**created)


async def _load_authorized(claim_id: str, permission: str, db, current_user, policy) -> dict:
    claim = await ClaimService(db).get(claim_id)
    if not claim:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found")
    await policy.authorize_document(current_user, permission, claim, resource_type="claim")
    return claim


@router.get("/claims/{claim_id}", response_model=Claim)
async def get_claim(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy)
    return Claim(**claim)


@router.put("/claims/{claim_id}", response_model=Claim)
async def update_claim(
    claim_id: str,
    payload: ClaimUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy)
    updated = await ClaimService(db).update(
        claim_id, payload.model_dump(exclude_unset=True), current_user, before=claim
    )
    return Claim(**(updated or claim))


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
async def delete_claim(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_DELETE, db, current_user, policy)
    await ClaimService(db).delete(claim_id, current_user, before=claim)
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
    content = await EvidenceBundleService(db).build(
        claim, events, generated_by=getattr(current_user, "id", None)
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
