"""Claim Bench and Notice Clock API.

Every route is under the claim permissions and the navbar selection, exactly
as the claim register is. The Bench and its signal routes are behind a
server-side rollout flag; the notice-requirement routes are not, because the
register's notice column uses them whether or not the Bench is on.

Static paths here have two segments after ``/claims`` (``/claims/bench/...``,
``/claims/notice/...``) so they can never be read as ``/claims/{claim_id}``.
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..core.tenant_context import ActiveScope, active_scope
from ..models.claim import ClaimRead
from ..models.claim_notice import (
    NoticeCandidatesResponse,
    NoticeRequirement,
    NoticeRequirementCreate,
)
from ..models.skill import SkillRunStep
from ..services.claim_bench import (
    BoardVersionRequest,
    ClaimBenchAvailability,
    ClaimBenchResponse,
    ClaimBenchService,
    ElementDecisionRequest,
    HeadCreateRequest,
    HeadPatchRequest,
    HeadTestRequest,
)
from ..services.claim_delay_lab import (
    DETERMINATIONS,
    HINDRANCES,
    SUBMISSIONS,
    ClaimDelayLab,
    CostBasisRequest,
    CostReviewRequest,
    DelaySettingsRequest,
    DelayView,
    EventDecisionRequest,
    LinkCreateRequest,
    LinkRemoveRequest,
    OverlapDecisionRequest,
)
from ..services.claim_notice_requirements import NoticeRequirementService
from ..services.claim_quantum import ClaimQuantum, QuantumLineRequest, QuantumView
from ..services.document_relationship_service import DocumentRelationshipService
from ..services.claim_signals import ClaimSignal, ClaimSignalService, PotentialClaimRequest
from ..services.policy_service import PolicyService
from ..utils.error_handler import BaseDomainError, handle_exceptions
from ..utils.rate_limiter import RateLimiter
from .claims import _load_authorized, _present_claim, get_policy

router = APIRouter()
head_test_limiter = RateLimiter(max_requests=30, window_seconds=60, scope="claim_bench_head_test")


class NoticeRequirementsResponse(BaseModel):
    current: Optional[NoticeRequirement] = None
    history: List[NoticeRequirement] = Field(default_factory=list)


class PotentialClaimResponse(BaseModel):
    claim: ClaimRead
    created: bool


def _user_id(current_user: CurrentUser) -> Optional[str]:
    value = getattr(current_user, "id", None)
    return str(value) if value else None


def get_bench(db=Depends(get_db)) -> ClaimBenchService:
    return ClaimBenchService(db)


async def _project_scope(
    selection: ActiveScope,
    policy: PolicyService,
    current_user: CurrentUser,
    permission: str,
    organization_id: Optional[str],
    project_id: Optional[str],
) -> tuple[str, str]:
    """The (organisation, project) a project-level route acts on, authorised."""
    organization_id, project_id = await selection.list_filters(organization_id, project_id)
    if not project_id:
        selection.require_selection()
    organization_id = organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user,
        permission,
        resource_type="claims",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    if not organization_id or not project_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Select a project in the navbar to continue.")
    return str(organization_id), str(project_id)


async def _may(policy: PolicyService, current_user: CurrentUser, permission: str, organization_id: str, project_id: str) -> bool:
    """Whether the caller holds ``permission`` here, without failing the request."""
    try:
        await policy.authorize(
            current_user,
            permission,
            resource_type="claims",
            organization_id=organization_id,
            project_id=project_id,
            audit=False,
        )
    except (BaseDomainError, HTTPException):
        return False
    return True


# --------------------------------------------------------------------------- #
# Availability, signals, potential claims
# --------------------------------------------------------------------------- #
@router.get("/claims/bench/availability", response_model=ClaimBenchAvailability)
@handle_exceptions
async def claim_bench_availability(
    organization_id: Optional[str] = Query(None),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    selection: ActiveScope = Depends(active_scope),
):
    """Whether the Claim Bench is on for the selected organisation."""
    organization_id, _project = await selection.list_filters(organization_id, None)
    return bench.availability(organization_id or getattr(current_user, "organization_id", None))


@router.get("/claims/bench/signals", response_model=List[ClaimSignal])
@handle_exceptions
async def list_claim_signals(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
    bench: ClaimBenchService = Depends(get_bench),
):
    """Records that indicate a claim nobody has raised yet, with the notice clock each is on.

    A source the caller may not read contributes nothing; it is not an error.
    """
    org, project = await _project_scope(selection, policy, current_user, Permissions.CLAIM_VIEW, organization_id, project_id)
    bench.ensure_enabled(org or getattr(current_user, "organization_id", None))
    scope = build_scope_query(current_user, organization_id=org, project_id=project)
    hindrances = scope if await _may(policy, current_user, Permissions.HINDRANCE_VIEW, org, project) else None
    boards = scope if await _may(policy, current_user, Permissions.DRAFTING_REQUEST_VIEW, org, project) else None
    return await ClaimSignalService(db).list(
        organization_id=org, hindrance_query=hindrances, board_query=boards
    )


@router.post("/claims/bench/potential", response_model=PotentialClaimResponse)
@handle_exceptions
async def raise_potential_claim(
    payload: PotentialClaimRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
    bench: ClaimBenchService = Depends(get_bench),
):
    """Accept a signal: create its potential claim, or return the one already raised from it."""
    signals = ClaimSignalService(db)
    source = await signals.load_source(payload.source_type, payload.source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source record not found")
    await selection.require_record(source)
    bench.ensure_enabled(str(source.get("organization_id") or "") or getattr(current_user, "organization_id", None))
    source_permission = (
        Permissions.HINDRANCE_VIEW if payload.source_type == "hindrance" else Permissions.DRAFTING_REQUEST_VIEW
    )
    await policy.authorize_document(current_user, source_permission, source, resource_type=payload.source_type)
    await policy.authorize(
        current_user,
        Permissions.CLAIM_CREATE,
        resource_type="claim",
        organization_id=source.get("organization_id"),
        project_id=source.get("project_id"),
    )
    selected_org, _project = await selection.list_filters(None, None)
    claim, created = await signals.raise_potential(
        payload.source_type, source, current_user, selected_organization_id=selected_org
    )
    return PotentialClaimResponse(claim=await _present_claim(claim, db, current_user), created=created)


# --------------------------------------------------------------------------- #
# Notice requirements: the period this project's contract sets
# --------------------------------------------------------------------------- #
@router.get("/claims/notice/candidates", response_model=NoticeCandidatesResponse)
@handle_exceptions
async def notice_period_candidates(
    claim_type: str = Query(..., min_length=1, max_length=40),
    claim_id: Optional[str] = Query(None),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Notice periods found in the wording of the project's indexed contract clauses.

    Proposals for a person to choose from and then verify. Clause wording is
    contract content, so this also needs the clause-read permission.
    """
    org, project = await _project_scope(selection, policy, current_user, Permissions.CLAIM_VIEW, organization_id, project_id)
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_CLAUSE_READ,
        resource_type="contract_clause",
        organization_id=org,
        project_id=project,
        audit=False,
    )
    cited: List[str] = []
    if claim_id:
        claim = await _load_authorized(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection)
        cited = list(claim.get("contract_clauses") or [])
        for head in ((claim.get("bench") or {}).get("heads") or []):
            cited.extend(head.get("clause_refs") or [])
    service = NoticeRequirementService(db)
    indexed, candidates = await service.candidates(org, project, cited)
    return NoticeCandidatesResponse(
        project_id=project,
        claim_type=claim_type,
        clause_index_available=indexed,
        candidates=candidates,
        current=await service.current(org, project, claim_type),
    )


