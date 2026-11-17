"""Two Chairs and the Submission Pack.

Determinations, simulations, the strategy note and the claim submission. Every
route is under the claim permissions and the navbar selection, as the claim
register is, and behind the Claim Bench's server-side rollout flag.

Three permissions do the separating:

- ``dms.claim.determine``: making and approving an official determination.
  Holding the ordinary claim permissions does not confer it.
- ``dms.claim.strategy.read``: every route that touches a simulation or the
  strategy note, read or write. Those routes return privileged material and
  nothing else returns it.
- the ordinary claim permissions: everything else, including recording a
  determination received from the other side.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, get_current_user
from ..core.tenant_context import ActiveScope, active_scope
from ..models.claim_findings import ClaimSimulation
from ..services.audit_event_service import AuditEventService
from ..services.claim_bench import ClaimBenchService
from ..services.picker_service import PickerService
from ..services.claim_determinations import (
    ClaimDeterminations,
    DeterminationCreateRequest,
    DeterminationSubmitRequest,
    DeterminationVersionRequest,
    DeterminationView,
    DeterminationsResponse,
    HeadFindingRequest,
)
from ..services.claim_privileged import (
    ClaimPrivileged,
    SimulationRequest,
    SimulationsResponse,
    StrategyNoteRequest,
    StrategyNoteResponse,
)
from ..services import claim_contract_scope as contract_scope
from ..services.claim_delay_lab import ClaimDelayLab
from ..services.claim_submission import (
    EVIDENCE_RESTRICTED,
    ClaimSubmissions,
    ComposeRequest,
    SectionEditRequest,
    SubmissionView,
    claim_evidence_ids,
)
from ..services.policy_service import PolicyService
from ..utils.error_handler import handle_exceptions
from ..utils.rate_limiter import RateLimiter
from .claim_bench import _claim, _claim_documents, _may, _user_id, get_bench
from .claims import get_policy

router = APIRouter()
findings_skill_limiter = RateLimiter(max_requests=20, window_seconds=60, scope="claim_findings_skill")


async def _holds(policy: PolicyService, current_user: CurrentUser, permission: str, claim: dict) -> bool:
    return await _may(
        policy, current_user, permission, str(claim.get("organization_id") or ""), str(claim.get("project_id") or "")
    )


def _approver_rule(db, policy: PolicyService, claim: dict):
    """The approver picker's own rule, read at the time it is asked (4B-26): a member of
    the claim's project holding determination authority by name."""
    pickers = PickerService(db, policy)

    async def eligible(user_id: str) -> bool:
        return await pickers.is_eligible(
            user_id,
            organization_id=str(claim.get("organization_id") or ""),
            project_id=str(claim.get("project_id") or ""),
            permission=Permissions.CLAIM_DETERMINE,
        )

    return eligible


def _determinations(db, bench: ClaimBenchService, policy: PolicyService, claim: dict) -> ClaimDeterminations:
    return ClaimDeterminations(db, bench, approver_rule=_approver_rule(db, policy, claim))


async def _for_determination(
    claim_id: str,
    determination_id: str,
    db,
    current_user: CurrentUser,
    policy: PolicyService,
    selection: ActiveScope,
    bench: ClaimBenchService,
) -> tuple[dict, ClaimDeterminations]:
    """Load the claim and authorise a change to one of its determinations.

    A determination made here needs determination authority; a transcription of
    one received from the other side needs only the right to edit the claim.
    """
    claim = await _claim(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench)
    service = _determinations(db, bench, policy, claim)
    determination = await service._load(claim, determination_id)
    permission = Permissions.CLAIM_DETERMINE if determination.origin == "authored" else Permissions.CLAIM_EDIT
    await policy.authorize_document(current_user, permission, claim, resource_type="claim")
    return claim, service


async def _privileged(
    claim_id: str,
    write: bool,
    db,
    current_user: CurrentUser,
    policy: PolicyService,
    selection: ActiveScope,
    bench: ClaimBenchService,
) -> dict:
    claim = await _claim(
        claim_id, Permissions.CLAIM_EDIT if write else Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench
    )
    await policy.authorize_document(current_user, Permissions.CLAIM_STRATEGY_READ, claim, resource_type="claim")
    return claim


