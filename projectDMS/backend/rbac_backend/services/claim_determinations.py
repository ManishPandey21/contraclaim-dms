"""Determinations: the official findings on a claim.

A determination is one of two things, told apart by ``origin``:

- **authored**: made in this workspace. Only a project that acts as Engineer
  or Employer can author one, only a person holding determination authority
  can draft it, and a second such person approves it. Approved, it is
  immutable; a change is a new revision that supersedes it.
- **received**: a transcription of a determination issued by the other side,
  tied to the document it arrived in. A Contractor's workspace records these.
  Recorded, it is immutable.

The division of labour is the Bench's. Records decide facts: the days and the
amount offered beside each head come from the delay assessment and the quantum
build-up. The skill, when a published version exists, proposes a result and
its reasons and never a figure. A person makes the finding.

Nothing here writes to the claim, its board, a linked record or another
determination. Authorisation is the router's job.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..models.claim import ELEMENT_KEYS, ClaimBench, ClaimHead, HeadElement
from ..models.claim_findings import (
    ClaimDetermination,
    DeterminationEvent,
    FindingProposal,
    HeadFinding,
    InformationRequest,
)
from ..models.claim_skill_outputs import CLAIM_DETERMINATION_SCHEMA_KEY, ClaimDeterminationOutput
from ..utils.datetime import now_utc
from .claim_delay_lab import ClaimDelayLab, build_view
from .claim_quantum import ClaimQuantum
from .letter_drafting.prompts import UNTRUSTED_CONTENT_GUARD, register_default_template
from .skills.system_skills import CLAIMS_SKILL_KEY

logger = logging.getLogger(__name__)

DETERMINATIONS = "claim_determinations"
AUTHORING_CAPACITIES = {"engineer", "employer"}
_RESULTS = {"full", "partial", "none", "insufficient_information"}
_ELEMENT_LABEL = {
    "event": "Event",
    "notice": "Notice",
    "contractual_basis": "Contractual basis",
    "causation": "Causation",
    "quantum": "Quantum",
}

CLAIM_DETERMINATION_PROMPT = "claims.v1.determination"
CLAIM_DETERMINATION_TEMPLATE = (
    """TASK: apply the skill above to ONE head of claim, from the position of the {role}.

{purpose}

Say whether the head is made out in full, in part, not at all, or cannot be
determined on the information held (result: full, partial, none or
insufficient_information), and give the reasons.

The position on the record. These are established and you must not contradict them:
{facts}

<head_of_claim>
Title: {title}
Kind: {kind}

{event_summary}
</head_of_claim>

OUTPUT: return one JSON object and nothing else, with exactly these keys:
{output_keys}

Rules:
- Never state a number of days or an amount of money. Figures are taken from the
  delay assessment and the quantum build-up by a person.
- Never invent facts, dates, clause wording or references.
- If an element on the record is missing or weak, the result cannot be "full".
- A late or unverified notice is never, on its own, a reason for "none".
- Put what must be supplied before a finding can be made in information_required.