@router.get("/claims/notice/requirements", response_model=NoticeRequirementsResponse)
@handle_exceptions
async def get_notice_requirement(
    claim_type: str = Query(..., min_length=1, max_length=40),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    org, project = await _project_scope(selection, policy, current_user, Permissions.CLAIM_VIEW, organization_id, project_id)
    service = NoticeRequirementService(db)
    history = await service.history(org, project, claim_type)
    return NoticeRequirementsResponse(
        current=next((item for item in history if item.status == "current"), None), history=history
    )


@router.post("/claims/notice/requirements", response_model=NoticeRequirement, status_code=status.HTTP_201_CREATED)
@handle_exceptions
async def set_notice_requirement(
    payload: NoticeRequirementCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Record the notice period for a claim type, with the clause wording it is read from.

    It starts unverified and replaces any current one, which is kept as history.
    The clause is resolved against the project's clause index, so recording one
    also needs clause-read rights (D-08).
    """
    org, project = await _project_scope(
        selection, policy, current_user, Permissions.CLAIM_EDIT, payload.organization_id, payload.project_id
    )
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_CLAUSE_READ,
        resource_type="contract_clause",
        organization_id=org,
        project_id=project,
    )
    return await NoticeRequirementService(db).set(org, payload, _user_id(current_user))


@router.post("/claims/notice/requirements/{requirement_id}/verify", response_model=NoticeRequirement)
@handle_exceptions
async def verify_notice_requirement(
    requirement_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Confirm a recorded period against its quoted clause.

    Needs claim-manage and clause-read rights, and a person other than the one
    who recorded it (D-08).
    """
    service = NoticeRequirementService(db)
    requirement = await service.get(requirement_id)
    if requirement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notice requirement not found")
    await selection.require_project(requirement.project_id, requirement.organization_id)
    await policy.authorize(
        current_user,
        Permissions.CLAIM_MANAGE,
        resource_type="claim_notice_requirement",
        resource_id=requirement.id,
        organization_id=requirement.organization_id,
        project_id=requirement.project_id,
    )
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_CLAUSE_READ,
        resource_type="contract_clause",
        organization_id=requirement.organization_id,
        project_id=requirement.project_id,
    )
    return await service.verify(requirement, _user_id(current_user))


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #
async def _claim(
    claim_id: str,
    permission: str,
    db,
    current_user: CurrentUser,
    policy: PolicyService,
    selection: ActiveScope,
    bench: ClaimBenchService,
) -> dict:
    claim = await _load_authorized(claim_id, permission, db, current_user, policy, selection)
    bench.ensure_enabled(str(claim.get("organization_id") or "") or None)
    return claim


@router.get("/claims/{claim_id}/bench", response_model=ClaimBenchResponse)
@handle_exceptions
async def get_claim_bench(
    claim_id: str,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _claim(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench)
    return await bench.get(claim)


@router.post("/claims/{claim_id}/bench/open", response_model=ClaimBenchResponse)
@handle_exceptions
async def open_claim_bench(
    claim_id: str,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Create the board, with one head carried over from the register. Idempotent."""
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await bench.open(claim, _user_id(current_user))


@router.post("/claims/{claim_id}/bench/heads", response_model=ClaimBenchResponse)
@handle_exceptions
async def add_claim_head(
    claim_id: str,
    payload: HeadCreateRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await bench.add_head(claim, payload, _user_id(current_user))


@router.patch("/claims/{claim_id}/bench/heads/{head_id}", response_model=ClaimBenchResponse)
@handle_exceptions
async def patch_claim_head(
    claim_id: str,
    head_id: str,
    payload: HeadPatchRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await bench.patch_head(claim, head_id, payload, _user_id(current_user))


@router.post("/claims/{claim_id}/bench/heads/{head_id}/remove", response_model=ClaimBenchResponse)
@handle_exceptions
async def remove_claim_head(
    claim_id: str,
    head_id: str,
    payload: BoardVersionRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await bench.remove_head(claim, head_id, payload, _user_id(current_user))


@router.put(
    "/claims/{claim_id}/bench/heads/{head_id}/elements/{element_key}", response_model=ClaimBenchResponse
)
@handle_exceptions
async def decide_claim_head_element(
    claim_id: str,
    head_id: str,
    element_key: str,
    payload: ElementDecisionRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Record a person's call on one element of one head."""
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await bench.decide_element(claim, head_id, element_key, payload, _user_id(current_user))


@router.post("/claims/{claim_id}/bench/heads/{head_id}/test", response_model=ClaimBenchResponse)
@handle_exceptions
async def test_claim_head(
    claim_id: str,
    head_id: str,
    payload: HeadTestRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Test a head on its five elements. Proposes; decides nothing."""
    claim = await _claim(claim_id, Permissions.CLAIM_ASSESS, db, current_user, policy, selection, bench)
    await head_test_limiter.check_user_limit(_user_id(current_user) or "")
    return await bench.test_head(claim, head_id, payload, _user_id(current_user))


@router.post("/claims/{claim_id}/bench/lock", response_model=ClaimBenchResponse)
@handle_exceptions
async def lock_claim_bench(
    claim_id: str,
    payload: BoardVersionRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await bench.lock(claim, payload, _user_id(current_user))


@router.post("/claims/{claim_id}/bench/unlock", response_model=ClaimBenchResponse)
@handle_exceptions
async def unlock_claim_bench(
    claim_id: str,
    payload: BoardVersionRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await bench.unlock(claim, payload, _user_id(current_user))


@router.get("/claims/{claim_id}/bench/steps/{step_id}", response_model=SkillRunStep)
@handle_exceptions
async def get_claim_bench_step(
    claim_id: str,
    step_id: str,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """One test step: which skill version ran, for which party, and what it returned."""
    claim = await _claim(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench)
    return await bench.get_step(claim, step_id)


# --------------------------------------------------------------------------- #
# Delay Lab
# --------------------------------------------------------------------------- #
_LINK_PERMISSION = {
    "hindrance": Permissions.HINDRANCE_VIEW,
    "eot_submission": "dms.keydate.view",
    "eot_determination": "dms.keydate.view",
}


class LinkCandidate(BaseModel):
    target_type: str
    target_id: str
    label: str
    title: Optional[str] = None
    date: Optional[Any] = None
    status: Optional[str] = None
    milestones: List[str] = Field(default_factory=list)


@router.get("/claims/{claim_id}/bench/heads/{head_id}/delay", response_model=DelayView)
@handle_exceptions
async def get_head_delay(
    claim_id: str,
    head_id: str,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """The delay assessment of a head: linked events, overlaps, decisions and the extension they give."""
    claim = await _claim(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench)
    return await ClaimDelayLab(db, bench).view(claim, head_id)


@router.get("/claims/{claim_id}/bench/delay/candidates", response_model=List[LinkCandidate])
@handle_exceptions
async def list_delay_link_candidates(
    claim_id: str,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Records of the claim's project that a head could link to. A register the
    caller may not read contributes nothing."""
    claim = await _claim(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench)
    org, project = str(claim.get("organization_id") or ""), str(claim.get("project_id") or "")
    if not org or not project:
        return []
    scope = build_scope_query(current_user, organization_id=org, project_id=project)
    result: List[LinkCandidate] = []
    if await _may(policy, current_user, Permissions.HINDRANCE_VIEW, org, project):
        rows = await db[HINDRANCES].find({**scope, "archived_at": None}).to_list(length=500)
        for row in rows:
            reference = row.get("hindrance_ref") or row.get("delay_ref") or str(row.get("_id"))
            result.append(
                LinkCandidate(
                    target_type="hindrance",
                    target_id=str(row.get("_id")),
                    label=f"Hindrance {reference}",
                    title=row.get("title"),
                    date=row.get("start_date"),
                    status=str(getattr(row.get("responsibility"), "value", row.get("responsibility")) or "") or None,
                )
            )
    if await _may(policy, current_user, "dms.keydate.view", org, project):
        for row in await db[SUBMISSIONS].find(scope).to_list(length=200):
            result.append(
                LinkCandidate(
                    target_type="eot_submission",
                    target_id=str(row.get("_id")),
                    label=f"EOT submission {row.get('eot_reference') or row.get('revision_label') or row.get('_id')}",
                    date=row.get("contractor_submission_date"),
                    status=str(getattr(row.get("status"), "value", row.get("status")) or "") or None,
                )
            )
        for row in await db[DETERMINATIONS].find(scope).to_list(length=200):
            items = await db.key_date_eot_determination_items.find(
                {"determination_id": str(row.get("_id"))}
            ).to_list(length=200)
            result.append(
                LinkCandidate(
                    target_type="eot_determination",
                    target_id=str(row.get("_id")),
                    label=f"EOT determination {row.get('determination_reference') or row.get('_id')}",
                    date=row.get("determination_date"),
                    status=str(getattr(row.get("status"), "value", row.get("status")) or "") or None,
                    milestones=sorted(str(item.get("milestone_ref")) for item in items if item.get("milestone_ref")),
                )
            )
    return result


@router.post("/claims/{claim_id}/bench/heads/{head_id}/delay/links", response_model=DelayView)
@handle_exceptions
async def add_head_link(
    claim_id: str,
    head_id: str,
    payload: LinkCreateRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Link a hindrance, EOT submission or EOT determination to a head. The record
    is not changed; the link is pinned to it as it is now."""
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    lab = ClaimDelayLab(db, bench)
    record = await lab.load_target(payload.target_type, payload.target_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found")
    await selection.require_record(record)
    await policy.authorize_document(
        current_user, _LINK_PERMISSION[payload.target_type], record, resource_type=payload.target_type
    )
    return await lab.add_link(claim, head_id, payload, record, _user_id(current_user))


@router.post(
    "/claims/{claim_id}/bench/heads/{head_id}/delay/links/{link_id}/remove", response_model=DelayView
)
@handle_exceptions
async def remove_head_link(
    claim_id: str,
    head_id: str,
    link_id: str,
    payload: LinkRemoveRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Remove a link, or with ``refresh`` replace it with one pinned to the record as it is now."""
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await ClaimDelayLab(db, bench).remove_link(claim, head_id, link_id, payload, _user_id(current_user))


@router.put("/claims/{claim_id}/bench/heads/{head_id}/delay/settings", response_model=DelayView)
@handle_exceptions
async def set_head_delay_settings(
    claim_id: str,
    head_id: str,
    payload: DelaySettingsRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await ClaimDelayLab(db, bench).set_settings(claim, head_id, payload, _user_id(current_user))


@router.put("/claims/{claim_id}/bench/heads/{head_id}/delay/events/{event_id}", response_model=DelayView)
@handle_exceptions
async def decide_head_delay_event(
    claim_id: str,
    head_id: str,
    event_id: str,
    payload: EventDecisionRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Record who carries the risk of a delay event and whether it was critical."""
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await ClaimDelayLab(db, bench).decide_event(claim, head_id, event_id, payload, _user_id(current_user))


@router.put(
    "/claims/{claim_id}/bench/heads/{head_id}/delay/overlaps/{overlap_id}", response_model=DelayView
)
@handle_exceptions
async def decide_head_delay_overlap(
    claim_id: str,
    head_id: str,
    overlap_id: str,
    payload: OverlapDecisionRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Record how an overlap between Employer and Contractor delay is treated, and on what basis."""
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await ClaimDelayLab(db, bench).decide_overlap(
        claim, head_id, overlap_id, payload, _user_id(current_user)
    )


@router.put(
    "/claims/{claim_id}/bench/heads/{head_id}/delay/overlaps/{overlap_id}/cost-basis", response_model=DelayView
)
@handle_exceptions
async def record_head_overlap_cost_basis(
    claim_id: str,
    head_id: str,
    overlap_id: str,
    payload: CostBasisRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Record why an overlap's days carry cost (D-16): basis, causation and cost evidence."""
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await ClaimDelayLab(db, bench).record_cost_basis(
        claim, head_id, overlap_id, payload, _user_id(current_user), await _claim_documents(claim, db, current_user)
    )


@router.post(
    "/claims/{claim_id}/bench/heads/{head_id}/delay/overlaps/{overlap_id}/cost-basis/review", response_model=DelayView
)
@handle_exceptions
async def review_head_overlap_cost_basis(
    claim_id: str,
    head_id: str,
    overlap_id: str,
    payload: CostReviewRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Accept a recorded cost basis. Only a holder of the claim's determination authority by name
    may review (D-16; a delay_planning_expert assignment will also qualify once 4B-07 lands)."""
    claim = await _claim(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench)
    await policy.authorize_document(current_user, Permissions.CLAIM_DETERMINE, claim, resource_type="claim")
    return await ClaimDelayLab(db, bench).review_cost_basis(claim, head_id, overlap_id, payload, _user_id(current_user))


# --------------------------------------------------------------------------- #
# Quantum Builder
# --------------------------------------------------------------------------- #
async def _claim_documents(claim: dict, db, current_user: CurrentUser) -> set:
    """Documents linked to the claim that the caller may see: the only records a quantum line may cite."""
    ids = await DocumentRelationshipService(db).authorized_document_ids(
        current_user,
        "claim",
        str(claim.get("_id") or ""),
        legacy_document_ids=claim.get("linked_document_ids") or [],
    )
    return {str(item) for item in ids}


@router.get("/claims/{claim_id}/bench/heads/{head_id}/quantum", response_model=QuantumView)
@handle_exceptions
async def get_head_quantum(
    claim_id: str,
    head_id: str,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _claim(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection, bench)
    return await ClaimQuantum(db, bench).view(claim, head_id)


@router.post("/claims/{claim_id}/bench/heads/{head_id}/quantum/lines", response_model=QuantumView)
@handle_exceptions
async def add_head_quantum_line(
    claim_id: str,
    head_id: str,
    payload: QuantumLineRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await ClaimQuantum(db, bench).add_line(
        claim, head_id, payload, await _claim_documents(claim, db, current_user), _user_id(current_user)
    )


@router.put("/claims/{claim_id}/bench/heads/{head_id}/quantum/lines/{line_id}", response_model=QuantumView)
@handle_exceptions
async def update_head_quantum_line(
    claim_id: str,
    head_id: str,
    line_id: str,
    payload: QuantumLineRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await ClaimQuantum(db, bench).update_line(
        claim, head_id, line_id, payload, await _claim_documents(claim, db, current_user), _user_id(current_user)
    )


@router.post(
    "/claims/{claim_id}/bench/heads/{head_id}/quantum/lines/{line_id}/remove", response_model=QuantumView
)
@handle_exceptions
async def remove_head_quantum_line(
    claim_id: str,
    head_id: str,
    line_id: str,
    payload: BoardVersionRequest,
    db=Depends(get_db),
    bench: ClaimBenchService = Depends(get_bench),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _claim(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection, bench)
    return await ClaimQuantum(db, bench).remove_line(
        claim, head_id, line_id, payload.board_version, _user_id(current_user)
    )


__all__ = ["router"]
