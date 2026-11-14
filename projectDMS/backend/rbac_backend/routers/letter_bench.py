"""Letter Bench API.

Authorisation happens in ``LetterBenchService`` through the same
``PolicyService.authorize`` / letter-access checks the drafting routes use,
before any state is read or changed. Long operations answer 202 with a poll
URL and run in the background; the board shows their progress.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse

from ..core.database import get_database
from ..core.security import CurrentUser, get_current_user
from ..models.letter_bench import (
    AssertionCreateRequest,
    AssertionPatchRequest,
    BenchAccepted,
    BenchAvailability,
    BenchOpenRequest,
    BenchOpenResponse,
    BenchResponse,
    BoardSettingsRequest,
    ChainUpdateRequest,
    ComposeRequest,
    LockPositionsRequest,
    ParagraphPatchRequest,
    QuestionAnswerRequest,
    SkillStep,
    StepRunRequest,
)
from ..services.letter_drafting.bench import LetterBenchService
from ..utils.error_handler import handle_exceptions
from ..utils.rate_limiter import RateLimiter

router = APIRouter(prefix="/letters", tags=["letter-bench"])
bench_run_limiter = RateLimiter(max_requests=30, window_seconds=60, scope="letter_bench_runs")
bench_edit_limiter = RateLimiter(max_requests=240, window_seconds=60, scope="letter_bench_edits")


async def get_bench_service() -> LetterBenchService:
    return LetterBenchService(await get_database())


def _user_id(current_user: CurrentUser) -> str:
    return str(getattr(current_user, "id", "") or "")


def _accepted(accepted: BenchAccepted) -> JSONResponse:
    return JSONResponse(status_code=202, content=accepted.model_dump(mode="json"))


# NOTE: literal "/bench/..." routes are declared before "/{letter_id}/..." so a
# letter id can never shadow them.
@router.get("/bench/availability", response_model=BenchAvailability)
@handle_exceptions
async def bench_availability(
    organization_id: Optional[str] = None,
    service: LetterBenchService = Depends(get_bench_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Whether the Bench is switched on for the caller's organisation. Reveals only the flag."""
    return service.availability(organization_id or getattr(current_user, "organization_id", None))