"""
    + UNTRUSTED_CONTENT_GUARD
    + "\n"
)
register_default_template(CLAIM_DETERMINATION_PROMPT, CLAIM_DETERMINATION_TEMPLATE)


# --------------------------------------------------------------------------- #
# Requests and responses
# --------------------------------------------------------------------------- #
class DeterminationCreateRequest(BaseModel):
    origin: str = "authored"
    # Received only.
    issued_by: Optional[str] = None
    source_document_id: Optional[str] = None
    reference: Optional[str] = Field(default=None, max_length=200)
    determination_date: Optional[datetime] = None


class HeadFindingRequest(BaseModel):
    version: int = Field(..., ge=0)
    result: Optional[str] = None
    days: Optional[int] = Field(default=None, ge=0)
    amount: Optional[float] = Field(default=None, ge=0)
    findings: str = Field(default="", max_length=6000)
    residual_risks: List[str] = Field(default_factory=list)
    information_required: List[InformationRequest] = Field(default_factory=list)


class DeterminationVersionRequest(BaseModel):
    version: int = Field(..., ge=0)
    comment: Optional[str] = Field(default=None, max_length=2000)


class DeterminationSubmitRequest(DeterminationVersionRequest):
    reviewer_id: str = Field(..., min_length=1)


ApproverRule = Callable[[str], Awaitable[bool]]


class HeadFigures(BaseModel):
    """What the record holds for a head, offered beside the finding. Never written into it."""

    head_id: str
    days_claimed: Optional[int] = None
    # The extension the delay assessment arrives at, when every overlap is decided.
    days_assessed: Optional[int] = None
    days_assessed_low: Optional[int] = None
    days_assessed_high: Optional[int] = None
    amount_claimed: Optional[float] = None
    amount_supported: Optional[float] = None
    amount_locked: Optional[float] = None


def _refusal(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "user_message": message, "detail": message})


class DeterminationView(BaseModel):
    determination: ClaimDetermination
    figures: List[HeadFigures] = Field(default_factory=list)
    # The board has changed since a draft's findings were made.
    board_changed: bool = False
    editable: bool = False
    blockers: List[str] = Field(default_factory=list)
    #: The viewer is the named reviewer of a determination in review: only they may
    #: approve it or send it back (D-13, 4B-26).
    may_decide: bool = False
    #: In review, and the named reviewer still holds determination authority as a
    #: member of the project, read now (4B-26). None when not in review.
    reviewer_eligible: Optional[bool] = None
    #: The viewer made or submitted it, and its reviewer has lost the authority:
    #: they may send it to another approver.
    may_reassign: bool = False


class DeterminationsResponse(BaseModel):
    claim_id: str
    #: The claim's project, for the approver picker (people holding authority there).
    project_id: Optional[str] = None
    # The party this project acts for.
    perspective: Optional[str] = None
    can_author: bool = False
    cannot_author_reason: Optional[str] = None
    items: List[DeterminationView] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Pure logic
# --------------------------------------------------------------------------- #
def head_position(head: ClaimHead) -> str:
    """The decided state of a head's five elements, as text for a prompt or a reader."""
    lines: List[str] = []
    for key in ELEMENT_KEYS:
        element = head.elements.get(key) or HeadElement()
        if element.state is not None:
            line = f"- {_ELEMENT_LABEL[key]}: decided {element.state.replace('_', ' ')}"
            if element.note:
                line += f" ({element.note})"
        elif element.proposed_state is not None:
            line = f"- {_ELEMENT_LABEL[key]}: not decided; record shows {element.proposed_state.replace('_', ' ')}"
        else:
            line = f"- {_ELEMENT_LABEL[key]}: not assessed"
        lines.append(line)
    lines.append(f"- Classification: {(head.classification or 'not decided').replace('_', ' ')}")
    return "\n".join(lines)


def record_proposal(head: ClaimHead) -> ClaimDeterminationOutput:
    """What the board alone supports. Never proposes an entitlement.

    This is the proposal when no published skill is available, and what a
    skill's proposal is held to when one is.
    """
    gaps: List[str] = []
    for key in ELEMENT_KEYS:
        element = head.elements.get(key) or HeadElement()
        state = element.state or element.proposed_state
        if state in (None, "weak", "missing"):
            reason = element.note or element.proposed_reason or "not assessed"
            gaps.append(f"{_ELEMENT_LABEL[key]}: {reason}")
    for item in head.missing_information:
        if item not in gaps:
            gaps.append(item)
    if gaps:
        findings = (
            "No finding is proposed. The board does not yet establish every element of this head; "
            "what is outstanding is listed as information required."
        )
    else:
        findings = (
            "No finding is proposed. Every element of this head is decided as supported or not applicable "
            "on the board; whether that amounts to an entitlement is for the person determining it."
        )
    return ClaimDeterminationOutput(
        result="insufficient_information",
        findings=findings,
        residual_risks=list(head.residual_risks)[:20],
        information_required=gaps[:20],
        clause_refs=list(head.clause_refs),
    )


def hold_to_record(proposed: ClaimDeterminationOutput, head: ClaimHead) -> ClaimDeterminationOutput:
    """A skill cannot propose "full" over an element the board holds as weak or missing."""
    result = proposed.model_copy(deep=True)
    short = [
        _ELEMENT_LABEL[key]
        for key in ELEMENT_KEYS
        if ((head.elements.get(key) or HeadElement()).state or (head.elements.get(key) or HeadElement()).proposed_state)
        in (None, "weak", "missing")
    ]
    if short and result.result == "full":
        result.result = "partial"
        result.findings = (
            f"{result.findings} Record check: not proposed as made out in full, because the board holds "
            f"{', '.join(short).lower()} as weak, missing or undecided."
        )
    result.clause_refs = [ref for ref in result.clause_refs if ref in set(head.clause_refs)]
    return result


def validate_finding(head: ClaimHead, request: HeadFindingRequest) -> None:
    if request.result is not None and request.result not in _RESULTS:
        raise HTTPException(status_code=422, detail="result must be full, partial, none or insufficient_information")
    has_figure = bool(request.days) or bool(request.amount)
    if request.result in {"none", "insufficient_information"} and has_figure:
        raise HTTPException(
            status_code=422,
            detail="A head found as not made out, or not yet determinable, cannot carry days or an amount.",
        )
    if request.result == "insufficient_information" and not request.information_required:
        raise HTTPException(
            status_code=422,
            detail="Say what information is required before this head can be determined.",
        )
    if request.days is not None and head.kind == "cost":
        raise HTTPException(status_code=422, detail="This head claims cost only; it cannot be given days.")
    if request.amount is not None and head.kind == "time":
        raise HTTPException(status_code=422, detail="This head claims time only; it cannot be given an amount.")