# --------------------------------------------------------------------------- #
# Determinations
# --------------------------------------------------------------------------- #
@router.get("/claims/{claim_id}/determinations", response_model=DeterminationsResponse)
@handle_exceptions
async def list_claim_determinations(
    claim_id: str,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Determinations of a claim: those made here and those received from the other side."""
    claim = await _claim(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench)
    return await _determinations(db, bench, policy, claim).list(
        claim,
        holds_authority=await _holds(policy, current_user, Permissions.CLAIM_DETERMINE, claim),
        viewer_id=_user_id(current_user),
    )


@router.post("/claims/{claim_id}/determinations", response_model=DeterminationView)
@handle_exceptions
async def create_claim_determination(
    claim_id: str,
    payload: DeterminationCreateRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Start a determination (Engineer or Employer workspace, with authority), or
    start the transcription of one received from the other side."""
    authored = payload.origin == "authored"
    claim = await _claim(
        claim_id,
        Permissions.CLAIM_DETERMINE if authored else Permissions.CLAIM_EDIT,
        db, current_user, policy, selection, bench,
    )
    return await ClaimDeterminations(db, bench).create(
        claim,
        payload,
        _user_id(current_user),
        holds_authority=authored,
        allowed_documents=None if authored else await _claim_documents(claim, db, current_user),
    )


@router.get("/claims/{claim_id}/determinations/{determination_id}", response_model=DeterminationView)
@handle_exceptions
async def get_claim_determination(
    claim_id: str,
    determination_id: str,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _claim(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench)
    return await _determinations(db, bench, policy, claim).get(claim, determination_id, _user_id(current_user))


@router.put(
    "/claims/{claim_id}/determinations/{determination_id}/heads/{head_id}", response_model=DeterminationView
)
@handle_exceptions
async def set_claim_determination_finding(
    claim_id: str,
    determination_id: str,
    head_id: str,
    payload: HeadFindingRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Record the finding on one head. The claim and its board are not changed."""
    claim, service = await _for_determination(claim_id, determination_id, db, current_user, policy, selection, bench)
    return await service.set_finding(claim, determination_id, head_id, payload, _user_id(current_user))


@router.post("/claims/{claim_id}/determinations/{determination_id}/propose", response_model=DeterminationView)
@handle_exceptions
async def propose_claim_determination(
    claim_id: str,
    determination_id: str,
    payload: DeterminationVersionRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Put a proposal beside each head. Proposes; decides nothing."""
    claim, service = await _for_determination(claim_id, determination_id, db, current_user, policy, selection, bench)
    await findings_skill_limiter.check_user_limit(_user_id(current_user) or "")
    return await service.propose(claim, determination_id, payload, _user_id(current_user))


@router.post("/claims/{claim_id}/determinations/{determination_id}/submit", response_model=DeterminationView)
@handle_exceptions
async def submit_claim_determination(
    claim_id: str,
    determination_id: str,
    payload: DeterminationSubmitRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim, service = await _for_determination(claim_id, determination_id, db, current_user, policy, selection, bench)
    return await service.submit(
        claim, determination_id, payload, _user_id(current_user), reviewer_eligible=_approver_rule(db, policy, claim)
    )


@router.post("/claims/{claim_id}/determinations/{determination_id}/reassign", response_model=DeterminationView)
@handle_exceptions
async def reassign_claim_determination(
    claim_id: str,
    determination_id: str,
    payload: DeterminationSubmitRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Send a determination in review to another approver, only when its reviewer no longer
    holds determination authority on the project (4B-26)."""
    claim, service = await _for_determination(claim_id, determination_id, db, current_user, policy, selection, bench)
    return await service.reassign(claim, determination_id, payload, _user_id(current_user))


@router.post("/claims/{claim_id}/determinations/{determination_id}/approve", response_model=DeterminationView)
@handle_exceptions
async def approve_claim_determination(
    claim_id: str,
    determination_id: str,
    payload: DeterminationVersionRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Approve a determination. Never by a person who made its findings."""
    claim = await _claim(claim_id, Permissions.CLAIM_DETERMINE, db, current_user, policy, selection, bench)
    return await _determinations(db, bench, policy, claim).approve(claim, determination_id, payload, _user_id(current_user))


@router.post("/claims/{claim_id}/determinations/{determination_id}/return", response_model=DeterminationView)
@handle_exceptions
async def return_claim_determination(
    claim_id: str,
    determination_id: str,
    payload: DeterminationVersionRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _claim(claim_id, Permissions.CLAIM_DETERMINE, db, current_user, policy, selection, bench)
    return await _determinations(db, bench, policy, claim).send_back(claim, determination_id, payload, _user_id(current_user))


@router.post("/claims/{claim_id}/determinations/{determination_id}/record", response_model=DeterminationView)
@handle_exceptions
async def record_received_claim_determination(
    claim_id: str,
    determination_id: str,
    payload: DeterminationVersionRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Close the transcription of a received determination. It does not change afterwards."""
    claim, service = await _for_determination(claim_id, determination_id, db, current_user, policy, selection, bench)
    return await service.record_received(claim, determination_id, payload, _user_id(current_user))


@router.post("/claims/{claim_id}/determinations/{determination_id}/revise", response_model=DeterminationView)
@handle_exceptions
async def revise_claim_determination(
    claim_id: str,
    determination_id: str,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Start a new revision of an approved determination. The approved one is kept as it is."""
    claim = await _claim(claim_id, Permissions.CLAIM_DETERMINE, db, current_user, policy, selection, bench)
    return await ClaimDeterminations(db, bench).revise(
        claim, determination_id, _user_id(current_user), holds_authority=True
    )


# --------------------------------------------------------------------------- #
# Privileged: simulations and the strategy note
# --------------------------------------------------------------------------- #
@router.get("/claims/{claim_id}/simulations", response_model=SimulationsResponse)
@handle_exceptions
async def list_claim_simulations(
    claim_id: str,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Internal analysis, not a determination. Privileged."""
    claim = await _privileged(claim_id, False, db, current_user, policy, selection, bench)
    return await ClaimPrivileged(db, bench).simulations(claim)


@router.post("/claims/{claim_id}/simulations", response_model=ClaimSimulation)
@handle_exceptions
async def simulate_claim_response(
    claim_id: str,
    payload: SimulationRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Model how another party is likely to respond. Changes nothing on the claim."""
    claim = await _privileged(claim_id, True, db, current_user, policy, selection, bench)
    await findings_skill_limiter.check_user_limit(_user_id(current_user) or "")
    return await ClaimPrivileged(db, bench).simulate(claim, payload, _user_id(current_user))


@router.get("/claims/{claim_id}/strategy-note", response_model=StrategyNoteResponse)
@handle_exceptions
async def get_claim_strategy_note(
    claim_id: str,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Internal, not for issue. Privileged."""
    claim = await _privileged(claim_id, False, db, current_user, policy, selection, bench)
    return await ClaimPrivileged(db, bench).strategy_note(claim)


@router.put("/claims/{claim_id}/strategy-note", response_model=StrategyNoteResponse)
@handle_exceptions
async def save_claim_strategy_note(
    claim_id: str,
    payload: StrategyNoteRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _privileged(claim_id, True, db, current_user, policy, selection, bench)
    return await ClaimPrivileged(db, bench).save_note(claim, payload, _user_id(current_user))


@router.post("/claims/{claim_id}/strategy-note/generate", response_model=StrategyNoteResponse)
@handle_exceptions
async def generate_claim_strategy_note(
    claim_id: str,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _privileged(claim_id, True, db, current_user, policy, selection, bench)
    await findings_skill_limiter.check_user_limit(_user_id(current_user) or "")
    return await ClaimPrivileged(db, bench).generate_note(claim, _user_id(current_user))


# --------------------------------------------------------------------------- #
# Submission Pack
# --------------------------------------------------------------------------- #
@router.get("/claims/{claim_id}/submission", response_model=SubmissionView)
@handle_exceptions
async def get_claim_submission(
    claim_id: str,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _claim(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench)
    return await ClaimSubmissions(db, bench).view(
        claim, await claim_evidence_ids(db, claim), await _claim_documents(claim, db, current_user)
    )


@router.post("/claims/{claim_id}/submission/compose", response_model=SubmissionView)
@handle_exceptions
async def compose_claim_submission(
    claim_id: str,
    payload: ComposeRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Compose the submission from the locked board. Sections whose record has not changed are kept."""
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    await findings_skill_limiter.check_user_limit(_user_id(current_user) or "")
    return await ClaimSubmissions(db, bench).compose(
        claim, payload, await claim_evidence_ids(db, claim), _user_id(current_user),
        accessible=await _claim_documents(claim, db, current_user),
    )


@router.put("/claims/{claim_id}/submission/sections/{section_key}", response_model=SubmissionView)
@handle_exceptions
async def edit_claim_submission_section(
    claim_id: str,
    section_key: str,
    payload: SectionEditRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """4B-32: rewrite a section's paragraphs. Marked edited; sources kept; text checked against the record."""
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    view = await ClaimSubmissions(db, bench).edit_section(
        claim, section_key, payload, await claim_evidence_ids(db, claim), _user_id(current_user),
        accessible=await _claim_documents(claim, db, current_user),
    )
    await AuditEventService(db).emit(
        action="claim.submission_section_edited",
        actor_id=_user_id(current_user),
        resource_type="claim",
        resource_id=str(claim_id),
        organization_id=claim.get("organization_id"),
        project_id=claim.get("project_id"),
        after={"section": section_key, "paragraphs": len(payload.paragraphs)},
    )
    return view


@router.get("/claims/{claim_id}/submission/export")
@handle_exceptions
async def export_claim_submission(
    claim_id: str,
    format: str = Query("docx"),
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Download the submission as DOCX or PDF. Marked as a draft until the claim
    is approved. Never contains the strategy note or a simulation."""
    claim = await _claim(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench)
    try:
        content, media_type, filename = await ClaimSubmissions(db, bench).export(
            claim, format, await claim_evidence_ids(db, claim), await _claim_documents(claim, db, current_user)
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") == EVIDENCE_RESTRICTED:
            # D-25: the refusal is audited; the event records the count, never an item.
            await AuditEventService(db).emit(
                action="claim.submission_export_refused",
                actor_id=_user_id(current_user),
                resource_type="claim",
                resource_id=str(claim_id),
                organization_id=claim.get("organization_id"),
                project_id=claim.get("project_id"),
                result="denied",
                reason=EVIDENCE_RESTRICTED,
                after={"format": format, "restricted_items": detail.get("restricted_items")},
            )
        raise
    await AuditEventService(db).emit(
        action="claim.submission_exported",
        actor_id=_user_id(current_user),
        resource_type="claim",
        resource_id=str(claim_id),
        organization_id=claim.get("organization_id"),
        project_id=claim.get("project_id"),
        after={"format": format, "bytes": len(content)},
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- #
# OD-1: a person confirms the contract of evidence whose contract is not shown
# --------------------------------------------------------------------------- #
class EvidenceConfirmationRequest(BaseModel):
    record_kind: str = Field(..., min_length=1, max_length=40)
    record_id: str = Field(..., min_length=1, max_length=200)
    reason: str = Field(..., max_length=2000)
    #: The letter, drawing or register entry that shows which contract the record belongs to.
    source_reference: str = Field("", max_length=500)


async def _linked_by(db, claim: dict, kind: str, record_id: str) -> set:
    """Everyone who linked this record to this claim, at any time (OD-1 independence)."""
    if kind == "document":
        rows = db.entity_document_links.find(
            {"target_type": "claim", "target_id": str(claim["_id"]), "document_id": record_id}, {"created_by": 1}
        )
    else:
        rows = db.claim_head_links.find({"claim_id": str(claim["_id"]), "target_id": record_id}, {"created_by": 1})
    return {str(row.get("created_by")) async for row in rows if row.get("created_by")}


class EvidenceWithdrawalRequest(BaseModel):
    reason: str = Field(..., max_length=2000)


async def _evidence_review(claim: dict, db, current_user: CurrentUser, bench: ClaimBenchService):
    links = await ClaimDelayLab(db, bench).links_for_claim(str(claim["_id"]))
    # The claim's full evidence set (4B-27): confirmations are about the claim, not the viewer.
    return await contract_scope.identity_review(db, claim, links, await claim_evidence_ids(db, claim))


def _review_body(claim: dict, review, accessible: set) -> dict:
    """D-25: a document the viewer may not read appears as "Restricted item", without its id."""

    def shown(item) -> dict:
        entry = vars(item)
        if item.kind == "document" and item.record_id not in accessible:
            return {**entry, "record_id": "", "label": "Restricted item", "confirmation_id": None,
                    "reason": None, "confirmed_by": None, "confirmed_at": None}
        return entry

    hidden = sum(1 for item in review.unconfirmed + review.confirmed if item.kind == "document" and item.record_id not in accessible)
    return {
        "claim_id": str(claim["_id"]),
        "contract_id": contract_scope.contract_of(claim),
        "conflicts": len(review.conflicts) if hidden else review.conflicts,
        "unconfirmed": [shown(item) for item in review.unconfirmed] if contract_scope.contract_of(claim) else [],
        "confirmed": [shown(item) for item in review.confirmed],
        "blocking": contract_scope.unconfirmed_reason(claim, review),
        "restricted_items": hidden,
    }


@router.get("/claims/{claim_id}/evidence-contract")
@handle_exceptions
async def get_claim_evidence_contract(
    claim_id: str,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """The claim's evidence whose contract a person must confirm before export or escalation (OD-1)."""
    claim = await _claim(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench)
    return _review_body(
        claim, await _evidence_review(claim, db, current_user, bench), await _claim_documents(claim, db, current_user)
    )


@router.post("/claims/{claim_id}/evidence-contract/confirmations")
@handle_exceptions
async def confirm_claim_evidence_contract(
    claim_id: str,
    payload: EvidenceConfirmationRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Confirm that one record belongs to the claim's contract. Audited; the record is not changed.

    Owner decision 2026-11-17: only a holder of ``dms.claim.evidence.confirm_contract`` by
    explicit grant on the claim's project (never through Super Admin, a wildcard or
    dms.admin), who did not link the record, giving a reason and a source reference."""
    claim = await _claim(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench)
    await policy.authorize_document(current_user, Permissions.CLAIM_EVIDENCE_CONFIRM_CONTRACT, claim, resource_type="claim")
    review = await _evidence_review(claim, db, current_user, bench)
    accessible = await _claim_documents(claim, db, current_user)
    if payload.record_kind == "document" and payload.record_id not in accessible:
        # Only a person who can read a document can vouch for it (D-25: and nothing is said about it).
        raise HTTPException(status_code=403, detail="You cannot confirm a record you are not allowed to read.")
    row = await contract_scope.confirm(
        db, claim, review, kind=payload.record_kind, record_id=payload.record_id, reason=payload.reason,
        user_id=_user_id(current_user), source_reference=payload.source_reference,
        linked_by=await _linked_by(db, claim, payload.record_kind, payload.record_id),
        confirmer={"id": _user_id(current_user), "username": getattr(current_user, "username", None),
                   "email": getattr(current_user, "email", None)},
    )
    await AuditEventService(db).emit(
        action="claim.evidence_contract_confirmed",
        actor_id=_user_id(current_user),
        resource_type="claim",
        resource_id=str(claim_id),
        organization_id=claim.get("organization_id"),
        project_id=claim.get("project_id"),
        reason=row["reason"],
        after={key: row[key] for key in (
            "_id", "contract_id", "project_id", "record_kind", "record_id", "state_at_confirmation", "record_revision",
            "source_reference", "superseded_confirmation_id",
        )},
    )
    return _review_body(claim, await _evidence_review(claim, db, current_user, bench), accessible)


@router.post("/claims/{claim_id}/evidence-contract/confirmations/{confirmation_id}/withdraw")
@handle_exceptions
async def withdraw_claim_evidence_contract(
    claim_id: str,
    confirmation_id: str,
    payload: EvidenceWithdrawalRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Withdraw a confirmation; the record needs confirming again. Kept, audited."""
    claim = await _claim(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench)
    await policy.authorize_document(current_user, Permissions.CLAIM_EVIDENCE_CONFIRM_CONTRACT, claim, resource_type="claim")
    row = await contract_scope.withdraw(db, claim, confirmation_id, reason=payload.reason, user_id=_user_id(current_user))
    await AuditEventService(db).emit(
        action="claim.evidence_contract_confirmation_withdrawn",
        actor_id=_user_id(current_user),
        resource_type="claim",
        resource_id=str(claim_id),
        organization_id=claim.get("organization_id"),
        project_id=claim.get("project_id"),
        reason=row.get("withdrawal_reason"),
        after={"_id": row["_id"], "record_kind": row["record_kind"], "record_id": row["record_id"]},
    )
    return _review_body(
        claim, await _evidence_review(claim, db, current_user, bench), await _claim_documents(claim, db, current_user)
    )


__all__ = ["router"]
