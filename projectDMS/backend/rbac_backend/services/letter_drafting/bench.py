"""Letter Bench service.

The Bench replaces the form-and-five-pages wizard with one working surface:
the incoming letter is dissected into assertions, a skill chain is routed over
them, the user decides each position, positions are locked, and the reply is
composed paragraph by paragraph.

It deliberately owns very little:

- **Lock positions** writes an ordinary accepted ``StrategyVersion`` through
  the existing repository, so it IS the strategy approval.
- **Compose** hands the assembled letter to ``DraftRunService.create_run``,
  which still builds the source ledger, runs the guardrail scan and the
  validators, and stores an ordinary ``DraftRun``. Review, approval, export,
  issue and rollback are unchanged.

Long operations come as a ``begin_*`` (authorise, validate, mark running) and
a ``run_*`` (the work). Routers schedule ``run_*`` in the background and answer
202; tests await both.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence

from bson import ObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from ...core.config import settings
from ...models.letter import Letter
from ...models.letter_bench import (
    VERDICT_LABELS,
    Assertion,
    AssertionCreateRequest,
    AssertionPatchRequest,
    BenchAccepted,
    BenchAvailability,
    BenchOpenRequest,
    BenchOpenResponse,
    BenchResponse,
    BoardQuestion,
    BoardSettingsRequest,
    ChainLink,
    ChainUpdateRequest,
    ClauseCheck,
    ComposeRequest,
    LetterAnalysisOutput,
    LockPositionsRequest,
    ParagraphPatchRequest,
    PositionBoard,
    QuestionAnswerRequest,
    ReplyParagraph,
    SkillStep,
    SkillStepSummary,
    StepRunRequest,
    TextSpan,
)
from ...models.letter_drafting import (
    DraftRun,
    DraftRunCreateRequest,
    IncomingLetterAnalysis,
    UserDirectionAnswer,
    ValidationFinding,
)
from ...models.skill import SkillVersion
from ..publication_policy import authoritative_fact_text, is_consumable
from ..skills.compiler import SkillRoleNotSupported, overlay_key_for
from ..skills.executor import SkillExecutor, canonical_hash, step_input_hash
from ..skills.registry import SkillRegistry
from ..skills.router import apply_routing, propose_chain
from ..skills.skill_validators import run_skill_validators
from ..skills.system_skills import ANALYSIS_SKILL_KEY, LETTERS_SKILL_KEY
from . import bench_logic as logic
from .clause_checker import normalize_cited_clause
from .context import condense_text
from .incoming_analyzer import (
    MAX_BENCH_TEXT_CHARS,
    clean_incoming_text,
    extract_assertions,
    extract_clause_refs,
    locate_quote,
)
from .prompts import COMPOSE_PROMPT_KEY, SKILL_STEP_PROMPT_KEY
from .user_direction import UserDirectionAgent

logger = logging.getLogger(__name__)

BOARDS = "letter_position_boards"
STEPS = "letter_skill_steps"
VALID_BENCH_MODES = {"off", "canary", "primary"}
READ_ONLY_STATUSES = {"review", "approval", "completed", "rejected"}
# Run-level questions the per-assertion verdicts already answer.
_QUESTIONS_REPLACED_BY_VERDICTS = {"position", "scope"}
# The whole cleaned letter goes to the analysis; a letter cut short would lose
# its closing demands without anyone being told.
MAX_MATERIAL_CHARS = MAX_BENCH_TEXT_CHARS
_CLOSING_TAGS = re.compile(r"</\s*(incoming_material|assertion|prior_step_output)\s*>", re.IGNORECASE)


def _untrusted(text: Optional[str], limit: int = MAX_MATERIAL_CHARS) -> str:
    """Letter text bound for a prompt: capped, and unable to close the tag that fences it."""
    return _CLOSING_TAGS.sub("[tag removed]", str(text or ""))[:limit]


def bench_rollout(tenant_id: Optional[str], config: Any = settings) -> BenchAvailability:
    """Server-authoritative rollout decision. No client flag can bypass it."""
    mode = str(getattr(config, "LETTER_BENCH_ROLLOUT_MODE", "off") or "off").lower()
    if mode not in VALID_BENCH_MODES:
        mode = "off"
    if mode == "primary":
        return BenchAvailability(enabled=True, rollout_mode=mode)
    if mode == "canary":
        allowed = {
            item.strip()
            for item in str(getattr(config, "LETTER_BENCH_CANARY_TENANT_IDS", "") or "").split(",")
            if item.strip()
        }
        return BenchAvailability(enabled=bool(tenant_id and str(tenant_id) in allowed), rollout_mode=mode)
    return BenchAvailability(enabled=False, rollout_mode=mode)


class BenchRunContext:
    """What the Bench hands to ``DraftRunService.create_run`` when composing."""

    def __init__(
        self,
        *,
        draft_letter: str,
        integrity_notes: str,
        plan: str,
        reply_matrix: List[Any],
        skill_chain: List[Dict[str, Any]],
        board_version: int,
        skill_manifest_hash: str,
        degraded_reasons: Optional[List[str]] = None,
        extra_findings: Optional[List[ValidationFinding]] = None,
    ) -> None:
        self.draft_letter = draft_letter
        self.integrity_notes = integrity_notes
        self.plan = plan
        self.reply_matrix = reply_matrix
        self.skill_chain = skill_chain
        self.board_version = board_version
        self.skill_manifest_hash = skill_manifest_hash
        self.degraded_reasons = list(degraded_reasons or [])
        self.extra_findings = list(extra_findings or [])


def _constant(value: Any) -> Callable[[], Any]:
    """A fallback that returns ``value``, bound now rather than when it is called."""

    def _fallback() -> Any:
        return value

    return _fallback


_HELD_DOCUMENT_MESSAGE = (
    "This document is held for review or marked as a duplicate, so it cannot be used "
    "for drafting. Resolve that on the document, then run the analysis again."
)


def _user_id(current_user: Any) -> Optional[str]:
    value = getattr(current_user, "id", None)
    return str(value) if value else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LetterBenchService:
    def __init__(
        self,
        db: Any,
        *,
        draft_service: Any = None,
        registry: Optional[SkillRegistry] = None,
        executor: Optional[SkillExecutor] = None,
        config: Any = settings,
    ) -> None:
        from .service import DraftRunService  # local import: service imports this module

        self.db = db
        self.config = config
        self.drafts = draft_service or DraftRunService(db)
        self.registry = registry or SkillRegistry(db)
        self.executor = executor or SkillExecutor(self.drafts.prompt_registry)
        self.boards = db[BOARDS]
        self.steps = db[STEPS]

    # ------------------------------------------------------------------ gates
    def availability(self, tenant_id: Optional[str]) -> BenchAvailability:
        return bench_rollout(tenant_id, self.config)

    def ensure_enabled(self, tenant_id: Optional[str]) -> None:
        if not self.availability(tenant_id).enabled:
            raise HTTPException(status_code=404, detail="The Letter Bench is not enabled for this organisation")

    async def _authorize(
        self, letter_id: str, current_user: Any, permission: Optional[str] = None
    ) -> Letter:
        """Authorise, then load. Reads need what reading a drafting run needs in the
        earlier screens (``drafting.request.view``), so the Bench is visible to
        exactly the people who can see the run it produces. A write names its own
        permission and is refused once the letter has left drafting: the read-only
        flag in the response is a courtesy to the UI, this is the rule."""
        letter = await self.drafts._load_and_authorize(
            letter_id,
            current_user,
            "write" if permission else "read",
            drafting_permission=permission or "drafting.request.view",
        )
        self.ensure_enabled(str(getattr(letter, "organization_id", "") or ""))
        if permission and str(getattr(letter, "status", "") or "").lower() in READ_ONLY_STATUSES:
            raise HTTPException(
                status_code=409,
                detail="This letter is no longer in drafting, so its positions and reply cannot be changed.",
            )
        return letter

    @staticmethod
    def _ensure_idle(board: PositionBoard) -> None:
        """An edit made while the reply is being written would be accepted and then
        overwritten when composing finishes. Refuse it instead."""
        if board.composing:
            raise HTTPException(
                status_code=409,
                detail="The reply is being composed. Make this change when it has finished.",
            )

    # ---------------------------------------------------------------- storage
    async def _load_board(self, letter_id: str) -> PositionBoard:
        doc = await self.boards.find_one({"letter_id": str(letter_id)})
        if not doc:
            raise HTTPException(status_code=404, detail="This letter has no Bench yet")
        return PositionBoard(**doc)

    async def _save(self, board: PositionBoard, expected_version: int, user_id: Optional[str]) -> PositionBoard:
        """Compare-and-set on ``board_version``. A lost race is a 409, never a silent overwrite."""
        board.board_version = expected_version + 1
        board.updated_at = _now()
        board.updated_by = user_id or board.updated_by
        payload = board.model_dump(by_alias=True, exclude={"id"})
        result = await self.boards.update_one(
            {"letter_id": board.letter_id, "board_version": expected_version},
            {"$set": payload},
        )
        if not getattr(result, "matched_count", 0):
            raise HTTPException(
                status_code=409,
                detail="The Bench changed since you loaded it. Your view has been refreshed; repeat the action.",
            )
        return board

    async def _mutate(
        self, letter_id: str, change: Callable[[PositionBoard], None], user_id: Optional[str] = None
    ) -> PositionBoard:
        """Apply a background change on top of whatever the board is now."""
        last: Optional[HTTPException] = None
        for _ in range(6):
            board = await self._load_board(letter_id)
            expected = board.board_version
            change(board)
            try:
                return await self._save(board, expected, user_id)
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                last = exc
        raise last or HTTPException(status_code=409, detail="Could not update the Bench")

    @classmethod
    def _check_version(cls, board: PositionBoard, client_version: int, *, allow_composing: bool = False) -> None:
        if not allow_composing:
            cls._ensure_idle(board)
        if board.board_version != client_version:
            raise HTTPException(
                status_code=409,
                detail="The Bench changed since you loaded it. Your view has been refreshed; repeat the action.",
            )

    async def _steps(self, letter_id: str) -> List[SkillStep]:
        docs = await self.steps.find({"letter_id": str(letter_id)}).to_list(length=2000)
        steps = [SkillStep(**doc) for doc in docs]
        steps.sort(key=lambda step: step.created_at)
        return steps

    async def _put_step(self, step: SkillStep) -> SkillStep:
        """Upsert on the idempotency key so replaying a step never duplicates it."""
        key = {
            "letter_id": step.letter_id,
            "skill_key": step.skill_key,
            "skill_hash": step.skill_hash,
            "input_hash": step.input_hash,
        }
        existing = await self.steps.find_one(key)
        if existing:
            step.step_id = existing["step_id"]
        await self.steps.update_one(
            key, {"$set": step.model_dump(by_alias=True, exclude={"id"})}, upsert=True
        )
        return step

    async def _cached_step(self, letter_id: str, skill: SkillVersion, input_hash: str) -> Optional[SkillStep]:
        doc = await self.steps.find_one(
            {
                "letter_id": str(letter_id),
                "skill_key": skill.skill_key,
                "skill_hash": skill.content_hash,
                "input_hash": input_hash,
            }
        )
        if not doc:
            return None
        step = SkillStep(**doc)
        if step.status != "completed" or step.degraded:
            return None
        if step.stale:
            await self.steps.update_one({"step_id": step.step_id}, {"$set": {"stale": False}})
            step.stale = False
        return step

    async def _mark_stale(self, letter_id: str, step_ids: Sequence[str]) -> None:
        if step_ids:
            await self.steps.update_many(
                {"letter_id": str(letter_id), "step_id": {"$in": list(step_ids)}},
                {"$set": {"stale": True}},
            )

    async def _ensure_source_usable(self, board: PositionBoard) -> None:
        """Refuse to build on the board's copy of a letter that is now held.

        The board keeps the text it analysed. If the document has since gone to
        review or been quarantined, that copy is no longer a source, and locking,
        evaluating or composing from it would draft on unverified content.
        Running the analysis again is allowed: it is what retracts the copy.
        """
        if not board.incoming_document_id:
            return
        document = await self._document(board.incoming_document_id)
        if document is not None and not is_consumable(document):
            raise HTTPException(status_code=409, detail=_HELD_DOCUMENT_MESSAGE)

    async def _document(self, document_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not document_id:
            return None
        candidates: List[Any] = [str(document_id)]
        try:
            candidates.append(ObjectId(str(document_id)))
        except Exception:
            pass
        return await self.db.documents.find_one({"_id": {"$in": candidates}})

    # ------------------------------------------------------------------- open
    async def open(self, request: BenchOpenRequest, current_user: Any) -> BenchOpenResponse:
        """Create the letter and its board from a document or a record. Idempotent."""
        trigger: Optional[Dict[str, Any]] = None
        incoming_text = ""
        determination_role: Optional[str] = None
        if request.incoming_document_id:
            document = await self._document(request.incoming_document_id)
            if not document:
                raise HTTPException(status_code=404, detail="Document not found")
            await self.drafts.policy_service.authorize_document(current_user, "dms.document.view", document)
            organization_id = str(document.get("organization_id") or "")
            project_id = str(document.get("project_id") or "") or None
            reference = document.get("letterNo") or document.get("subject") or "incoming letter"
            title = f"Reply to {reference}"
            subject = f"Re: {document.get('subject') or reference}"
            recipient = str(document.get("from") or "")
            origin_key = {"incoming_document_id": str(request.incoming_document_id)}
        elif request.trigger_type == "claim":
            organization_id, project_id, title, subject, recipient, trigger, incoming_text = (
                await self._claim_trigger(str(request.trigger_id), current_user)
            )
            origin_key = {"trigger.type": "claim", "trigger.id": str(request.trigger_id)}
        elif request.trigger_type in ("determination", "information_request"):
            source = await self._determination_trigger(
                str(request.trigger_id), request.trigger_type, current_user
            )
            organization_id, project_id = source.organization_id, source.project_id
            title, subject, recipient = source.title, source.subject, "[CONFIRM: addressee]"
            trigger, incoming_text = source.trigger, clean_incoming_text(source.text)
            # The letter is written as the party that made the determination.
            determination_role = source.role
            origin_key = {"trigger.type": request.trigger_type, "trigger.id": str(request.trigger_id)}
        else:
            from ..variation_service import VariationService

            record = await VariationService(self.db).get(str(request.trigger_id))
            if not record:
                raise HTTPException(status_code=404, detail="Variation not found")
            await self.drafts.policy_service.authorize_document(
                current_user, "dms.variation.view", record, resource_type="variation"
            )
            organization_id = str(record.get("organization_id") or "")
            project_id = str(record.get("project_id") or "") or None
            number = record.get("variation_number") or str(request.trigger_id)
            title = f"Letter on variation {number}"
            subject = f"Variation {number}"
            recipient = "[CONFIRM: addressee]"
            trigger = {
                "type": "variation",
                "id": str(request.trigger_id),
                "label": f"Variation {number}",
                "status": str(record.get("status") or ""),
            }
            incoming_text = clean_incoming_text(
                "\n\n".join(
                    part
                    for part in [
                        f"Variation {number}",
                        str(record.get("description") or ""),
                        f"Letter reference: {record['letter_reference']}" if record.get("letter_reference") else "",
                        f"Remarks: {record['remarks']}" if record.get("remarks") else "",
                    ]
                    if part
                )
            )
            origin_key = {"trigger.type": "variation", "trigger.id": str(request.trigger_id)}

        if not organization_id:
            raise HTTPException(status_code=422, detail="The source record has no organisation")
        self.ensure_enabled(organization_id)
        await self.drafts.policy_service.authorize(
            current_user,
            "drafting.request.create",
            resource_type="letter_bench",
            organization_id=organization_id,
            project_id=project_id,
        )

        existing = await self.boards.find_one({"organization_id": organization_id, **origin_key})
        if existing and not await self._letter_exists(existing["letter_id"]):
            # The letter was deleted through the letters screen. Its board would
            # otherwise answer every future open with a letter that is gone.
            await self.steps.delete_many({"letter_id": existing["letter_id"]})
            await self.boards.delete_one({"letter_id": existing["letter_id"]})
            existing = None
        if existing:
            return BenchOpenResponse(
                letter_id=existing["letter_id"], created=False, board_version=existing.get("board_version", 0)
            )

        role = determination_role or request.role or await self._default_role(organization_id, project_id)
        user_id = _user_id(current_user) or "system"
        letter = await self.drafts.letter_service.create_letter(
            Letter(
                _id=None,
                letter_no=None,
                title=title[:300],
                recipient=recipient[:500],
                subject=subject[:500],
                content="",
                status="Draft",
                created_by=user_id,
                assigned_to=user_id,
                organization_id=organization_id,
                project_id=project_id,
                strategy_role=role,
            )
        )
        board = PositionBoard(
            letter_id=str(letter.id),
            organization_id=organization_id,
            project_id=project_id,
            role=role,  # type: ignore[arg-type]
            incoming_document_id=request.incoming_document_id,
            trigger=trigger,
            incoming_text=incoming_text,
            stage="dissecting",
            created_by=user_id,
            updated_by=user_id,
        )
        try:
            await self.boards.insert_one(board.model_dump(by_alias=True, exclude={"id"}))
        except DuplicateKeyError:
            # Two opens raced for the same document; the unique index let one win.
            # Drop the letter this request created and hand back the winner's.
            await self.db.letters.delete_one({"_id": ObjectId(str(letter.id))})
            winner = await self.boards.find_one({"organization_id": organization_id, **origin_key})
            if not winner:
                raise
            return BenchOpenResponse(
                letter_id=winner["letter_id"], created=False, board_version=winner.get("board_version", 0)
            )
        return BenchOpenResponse(letter_id=board.letter_id, created=True, board_version=0)

    async def _claim_trigger(self, claim_id: str, current_user: Any) -> tuple:
        """Origin of a letter drafted from a claim (its notice, typically).

        The text handed to the analysis is the claim's own record: what
        happened, the dates, the clauses relied on and the heads. Nothing is
        added that the register does not hold.
        """
        from ..claim_service import ClaimService

        record = await ClaimService(self.db).get(claim_id)
        if not record:
            raise HTTPException(status_code=404, detail="Claim not found")
        await self.drafts.policy_service.authorize_document(
            current_user, "dms.claim.view", record, resource_type="claim"
        )
        reference = record.get("claim_ref") or record.get("title") or claim_id
        heads = ((record.get("bench") or {}).get("heads")) or []
        clauses = list(record.get("contract_clauses") or [])
        for head in heads:
            for ref in head.get("clause_refs") or []:
                if ref not in clauses:
                    clauses.append(ref)

        def _day(value: Any) -> str:
            return str(value)[:10] if value else ""

        parts = [
            f"Claim {reference}: {record.get('title') or ''}".strip(),
            str(record.get("description") or ""),
            f"Event date: {_day(record.get('event_date'))}" if record.get("event_date") else "",
            f"Became aware on: {_day(record.get('awareness_date'))}" if record.get("awareness_date") else "",
            f"Notice served on: {_day(record.get('notice_date'))}" if record.get("notice_date") else "",
            ("Clauses relied on: " + ", ".join(f"Sub-Clause {ref}" for ref in clauses)) if clauses else "",
            *[
                f"Head of claim: {head.get('title')}. {head.get('event_summary') or ''}".strip()
                for head in heads
            ],
        ]
        trigger = {
            "type": "claim",
            "id": claim_id,
            "label": f"Claim {reference}",
            "status": str(record.get("status") or ""),
            "claim_type": str(record.get("type") or ""),
        }
        return (
            str(record.get("organization_id") or ""),
            str(record.get("project_id") or "") or None,
            f"Notice for claim {reference}",
            f"Notice of claim: {record.get('title') or reference}",
            "[CONFIRM: addressee]",
            trigger,
            clean_incoming_text("\n\n".join(part for part in parts if part)),
        )

    async def _determination_trigger(self, determination_id: str, purpose: str, current_user: Any) -> Any:
        """Origin of a determination letter or a request for information (4B-31).

        The determination's claim must be readable by the caller; the letter carries
        the determination's findings and information items, with their deadlines,
        and nothing else (``claim_determinations.letter_source``).
        """
        from ..claim_service import ClaimService
        from ..claim_bench import ClaimBenchService
        from ..claim_determinations import DETERMINATIONS, load_letter_source

        row = await self.db[DETERMINATIONS].find_one({"_id": determination_id})
        if not row:
            raise HTTPException(status_code=404, detail="Determination not found")
        record = await ClaimService(self.db).get(str(row.get("claim_id")))
        if not record:
            raise HTTPException(status_code=404, detail="Determination not found")
        await self.drafts.policy_service.authorize_document(
            current_user, "dms.claim.view", record, resource_type="claim"
        )
        return await load_letter_source(self.db, ClaimBenchService(self.db), record, determination_id, purpose)  # type: ignore[arg-type]

    async def _letter_exists(self, letter_id: str) -> bool:
        try:
            return bool(await self.db.letters.find_one({"_id": ObjectId(str(letter_id))}))
        except Exception:
            return True  # never delete a board because a lookup failed

    async def _default_role(self, organization_id: str, project_id: Optional[str]) -> str:
        """Draft as the party this project last drafted as; contractor when unknown."""
        try:
            previous = await self.db.letters.find_one(
                {
                    "organization_id": organization_id,
                    "project_id": project_id,
                    "strategy_role": {"$in": ["contractor", "engineer", "employer"]},
                },
                sort=[("updated_at", -1)],
            )
        except Exception:
            previous = None
        return str((previous or {}).get("strategy_role") or "contractor")

    # -------------------------------------------------------------------- get
    async def get(self, letter_id: str, current_user: Any) -> BenchResponse:
        letter = await self._authorize(letter_id, current_user)
        board = await self._load_board(letter_id)
        steps = await self._expire_abandoned(letter_id, await self._steps(letter_id), board)
        board.deadline = logic.refresh_deadline(board.deadline)
        status = str(getattr(letter, "status", "") or "")
        return BenchResponse(
            letter_id=str(letter_id),
            letter_title=getattr(letter, "title", None),
            letter_status=status,
            board=board,
            steps=[SkillStepSummary(**step.model_dump()) for step in steps],
            gate=logic.compute_gate(board, steps),
            rollout_mode=self.availability(board.organization_id).rollout_mode,
            read_only=status.lower() in READ_ONLY_STATUSES,
        )

    async def list_skills(self, letter_id: str, current_user: Any) -> List[Any]:
        """Skills in force for this letter's organisation and project.

        Authorised by access to the letter itself, like the board: a drafter
        who can see the chain on a letter can see which skills could be added
        to it. Names, versions and availability only; never skill text.
        """
        await self._authorize(letter_id, current_user)
        board = await self._load_board(letter_id)
        return await self.registry.list_effective(board.organization_id, board.project_id)

    async def get_step(self, letter_id: str, step_id: str, current_user: Any) -> SkillStep:
        await self._authorize(letter_id, current_user)
        doc = await self.steps.find_one({"letter_id": str(letter_id), "step_id": step_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Skill step not found")
        return SkillStep(**doc)

    async def _expire_abandoned(
        self, letter_id: str, steps: List[SkillStep], board: PositionBoard
    ) -> List[SkillStep]:
        """A worker that died mid-step must not leave the Bench 'running' forever."""
        cutoff = _now() - timedelta(minutes=logic.STALE_RUNNING_MINUTES)
        updated = board.updated_at if board.updated_at.tzinfo else board.updated_at.replace(tzinfo=timezone.utc)
        stuck_links = [link for link in board.chain if link.status in {"queued", "running"}]
        if (stuck_links or board.composing or board.stage == "dissecting") and updated < cutoff:
            def _fail(fresh: PositionBoard) -> None:
                for link in fresh.chain:
                    if link.status in {"queued", "running"}:
                        link.status = "failed"
                        link.reason = "Interrupted. Run it again."
                fresh.composing = False
                if fresh.stage == "dissecting":
                    fresh.stage = "positions"
                fresh.last_error = "A step was interrupted before it finished. Run it again."

            fresh = await self._mutate(letter_id, _fail)
            board.chain, board.composing, board.stage = fresh.chain, fresh.composing, fresh.stage
            board.last_error, board.board_version = fresh.last_error, fresh.board_version
        return steps

    # ---------------------------------------------------------------- dissect
    async def begin_dissect(self, letter_id: str, current_user: Any) -> BenchAccepted:
        await self._authorize(letter_id, current_user, "drafting.draft.create")
        self._ensure_idle(await self._load_board(letter_id))
        return await self.begin_first_dissect(letter_id, current_user)

    async def begin_first_dissect(self, letter_id: str, current_user: Any) -> BenchAccepted:
        """Mark the analysis as running WITHOUT a further permission check.

        Only for the request that has just created the board through ``open``:
        that call already authorised reading the document and starting a
        drafting request in its scope, and the first analysis is part of
        opening. Requiring ``drafting.draft.create`` here would let a user who
        may request a draft create a board that can never be analysed. Running
        the analysis again goes through ``begin_dissect`` and its own check.
        """

        def _running(board: PositionBoard) -> None:
            board.stage = "dissecting" if not board.assertions else board.stage
            board.last_error = None
            for link in board.chain:
                if link.skill_key == ANALYSIS_SKILL_KEY:
                    link.status = "running"

        await self._mutate(letter_id, _running, _user_id(current_user))
        return BenchAccepted(
            letter_id=str(letter_id), operation="dissect", poll_url=f"/api/letters/{letter_id}/bench"
        )

    async def run_dissect(self, letter_id: str, current_user: Any) -> None:
        try:
            await self._dissect(letter_id, current_user)
        except Exception as exc:
            logger.exception("letter bench dissect failed for %s", letter_id)
            await self._record_failure(letter_id, ANALYSIS_SKILL_KEY, f"Analysis failed: {str(exc)[:300]}")

    async def _record_failure(self, letter_id: str, skill_key: Optional[str], message: str) -> None:
        def _fail(board: PositionBoard) -> None:
            board.last_error = message
            board.composing = False
            if board.stage == "dissecting":
                board.stage = "positions"
            for link in board.chain:
                if link.status in {"queued", "running"} and (skill_key is None or link.skill_key == skill_key):
                    link.status = "failed"
                    link.reason = message

        try:
            await self._mutate(letter_id, _fail)
        except Exception:
            logger.exception("letter bench could not record failure for %s", letter_id)

    async def _dissect(self, letter_id: str, current_user: Any) -> None:
        user_id = _user_id(current_user)
        letter = await self.drafts.letter_service.get_letter(letter_id)
        board = await self._load_board(letter_id)
        document = await self._document(board.incoming_document_id)

        # A document under review, or quarantined as a duplicate, is not a source:
        # nothing it carries - body, summary or extracted metadata - is analysed,
        # sent to the model or kept on the board (services/publication_policy.py).
        held = document is not None and not is_consumable(document)
        if document is not None:
            # The letter's own body only. A summary is the model's paraphrase and
            # must never be shown, quoted or answered as the letter.
            text = (
                ""
                if held
                else clean_incoming_text(authoritative_fact_text(document) or document.get("content"))
            )
            request = DraftRunCreateRequest(
                mode="background",
                draft_type="reply",
                role=board.role,
                incoming_document_id=board.incoming_document_id,
            )
            if letter is None:
                raise RuntimeError("The letter for this bench no longer exists")
            if held:
                analysis = IncomingLetterAnalysis(subject=getattr(letter, "subject", None))
            else:
                analysis = await self.drafts.incoming_analyzer.analyze(
                    letter, request, _SingleDocumentService(document)
                )
                try:
                    analysis = await self.drafts.clause_checker.enrich(
                        analysis, board.organization_id, board.project_id or ""
                    )
                except Exception as exc:  # visible, not fatal: checks stay "not checked"
                    logger.warning("letter bench clause check skipped: %s", exc)
        else:
            text = board.incoming_text
            request = DraftRunCreateRequest(mode="background", draft_type="fresh", role=board.role)
            analysis = IncomingLetterAnalysis(
                subject=getattr(letter, "subject", None),
                issue_type=_trigger_issue_type(board.trigger),
                issue_type_source="system_default",
                main_request=condense_text(text, 500),
                clauses_cited=extract_clause_refs(text),
            )

        # The incoming letter is the most adversarial input in the product. Scan it
        # BEFORE any skill step: text that tries to instruct the model is not sent
        # to the model at all, here or in any later step on this board.
        injection = self.drafts.guardrails.scan_evidence([text])
        model_blocked = (
            "This letter contains instruction-like text"
            + (f" (\"{injection[0].evidence}\")" if getattr(injection[0], "evidence", None) else "")
            + ", so it was not sent to the language model. Rule-based analysis and template wording "
            "are used instead. Read the letter and every reply paragraph yourself."
            if injection
            else None
        )
        notices = [model_blocked] if model_blocked else []
        if len(text) >= MAX_BENCH_TEXT_CHARS:
            notices.append(
                f"Only the first {MAX_BENCH_TEXT_CHARS:,} characters of this document were read. "
                "Check the end of the letter for points that are missing here."
            )

        extracted = extract_assertions(text)
        if not extracted and board.trigger and text.strip():
            extracted = [{"quoted_text": text, "start": 0, "end": len(text), "clause_refs": extract_clause_refs(text)}]

        skills = await self.registry.list_effective(board.organization_id, board.project_id)
        version, _binding, reason = await self.registry.resolve(
            ANALYSIS_SKILL_KEY, board.organization_id, board.project_id
        )
        step: Optional[SkillStep] = None
        output = logic.fallback_analysis(analysis, extracted)
        if version is not None and text.strip():
            step = await self._execute(
                letter_id=letter_id,
                version=version,
                role=board.role,
                targets=[],
                prompt_key=SKILL_STEP_PROMPT_KEY,
                inputs={"text": text, "document_id": board.incoming_document_id, "trigger": board.trigger},
                payload={
                    "task": (
                        "Analyse the incoming letter. In 'assertions' list every separate assertion, "
                        "demand or notice the sender makes, each quoted VERBATIM from the letter, with "
                        "the clauses it relies on, a proposed verdict (entitlement, partial, none or "
                        "need_information) from the drafting party's side, and a one-sentence reason."
                    ),
                    "subject": _untrusted(analysis.subject or getattr(letter, "subject", "") or "Not identified", 500),
                    "contract_form": "Not resolved for this project. Do not assume an edition.",
                    "material": _untrusted(text),
                    "sources": self._clause_sources_text(analysis),
                    "inputs": "None.",
                },
                fallback=lambda: output,
                parents=[],
                user_id=user_id,
                force=True,
                skip_llm=model_blocked,
            )
            output = LetterAnalysisOutput(**step.output)

        located = self._located_assertions(text, output, extracted)
        clause_checks = self._clause_check_map(analysis)
        questions = [
            question
            for question in UserDirectionAgent.build_questions(analysis, request, [])
            if question.question_id not in _QUESTIONS_REPLACED_BY_VERDICTS
        ]
        deadline = logic.resolve_deadline(
            analysis.response_deadline, analysis.letter_date, risk=analysis.deadline_risk
        )
        previous_steps = [s for s in await self._steps(letter_id) if not step or s.step_id != step.step_id]
        await self._mark_stale(letter_id, [s.step_id for s in previous_steps])

        def _apply(fresh: PositionBoard) -> None:
            carried = {" ".join(a.quoted_text.split()): a for a in fresh.assertions}
            assertions: List[Assertion] = []
            for index, item in enumerate(located, start=1):
                refs = item.get("clause_refs") or extract_clause_refs(item["quoted_text"])
                assertion = Assertion(
                    assertion_id=f"A{index}",
                    order=index,
                    quoted_text=item["quoted_text"],
                    span=TextSpan(start=item["start"], end=item["end"]) if item.get("end") else None,
                    clause_refs=refs,
                    clause_checks=[
                        clause_checks.get(normalize_cited_clause(ref) or ref)
                        or ClauseCheck(clause_number=ref, state="not_checked")
                        for ref in refs
                    ],
                )
                logic.propose_verdict(assertion)
                if item.get("proposed_verdict"):
                    assertion.proposed_verdict = item["proposed_verdict"]
                    assertion.proposed_reason = item.get("reason") or assertion.proposed_reason
                old = carried.get(" ".join(assertion.quoted_text.split()))
                if old and old.verdict:
                    assertion.verdict, assertion.verdict_source = old.verdict, old.verdict_source
                    assertion.user_direction, assertion.state = old.user_direction, "decided"
                assertions.append(assertion)
            # Assertions the user added by hand survive a re-run of the analysis.
            for manual in fresh.assertions:
                if manual.origin == "user" and " ".join(manual.quoted_text.split()) not in {
                    " ".join(a.quoted_text.split()) for a in assertions
                }:
                    manual.assertion_id = f"A{len(assertions) + 1}"
                    assertions.append(manual)
            _reorder(assertions)
            chain = propose_chain(
                issue_type=analysis.issue_type, assertions=assertions, skills=skills, role=fresh.role
            )
            apply_routing(chain, assertions)
            analysis_link = chain[0]
            if step is not None:
                analysis_link.status, analysis_link.step_id = "done", step.step_id
                if step.degraded:
                    analysis_link.reason = (
                        "Not sent to the language model. Rule-based extraction was used."
                        if model_blocked
                        else "Language model unavailable. Rule-based extraction was used."
                    )
            elif version is None:
                analysis_link.status, analysis_link.reason = "unavailable", reason
            answers = {item.question.question_id: item.answer for item in fresh.questions}
            fresh.assertions = assertions
            fresh.chain = chain
            fresh.incoming_text = text
            fresh.issue_type = analysis.issue_type
            fresh.incoming_meta = {
                "letter_no": analysis.letter_no,
                "letter_date": analysis.letter_date,
                "sender": analysis.sender,
                "recipient": analysis.recipient,
                "subject": analysis.subject,
                "analysis": analysis.model_dump(mode="json"),
                "summary": output.summary_lines,
                "immediate_action": output.immediate_action,
            }
            fresh.questions = [
                BoardQuestion(question=question, answer=answers.get(question.question_id))
                for question in questions
            ]
            if fresh.deadline.source != "manual":
                fresh.deadline = deadline
            fresh.analysis_step_id = step.step_id if step else None
            for paragraph in fresh.reply_paragraphs:
                paragraph.stale = True
            fresh.stage = "positions"
            fresh.locked_strategy_version = None
            fresh.model_blocked_reason = model_blocked
            fresh.last_error = " ".join(notices) or None
            if held:
                fresh.last_error = _HELD_DOCUMENT_MESSAGE
            elif not text.strip():
                fresh.last_error = (
                    "No text could be read from this document. Check that it has been processed, "
                    "then run the analysis again."
                )

        board = await self._mutate(letter_id, _apply, user_id)
        for link in board.chain:
            if link.kind == "evaluation" and link.status == "pending":
                await self._run_evaluation_link(letter_id, link.skill_key, link.target_assertion_ids, current_user)

    @staticmethod
    def _clause_sources_text(analysis: IncomingLetterAnalysis) -> str:
        lines = []
        for evaluation in analysis.cited_clause_evaluations:
            if evaluation.exists_in_contract:
                lines.append(
                    f"- Clause {evaluation.clause_number}"
                    + (f" ({evaluation.clause_title})" if evaluation.clause_title else "")
                    + (f": {evaluation.quoted_text}" if evaluation.quoted_text else "")
                )
        return "\n".join(lines) or "No contract clause sources are available for this project."

    @staticmethod
    def _clause_check_map(analysis: IncomingLetterAnalysis) -> Dict[str, ClauseCheck]:
        checks: Dict[str, ClauseCheck] = {}
        for evaluation in analysis.cited_clause_evaluations:
            key = normalize_cited_clause(evaluation.clause_number) or evaluation.clause_number
            state = (
                "found"
                if evaluation.exists_in_contract
                else "not_found"
                if evaluation.exists_in_contract is False
                else "not_checked"
            )
            checks[key] = ClauseCheck(
                clause_number=key,
                state=state,  # type: ignore[arg-type]
                title=evaluation.clause_title,
                note=evaluation.drafter_comment,
            )
        return checks

    @staticmethod
    def _located_assertions(
        text: str, output: LetterAnalysisOutput, extracted: Sequence[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Keep only model assertions that are verbatim in the letter; else the rule-based set."""
        located: List[Dict[str, Any]] = []
        for item in output.assertions:
            span = locate_quote(text, item.quoted_text)
            if span is None:
                continue
            if any(span[0] < other["end"] and other["start"] < span[1] for other in located):
                continue
            located.append(
                {
                    "quoted_text": text[span[0] : span[1]],
                    "start": span[0],
                    "end": span[1],
                    "clause_refs": extract_clause_refs(text[span[0] : span[1]]) or item.clause_refs,
                    "proposed_verdict": item.proposed_verdict,
                    "reason": item.reason,
                }
            )
        # A model can miss a point. "Never leave an assertion unanswered" starts with
        # finding them all, so rule-based finds the model did not cover are kept;
        # a surplus card is removed in one click, a missed demand is not noticed.
        for found in extracted:
            if not any(found["start"] < other["end"] and other["start"] < found["end"] for other in located):
                located.append(dict(found))
        return sorted(located, key=lambda item: item["start"])

    # -------------------------------------------------------- step execution
    async def _execute(
        self,
        *,
        letter_id: str,
        version: SkillVersion,
        role: str,
        targets: List[str],
        prompt_key: str,
        inputs: Dict[str, Any],
        payload: Dict[str, str],
        fallback: Callable[[], Any],
        parents: List[str],
        user_id: Optional[str],
        force: bool = False,
        schema_key: Optional[str] = None,
        skip_llm: Optional[str] = None,
    ) -> SkillStep:
        schema_key = schema_key or version.manifest.output_schema_key
        input_hash = step_input_hash(
            skill_key=version.skill_key,
            skill_hash=version.content_hash,
            role=role,
            overlay_key=overlay_key_for(role),
            schema_key=schema_key,
            inputs={**inputs, "targets": sorted(targets)},
        )
        if not force:
            cached = await self._cached_step(letter_id, version, input_hash)
            if cached is not None:
                return cached
        try:
            result = await self.executor.run(
                version=version,
                role=role,
                prompt_key=prompt_key,
                payload=payload,
                fallback=fallback,
                schema_key=schema_key,
                skip_llm=skip_llm,
            )
        except SkillRoleNotSupported as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        step = SkillStep(
            step_id=uuid.uuid4().hex,
            letter_id=str(letter_id),
            skill_key=version.skill_key,
            skill_version=version.version,
            skill_hash=version.content_hash,
            skill_name=version.name,
            role=role,
            overlay_key=result.compiled.overlay_key,
            target_assertion_ids=list(targets),
            input_hash=input_hash,
            output=result.output,
            output_schema_key=schema_key,
            status="completed",
            degraded=result.degraded,
            error=result.error,
            parent_step_ids=list(parents),
            model=result.model,
            created_by=user_id,
            completed_at=_now(),
        )
        return await self._put_step(step)

    async def begin_step(self, letter_id: str, request: StepRunRequest, current_user: Any) -> BenchAccepted:
        await self._authorize(letter_id, current_user, "drafting.draft.create")
        if request.skill_key == ANALYSIS_SKILL_KEY:
            return await self.begin_dissect(letter_id, current_user)
        board = await self._load_board(letter_id)
        self._ensure_idle(board)
        await self._ensure_source_usable(board)
        version, _binding, reason = await self.registry.resolve(
            request.skill_key, board.organization_id, board.project_id
        )
        if version is None:
            raise HTTPException(status_code=422, detail=f"This skill cannot be run: {reason}")
        if board.role not in version.manifest.roles_supported:
            raise HTTPException(
                status_code=422, detail=f"{version.name} is not available when drafting as {board.role}"
            )
        known = {assertion.assertion_id for assertion in board.assertions}
        unknown = [target for target in request.target_assertion_ids if target not in known]
        if unknown:
            raise HTTPException(status_code=404, detail=f"Unknown assertion: {', '.join(unknown)}")
        if version.kind == "drafting":
            gate = logic.compute_gate(board, await self._steps(letter_id))
            if not gate.can_compose:
                raise HTTPException(status_code=409, detail="Lock positions before composing the reply")

        def _running(fresh: PositionBoard) -> None:
            link = logic.link_for(fresh.chain, request.skill_key)
            if link is None:
                link = ChainLink(
                    link_id=uuid.uuid4().hex[:12],
                    skill_key=version.skill_key,
                    label=version.name,
                    kind=version.kind,
                    target_assertion_ids=list(request.target_assertion_ids),
                    skill_version=version.version,
                    added_by="user",
                )
                fresh.chain.insert(max(len(fresh.chain) - 1, 1), link)
            elif link.target_assertion_ids:
                for target in request.target_assertion_ids:
                    if target not in link.target_assertion_ids:
                        link.target_assertion_ids.append(target)
            link.status, link.reason = "running", None
            if version.kind == "drafting":
                fresh.composing = True
            fresh.last_error = None

        await self._mutate(letter_id, _running, _user_id(current_user))
        return BenchAccepted(
            letter_id=str(letter_id), operation="step", poll_url=f"/api/letters/{letter_id}/bench"
        )

    async def run_step(self, letter_id: str, request: StepRunRequest, current_user: Any) -> None:
        if request.skill_key == ANALYSIS_SKILL_KEY:
            return await self.run_dissect(letter_id, current_user)
        try:
            board = await self._load_board(letter_id)
            version, _binding, _reason = await self.registry.resolve(
                request.skill_key, board.organization_id, board.project_id
            )
            if version is not None and version.kind == "drafting":
                await self._compose(letter_id, current_user, only_assertions=request.target_assertion_ids, force=request.force)
            else:
                await self._run_evaluation_link(
                    letter_id, request.skill_key, request.target_assertion_ids, current_user, force=request.force
                )
        except Exception as exc:
            logger.exception("letter bench step failed for %s", letter_id)
            await self._record_failure(letter_id, request.skill_key, f"The skill could not be run: {str(exc)[:300]}")

    async def _run_evaluation_link(
        self,
        letter_id: str,
        skill_key: str,
        targets: Sequence[str],
        current_user: Any,
        *,
        force: bool = False,
    ) -> None:
        user_id = _user_id(current_user)
        board = await self._load_board(letter_id)
        version, _binding, reason = await self.registry.resolve(skill_key, board.organization_id, board.project_id)
        if version is None:
            await self._record_failure(letter_id, skill_key, f"Skill unavailable: {reason}")
            return
        assertions = [a for a in board.assertions if not targets or a.assertion_id in targets]
        produced: Dict[str, SkillStep] = {}
        meta = board.incoming_meta or {}
        for assertion in assertions:
            fallback_output = logic.fallback_variation(assertion)
            produced[assertion.assertion_id] = await self._execute(
                letter_id=letter_id,
                version=version,
                role=board.role,
                targets=[assertion.assertion_id],
                prompt_key=SKILL_STEP_PROMPT_KEY,
                inputs={
                    "assertion": assertion.quoted_text,
                    "clause_refs": assertion.clause_refs,
                    "direction": assertion.user_direction,
                },
                payload={
                    "task": (
                        f"Evaluate the assertion below using this skill. The assertion is point "
                        f"{assertion.assertion_id} of the incoming letter. In 'suggested_verdict' give "
                        "entitlement, partial, none or need_information from the drafting party's side. "
                        "If data is insufficient, classify accordingly and list exactly what is missing."
                    ),
                    "subject": _untrusted(meta.get("subject") or "Not identified", 500),
                    "contract_form": "Not resolved for this project. Do not assume an edition.",
                    "material": _untrusted(assertion.quoted_text),
                    "sources": "\n".join(
                        f"- Clause {check.clause_number}: {check.label}"
                        + (f" ({check.title})" if check.title else "")
                        for check in assertion.clause_checks
                    )
                    or "No contract clause sources are available for this assertion.",
                    "inputs": (
                        f"User direction: {assertion.user_direction}" if assertion.user_direction else "None."
                    )
                    + "\nBOQ rates: not on record.\nOriginal scope documents: not on record.",
                },
                fallback=_constant(fallback_output),
                parents=[board.analysis_step_id] if board.analysis_step_id else [],
                user_id=user_id,
                force=force,
                skip_llm=board.model_blocked_reason,
            )

        steps = await self._steps(letter_id)
        stale_ids: set[str] = set()
        for step in produced.values():
            stale_ids |= logic.descendants(steps, [step.step_id])
        await self._mark_stale(letter_id, sorted(stale_ids))

        def _apply(fresh: PositionBoard) -> None:
            link = logic.link_for(fresh.chain, skill_key)
            degraded = False
            for assertion in fresh.assertions:
                step = produced.get(assertion.assertion_id)
                if step is None:
                    continue
                output = step.output or {}
                degraded = degraded or step.degraded
                assertion.needs = [str(item) for item in output.get("missing_information") or []][:10]
                assertion.routed_skill_key = assertion.routed_skill_key or skill_key
                if step.step_id not in assertion.step_ids:
                    assertion.step_ids.append(step.step_id)
                suggested = output.get("suggested_verdict")
                if suggested in VERDICT_LABELS and assertion.verdict_source != "user":
                    assertion.proposed_verdict = suggested
                    assertion.proposed_reason = (
                        output.get("valuation_justification")
                        or f"{version.name}: {str(output.get('classification') or '').replace('_', ' ')}"
                    )
            logic.apply_stale_to_board(fresh, stale_ids, steps)
            for paragraph in fresh.reply_paragraphs:
                if paragraph.assertion_id in produced:
                    paragraph.stale = True
            if link is not None:
                link.status = "done"
                link.skill_version = version.version
                link.step_id = next(iter(produced.values())).step_id if len(produced) == 1 else None
                link.reason = (
                    "No language model result. The skill's 'insufficient information' outcome was recorded."
                    if degraded
                    else None
                )

        await self._mutate(letter_id, _apply, user_id)

    # ------------------------------------------------------- position edits
    async def patch_assertion(
        self, letter_id: str, assertion_id: str, request: AssertionPatchRequest, current_user: Any
    ) -> BenchResponse:
        await self._authorize(letter_id, current_user, "drafting.draft.create")
        board = await self._load_board(letter_id)
        self._check_version(board, request.board_version)
        assertion = _find(board, assertion_id)
        steps = await self._steps(letter_id)
        position_changed = False
        routing_changed = False
        touched = [assertion_id]

        if request.remove:
            board.assertions = [a for a in board.assertions if a.assertion_id != assertion_id]
            board.reply_paragraphs = [p for p in board.reply_paragraphs if p.assertion_id != assertion_id]
            for link in board.chain:
                link.target_assertion_ids = [t for t in link.target_assertion_ids if t != assertion_id]
            _reorder(board.assertions)
            routing_changed = True
        elif request.split_at is not None:
            text = assertion.quoted_text
            if request.split_at >= len(text) - 1:
                raise HTTPException(status_code=422, detail="Split point is outside the assertion")
            left, right = text[: request.split_at].rstrip(), text[request.split_at :].lstrip()
            if len(left) < 10 or len(right) < 10:
                raise HTTPException(status_code=422, detail="Each part of a split must be at least 10 characters")
            second = Assertion(
                assertion_id=_next_id(board),
                quoted_text=right,
                clause_refs=extract_clause_refs(right),
                origin="user",
            )
            if assertion.span:
                right_start = assertion.span.start + request.split_at + (
                    len(text[request.split_at :]) - len(right)
                )
                second.span = TextSpan(start=right_start, end=assertion.span.end)
                assertion.span = TextSpan(start=assertion.span.start, end=assertion.span.start + len(left))
            assertion.quoted_text = left
            assertion.clause_refs = extract_clause_refs(left)
            checks = {check.clause_number: check for check in assertion.clause_checks}
            for item in (assertion, second):
                item.clause_checks = [
                    checks.get(normalize_cited_clause(ref) or ref) or ClauseCheck(clause_number=ref)
                    for ref in item.clause_refs
                ]
            logic.propose_verdict(second)
            # The evaluation was made on the whole text; neither half inherits it.
            assertion.needs = []
            board.assertions.append(second)
            _reorder(board.assertions)
            position_changed = routing_changed = True
        elif request.merge_with:
            other = _find(board, request.merge_with)
            if other.assertion_id == assertion.assertion_id:
                raise HTTPException(status_code=422, detail="An assertion cannot be merged with itself")
            first, second = sorted(
                (assertion, other), key=lambda item: (item.span.start if item.span else 10**9, item.order)
            )
            if first.span and second.span and board.incoming_text:
                span = TextSpan(start=first.span.start, end=max(first.span.end, second.span.end))
                assertion.span, assertion.quoted_text = span, board.incoming_text[span.start : span.end]
            else:
                assertion.span, assertion.quoted_text = None, f"{first.quoted_text} {second.quoted_text}"
            assertion.clause_refs = list(dict.fromkeys([*first.clause_refs, *second.clause_refs]))
            merged_checks = {c.clause_number: c for c in [*first.clause_checks, *second.clause_checks]}
            assertion.clause_checks = list(merged_checks.values())
            if assertion.verdict != other.verdict:
                assertion.verdict, assertion.verdict_source = None, None
            board.assertions = [a for a in board.assertions if a.assertion_id != other.assertion_id]
            board.reply_paragraphs = [p for p in board.reply_paragraphs if p.assertion_id != other.assertion_id]
            for link in board.chain:
                link.target_assertion_ids = [t for t in link.target_assertion_ids if t != other.assertion_id]
            _reorder(board.assertions)
            touched.append(other.assertion_id)
            position_changed = routing_changed = True

        if not request.remove:
            if request.clear_verdict and assertion.verdict is not None:
                assertion.verdict, assertion.verdict_source = None, None
                position_changed = True
            elif request.verdict is not None and request.verdict != assertion.verdict:
                assertion.verdict = request.verdict
                assertion.verdict_source = "ai" if request.verdict == assertion.proposed_verdict else "user"
                position_changed = True
            if request.user_direction is not None:
                direction = request.user_direction.strip() or None
                if direction != assertion.user_direction:
                    self._scan_user_text(direction)
                    assertion.user_direction = direction
                    position_changed = True
            if request.clear_routed_skill or request.routed_skill_key is not None:
                new_key = None if request.clear_routed_skill else request.routed_skill_key
                if new_key != assertion.routed_skill_key:
                    await self._reroute(board, assertion, new_key)
                    routing_changed = True
            assertion.state = "decided" if assertion.verdict else "open"

        if position_changed or routing_changed:
            stale_ids: set[str] = set()
            for target in touched:
                stale_ids |= logic.steps_stale_after_position_change(
                    steps, target, include_evaluations=routing_changed
                )
            await self._mark_stale(letter_id, sorted(stale_ids))
            logic.apply_stale_to_board(board, stale_ids, steps)
            for paragraph in board.reply_paragraphs:
                if paragraph.assertion_id in touched:
                    paragraph.stale = True
            self._unlock(board)
        await self._save(board, request.board_version, _user_id(current_user))
        return await self.get(letter_id, current_user)

    async def _reroute(self, board: PositionBoard, assertion: Assertion, new_key: Optional[str]) -> None:
        for link in board.chain:
            if link.kind == "evaluation" and assertion.assertion_id in link.target_assertion_ids:
                link.target_assertion_ids.remove(assertion.assertion_id)
        board.chain = [
            link
            for link in board.chain
            if not (link.kind == "evaluation" and link.added_by == "user" and not link.target_assertion_ids and link.status == "pending")
        ]
        assertion.routed_skill_key = None
        assertion.needs = []
        if not new_key:
            return
        version, _binding, reason = await self.registry.resolve(new_key, board.organization_id, board.project_id)
        if version is None:
            raise HTTPException(status_code=422, detail=f"This skill cannot be used here: {reason}")
        if version.kind != "evaluation":
            raise HTTPException(status_code=422, detail="Only an evaluation skill can be routed to an assertion")
        if board.role not in version.manifest.roles_supported:
            raise HTTPException(status_code=422, detail=f"{version.name} is not available when drafting as {board.role}")
        target: Optional[ChainLink] = next((item for item in board.chain if item.skill_key == new_key), None)
        if target is None:
            target = ChainLink(
                link_id=uuid.uuid4().hex[:12],
                skill_key=new_key,
                label=version.name,
                kind="evaluation",
                skill_version=version.version,
                added_by="user",
            )
            board.chain.insert(max(len(board.chain) - 1, 1), target)
        if assertion.assertion_id not in target.target_assertion_ids:
            target.target_assertion_ids.append(assertion.assertion_id)
        target.status = "pending" if target.status in {"done", "unavailable", "failed"} else target.status
        assertion.routed_skill_key = new_key

    @staticmethod
    def _unlock(board: PositionBoard) -> None:
        """Any change to a position means the lock no longer describes the board."""
        if board.stage in {"locked", "composed"}:
            board.stage = "positions"
        for assertion in board.assertions:
            if assertion.state == "locked":
                assertion.state = "decided" if assertion.verdict else "open"

    def _scan_user_text(self, text: Optional[str]) -> None:
        if not text:
            return
        # Directions and answers are written into skill prompts verbatim, so any
        # injection phrasing is refused here rather than merely flagged later.
        if self.drafts.guardrails.scan_input(text, origin="user_direction"):
            raise HTTPException(
                status_code=422,
                detail="This direction contains instruction-like text that cannot be sent to the drafting model. Rephrase it.",
            )

    async def create_assertion(
        self, letter_id: str, request: AssertionCreateRequest, current_user: Any
    ) -> BenchResponse:
        await self._authorize(letter_id, current_user, "drafting.draft.create")
        board = await self._load_board(letter_id)
        self._check_version(board, request.board_version)
        span = request.span
        if span is not None:
            if span.end > len(board.incoming_text):
                raise HTTPException(status_code=422, detail="Selection is outside the letter")
            quoted = board.incoming_text[span.start : span.end]
            trimmed_left = len(quoted) - len(quoted.lstrip())
            quoted = quoted.strip()
            span = TextSpan(start=span.start + trimmed_left, end=span.start + trimmed_left + len(quoted)) if quoted else None
        else:
            quoted = (request.quoted_text or "").strip()
        if len(quoted) < 10:
            raise HTTPException(status_code=422, detail="Select at least 10 characters to create an assertion")
        if span and any(
            a.span and span.start < a.span.end and a.span.start < span.end for a in board.assertions
        ):
            raise HTTPException(status_code=409, detail="That text is already part of an assertion")
        analysis = (board.incoming_meta or {}).get("analysis")
        checks = self._clause_check_map(IncomingLetterAnalysis(**analysis)) if analysis else {}
        refs = extract_clause_refs(quoted)
        assertion = Assertion(
            assertion_id=_next_id(board),
            quoted_text=quoted,
            span=span,
            clause_refs=refs,
            clause_checks=[
                checks.get(normalize_cited_clause(ref) or ref) or ClauseCheck(clause_number=ref) for ref in refs
            ],
            origin="user",
        )
        logic.propose_verdict(assertion)
        board.assertions.append(assertion)
        _reorder(board.assertions)
        self._unlock(board)
        await self._save(board, request.board_version, _user_id(current_user))
        return await self.get(letter_id, current_user)

    async def update_chain(self, letter_id: str, request: ChainUpdateRequest, current_user: Any) -> BenchResponse:
        await self._authorize(letter_id, current_user, "drafting.draft.create")
        board = await self._load_board(letter_id)
        self._check_version(board, request.board_version)
        existing = {link.link_id: link for link in board.chain}
        known = {assertion.assertion_id for assertion in board.assertions}
        skills = {s.skill_key: s for s in await self.registry.list_effective(board.organization_id, board.project_id)}
        analysis = next((l for l in board.chain if l.kind == "analysis"), None)
        drafting = next((l for l in reversed(board.chain) if l.kind == "drafting"), None)
        middle: List[ChainLink] = []
        seen: set[str] = set()
        for item in request.links:
            link = existing.get(item.link_id or "")
            targets = [target for target in item.target_assertion_ids if target in known]
            if link is not None:
                if link.kind != "evaluation" or link.link_id in seen:
                    continue  # analysis stays first, the reply stays last
                if set(targets) != set(link.target_assertion_ids):
                    link.target_assertion_ids = targets
                    link.status = "pending" if link.status == "done" else link.status
                seen.add(link.link_id)
                middle.append(link)
                continue
            skill = skills.get(item.skill_key)
            if skill is not None and skill.kind != "evaluation" and skill.available:
                raise HTTPException(status_code=422, detail=f"{skill.name} cannot be added as a chain step")
            new_link = ChainLink(
                link_id=uuid.uuid4().hex[:12],
                skill_key=item.skill_key,
                label=skill.name if skill else item.skill_key,
                kind="evaluation",
                target_assertion_ids=targets,
                skill_version=skill.version if skill else None,
                added_by="user",
            )
            if skill is None or not skill.available:
                new_link.status = "unavailable"
                new_link.reason = (skill.unavailable_reason if skill else None) or "Not installed"
            elif board.role not in skill.roles_supported:
                new_link.status = "unavailable"
                new_link.reason = f"Not available when drafting as {board.role}"
            middle.append(new_link)

        removed = [l for l in board.chain if l.kind == "evaluation" and l.link_id not in seen]
        steps = await self._steps(letter_id)
        stale_ids: set[str] = set()
        for link in removed:
            for assertion in board.assertions:
                if assertion.routed_skill_key == link.skill_key and (
                    not link.target_assertion_ids or assertion.assertion_id in link.target_assertion_ids
                ):
                    assertion.routed_skill_key = None
                    assertion.needs = []
            for step in steps:
                if step.skill_key == link.skill_key:
                    stale_ids |= {step.step_id} | logic.descendants(steps, [step.step_id])
        board.chain = [l for l in [analysis, *middle, drafting] if l is not None]
        for link in middle:
            if link.status != "unavailable":
                for assertion in board.assertions:
                    if assertion.assertion_id in link.target_assertion_ids and not assertion.routed_skill_key:
                        assertion.routed_skill_key = link.skill_key
        if stale_ids:
            await self._mark_stale(letter_id, sorted(stale_ids))
            logic.apply_stale_to_board(board, stale_ids, steps)
            self._unlock(board)
        await self._save(board, request.board_version, _user_id(current_user))
        return await self.get(letter_id, current_user)

    async def answer_question(
        self, letter_id: str, request: QuestionAnswerRequest, current_user: Any
    ) -> BenchResponse:
        await self._authorize(letter_id, current_user, "drafting.draft.create")
        board = await self._load_board(letter_id)
        self._check_version(board, request.board_version)
        item = next((q for q in board.questions if q.question.question_id == request.question_id), None)
        if item is None:
            raise HTTPException(status_code=404, detail="Question not found")
        self._scan_user_text(request.answer)
        item.answer = request.answer.strip()
        await self._save(board, request.board_version, _user_id(current_user))
        return await self.get(letter_id, current_user)

    async def update_settings(
        self, letter_id: str, request: BoardSettingsRequest, current_user: Any
    ) -> BenchResponse:
        await self._authorize(letter_id, current_user, "drafting.draft.create")
        board = await self._load_board(letter_id)
        self._check_version(board, request.board_version)
        if request.deadline_date:
            try:
                board.deadline = logic.manual_deadline(request.deadline_date, request.deadline_basis)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="That is not a valid date") from exc
        if request.role and request.role != board.role:
            # The role overlay is part of every step's input: everything is out of date.
            board.role = request.role
            steps = await self._steps(letter_id)
            await self._mark_stale(letter_id, [step.step_id for step in steps])
            for paragraph in board.reply_paragraphs:
                paragraph.stale = True
            for link in board.chain:
                if link.status == "done":
                    link.status = "stale"
            self._unlock(board)
            await self.db.letters.update_one(
                {"_id": ObjectId(str(letter_id))}, {"$set": {"strategy_role": request.role}}
            )
        await self._save(board, request.board_version, _user_id(current_user))
        return await self.get(letter_id, current_user)

    async def patch_paragraph(
        self, letter_id: str, paragraph_id: str, request: ParagraphPatchRequest, current_user: Any
    ) -> BenchResponse:
        await self._authorize(letter_id, current_user, "drafting.draft.edit")
        board = await self._load_board(letter_id)
        self._check_version(board, request.board_version)
        paragraph = next((p for p in board.reply_paragraphs if p.paragraph_id == paragraph_id), None)
        if paragraph is None:
            raise HTTPException(status_code=404, detail="Paragraph not found")
        if paragraph.text != request.text:
            paragraph.text = request.text
            paragraph.edited_by_user = True
            paragraph.stale = False
            if board.stage == "composed":
                board.stage = "locked"  # the saved draft no longer matches; compose saves a new version
        await self._save(board, request.board_version, _user_id(current_user))
        return await self.get(letter_id, current_user)

    # ------------------------------------------------------------------- lock
    async def lock_positions(
        self, letter_id: str, request: LockPositionsRequest, current_user: Any
    ) -> BenchResponse:
        letter = await self._authorize(letter_id, current_user, "drafting.request.accept")
        board = await self._load_board(letter_id)
        self._check_version(board, request.board_version)
        await self._ensure_source_usable(board)
        steps = await self._steps(letter_id)
        gate = logic.compute_gate(board, steps)
        if gate.blockers:
            raise HTTPException(status_code=409, detail="; ".join(gate.blockers))
        user_id = _user_id(current_user)
        now = _now()
        # Claim the board first. If someone else changed it, this is a 409 and
        # nothing has been accepted. Only the winner goes on to write a strategy.
        for assertion in board.assertions:
            assertion.state = "locked"
        board.stage = "locked"
        board.locked_strategy_version = None
        board.locked_at, board.locked_by = now, user_id
        board = await self._save(board, request.board_version, user_id)

        rows = logic.build_reply_matrix(board)
        analysis = (board.incoming_meta or {}).get("analysis")
        sheet = logic.build_planning_sheet(board, analysis)
        plan = logic.build_plan_text(board, rows)
        skill_chain = self._skill_chain(board, steps)
        run = DraftRun(
            run_id=str(uuid.uuid4()),
            letter_id=str(letter_id),
            draft_type="reply" if board.incoming_document_id else "fresh",
            mode="strategy",
            status="completed",
            role=board.role,
            inputs={
                "origin": "letter_bench",
                "incoming_document_id": board.incoming_document_id,
                "trigger": board.trigger,
            },
            incoming_analysis=IncomingLetterAnalysis(**analysis) if analysis else None,
            planning_sheet=sheet,
            reply_matrix=rows,
            plan=plan,
            skill_chain=skill_chain,
            board_version=board.board_version,
            skill_manifest_hash=canonical_hash(skill_chain),
            started_at=now,
            completed_at=now,
            updated_at=now,
            created_by=user_id,
            trace=[{"stage": "positions_locked", "status": "success", "assertions": len(board.assertions)}],
        )
        stored = await self.drafts._create_and_record(run, current_user)
        strategy_version = await self.drafts.repository.mark_accepted_plan(letter_id, stored, user_id)
        await self.drafts.repository.append_event(
            str(letter_id),
            stored.run_id,
            "positions_locked",
            actor_user_id=user_id,
            status=stored.status,
            detail="Positions locked on the Letter Bench and accepted as the strategic plan.",
            payload={
                "strategy_version": strategy_version,
                "board_version": board.board_version,
                "verdicts": {a.assertion_id: a.verdict for a in board.assertions},
            },
        )
        if getattr(letter, "strategy_role", None) != board.role:
            await self.db.letters.update_one(
                {"_id": ObjectId(str(letter_id))}, {"$set": {"strategy_role": board.role}}
            )
        locked_board_version = board.board_version

        def _accepted(fresh: PositionBoard) -> None:
            # If the board moved on since the claim, its positions are no longer
            # "locked" and the gate will ask for a fresh lock; recording the
            # version here is then harmless.
            fresh.locked_strategy_version = strategy_version
            fresh.locked_board_version = locked_board_version
            fresh.strategy_run_id = stored.run_id

        await self._mutate(letter_id, _accepted, user_id)
        return await self.get(letter_id, current_user)

    @staticmethod
    def _skill_chain(board: PositionBoard, steps: Sequence[SkillStep]) -> List[Dict[str, Any]]:
        """Provenance pinned on the run: every skill, version and hash that shaped it."""
        chain: List[Dict[str, Any]] = []
        for step in steps:
            if step.stale or step.status != "completed":
                continue
            chain.append(
                {
                    "step_id": step.step_id,
                    "skill_key": step.skill_key,
                    "version": step.skill_version,
                    "hash": step.skill_hash,
                    "overlay_key": step.overlay_key,
                    "targets": list(step.target_assertion_ids),
                    "degraded": step.degraded,
                }
            )
        for link in board.chain:
            if link.status == "unavailable":
                chain.append(
                    {"step_id": None, "skill_key": link.skill_key, "version": None, "hash": None,
                     "unavailable": True, "reason": link.reason}
                )
        return chain

    # ---------------------------------------------------------------- compose
    async def begin_compose(self, letter_id: str, request: ComposeRequest, current_user: Any) -> BenchAccepted:
        await self._authorize(letter_id, current_user, "drafting.draft.create")
        board = await self._load_board(letter_id)
        self._check_version(board, request.board_version)
        await self._ensure_source_usable(board)
        gate = logic.compute_gate(board, await self._steps(letter_id))
        if not gate.can_compose:
            raise HTTPException(
                status_code=409,
                detail="; ".join(gate.blockers) or "Lock positions before composing the reply",
            )
        board.composing = True
        board.last_error = None
        for link in board.chain:
            if link.kind == "drafting" and link.status != "unavailable":
                link.status = "running"
        await self._save(board, request.board_version, _user_id(current_user))
        return BenchAccepted(
            letter_id=str(letter_id), operation="compose", poll_url=f"/api/letters/{letter_id}/bench"
        )

    async def run_compose(self, letter_id: str, request: ComposeRequest, current_user: Any) -> None:
        try:
            await self._compose(letter_id, current_user)
        except Exception as exc:
            logger.exception("letter bench compose failed for %s", letter_id)
            await self._record_failure(letter_id, None, f"The reply could not be composed: {str(exc)[:300]}")

    async def _compose(
        self,
        letter_id: str,
        current_user: Any,
        *,
        only_assertions: Sequence[str] = (),
        force: bool = False,
    ) -> None:
        user_id = _user_id(current_user)
        board = await self._load_board(letter_id)
        letter = await self.drafts.letter_service.get_letter(letter_id)
        version, _binding, reason = await self.registry.resolve(
            LETTERS_SKILL_KEY, board.organization_id, board.project_id
        )
        if version is None:
            raise RuntimeError(f"The drafting skill is unavailable: {reason}")
        letters_skill_version = version.version
        steps = await self._steps(letter_id)
        by_assertion = {p.assertion_id: p for p in board.reply_paragraphs if p.assertion_id}
        new_points: Dict[str, ReplyParagraph] = {}
        notes: List[str] = []
        degraded: List[str] = []
        left_stale: List[str] = []

        for assertion in sorted(board.assertions, key=lambda item: item.order):
            current = by_assertion.get(assertion.assertion_id)
            wanted = not only_assertions or assertion.assertion_id in only_assertions
            if current is not None and not current.stale and not (force and wanted) and current.text.strip():
                new_points[assertion.assertion_id] = current  # frozen: reused verbatim
                continue
            if current is not None and not wanted:
                # Not asked for in this run: it stays exactly as it is, stale flag included.
                new_points[assertion.assertion_id] = current
                if current.stale:
                    left_stale.append(assertion.assertion_id)
                continue
            evaluations = [
                step
                for step in steps
                if assertion.assertion_id in step.target_assertion_ids
                and step.output_schema_key != "reply_paragraph.v1"
                and step.status == "completed"
                and not step.stale
            ]
            fallback_output = logic.fallback_paragraph(assertion, board.role)
            step = await self._execute(
                letter_id=letter_id,
                version=version,
                role=board.role,
                targets=[assertion.assertion_id],
                prompt_key=COMPOSE_PROMPT_KEY,
                schema_key="reply_paragraph.v1",
                inputs={
                    "assertion": assertion.quoted_text,
                    "verdict": assertion.verdict,
                    "direction": assertion.user_direction,
                    # Outputs, not ids: a re-run evaluation keeps its id, and a paragraph
                    # written from the old result must not be served from the cache.
                    "evaluations": [canonical_hash(item.output) for item in evaluations],
                    "clause_checks": [check.model_dump() for check in assertion.clause_checks],
                },
                payload={
                    "verdict_label": VERDICT_LABELS.get(assertion.verdict or "", "Undecided"),
                    "direction": assertion.user_direction or "None given.",
                    "assertion": _untrusted(assertion.quoted_text),
                    "clause_check": "; ".join(
                        f"Clause {check.clause_number}: {check.label}" for check in assertion.clause_checks
                    )
                    or "No clause cited by the sender.",
                    "evaluation": "\n".join(
                        f"- {item.skill_name}: {_untrusted(condense_text(str(item.output), 1200))}"
                        for item in evaluations
                    )
                    or "None.",
                    "sources": "\n".join(
                        f"- Clause {check.clause_number}" + (f" ({check.title})" if check.title else "")
                        for check in assertion.clause_checks
                        if check.state == "found"
                    )
                    or "No contract clause sources are available. Use [CONFIRM: ...] for clause wording.",
                },
                fallback=_constant(fallback_output),
                parents=[item.step_id for item in evaluations],
                user_id=user_id,
                force=force and wanted,
                skip_llm=board.model_blocked_reason,
            )
            output = step.output or {}
            if step.degraded:
                degraded.append(assertion.assertion_id)
            if output.get("internal_note"):
                notes.append(f"{assertion.assertion_id}: {output['internal_note']}")
            for risk in output.get("residual_risks") or []:
                notes.append(f"{assertion.assertion_id} residual risk: {risk}")
            new_points[assertion.assertion_id] = ReplyParagraph(
                paragraph_id=f"P-{assertion.assertion_id}",
                assertion_id=assertion.assertion_id,
                part="point",
                text=str(output.get("text") or "").strip(),
                step_id=step.step_id,
                degraded=step.degraded,
                clause_refs=[str(ref) for ref in output.get("clause_refs") or []],
            )

        framing = logic.framing_paragraphs(board, board.reply_paragraphs)
        board.reply_paragraphs = logic.ordered_paragraphs(board, [*framing.values(), *new_points.values()])
        if left_stale:
            # Some paragraphs are still out of date, so this is not yet a reply
            # that can be saved as a draft. Keep what was written and say so.
            kept = board.reply_paragraphs

            def _partial(fresh: PositionBoard) -> None:
                fresh.reply_paragraphs = kept
                fresh.composing = False
                for link in fresh.chain:
                    if link.kind == "drafting" and link.status == "running":
                        link.status = "stale"
                        link.reason = f"Still out of date: {', '.join(left_stale)}"

            await self._mutate(letter_id, _partial, user_id)
            return

        steps = await self._steps(letter_id)
        enabled = {
            name
            for link in board.chain
            for name in await self._validators_for(link.skill_key, board)
        }
        findings = run_skill_validators(board, steps, enabled)
        blocking = [finding for finding in findings if finding.level == "error"]
        paragraphs = board.reply_paragraphs

        if blocking:
            def _blocked(fresh: PositionBoard) -> None:
                fresh.reply_paragraphs = paragraphs
                fresh.composing = False
                fresh.last_error = "; ".join(finding.message for finding in blocking)
                for link in fresh.chain:
                    if link.kind == "drafting" and link.status == "running":
                        link.status, link.reason = "failed", fresh.last_error

            await self._mutate(letter_id, _blocked, user_id)
            return

        rows = logic.build_reply_matrix(board)
        plan = logic.build_plan_text(board, rows)
        skill_chain = self._skill_chain(board, steps)
        note_text = "\n".join(notes) or "No residual risks were recorded by the drafting skill."
        meta = board.incoming_meta or {}
        stored_analysis = meta.get("analysis") or {}
        origin = (board.trigger or {}).get("label") or meta.get("letter_no") or "the incoming letter"
        # The run pipeline refuses a request with no stated basis. On the Bench the
        # basis is the board itself: the sender's points and the positions on them.
        positions_text = "\n".join(
            f"{a.assertion_id}: {VERDICT_LABELS.get(a.verdict or '', 'Undecided')}."
            + (f" {a.user_direction}" if a.user_direction else "")
            for a in sorted(board.assertions, key=lambda item: item.order)
        )
        run = await self.drafts.create_run(
            str(letter_id),
            DraftRunCreateRequest(
                mode="draft",
                draft_type="reply" if board.incoming_document_id else "fresh",
                role=board.role,
                subject=(meta.get("subject") or getattr(letter, "subject", None) or f"Letter on {origin}")[:500],
                recipient=(
                    meta.get("sender") or getattr(letter, "recipient", None) or "[CONFIRM: addressee]"
                )[:500],
                purpose=f"Point-by-point letter on {origin}, as decided on the Letter Bench."[:2000],
                points=positions_text[:8000],
                trigger_event=(
                    condense_text(stored_analysis.get("main_request"), 1500) or f"Letter on {origin}"
                )[:2000],
                background_facts="\n".join(
                    f"{a.assertion_id}: {condense_text(a.quoted_text, 500)}"
                    for a in sorted(board.assertions, key=lambda item: item.order)
                )[:8000],
                required_action=next(
                    (p.text for p in board.reply_paragraphs if p.part == "request"), None
                ),
                incoming_document_id=board.incoming_document_id,
                plan_override=plan[:12000],
                response_deadline=board.deadline.due_date or board.deadline.raw,
                user_direction_answers=[
                    UserDirectionAnswer(question_id=item.question.question_id, answer=item.answer or "")
                    for item in board.questions
                    if (item.answer or "").strip()
                ],
            ),
            current_user,
            bench=BenchRunContext(
                draft_letter=logic.assemble_letter(board),
                integrity_notes=(
                    "Composed on the Letter Bench from locked positions, one paragraph per assertion.\n"
                    f"Internal strategy note (not for issue):\n{note_text}"
                ),
                plan=plan,
                reply_matrix=rows,
                skill_chain=skill_chain,
                board_version=board.board_version + 1,
                skill_manifest_hash=canonical_hash(skill_chain),
                degraded_reasons=[
                    f"Template wording was used for {', '.join(degraded)} because the language model was unavailable."
                ]
                if degraded
                else [],
                extra_findings=[finding.as_validation_finding() for finding in findings],
            ),
        )
        if run.status in {"blocked", "failed"} or not (
            run.draft_artifact and (run.draft_artifact.draft_letter or "").strip()
        ):
            # No governed draft exists. Never present this as a composed reply.
            reasons = [f.message for f in run.validation_report.findings if f.level == "error"]
            reasons = reasons or [str(w) for w in run.warnings[-2:]] or ["The drafting pipeline returned no draft."]
            failure = "The reply could not be saved as a draft: " + " ".join(dict.fromkeys(reasons))
            kept = board.reply_paragraphs

            def _refused(fresh: PositionBoard) -> None:
                fresh.reply_paragraphs = kept
                fresh.composing = False
                fresh.last_error = failure
                for link in fresh.chain:
                    if link.kind == "drafting" and link.status != "unavailable":
                        link.status, link.reason = "failed", failure

            await self._mutate(letter_id, _refused, user_id)
            return
        if run.draft_artifact and run.draft_artifact.draft_letter:
            # "Submit for review" binds the letter to the governed artifact by
            # hash, so the letter body must be exactly what the run recorded.
            await self.db.letters.update_one(
                {"_id": ObjectId(str(letter_id))},
                {"$set": {"content": run.draft_artifact.draft_letter, "updated_at": _now()}},
            )
        clause_sources: Dict[str, List[str]] = {}
        for source in run.sources:
            if source.clause_number:
                key = normalize_cited_clause(source.clause_number) or source.clause_number
                clause_sources.setdefault(key, []).append(source.source_id)

        def _done(fresh: PositionBoard) -> None:
            fresh.reply_paragraphs = list(paragraphs)
            fresh.internal_note = note_text
            fresh.draft_run_id = run.run_id
            fresh.composing = False
            fresh.stage = "composed"
            fresh.last_error = (
                None
                if run.status in {"completed"}
                else "The draft was saved but needs attention. Open its checks before submitting."
            )
            for assertion in fresh.assertions:
                paragraph = next((p for p in paragraphs if p.assertion_id == assertion.assertion_id), None)
                assertion.reply_paragraph_id = paragraph.paragraph_id if paragraph else None
                if paragraph and paragraph.step_id and paragraph.step_id not in assertion.step_ids:
                    assertion.step_ids.append(paragraph.step_id)
                assertion.source_ids = sorted(
                    {sid for ref in assertion.clause_refs for sid in clause_sources.get(normalize_cited_clause(ref) or ref, [])}
                )
            for link in fresh.chain:
                if link.kind == "drafting" and link.status != "unavailable":
                    link.status, link.skill_version, link.reason = "done", letters_skill_version, None

        await self._mutate(letter_id, _done, user_id)

    async def _validators_for(self, skill_key: str, board: PositionBoard) -> List[str]:
        version, _binding, _reason = await self.registry.resolve(skill_key, board.organization_id, board.project_id)
        return list(version.manifest.validators) if version else []