def submit_blockers(determination: ClaimDetermination) -> List[str]:
    blockers: List[str] = []
    undecided = [finding.head_id for finding in determination.heads if finding.result is None]
    if not determination.heads:
        blockers.append("The claim has no heads to determine")
    if undecided:
        blockers.append("No finding yet on: " + ", ".join(undecided))
    for finding in determination.heads:
        if finding.result is not None and not finding.findings.strip():
            blockers.append(f"{finding.head_id}: give the reasons for the finding")
    return blockers


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class ClaimDeterminations:
    def __init__(self, db: Any, bench: Any, approver_rule: Optional[ApproverRule] = None) -> None:
        """``approver_rule`` is the approver picker's own rule (a project member holding
        determination authority by name; PickerService.is_eligible), read at the time of
        each decision, not only when the reviewer was named (4B-26)."""
        self.db = db
        self.bench = bench
        self.approver_rule = approver_rule

    async def _approver_eligible(self, user_id: Optional[str]) -> bool:
        if self.approver_rule is None:
            raise RuntimeError("ClaimDeterminations needs the approver rule to decide or reassign")
        return bool(user_id) and await self.approver_rule(str(user_id))

    @property
    def _collection(self) -> Any:
        return self.db[DETERMINATIONS]

    # --------------------------------------------------------------- reads
    async def figures(self, claim: Dict[str, Any], bench: ClaimBench) -> List[HeadFigures]:
        lab = ClaimDelayLab(self.db, self.bench)
        quantum = ClaimQuantum(self.db, self.bench)
        links = await lab.links_for_claim(str(claim["_id"]))
        result: List[HeadFigures] = []
        for head in bench.heads:
            head_links = [link for link in links if link.head_id == head.head_id]
            figures = HeadFigures(
                head_id=head.head_id, days_claimed=head.eot_days_claimed, amount_claimed=head.amount_claimed
            )
            if head_links:
                view = build_view(head, head_links, await lab.current_revisions(head_links))
                figures.days_assessed = view.net.days if view.net.settled else None
                figures.days_assessed_low, figures.days_assessed_high = view.net.low, view.net.high
            if head.quantum_lines:
                build_up = await quantum.view(claim, head.head_id)
                figures.amount_supported = build_up.supported_total
                figures.amount_locked = build_up.locked_total
            result.append(figures)
        return result

    async def _view(
        self,
        claim: Dict[str, Any],
        determination: ClaimDetermination,
        figures: List[HeadFigures],
        viewer_id: Optional[str] = None,
    ) -> DeterminationView:
        bench = self.bench._bench_of(claim)
        editable = determination.status in {"draft", "returned"}
        reviewer_eligible: Optional[bool] = None
        if determination.status == "in_review" and self.approver_rule is not None:
            reviewer_eligible = await self._approver_eligible(determination.reviewer_id)
        authors = {determination.created_by, determination.submitted_by}
        return DeterminationView(
            determination=determination,
            figures=figures,
            board_changed=bool(
                editable and bench is not None and determination.board_version is not None
                and bench.board_version != determination.board_version
            ),
            editable=editable,
            may_decide=bool(
                viewer_id and determination.status == "in_review" and determination.reviewer_id == viewer_id
                and reviewer_eligible
            ),
            reviewer_eligible=reviewer_eligible,
            may_reassign=bool(viewer_id and viewer_id in authors and reviewer_eligible is False),
            blockers=submit_blockers(determination) if editable else [],
        )

    async def list(
        self, claim: Dict[str, Any], *, holds_authority: bool, viewer_id: Optional[str] = None
    ) -> DeterminationsResponse:
        perspective = await self.bench._perspective(claim)
        bench = self.bench._bench_of(claim)
        rows = await self._collection.find({"claim_id": str(claim["_id"])}).to_list(length=200)
        items = [ClaimDetermination(**row) for row in rows]
        items.sort(key=lambda item: (item.created_at, item.revision), reverse=True)
        figures = await self.figures(claim, bench) if bench is not None else []
        reason = self._cannot_author_reason(perspective, holds_authority)
        return DeterminationsResponse(
            claim_id=str(claim["_id"]),
            perspective=perspective,
            can_author=reason is None,
            cannot_author_reason=reason,
            project_id=str(claim.get("project_id") or "") or None,
            items=[await self._view(claim, item, figures, viewer_id) for item in items],
        )

    @staticmethod
    def _cannot_author_reason(perspective: Optional[str], holds_authority: bool) -> Optional[str]:
        if perspective is None:
            return "State which party this project acts for before a determination can be made."
        if perspective not in AUTHORING_CAPACITIES:
            return (
                "This project acts as Contractor. A determination is made by the Engineer or the Employer; "
                "one received from them can be recorded here."
            )
        if not holds_authority:
            return "You do not hold determination authority on this project."
        return None

    async def _require_capacity(self, claim: Dict[str, Any], determination: ClaimDetermination) -> None:
        """Re-read the party the project acts for on every write (D-13, 4B-26; left here by 4B-15).

        A determination made here belongs to the party that made it: if the project now
        acts for someone else, nothing more is written to it. A received one must still
        come from the other side.
        """
        perspective = await self.bench._perspective(claim)
        if determination.origin == "authored":
            changed = perspective != determination.issued_by
        else:
            changed = perspective is not None and perspective == determination.issued_by
        if changed:
            raise _refusal(
                409, "determination_capacity_changed",
                "The party this project acts for has changed since this determination was started. "
                "It can no longer be changed or decided here.",
            )

    async def _load(self, claim: Dict[str, Any], determination_id: str) -> ClaimDetermination:
        row = await self._collection.find_one({"_id": determination_id, "claim_id": str(claim["_id"])})
        if not row:
            raise HTTPException(status_code=404, detail="Determination not found")
        return ClaimDetermination(**row)

    async def get(self, claim: Dict[str, Any], determination_id: str, viewer_id: Optional[str] = None) -> DeterminationView:
        determination = await self._load(claim, determination_id)
        bench = self.bench._bench_of(claim)
        return await self._view(claim, determination, await self.figures(claim, bench) if bench else [], viewer_id)

    # -------------------------------------------------------------- writes
    async def _save(self, determination: ClaimDetermination, expected_version: int) -> None:
        """Compare-and-set on the draft's own counter. Only drafts are ever saved."""
        determination.version = expected_version + 1
        determination.updated_at = now_utc()
        result = await self._collection.update_one(
            {
                "_id": determination.id,
                "version": expected_version,
                "status": {"$in": ["draft", "returned", "in_review"]},
            },
            {"$set": determination.model_dump(by_alias=True, exclude={"id"})},
        )
        if not getattr(result, "matched_count", 0):
            raise HTTPException(
                status_code=409,
                detail="This determination changed since you loaded it, or is no longer open to change.",
            )

    def _editable(self, determination: ClaimDetermination, version: int) -> None:
        if determination.status not in {"draft", "returned"}:
            raise HTTPException(
                status_code=409,
                detail=(
                    "An approved or recorded determination cannot be changed. Start a new revision instead."
                    if determination.status in {"approved", "recorded"}
                    else "This determination is with its reviewer and cannot be changed until it is returned."
                ),
            )
        if determination.version != version:
            raise HTTPException(status_code=409, detail="This determination changed since you loaded it.")

    async def create(
        self,
        claim: Dict[str, Any],
        request: DeterminationCreateRequest,
        user_id: Optional[str],
        *,
        holds_authority: bool,
        allowed_documents: Optional[set] = None,
        supersedes: Optional[ClaimDetermination] = None,
    ) -> DeterminationView:
        bench = self.bench._require_bench(claim)
        perspective = await self.bench._perspective(claim)
        if request.origin not in {"authored", "received"}:
            raise HTTPException(status_code=422, detail="origin must be authored or received")
        if request.origin == "authored":
            reason = self._cannot_author_reason(perspective, holds_authority)
            if reason:
                raise HTTPException(status_code=403 if perspective in AUTHORING_CAPACITIES else 409, detail=reason)
            issued_by = perspective
            open_draft = await self._collection.find_one(
                {"claim_id": str(claim["_id"]), "origin": "authored", "status": {"$in": ["draft", "returned", "in_review"]}}
            )
            if open_draft:
                raise HTTPException(
                    status_code=409,
                    detail="A determination is already being prepared for this claim. Finish or continue that one.",
                )
        else:
            if request.issued_by not in AUTHORING_CAPACITIES:
                raise HTTPException(status_code=422, detail="Say who issued the determination: engineer or employer")
            if perspective is not None and request.issued_by == perspective:
                raise HTTPException(
                    status_code=409,
                    detail=f"This project acts as {perspective.title()}; its own determinations are authored here, not recorded as received.",
                )
            if not request.source_document_id or str(request.source_document_id) not in (allowed_documents or set()):
                raise HTTPException(
                    status_code=422,
                    detail="A received determination must be tied to the document it arrived in, linked to this claim.",
                )
            issued_by = request.issued_by
        previous = await self._collection.find({"claim_id": str(claim["_id"]), "origin": request.origin}).to_list(length=500)
        determination = ClaimDetermination(
            organization_id=claim.get("organization_id"),
            project_id=claim.get("project_id"),
            claim_id=str(claim["_id"]),
            origin=request.origin,  # type: ignore[arg-type]
            revision=max([int(row.get("revision") or 0) for row in previous] or [0]) + 1,
            supersedes_id=supersedes.id if supersedes else None,
            issued_by=issued_by,  # type: ignore[arg-type]
            heads=[
                (self._carried(head, supersedes) if supersedes else HeadFinding(head_id=head.head_id, head_title=head.title))
                for head in bench.heads
            ],
            board_version=bench.board_version,
            source_document_id=str(request.source_document_id) if request.origin == "received" else None,
            reference=(request.reference or "").strip() or None,
            determination_date=request.determination_date,
            created_by=user_id,
            history=[DeterminationEvent(action="revised" if supersedes else "created", actor_id=user_id)],
        )
        await self._collection.insert_one(determination.model_dump(by_alias=True))
        return await self._view(claim, determination, await self.figures(claim, bench), user_id)

    @staticmethod
    def _carried(head: ClaimHead, previous: ClaimDetermination) -> HeadFinding:
        """A new revision starts from the approved findings' reasons and figures, but every
        result is cleared: each head must be decided again before it can be submitted (D-13)."""
        for finding in previous.heads:
            if finding.head_id == head.head_id:
                copy = finding.model_copy(deep=True)
                copy.head_title = head.title
                copy.result = None
                copy.decided_by, copy.decided_at, copy.proposal = None, None, None
                return copy
        return HeadFinding(head_id=head.head_id, head_title=head.title)

    async def revise(
        self, claim: Dict[str, Any], determination_id: str, user_id: Optional[str], *, holds_authority: bool
    ) -> DeterminationView:
        previous = await self._load(claim, determination_id)
        if previous.origin != "authored" or previous.status != "approved":
            raise HTTPException(status_code=409, detail="Only an approved determination made here can be revised.")
        later = await self._collection.find_one(
            {"claim_id": str(claim["_id"]), "origin": "authored", "revision": {"$gt": previous.revision}}
        )
        if later:
            raise _refusal(
                409, "determination_superseded",
                f"Revision {previous.revision} has already been revised. Only the latest revision can be revised.",
            )
        await self._require_capacity(claim, previous)
        return await self.create(
            claim, DeterminationCreateRequest(origin="authored"), user_id,
            holds_authority=holds_authority, supersedes=previous,
        )

    async def set_finding(
        self, claim: Dict[str, Any], determination_id: str, head_id: str, request: HeadFindingRequest, user_id: Optional[str]
    ) -> DeterminationView:
        determination = await self._load(claim, determination_id)
        self._editable(determination, request.version)
        await self._require_capacity(claim, determination)
        bench = self.bench._require_bench(claim)
        head = next((item for item in bench.heads if item.head_id == head_id), None)
        finding = next((item for item in determination.heads if item.head_id == head_id), None)
        if head is None or finding is None:
            raise HTTPException(status_code=404, detail="Head of claim not found on this determination")
        validate_finding(head, request)
        finding.result = request.result  # type: ignore[assignment]
        finding.days = request.days
        finding.amount = request.amount
        finding.findings = request.findings.strip()
        finding.residual_risks = [item.strip() for item in request.residual_risks if item.strip()][:20]
        finding.information_required = request.information_required[:20]
        finding.decided_by = user_id if request.result is not None else None
        finding.decided_at = now_utc() if request.result is not None else None
        await self._save(determination, request.version)
        return await self._view(claim, determination, await self.figures(claim, bench), user_id)

    async def propose(
        self, claim: Dict[str, Any], determination_id: str, request: DeterminationVersionRequest, user_id: Optional[str]
    ) -> DeterminationView:
        """Put a proposal beside each head: the skill's if one is published, else the record's."""
        determination = await self._load(claim, determination_id)
        self._editable(determination, request.version)
        if determination.origin != "authored":
            raise HTTPException(status_code=409, detail="A received determination is transcribed, not proposed.")
        await self._require_capacity(claim, determination)
        bench = self.bench._require_bench(claim)
        role = determination.issued_by
        known = {finding.head_id: finding for finding in determination.heads}
        for head in bench.heads:
            finding = known.get(head.head_id)
            if finding is None:
                finding = HeadFinding(head_id=head.head_id, head_title=head.title)
                determination.heads.append(finding)
            finding.head_title = head.title
            floor = record_proposal(head)
            position = head_position(head)
            step = await self.bench.runner.run(
                skill_key=CLAIMS_SKILL_KEY,
                subject_type="claim_head",
                subject_id=f"{claim['_id']}:{head.head_id}:determination",
                # Kept apart from the head tests, which the Bench lists by claim.
                parent_id=f"{claim['_id']}:findings",
                organization_id=str(claim.get("organization_id") or "") or None,
                project_id=claim.get("project_id"),
                role=role,
                prompt_key=CLAIM_DETERMINATION_PROMPT,
                inputs={"facts": position, "title": head.title, "kind": head.kind, "event_summary": head.event_summary},
                payload={
                    "purpose": "You are preparing a recommendation for a determination. A person makes the finding.",
                    "facts": position,
                    "title": head.title,
                    "kind": head.kind.replace("_", " "),
                    "event_summary": head.event_summary or "(no description recorded)",
                },
                fallback=lambda floor=floor: floor,
                schema_key=CLAIM_DETERMINATION_SCHEMA_KEY,
                user_id=user_id,
            )
            output = ClaimDeterminationOutput(**step.output)
            if not step.degraded:
                output = hold_to_record(output, head)
            finding.proposal = FindingProposal(
                result=output.result,
                findings=output.findings,
                residual_risks=output.residual_risks[:20],
                information_required=output.information_required[:20],
                proposed_by="record_check" if step.degraded else "skill",
                step_id=step.step_id,
            )
        determination.heads = [finding for finding in determination.heads if finding.head_id in {h.head_id for h in bench.heads}]
        determination.board_version = bench.board_version
        await self._save(determination, request.version)
        return await self._view(claim, determination, await self.figures(claim, bench), user_id)

    async def _transition(
        self,
        claim: Dict[str, Any],
        determination: ClaimDetermination,
        version: int,
        action: str,
        user_id: Optional[str],
        comment: Optional[str],
    ) -> DeterminationView:
        determination.history.append(DeterminationEvent(action=action, actor_id=user_id, comment=(comment or "").strip() or None))
        await self._save(determination, version)
        logger.info("claim determination %s: %s by %s", determination.id, action, user_id)
        bench = self.bench._bench_of(claim)
        return await self._view(claim, determination, await self.figures(claim, bench) if bench else [], user_id)

    async def submit(
        self,
        claim: Dict[str, Any],
        determination_id: str,
        request: DeterminationSubmitRequest,
        user_id: Optional[str],
        *,
        reviewer_eligible: Callable[[str], Awaitable[bool]],
    ) -> DeterminationView:
        """Send for approval to a named reviewer who exists, is a member of the project and
        holds determination authority by name (D-13). ``reviewer_eligible`` is the picker's
        own rule (services/picker_service.PickerService.is_eligible)."""
        determination = await self._load(claim, determination_id)
        self._editable(determination, request.version)
        if determination.origin != "authored":
            raise HTTPException(status_code=409, detail="A received determination is recorded, not submitted for approval.")
        blockers = submit_blockers(determination)
        if blockers:
            raise HTTPException(status_code=409, detail="; ".join(blockers))
        await self._require_capacity(claim, determination)
        authors = {determination.created_by, user_id} | {finding.decided_by for finding in determination.heads}
        if request.reviewer_id in authors:
            raise HTTPException(status_code=422, detail="The reviewer must be someone other than the person who made the findings.")
        if not await reviewer_eligible(request.reviewer_id):
            raise _refusal(
                422, "determination_reviewer_not_authorised",
                "That person cannot approve this determination: the approver must be a member of the project "
                "who holds determination authority.",
            )
        determination.status = "in_review"
        determination.reviewer_id = request.reviewer_id
        determination.submitted_by, determination.submitted_at = user_id, now_utc()
        return await self._transition(claim, determination, request.version, "submitted", user_id, request.comment)

    def _guard_decider(self, determination: ClaimDetermination, version: int, user_id: Optional[str]) -> None:
        if determination.status != "in_review":
            raise HTTPException(status_code=409, detail="Only a determination that is in review can be approved or returned.")
        if determination.version != version:
            raise HTTPException(status_code=409, detail="This determination changed since you loaded it.")
        authors = {determination.created_by, determination.submitted_by} | {f.decided_by for f in determination.heads}
        if user_id is None or user_id in authors:
            raise HTTPException(status_code=403, detail="A determination cannot be approved or returned by a person who made its findings.")
        if user_id != determination.reviewer_id:
            raise _refusal(
                403, "determination_not_the_reviewer",
                "Only the person this determination was sent to can approve it or send it back.",
            )

    async def _require_still_approver(self, user_id: Optional[str]) -> None:
        """The named reviewer must still be an approver when they decide (4B-26): a member
        of the project holding determination authority by name, read now."""
        if not await self._approver_eligible(user_id):
            raise _refusal(
                403, "determination_reviewer_no_longer_authorised",
                "You can no longer approve or return this determination: you no longer hold determination "
                "authority as a member of this project. The person who sent it can send it to another approver.",
            )

    async def reassign(
        self, claim: Dict[str, Any], determination_id: str, request: DeterminationSubmitRequest, user_id: Optional[str]
    ) -> DeterminationView:
        """Send a determination in review to another approver, only when its reviewer no
        longer holds the authority. The findings are not touched (4B-26)."""
        determination = await self._load(claim, determination_id)
        if determination.status != "in_review":
            raise HTTPException(status_code=409, detail="Only a determination that is in review can be sent to another approver.")
        if determination.version != request.version:
            raise HTTPException(status_code=409, detail="This determination changed since you loaded it.")
        if user_id is None or user_id not in {determination.created_by, determination.submitted_by}:
            raise _refusal(
                403, "determination_reassign_not_sender",
                "Only the person who made or sent this determination can send it to another approver.",
            )
        await self._require_capacity(claim, determination)
        if await self._approver_eligible(determination.reviewer_id):
            raise _refusal(
                409, "determination_reviewer_still_authorised",
                "The approver this was sent to can still decide it. It can go to another approver only if "
                "they lose determination authority on this project.",
            )
        authors = {determination.created_by, determination.submitted_by, user_id} | {
            finding.decided_by for finding in determination.heads
        }
        if request.reviewer_id in authors:
            raise HTTPException(status_code=422, detail="The reviewer must be someone other than the person who made the findings.")
        if not await self._approver_eligible(request.reviewer_id):
            raise _refusal(
                422, "determination_reviewer_not_authorised",
                "That person cannot approve this determination: the approver must be a member of the project "
                "who holds determination authority.",
            )
        previous = determination.reviewer_id
        determination.reviewer_id = request.reviewer_id
        comment = (request.comment or "").strip() or f"Sent to another approver; {previous} no longer holds the authority."
        return await self._transition(claim, determination, request.version, "reassigned", user_id, comment)

    async def approve(
        self, claim: Dict[str, Any], determination_id: str, request: DeterminationVersionRequest, user_id: Optional[str]
    ) -> DeterminationView:
        determination = await self._load(claim, determination_id)
        self._guard_decider(determination, request.version, user_id)
        await self._require_capacity(claim, determination)
        await self._require_still_approver(user_id)
        determination.status = "approved"
        determination.decided_by, determination.decided_at = user_id, now_utc()
        return await self._transition(claim, determination, request.version, "approved", user_id, request.comment)

    async def send_back(
        self, claim: Dict[str, Any], determination_id: str, request: DeterminationVersionRequest, user_id: Optional[str]
    ) -> DeterminationView:
        determination = await self._load(claim, determination_id)
        self._guard_decider(determination, request.version, user_id)
        await self._require_capacity(claim, determination)
        await self._require_still_approver(user_id)
        if not (request.comment or "").strip():
            raise HTTPException(status_code=422, detail="Say what needs to change.")
        determination.status = "returned"
        determination.decided_by, determination.decided_at = user_id, now_utc()
        return await self._transition(claim, determination, request.version, "returned", user_id, request.comment)

    async def record_received(
        self, claim: Dict[str, Any], determination_id: str, request: DeterminationVersionRequest, user_id: Optional[str]
    ) -> DeterminationView:
        """Close the transcription of a received determination. It does not change afterwards."""
        determination = await self._load(claim, determination_id)
        self._editable(determination, request.version)
        if determination.origin != "received":
            raise HTTPException(status_code=409, detail="A determination made here goes through approval.")
        await self._require_capacity(claim, determination)
        blockers = submit_blockers(determination)
        if blockers:
            raise HTTPException(status_code=409, detail="; ".join(blockers))
        determination.status = "recorded"
        determination.decided_by, determination.decided_at = user_id, now_utc()
        return await self._transition(claim, determination, request.version, "recorded", user_id, request.comment)


