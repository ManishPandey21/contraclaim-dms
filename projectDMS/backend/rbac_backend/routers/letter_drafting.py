from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..core.database import get_database
from ..core.security import CurrentUser, get_current_user
from ..models.letter_drafting import (
    AssignReviewerRequest,
    ConfirmAnalysisRequest,
    ConfirmPlanRequest,
    DraftAuditResponse,
    DraftCommentRequest,
    DraftContextPack,
    DraftGovernanceResponse,
    DraftingStartRequest,
    DraftingStartResponse,
    DraftMode,
    DraftQualityDashboardResponse,
    DraftRunCreateRequest,
    DraftRunResponse,
    ExactClauseSearchRequest,
    ExactReferenceSearchRequest,
    LockParagraphsRequest,
    ReturnForCorrectionRequest,
    ReviseDraftRequest,
    SourceLedgerResponse,
    UserDirectionRequest,
)
from ..services.letter_drafting import DraftRunService
from ..services.policy_service import PolicyService
from ..utils.error_handler import handle_exceptions

router = APIRouter(prefix="/letters/{letter_id}/drafting", tags=["letter-drafting"])
session_router = APIRouter(prefix="/letter-drafting", tags=["letter-drafting"])


class IssueDraftRequest(BaseModel):
    issued_document_id: Optional[str] = None


async def get_draft_run_service() -> DraftRunService:
    db = await get_database()
    return DraftRunService(db)


async def get_policy_service() -> PolicyService:
    db = await get_database()
    return PolicyService(db)