class _SingleDocumentService:
    """Lets IncomingLetterAnalyzer read a document this service already loaded and authorised."""

    def __init__(self, document: Dict[str, Any]) -> None:
        fields = dict(document)
        fields["from_"] = fields.get("from")
        self._document = SimpleNamespace(**fields)

    async def get_documents_by_ids(self, _ids: List[str]) -> List[Any]:
        return [self._document]


def _trigger_issue_type(trigger: Optional[Dict[str, Any]]) -> str:
    """Issue type of a letter started from a record rather than from a letter."""
    kind = (trigger or {}).get("type")
    if kind == "variation":
        return "variation"
    if kind == "claim":
        # A delay claim routes to the EOT skill; any other claim to the claims skill.
        return "delay" if (trigger or {}).get("claim_type") == "eot" else "claim"
    return "other"


def _find(board: PositionBoard, assertion_id: str) -> Assertion:
    for assertion in board.assertions:
        if assertion.assertion_id == assertion_id:
            return assertion
    raise HTTPException(status_code=404, detail="Assertion not found")


def _next_id(board: PositionBoard) -> str:
    highest = 0
    for assertion in board.assertions:
        digits = assertion.assertion_id[1:]
        if digits.isdigit():
            highest = max(highest, int(digits))
    return f"A{highest + 1}"