__all__ = [
    "AUTHORING_CAPACITIES",
    "CLAIM_DETERMINATION_PROMPT",
    "DETERMINATIONS",
    "ClaimDeterminations",
    "DeterminationCreateRequest",
    "DeterminationSubmitRequest",
    "DeterminationVersionRequest",
    "DeterminationView",
    "DeterminationsResponse",
    "HeadFigures",
    "HeadFindingRequest",
    "head_position",
    "hold_to_record",
    "record_proposal",
    "submit_blockers",
    "validate_finding",
]


# --------------------------------------------------------------------------- #
# Letters from a determination (4B-31)
# --------------------------------------------------------------------------- #
LetterPurpose = Literal["determination", "information_request"]
_LETTER_RESULT = {
    "full": "Entitlement established in full",
    "partial": "Entitlement established in part",
    "none": "No entitlement established",
    "insufficient_information": "Not determined: further information is required",
}
_RFI_STATUSES = {"draft", "returned", "in_review", "approved"}


class DeterminationLetterSource(BaseModel):
    """What a letter started from a determination carries onto the Letter Bench.

    Only the determination's own content: its findings and the information it
    asks for, each item with its deadline and what follows if it is not
    supplied. Nothing from a simulation or the strategy note (both privileged)
    and no figure the determination does not hold.
    """

    organization_id: str
    project_id: Optional[str] = None
    role: str
    title: str
    subject: str
    trigger: Dict[str, Any]
    text: str


