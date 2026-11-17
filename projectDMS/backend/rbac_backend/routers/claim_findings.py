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

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

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
from ..services.claim_submission import ClaimSubmissions, ComposeRequest, SubmissionView
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
    service = ClaimDeterminations(db, bench)
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
    return await ClaimDeterminations(db, bench).list(
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
    return await ClaimDeterminations(db, bench).get(claim, determination_id, _user_id(current_user))


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
    pickers = PickerService(db, policy)

    async def reviewer_eligible(user_id: str) -> bool:
        # The approver picker's own rule: a project member holding determination authority by name.
        return await pickers.is_eligible(
            user_id,
            organization_id=str(claim.get("organization_id") or ""),
            project_id=str(claim.get("project_id") or ""),
            permission=Permissions.CLAIM_DETERMINE,
        )

    return await service.submit(
        claim, determination_id, payload, _user_id(current_user), reviewer_eligible=reviewer_eligible
    )


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
    return await ClaimDeterminations(db, bench).approve(claim, determination_id, payload, _user_id(current_user))


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
    return await ClaimDeterminations(db, bench).send_back(claim, determination_id, payload, _user_id(current_user))


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
    return await ClaimSubmissions(db, bench).view(claim, await _claim_documents(claim, db, current_user))


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
        claim, payload, await _claim_documents(claim, db, current_user), _user_id(current_user)
    )


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
    content, media_type, filename = await ClaimSubmissions(db, bench).export(
        claim, format, await _claim_documents(claim, db, current_user)
    )
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


__all__ = ["router"]