def _reorder(assertions: List[Assertion]) -> None:
    """Reading order follows the letter; assertions without a span keep their place at the end."""
    assertions.sort(key=lambda item: (item.span.start if item.span else 10**9, item.order or 10**6))
    for index, assertion in enumerate(assertions, start=1):
        assertion.order = index


async def bench_approval_blockers(db: Any, run: DraftRun) -> List[str]:
    """Why a Bench-composed run may not be approved yet. Empty for non-Bench runs."""
    if db is None or getattr(run, "board_version", None) is None:
        return []
    doc = await db[BOARDS].find_one({"letter_id": str(run.letter_id)})
    if not doc:
        return []
    board = PositionBoard(**doc)
    blockers: List[str] = []
    undecided = [a.assertion_id for a in board.assertions if not a.verdict]
    if undecided:
        blockers.append(f"Undecided positions on the Letter Bench: {', '.join(undecided)}")
    if any(a.state != "locked" for a in board.assertions):
        blockers.append("Positions were changed after they were locked. Lock them again and recompose.")
    if any(p.stale for p in board.reply_paragraphs):
        blockers.append("Some reply paragraphs are out of date. Re-run them on the Letter Bench.")
    if board.draft_run_id and board.draft_run_id != run.run_id and run.mode == "draft":
        blockers.append("A newer draft has been composed on the Letter Bench.")
    return blockers


__all__ = [
    "BOARDS",
    "STEPS",
    "BenchRunContext",
    "LetterBenchService",
    "bench_approval_blockers",
    "bench_rollout",
]