def _day(value: Optional[datetime]) -> Optional[str]:
    return value.date().isoformat() if value else None


def _amount(value: float) -> str:
    from .claim_submission import format_paise

    return format_paise(int(round(float(value) * 100)))


def information_items(determination: ClaimDetermination) -> List[Dict[str, Any]]:
    """Every information item of the determination, head by head, earliest deadline first."""
    items = [
        {
            "head_id": head.head_id,
            "head_title": head.head_title,
            "item": request.item,
            "deadline": _day(request.deadline),
            "consequence": request.consequence,
        }
        for head in determination.heads
        for request in head.information_required
    ]
    items.sort(key=lambda entry: (entry["deadline"] is None, entry["deadline"] or ""))
    return items


def letter_source(
    claim: Dict[str, Any], determination: ClaimDetermination, purpose: LetterPurpose
) -> DeterminationLetterSource:
    """Pure: the title, subject, structured context and text of the letter.

    Refuses what must not be issued: a determination letter only from an approved,
    authored determination; a request for information only from an authored one
    still open or approved that asks for something.
    """
    if determination.origin != "authored":
        raise _refusal(
            409, "determination_letter_not_authored",
            "A letter can be issued only from a determination made in this workspace.",
        )
    items = information_items(determination)
    if purpose == "determination" and determination.status != "approved":
        raise _refusal(
            409, "determination_letter_not_approved",
            "The determination letter can be issued once the determination has been approved.",
        )
    if purpose == "information_request":
        if determination.status not in _RFI_STATUSES:
            raise _refusal(409, "determination_letter_not_open", "This determination cannot ask for information.")
        if not items:
            raise _refusal(
                409, "determination_no_information_items",
                "No information is asked for in this determination. Add the items, with their deadlines, first.",
            )
    reference = claim.get("claim_ref") or claim.get("title") or str(claim.get("_id"))
    heads: List[Dict[str, Any]] = [
        {
            "head_id": head.head_id,
            "head_title": head.head_title,
            "result": head.result,
            "days": head.days,
            "amount": head.amount,
            "findings": head.findings,
        }
        for head in determination.heads
    ]
    trigger: Dict[str, Any] = {
        "type": purpose,
        "id": determination.id,
        "label": (
            f"Determination on claim {reference} (revision {determination.revision})"
            if purpose == "determination"
            else f"Information requested on claim {reference} (revision {determination.revision})"
        ),
        "status": determination.status,
        "claim_id": str(claim.get("_id")),
        "determination_revision": determination.revision,
        "issued_by": determination.issued_by,
        "information_items": items,
    }
    lines: List[str] = [f"Claim {reference}: {claim.get('title') or ''}".strip()]
    if purpose == "determination":
        trigger["heads"] = heads
        for head in heads:
            figures = []
            if head["days"] is not None:
                figures.append(f"{head['days']} days extension of time")
            if head["amount"] is not None:
                figures.append(_amount(head["amount"]))
            lines.append(
                f"Head of claim {head['head_id']}: {head['head_title']}. "
                f"Finding: {_LETTER_RESULT.get(str(head['result']), 'No finding recorded')}."
                + (f" Determined: {'; '.join(figures)}." if figures else "")
                + (f" Reasons: {head['findings']}" if head["findings"] else "")
            )
    if items:
        lines.append("Information required:")
        for entry in items:
            lines.append(
                f"- {entry['item']} (head {entry['head_id']})"
                + (f", by {entry['deadline']}" if entry["deadline"] else ", no date set")
                + (f". If not supplied: {entry['consequence']}" if entry["consequence"] else "")
            )
    title = (
        f"Determination of claim {reference}" if purpose == "determination"
        else f"Request for information on claim {reference}"
    )
    subject = (
        f"Determination: {claim.get('title') or reference}" if purpose == "determination"
        else f"Request for information: {claim.get('title') or reference}"
    )
    return DeterminationLetterSource(
        organization_id=str(claim.get("organization_id") or ""),
        project_id=str(claim.get("project_id") or "") or None,
        role=determination.issued_by,
        title=title,
        subject=subject,
        trigger=trigger,
        text="\n\n".join(line for line in lines if line),
    )


async def load_letter_source(
    db: Any, bench: Any, claim: Dict[str, Any], determination_id: str, purpose: LetterPurpose
) -> DeterminationLetterSource:
    """The determination, checked as current and still in the project's capacity."""
    service = ClaimDeterminations(db, bench)
    determination = await service._load(claim, determination_id)
    if await service._collection.find_one({"supersedes_id": determination.id}):
        raise _refusal(
            409, "determination_letter_superseded",
            "A later revision of this determination exists. Issue the letter from the latest revision.",
        )
    source = letter_source(claim, determination, purpose)
    await service._require_capacity(claim, determination)
    return source