@router.post("/bench/open", response_model=BenchOpenResponse)
@handle_exceptions
async def open_bench(
    payload: BenchOpenRequest,
    background: BackgroundTasks,
    service: LetterBenchService = Depends(get_bench_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Create the letter and its board from an incoming document or a record. Idempotent."""
    await bench_run_limiter.check_user_limit(_user_id(current_user))
    opened = await service.open(payload, current_user)
    if opened.created:
        await service.begin_dissect(opened.letter_id, current_user)
        background.add_task(service.run_dissect, opened.letter_id, current_user)
    return opened


@router.get("/{letter_id}/bench", response_model=BenchResponse)
@handle_exceptions
async def get_bench(
    letter_id: str,
    service: LetterBenchService = Depends(get_bench_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Board, chain, step summaries, staleness, gate and deadline."""
    return await service.get(letter_id, current_user)


@router.post("/{letter_id}/bench/dissect", response_model=BenchAccepted, status_code=202)
@handle_exceptions
async def dissect_letter(
    letter_id: str,
    background: BackgroundTasks,
    service: LetterBenchService = Depends(get_bench_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Run the analysis skill. 202 with a poll URL."""
    await bench_run_limiter.check_user_limit(_user_id(current_user))
    accepted = await service.begin_dissect(letter_id, current_user)
    background.add_task(service.run_dissect, letter_id, current_user)
    return _accepted(accepted)


@router.post("/{letter_id}/bench/assertions", response_model=BenchResponse)
@handle_exceptions
async def create_assertion(
    letter_id: str,
    payload: AssertionCreateRequest,
    service: LetterBenchService = Depends(get_bench_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Add an assertion from a text selection in the letter."""
    await bench_edit_limiter.check_user_limit(_user_id(current_user))
    return await service.create_assertion(letter_id, payload, current_user)


@router.patch("/{letter_id}/bench/assertions/{assertion_id}", response_model=BenchResponse)
@handle_exceptions
async def patch_assertion(
    letter_id: str,
    assertion_id: str,
    payload: AssertionPatchRequest,
    service: LetterBenchService = Depends(get_bench_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Set verdict, direction or routed skill; split, merge or remove. Optimistically locked."""
    await bench_edit_limiter.check_user_limit(_user_id(current_user))
    return await service.patch_assertion(letter_id, assertion_id, payload, current_user)


@router.put("/{letter_id}/bench/chain", response_model=BenchResponse)
@handle_exceptions
async def update_chain(
    letter_id: str,
    payload: ChainUpdateRequest,
    service: LetterBenchService = Depends(get_bench_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Add, remove or reorder the evaluation links of the chain."""
    await bench_edit_limiter.check_user_limit(_user_id(current_user))
    return await service.update_chain(letter_id, payload, current_user)


@router.put("/{letter_id}/bench/questions", response_model=BenchResponse)
@handle_exceptions
async def answer_question(
    letter_id: str,
    payload: QuestionAnswerRequest,
    service: LetterBenchService = Depends(get_bench_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Answer a 'need information' question that gates composing."""
    await bench_edit_limiter.check_user_limit(_user_id(current_user))
    return await service.answer_question(letter_id, payload, current_user)


@router.put("/{letter_id}/bench/settings", response_model=BenchResponse)
@handle_exceptions
async def update_bench_settings(
    letter_id: str,
    payload: BoardSettingsRequest,
    service: LetterBenchService = Depends(get_bench_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Change the drafting role, or enter the reply deadline by hand."""
    await bench_edit_limiter.check_user_limit(_user_id(current_user))
    return await service.update_settings(letter_id, payload, current_user)


@router.patch("/{letter_id}/bench/paragraphs/{paragraph_id}", response_model=BenchResponse)
@handle_exceptions
async def patch_paragraph(
    letter_id: str,
    paragraph_id: str,
    payload: ParagraphPatchRequest,
    service: LetterBenchService = Depends(get_bench_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Edit one reply paragraph by hand."""
    await bench_edit_limiter.check_user_limit(_user_id(current_user))
    return await service.patch_paragraph(letter_id, paragraph_id, payload, current_user)


@router.post("/{letter_id}/bench/steps", response_model=BenchAccepted, status_code=202)
@handle_exceptions
async def run_skill_step(
    letter_id: str,
    payload: StepRunRequest,
    background: BackgroundTasks,
    service: LetterBenchService = Depends(get_bench_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Run or re-run one skill on a target. 202 with a poll URL."""
    await bench_run_limiter.check_user_limit(_user_id(current_user))
    accepted = await service.begin_step(letter_id, payload, current_user)
    background.add_task(service.run_step, letter_id, payload, current_user)
    return _accepted(accepted)


@router.get("/{letter_id}/bench/steps/{step_id}", response_model=SkillStep)
@handle_exceptions
async def get_skill_step(
    letter_id: str,
    step_id: str,
    service: LetterBenchService = Depends(get_bench_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Full structured output of one skill step."""
    return await service.get_step(letter_id, step_id, current_user)


@router.post("/{letter_id}/bench/lock-positions", response_model=BenchResponse)
@handle_exceptions
async def lock_positions(
    letter_id: str,
    payload: LockPositionsRequest,
    service: LetterBenchService = Depends(get_bench_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Write a StrategyVersion from the board and accept it."""
    await bench_run_limiter.check_user_limit(_user_id(current_user))
    return await service.lock_positions(letter_id, payload, current_user)


@router.post("/{letter_id}/bench/compose", response_model=BenchAccepted, status_code=202)
@handle_exceptions
async def compose_reply(
    letter_id: str,
    payload: ComposeRequest,
    background: BackgroundTasks,
    service: LetterBenchService = Depends(get_bench_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Compose the reply and create the drafting run. 202; the existing run endpoints take over."""
    await bench_run_limiter.check_user_limit(_user_id(current_user))
    accepted = await service.begin_compose(letter_id, payload, current_user)
    background.add_task(service.run_compose, letter_id, payload, current_user)
    return _accepted(accepted)
