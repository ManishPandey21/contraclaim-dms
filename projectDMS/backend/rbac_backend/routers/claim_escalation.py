"""Escalate claims into an arbitration case.

Two routes: a preview that shows exactly what would be carried over and what
is weak or left behind, and the escalation itself. Both need the right to
manage every claim named and the arbitration permission for the target (create
for a new case, edit for an existing one), and both sit behind the Claim
Bench's server-side rollout flag.

Static paths have two segments after ``/claims`` so they are never read as
``/claims/{claim_id}``.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, get_current_user
from ..core.tenant_context import ActiveScope, active_scope
from ..services.arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService
from ..services.claim_bench import ClaimBenchService
from ..services.claim_escalation import ClaimEscalation, EscalationPreview, EscalationRequest, EscalationResult
from ..services.document_relationship_service import DocumentRelationshipService
from ..services.policy_service import PolicyService
from ..utils.error_handler import handle_exceptions
from ..utils.rate_limiter import RateLimiter
from .claim_bench import _claim, _claim_documents, _user_id, get_bench
from .claims import get_policy

router = APIRouter()
#: The claim document role that means "this letter gave notice of the claim".
NOTICE_ROLE = "notice"
escalation_limiter = RateLimiter(max_requests=10, window_seconds=60, scope="claim_escalation")


async def _claim_notice_documents(claim: dict, db, current_user: CurrentUser) -> Set[str]:
    """Documents linked to the claim with the role "notice" that the caller may see (4B-34)."""
    links = await DocumentRelationshipService(db).list_for_target(current_user, "claim", str(claim.get("_id") or ""))
    return {str(link.document_id) for link in links if str(link.relationship_role or "") == NOTICE_ROLE}


async def _gather(
    payload: EscalationRequest,
    db,
    bench: ClaimBenchService,
    current_user: CurrentUser,
    policy: PolicyService,
    selection: ActiveScope,
) -> tuple[List[dict], Dict[str, Set[str]], Optional[dict], Dict[str, Set[str]]]:
    """The claims, their visible documents and the target case, each authorised."""
    claims: List[dict] = []
    documents: Dict[str, Set[str]] = {}
    notices: Dict[str, Set[str]] = {}
    for claim_id in payload.claim_ids:
        claim = await _claim(claim_id, Permissions.CLAIM_MANAGE, db, current_user, policy, selection, bench)
        claims.append(claim)
        documents[str(claim["_id"])] = await _claim_documents(claim, db, current_user)
        notices[str(claim["_id"])] = await _claim_notice_documents(claim, db, current_user)
    case: Optional[dict] = None
    if payload.case_id:
        case = await ArbitrationCaseWorkspaceService(db).get_case(payload.case_id)
        await selection.require_record(case)
        await policy.authorize_document(current_user, Permissions.ARBITRATION_EDIT, case, resource_type="arbitration_case")
    else:
        first = claims[0]
        await policy.authorize(
            current_user,
            Permissions.ARBITRATION_CREATE,
            resource_type="arbitration_case",
            organization_id=first.get("organization_id"),
            project_id=first.get("project_id"),
        )
    return claims, documents, case, notices


@router.post("/claims/escalation/preview", response_model=EscalationPreview)
@handle_exceptions
async def preview_claim_escalation(
    payload: EscalationRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """What escalating these claims would carry into a case. Changes nothing."""
    claims, documents, case, notices = await _gather(payload, db, bench, current_user, policy, selection)
    return await ClaimEscalation(db, bench).preview(claims, documents, case, notices)


@router.post("/claims/escalation/run", response_model=EscalationResult, status_code=status.HTTP_201_CREATED)
@handle_exceptions
async def run_claim_escalation(
    payload: EscalationRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Create or extend a case from these claims. Every row carried starts as needs-review."""
    if payload.case_id and payload.new_case:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Choose an existing case or a new one, not both."
        )
    claims, documents, case, notices = await _gather(payload, db, bench, current_user, policy, selection)
    await escalation_limiter.check_user_limit(_user_id(current_user) or "")
    return await ClaimEscalation(db, bench).escalate(claims, documents, payload, current_user, case, notices)


__all__ = ["router"]