@session_router.post("/start", response_model=DraftingStartResponse)
@handle_exceptions
async def start_drafting_session(
    payload: DraftingStartRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Start a frontend drafting session and return the next workflow step."""
    await policy.authorize(
        current_user,
        "drafting.request.create",
        resource_type="letter_drafting_session",
        organization_id=payload.organization_id,
        project_id=payload.project_id,
    )
    return await service.start_session(payload, current_user)


@session_router.post("/retrieval/exact-clause", response_model=SourceLedgerResponse)
@handle_exceptions
async def exact_clause_retrieval(
    payload: ExactClauseSearchRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Retrieve exact contract clause evidence for source-grounded drafting."""
    await policy.authorize(
        current_user,
        "drafting.request.view",
        resource_type="letter_drafting_retrieval",
        organization_id=payload.organization_id,
        project_id=payload.project_id,
    )
    return await service.exact_clause_search(payload, current_user)


@session_router.post("/retrieval/exact-reference", response_model=SourceLedgerResponse)
@handle_exceptions
async def exact_reference_retrieval(
    payload: ExactReferenceSearchRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Retrieve exact letter/document reference evidence for source-grounded drafting."""
    await policy.authorize(
        current_user,
        "drafting.request.view",
        resource_type="letter_drafting_retrieval",
        organization_id=payload.organization_id,
        project_id=payload.project_id,
    )
    return await service.exact_reference_search(payload, current_user)


@session_router.get("/metrics/dashboard", response_model=DraftQualityDashboardResponse)
@handle_exceptions
async def get_letter_drafting_quality_dashboard(
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
    window_days: int = 30,
    service: DraftRunService = Depends(get_draft_run_service),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return scoped metrics for drafting quality, cycle time, governance, and issued artifacts."""
    if organization_id or project_id:
        await policy.authorize(
            current_user,
            "drafting.audit.view",
            resource_type="letter_drafting_metrics",
            organization_id=organization_id,
            project_id=project_id,
        )
    else:
        await policy.authorize(
            current_user,
            "drafting.admin",
            resource_type="letter_drafting_metrics",
        )
    return await service.get_quality_dashboard(
        current_user,
        organization_id=organization_id,
        project_id=project_id,
        window_days=window_days,
    )


@router.post("/runs", response_model=DraftRunResponse)
@handle_exceptions
async def create_draft_run(
    letter_id: str,
    payload: DraftRunCreateRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Start a v2 background, strategy, draft, or review drafting run."""
    return await service.create_run(letter_id, payload, current_user)


@router.post("/analyze-incoming", response_model=DraftRunResponse)
@handle_exceptions
async def analyze_incoming_letter(
    letter_id: str,
    payload: DraftRunCreateRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Analyze an incoming letter/document and persist a background run."""
    return await service.analyze_incoming(letter_id, payload, current_user)


@router.post("/runs/{run_id}/confirm-analysis", response_model=DraftRunResponse)
@handle_exceptions
async def confirm_incoming_analysis(
    letter_id: str,
    run_id: str,
    payload: ConfirmAnalysisRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Persist user-confirmed incoming letter analysis on a run."""
    return await service.confirm_analysis(letter_id, run_id, payload, current_user)


@router.post("/runs/{run_id}/user-direction", response_model=DraftRunResponse)
@handle_exceptions
async def provide_user_direction(
    letter_id: str,
    run_id: str,
    payload: UserDirectionRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Record the drafter's answers to the probing questions / line of action.

    Directions are stored on the run and merged into the inputs of subsequent
    strategy/draft runs for this letter."""
    return await service.provide_user_direction(letter_id, run_id, payload, current_user)


@router.post("/runs/{run_id}/lock-paragraphs", response_model=DraftRunResponse)
@handle_exceptions
async def lock_paragraphs(
    letter_id: str,
    run_id: str,
    payload: LockParagraphsRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Lock human-approved paragraphs so AI redrafts cannot change them."""
    return await service.lock_paragraphs(letter_id, run_id, payload, current_user)


@router.post("/prepare-plan", response_model=DraftRunResponse)
@handle_exceptions
async def prepare_drafting_plan(
    letter_id: str,
    payload: DraftRunCreateRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Prepare a structured planning sheet and reply matrix."""
    return await service.prepare_plan(letter_id, payload, current_user)


@router.post("/runs/{run_id}/confirm-plan", response_model=DraftRunResponse)
@handle_exceptions
async def confirm_drafting_plan(
    letter_id: str,
    run_id: str,
    payload: ConfirmPlanRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Persist user-confirmed planning sheet and reply matrix."""
    return await service.confirm_plan(letter_id, run_id, payload, current_user)


@router.post("/generate-draft", response_model=DraftRunResponse)
@handle_exceptions
async def generate_structured_draft(
    letter_id: str,
    payload: DraftRunCreateRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Generate a structured draft artifact."""
    return await service.generate_draft(letter_id, payload, current_user)


@router.post("/runs/{run_id}/revise", response_model=DraftRunResponse)
@handle_exceptions
async def revise_draft_run(
    letter_id: str,
    run_id: str,
    payload: ReviseDraftRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create a new draft run as a revision of an existing run."""
    return await service.revise_run(letter_id, run_id, payload, current_user)


@router.post("/runs/{run_id}/validate", response_model=DraftRunResponse)
@handle_exceptions
async def validate_draft_run(
    letter_id: str,
    run_id: str,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Re-run deterministic source and clause validation for a draft run."""
    return await service.validate_run(letter_id, run_id, current_user)


@router.post("/runs/{run_id}/critique", response_model=DraftRunResponse)
@handle_exceptions
async def critique_draft_run(
    letter_id: str,
    run_id: str,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Run deterministic contractual red-flag critique for a draft run."""
    return await service.critique_run(letter_id, run_id, current_user)


@router.get("/runs/latest", response_model=DraftRunResponse)
@handle_exceptions
async def get_latest_draft_run(
    letter_id: str,
    mode: Optional[DraftMode] = None,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Fetch latest v2 drafting run for a letter, optionally filtered by mode."""
    return await service.latest_run(letter_id, mode, current_user)


@router.get("/runs/{run_id}", response_model=DraftRunResponse)
@handle_exceptions
async def get_draft_run(
    letter_id: str,
    run_id: str,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Fetch a full typed v2 drafting run."""
    return await service.get_run(letter_id, run_id, current_user)


@router.get("/runs/{run_id}/audit", response_model=DraftAuditResponse)
@handle_exceptions
async def get_draft_run_audit(
    letter_id: str,
    run_id: str,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Fetch lifecycle audit events for a v2 draft run."""
    return await service.get_audit(letter_id, run_id, current_user)


@router.get("/runs/{run_id}/context-pack", response_model=DraftContextPack)
@handle_exceptions
async def get_draft_run_context_pack(
    letter_id: str,
    run_id: str,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Fetch the structured source-grounded context pack for a v2 draft run."""
    return await service.get_context_pack(letter_id, run_id, current_user)


@router.get("/runs/{run_id}/source-ledger", response_model=SourceLedgerResponse)
@handle_exceptions
async def get_draft_run_source_ledger(
    letter_id: str,
    run_id: str,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Fetch normalized source ledger entries for a v2 draft run."""
    return await service.get_source_ledger(letter_id, run_id, current_user)


@router.get("/runs/{run_id}/governance", response_model=DraftGovernanceResponse)
@handle_exceptions
async def get_draft_run_governance(
    letter_id: str,
    run_id: str,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Fetch assignments, comments, and lifecycle events for governance review."""
    return await service.get_governance(letter_id, run_id, current_user)


@router.post("/runs/{run_id}/assign-reviewer", response_model=DraftGovernanceResponse)
@handle_exceptions
async def assign_draft_reviewer(
    letter_id: str,
    run_id: str,
    payload: AssignReviewerRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Assign a reviewer and move the draft into review governance."""
    return await service.assign_reviewer(letter_id, run_id, payload, current_user)


@router.post("/runs/{run_id}/comments", response_model=DraftGovernanceResponse)
@handle_exceptions
async def add_draft_review_comment(
    letter_id: str,
    run_id: str,
    payload: DraftCommentRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Add a governance comment to a draft run."""
    return await service.add_comment(letter_id, run_id, payload, current_user)


@router.post("/runs/{run_id}/return-for-correction", response_model=DraftRunResponse)
@handle_exceptions
async def return_draft_for_correction(
    letter_id: str,
    run_id: str,
    payload: ReturnForCorrectionRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return a draft for correction with required changes."""
    return await service.return_for_correction(letter_id, run_id, payload, current_user)


@router.post("/runs/{run_id}/accept-plan", response_model=DraftRunResponse)
@handle_exceptions
async def accept_draft_plan(
    letter_id: str,
    run_id: str,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Accept a plan from a v2 drafting run and store it on the letter."""
    return await service.accept_plan(letter_id, run_id, current_user)


@router.post("/runs/{run_id}/accept-draft", response_model=DraftRunResponse)
@handle_exceptions
async def accept_draft(
    letter_id: str,
    run_id: str,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Accept a non-blocking draft and create an immutable draft version."""
    return await service.accept_draft(letter_id, run_id, current_user)


@router.post("/runs/{run_id}/approve", response_model=DraftRunResponse)
@handle_exceptions
async def approve_draft_run(
    letter_id: str,
    run_id: str,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Approve a non-blocking draft and create the accepted draft version."""
    return await service.approve_run(letter_id, run_id, current_user)


@router.post("/runs/{run_id}/export", response_model=DraftRunResponse)
@handle_exceptions
async def export_draft_run(
    letter_id: str,
    run_id: str,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Mark a draft run as exported."""
    return await service.export_run(letter_id, run_id, current_user)


@router.post("/runs/{run_id}/issue", response_model=DraftRunResponse)
@handle_exceptions
async def issue_draft_run(
    letter_id: str,
    run_id: str,
    payload: IssueDraftRequest,
    service: DraftRunService = Depends(get_draft_run_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Mark a draft run as issued and attach the issued document id if supplied."""
    return await service.issue_run(letter_id, run_id, payload.issued_document_id, current_user)
