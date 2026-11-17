"""Claim Bench: a claim worked as a board of heads.

Each head of claim is tested on five elements: event, notice, contractual
basis, causation and quantum. The division of labour is fixed:

- **Records decide facts.** Whether notice was served and when, which cited
  clauses exist in the project's clause index, what amount or days are
  recorded: these are read from the claim and the contract index by code.
- **The skill proposes a view.** When a published version of the claims skill
  exists it reads those facts and proposes a state and a reason for each
  element. It cannot contradict a recorded fact; guards below hold it to them.
  When no published version exists the record checks alone make the proposal,
  the step is marked degraded and says why, and no proposal is ever
  "supported".
- **A person decides.** A proposal is not a decision. The board locks only
  when a person has decided every element and the classification of every head.

Edits use compare-and-set on ``bench.board_version``; a lost race is a 409.
Authorisation is the router's job: every method here takes a claim the caller
has already been authorised to act on.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..core.config import settings
from ..models.claim import (
    ELEMENT_KEYS,
    ClaimBench,
    ClaimHead,
    FigureField,
    FigureToReenter,
    HeadElement,
    NoticeAssessment,
)
from ..models.claim_notice import NoticeRequirement
from ..models.claim_skill_outputs import CLAIM_HEAD_TEST_SCHEMA_KEY, ClaimHeadTestOutput, ElementFinding
from ..models.skill import SkillRunStep
from ..utils.datetime import now_utc
from .audit_event_service import AuditEventService
from .claim_legacy import coerce_legacy_amount, coerce_legacy_days, unrecognised_status
from .claim_notice_requirements import NoticeRequirementService
from .claims_notice import assess_notice
from .letter_drafting.clause_checker import normalize_cited_clause
from .letter_drafting.prompts import UNTRUSTED_CONTENT_GUARD, PromptRegistry, register_default_template
from .skills.executor import SkillExecutor
from .skills.registry import SkillRegistry
from .skills.steps import SkillStepRunner
from .skills.system_skills import CLAIMS_SKILL_KEY
from .sla_service import SlaService

logger = logging.getLogger(__name__)

VALID_MODES = {"off", "canary", "primary"}
MAX_HEADS = 40
MAX_CLAUSE_CHARS = 2500

CLAIM_HEAD_TEST_PROMPT = "claims.v1.head_test"
CLAIM_HEAD_TEST_TEMPLATE = (
    """TASK: apply the skill above to ONE head of claim, acting for the {role}.

Test the head on five elements: event, notice, contractual_basis, causation, quantum.
For each, give a state (supported, weak, missing or not_applicable) and a short reason.

Recorded facts. These are established by the project's records and you must not
contradict them. If a fact is "not recorded", the matching element cannot be "supported":
{facts}

<head_of_claim>
Title: {title}
Kind: {kind}

{event_summary}
</head_of_claim>

Contract clauses cited for this head, as indexed for this project (the only
permitted clause wording):
{clauses}

OUTPUT: return one JSON object and nothing else, with exactly these keys:
{output_keys}

Rules:
- Never invent facts, dates, amounts, days, clause wording or references.
- Do not compute or restate any number of days or any amount that is not in the recorded facts.
- A late or unverified notice is never, on its own, a reason to deny the head; say what the record shows.
- "supported" means the records listed here establish the element. If you are relying on
  something not listed, the state is "weak" and missing_information names what is needed.
- Put what must still be obtained, and from whom, in missing_information.

"""
    + UNTRUSTED_CONTENT_GUARD
    + "\n"
)
register_default_template(CLAIM_HEAD_TEST_PROMPT, CLAIM_HEAD_TEST_TEMPLATE)


# --------------------------------------------------------------------------- #
# Requests and responses
# --------------------------------------------------------------------------- #
class ClaimBenchAvailability(BaseModel):
    enabled: bool
    rollout_mode: str


class HeadCreateRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    title: str = Field(..., min_length=1, max_length=300)
    kind: str = "time_and_cost"
    event_summary: Optional[str] = Field(default=None, max_length=4000)
    clause_refs: List[str] = Field(default_factory=list)
    amount_claimed: Optional[float] = Field(default=None, ge=0)
    eot_days_claimed: Optional[int] = Field(default=None, ge=0)


class HeadPatchRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    kind: Optional[str] = None
    event_summary: Optional[str] = Field(default=None, max_length=4000)
    clause_refs: Optional[List[str]] = None
    amount_claimed: Optional[float] = Field(default=None, ge=0)
    clear_amount: bool = False
    eot_days_claimed: Optional[int] = Field(default=None, ge=0)
    clear_eot_days: bool = False
    # 4B-19: figures carried from the claim record that this head does not need.
    figures_not_needed: List[FigureField] = Field(default_factory=list, max_length=2)
    classification: Optional[str] = None
    clear_classification: bool = False


class ElementDecisionRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    state: Optional[str] = None
    clear: bool = False
    note: Optional[str] = Field(default=None, max_length=2000)


class BoardVersionRequest(BaseModel):
    board_version: int = Field(..., ge=0)


class UnlockRequest(BaseModel):
    """Reopening a locked board (D-22): claim-manage rights and a reason, kept in the audit trail."""

    board_version: int = Field(..., ge=0)
    reason: str = Field(..., min_length=10, max_length=1000)


class HeadTestRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    force: bool = False


class ClauseCheck(BaseModel):
    clause_no: str
    found: bool
    title: Optional[str] = None
    document_type: Optional[str] = None


class BenchGate(BaseModel):
    undecided_elements: int = 0
    unclassified_heads: int = 0
    stale_tests: int = 0
    blockers: List[str] = Field(default_factory=list)
    can_lock: bool = False


class BenchSkillState(BaseModel):
    skill_key: str = CLAIMS_SKILL_KEY
    available: bool = False
    version: Optional[str] = None
    reason: Optional[str] = None


class BenchStepSummary(BaseModel):
    step_id: str
    subject_id: str
    skill_key: str
    skill_version: str = ""
    skill_name: Optional[str] = None
    role: Optional[str] = None
    degraded: bool = False
    stale: bool = False
    error: Optional[str] = None
    model: Optional[str] = None
    completed_at: Optional[datetime] = None


class ClaimBenchResponse(BaseModel):
    claim_id: str
    claim_ref: Optional[str] = None
    title: str
    claim_type: str
    status: str
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    exists: bool = True
    bench: ClaimBench
    gate: BenchGate
    steps: List[BenchStepSummary] = Field(default_factory=list)
    notice_assessment: Optional[NoticeAssessment] = None
    notice_requirement: Optional[NoticeRequirement] = None
    # The party this project acts for. None means it has not been stated, and
    # a head cannot be tested until it is: the skill never assumes a side.
    perspective: Optional[str] = None
    skill: BenchSkillState = Field(default_factory=BenchSkillState)
    read_only: bool = False
    read_only_reason: Optional[str] = None
    # The arbitration case the claim was escalated into, if any.
    arbitration_case_id: Optional[str] = None
    rollout_mode: str = "off"


def claim_bench_rollout(tenant_id: Optional[str], config: Any = settings) -> ClaimBenchAvailability:
    """Server-authoritative rollout decision. No client flag can bypass it."""
    mode = str(getattr(config, "CLAIM_BENCH_ROLLOUT_MODE", "off") or "off").strip().lower()
    if mode not in VALID_MODES:
        mode = "off"
    if mode == "primary":
        return ClaimBenchAvailability(enabled=True, rollout_mode=mode)
    if mode == "canary":
        allowed = {
            item.strip()
            for item in str(getattr(config, "CLAIM_BENCH_CANARY_TENANT_IDS", "") or "").split(",")
            if item.strip()
        }
        return ClaimBenchAvailability(enabled=bool(tenant_id and str(tenant_id) in allowed), rollout_mode=mode)
    return ClaimBenchAvailability(enabled=False, rollout_mode=mode)


# --------------------------------------------------------------------------- #
# Pure logic
# --------------------------------------------------------------------------- #
_VALID_STATES = {"supported", "weak", "missing", "not_applicable"}
_VALID_KINDS = {"time", "cost", "time_and_cost"}
_VALID_CLASSIFICATIONS = {"excusable", "compensable", "contractor_risk", "undetermined"}


def clean_clause_refs(values: Optional[List[str]]) -> List[str]:
    seen: List[str] = []
    for value in values or []:
        clause = normalize_cited_clause(value)
        if clause and clause not in seen:
            seen.append(clause[:40])
    return seen[:30]


def _legacy_clauses(value: Any) -> List[str]:
    if isinstance(value, str):
        return [part for part in re.split(r"[,;\n]", value) if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if isinstance(item, (str, int, float)) and not isinstance(item, bool)]
    return []


def initial_head(claim: Dict[str, Any]) -> ClaimHead:
    """The one head a claim starts with, carried over from its register fields.

    The record may predate the board and hold figures in any form (D-10): they
    are coerced only when that is unambiguous, otherwise left empty and listed
    for re-entry."""
    raw_amount = claim.get("amount_claimed")
    raw_days = claim.get("eot_days_claimed")
    amount, amount_recorded = coerce_legacy_amount(raw_amount)
    days, days_recorded = coerce_legacy_days(raw_days)
    # The kind follows what the record holds, including a figure awaiting re-entry.
    has_amount = amount is not None or amount_recorded is not None
    has_days = days is not None or days_recorded is not None
    if has_days and not has_amount:
        kind = "time"
    elif has_amount and not has_days and str(claim.get("type") or "") != "eot":
        kind = "cost"
    else:
        kind = "time_and_cost"
    to_reenter = []
    if amount_recorded is not None:
        to_reenter.append(FigureToReenter(field="amount_claimed", recorded=amount_recorded))
    if days_recorded is not None:
        to_reenter.append(FigureToReenter(field="eot_days_claimed", recorded=days_recorded))
    description = claim.get("description")
    return ClaimHead(
        head_id="H1",
        title=(str(claim.get("title") or "").strip() or "Head of claim")[:300],
        kind=kind,  # type: ignore[arg-type]
        event_summary=(str(description).strip()[:4000] or None) if description else None,
        clause_refs=clean_clause_refs(_legacy_clauses(claim.get("contract_clauses"))),
        amount_claimed=amount,
        eot_days_claimed=days,
        figures_to_reenter=to_reenter,
    )


def _figure_settled(head: ClaimHead, field: str) -> None:
    head.figures_to_reenter = [item for item in head.figures_to_reenter if item.field != field]


def next_head_id(bench: ClaimBench) -> str:
    used = {head.head_id for head in bench.heads}
    index = len(bench.heads) + 1
    while f"H{index}" in used:
        index += 1
    return f"H{index}"


def compute_gate(bench: ClaimBench) -> BenchGate:
    undecided = sum(
        1 for head in bench.heads for key in ELEMENT_KEYS if (head.elements.get(key) or HeadElement()).state is None
    )
    unclassified = sum(1 for head in bench.heads if head.classification is None)
    stale = sum(1 for head in bench.heads if head.test_stale)
    to_reenter = sum(len(head.figures_to_reenter) for head in bench.heads)
    blockers: List[str] = []
    if not bench.heads:
        blockers.append("Add at least one head of claim")
    if to_reenter:
        # D-10 (4B-19): a figure from the claim record that was not carried over is
        # re-entered or set aside by a person before the position is locked.
        blockers.append(
            f"{to_reenter} figure{'' if to_reenter == 1 else 's'} from the claim record "
            f"need{'s' if to_reenter == 1 else ''} re-entry"
        )
    if undecided:
        blockers.append(f"{undecided} element{'' if undecided == 1 else 's'} still need your call")
    if unclassified:
        blockers.append(
            f"{unclassified} head{'' if unclassified == 1 else 's'} not yet classified "
            "(excusable, compensable or contractor risk)"
        )
    if stale:
        # D-22 (4B-16, C-17): a test that no longer matches the facts is not a basis for a lock.
        blockers.append(
            f"{stale} head{'' if stale == 1 else 's'} tested against facts that have since changed: re-test "
            + ("it" if stale == 1 else "them")
        )
    return BenchGate(
        undecided_elements=undecided,
        unclassified_heads=unclassified,
        stale_tests=stale,
        blockers=blockers,
        can_lock=not blockers and bench.stage != "locked",
    )


def record_checks(
    claim: Dict[str, Any],
    head: ClaimHead,
    notice: Optional[NoticeAssessment],
    clause_checks: List[ClauseCheck],
    clause_index_available: bool,
) -> ClaimHeadTestOutput:
    """What the records alone establish for a head. Never proposes "supported".

    This is the proposal when no published skill is available, and the floor
    the skill's own proposal is held to when one is.
    """
    has_event_date = bool(claim.get("event_date") or claim.get("awareness_date"))
    has_summary = bool((head.event_summary or "").strip())
    if has_event_date and has_summary:
        event = ElementFinding(state="weak", reason="An event and its date are recorded. The evidence for it has not been assessed.")
    elif has_event_date or has_summary:
        missing_part = "a description of what happened" if has_event_date else "the date of the event or of becoming aware of it"
        event = ElementFinding(state="weak", reason=f"Partly recorded: {missing_part} is missing.")
    else:
        event = ElementFinding(state="missing", reason="No event is described and no event or awareness date is recorded.")

    if notice is None:
        notice_finding = ElementFinding(
            state="missing",
            reason="The notice position cannot be worked out: no event or awareness date is recorded.",
        )
    elif notice.served_on is None:
        notice_finding = ElementFinding(state="missing", reason="No notice is recorded as served.")
    else:
        verified = notice.period_source == "contract_verified"
        period = (
            f"the {notice.period_days}-day period in clause {notice.clause_no}"
            if verified
            else f"a {notice.period_days}-day period that has not been verified against the contract"
        )
        if notice.state == "served_late":
            reason = (
                f"Notice was served {notice.served_late_by_days} day(s) after {period}. "
                "Lateness alone does not dispose of the head: record what prejudice, if any, the other party suffered."
            )
        else:
            reason = f"Notice was served within {period}."
            if not verified:
                reason += " Confirm the period from the contract."
        notice_finding = ElementFinding(state="weak", reason=reason)

    if not head.clause_refs:
        basis = ElementFinding(state="missing", reason="No contract clause is cited for this head.")
    elif not clause_index_available:
        basis = ElementFinding(
            state="weak",
            reason="Clauses are cited, but this project's contract is not clause-indexed, so they could not be checked.",
        )
    else:
        absent = [check.clause_no for check in clause_checks if not check.found]
        if absent:
            basis = ElementFinding(
                state="weak",
                reason="Not found in this project's clause index: " + ", ".join(absent) + ". Confirm the references.",
            )
        else:
            basis = ElementFinding(
                state="weak",
                reason="Every cited clause exists in the clause index. Whether each applies to this event has not been assessed.",
            )

    causation = ElementFinding(
        state="missing",
        reason="The link from the event to the time or cost claimed has not been assessed.",
    )

    wants_time = head.kind in {"time", "time_and_cost"}
    wants_cost = head.kind in {"cost", "time_and_cost"}
    lacking = []
    if wants_time and head.eot_days_claimed is None:
        lacking.append("days claimed")
    if wants_cost and head.amount_claimed is None:
        lacking.append("amount claimed")
    if lacking and len(lacking) == int(wants_time) + int(wants_cost):
        quantum = ElementFinding(state="missing", reason="Not recorded: " + " and ".join(lacking) + ".")
    elif lacking:
        quantum = ElementFinding(state="weak", reason="Not recorded: " + " and ".join(lacking) + ". No build-up has been assessed.")
    else:
        quantum = ElementFinding(state="weak", reason="A figure is recorded. Its build-up and supporting records have not been assessed.")

    missing_information: List[str] = []
    if notice is not None and notice.period_source != "contract_verified":
        missing_information.append("The notice period, confirmed from the contract's own clause")
    if head.clause_refs and not clause_index_available:
        missing_information.append("The contract, clause-indexed for this project")
    return ClaimHeadTestOutput(
        event=event,
        notice=notice_finding,
        contractual_basis=basis,
        causation=causation,
        quantum=quantum,
        classification="undetermined",
        clause_refs=list(head.clause_refs),
        missing_information=missing_information,
    )


_RANK = {"missing": 0, "weak": 1, "supported": 2}


def apply_fact_guards(proposed: ClaimHeadTestOutput, floor: ClaimHeadTestOutput) -> ClaimHeadTestOutput:
    """Hold a skill's proposal to the recorded facts.

    Where the records say an element is missing (no notice served, no clause
    cited, no figure recorded), the skill cannot propose better than missing.
    Where they say a clause could not be found, or a notice period is
    unverified, "supported" is brought down to "weak". The skill's reasoning
    is kept and the record's finding is appended to it.
    """
    result = proposed.model_copy(deep=True)
    for key in ("event", "notice", "contractual_basis", "quantum"):
        record: ElementFinding = getattr(floor, key)
        view: ElementFinding = getattr(result, key)
        if view.state == "not_applicable":
            continue
        if record.state == "missing" and _RANK.get(view.state, 0) > 0:
            setattr(result, key, ElementFinding(state="missing", reason=record.reason, source_ids=view.source_ids))
        elif key in {"notice", "contractual_basis"} and view.state == "supported" and _needs_confirmation(key, record):
            setattr(
                result,
                key,
                ElementFinding(
                    state="weak",
                    reason=f"{view.reason} Record check: {record.reason}",
                    source_ids=view.source_ids,
                ),
            )
    result.clause_refs = [ref for ref in result.clause_refs if ref in set(floor.clause_refs)]
    return result


def _needs_confirmation(key: str, record: ElementFinding) -> bool:
    reason = record.reason.lower()
    if key == "notice":
        return "not been verified" in reason or "after" in reason
    return "not found" in reason or "not clause-indexed" in reason


def apply_proposals(head: ClaimHead, output: Dict[str, Any], by_skill: bool, step_id: str) -> None:
    parsed = ClaimHeadTestOutput(**output)
    for key in ELEMENT_KEYS:
        finding: ElementFinding = getattr(parsed, key)
        element = head.elements.get(key) or HeadElement()
        element.proposed_state = finding.state
        element.proposed_reason = finding.reason
        element.proposed_by = "skill" if by_skill else "record_check"
        element.source_ids = list(finding.source_ids)
        head.elements[key] = element
    head.proposed_classification = parsed.classification
    head.residual_risks = list(parsed.residual_risks)[:20]
    head.missing_information = list(parsed.missing_information)[:20]
    head.step_id = step_id
    head.test_stale = False
    head.tested_at = now_utc()


def _find(bench: ClaimBench, head_id: str) -> ClaimHead:
    for head in bench.heads:
        if head.head_id == head_id:
            return head
    raise HTTPException(status_code=404, detail="Head of claim not found")


def _facts_text(
    claim: Dict[str, Any], head: ClaimHead, notice: Optional[NoticeAssessment], checks: List[ClauseCheck], indexed: bool
) -> str:
    def day(value: Any) -> str:
        return str(value)[:10] if value else "not recorded"

    lines = [
        f"- Claim type: {claim.get('type') or 'other'}",
        f"- Event date: {day(claim.get('event_date'))}",
        f"- Date of becoming aware: {day(claim.get('awareness_date'))}",
        f"- Notice served on: {day(claim.get('notice_date'))}",
    ]
    if notice is not None:
        source = {
            "contract_verified": f"clause {notice.clause_no} of the contract, verified",
            "contract_unverified": f"clause {notice.clause_no} of the contract, NOT yet verified by a person",
            "organisation_rule": "a figure set by the organisation, NOT read from the contract",
            "assumed_default": "an ASSUMED typical figure, NOT read from the contract",
        }[notice.period_source]
        lines.append(f"- Notice period: {notice.period_days} days, from {source}")
        if notice.served_on is not None:
            lines.append(
                "- Notice timing: "
                + (
                    f"served {notice.served_late_by_days} day(s) after that period ended"
                    if notice.state == "served_late"
                    else "served within that period"
                )
            )
    lines.append(f"- Days claimed under this head: {head.eot_days_claimed if head.eot_days_claimed is not None else 'not recorded'}")
    lines.append(
        f"- Amount claimed under this head: "
        f"{(str(claim.get('currency') or 'INR') + ' ' + format(head.amount_claimed, ',.2f')) if head.amount_claimed is not None else 'not recorded'}"
    )
    if not head.clause_refs:
        lines.append("- Clauses cited: none")
    elif not indexed:
        lines.append("- Clauses cited: " + ", ".join(head.clause_refs) + " (the contract is not clause-indexed; none could be checked)")
    else:
        for check in checks:
            lines.append(f"- Clause {check.clause_no}: {'found in the clause index' if check.found else 'NOT found in the clause index'}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class ClaimBenchService:
    def __init__(
        self,
        db: Any,
        *,
        registry: Optional[SkillRegistry] = None,
        runner: Optional[SkillStepRunner] = None,
        config: Any = settings,
    ) -> None:
        self.db = db
        self.config = config
        self.registry = registry or SkillRegistry(db)
        self.runner = runner or SkillStepRunner(db, self.registry, SkillExecutor(PromptRegistry(db)))
        self.requirements = NoticeRequirementService(db)

    # ------------------------------------------------------------- rollout
    def availability(self, tenant_id: Optional[str]) -> ClaimBenchAvailability:
        return claim_bench_rollout(tenant_id, self.config)

    def ensure_enabled(self, tenant_id: Optional[str]) -> None:
        if not self.availability(tenant_id).enabled:
            raise HTTPException(status_code=404, detail="The Claim Bench is not enabled for this organisation")

    # ------------------------------------------------------------- storage
    @staticmethod
    def _bench_of(claim: Dict[str, Any]) -> Optional[ClaimBench]:
        raw = claim.get("bench")
        return ClaimBench(**raw) if isinstance(raw, dict) else None

    def _require_bench(self, claim: Dict[str, Any]) -> ClaimBench:
        bench = self._bench_of(claim)
        if bench is None:
            raise HTTPException(status_code=404, detail="This claim has no Bench yet")
        return bench

    @staticmethod
    def _read_only_reason(claim: Dict[str, Any]) -> Optional[str]:
        if claim.get("evidence_frozen_at"):
            return "This claim's evidence is frozen, so its heads can no longer be changed."
        status_text = unrecognised_status(claim)
        if status_text:
            # D-10 (4B-19): nothing is built on a status the application cannot place.
            return (
                f"This claim's recorded status (\"{status_text[:40]}\") is not one the Bench recognises, so it "
                "opens read-only until the status on the claim record is corrected."
            )
        return None

    def _ensure_writable(self, claim: Dict[str, Any], bench: ClaimBench, client_version: int) -> None:
        reason = self._read_only_reason(claim)
        if reason:
            raise HTTPException(status_code=409, detail=reason)
        if bench.board_version != client_version:
            raise HTTPException(
                status_code=409,
                detail="The Bench changed since you loaded it. Your view has been refreshed; repeat the action.",
            )

    async def _save(
        self, claim: Dict[str, Any], bench: ClaimBench, expected_version: Optional[int], user_id: Optional[str]
    ) -> Dict[str, Any]:
        """Compare-and-set. ``expected_version`` None means the claim must have no board yet."""
        bench.board_version = 0 if expected_version is None else expected_version + 1
        bench.updated_at = now_utc()
        bench.updated_by = user_id
        update: Dict[str, Any] = {"bench": bench.model_dump(mode="python"), "updated_at": datetime.utcnow(), "updated_by": user_id}
        amounts = [head.amount_claimed for head in bench.heads if head.amount_claimed is not None]
        # The register's figure follows the heads, including when every head amount
        # is cleared: then the claim has no amount (D-14, 4B-17, C-07). Days are not
        # summed: heads can overlap in time, and adding them would overstate the claim.
        update["amount_claimed"] = float(sum(amounts)) if amounts else None
        condition: Dict[str, Any] = {"_id": claim["_id"]}
        if expected_version is None:
            condition["bench"] = None
        else:
            condition["bench.board_version"] = expected_version
        result = await self.db.claims.update_one(condition, {"$set": update})
        if not getattr(result, "matched_count", 0):
            raise HTTPException(
                status_code=409,
                detail="The Bench changed since you loaded it. Your view has been refreshed; repeat the action.",
            )
        return {**claim, **update}

    # ------------------------------------------------------------- context
    async def _perspective(self, claim: Dict[str, Any]) -> Optional[str]:
        project_id = claim.get("project_id")
        if not project_id:
            return None
        from .project_perspective import resolve_perspective

        # The project's own position, or its organisation's default (plan 4B-15).
        value, _source = await resolve_perspective(self.db, await self._find_project(str(project_id)))
        return value

    async def _find_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        from bson import ObjectId

        candidates: List[Any] = [project_id]
        if ObjectId.is_valid(project_id):
            candidates.append(ObjectId(project_id))
        for candidate in candidates:
            doc = await self.db.projects.find_one({"_id": candidate})
            if doc:
                return doc
        return None

    async def _notice(self, claim: Dict[str, Any]) -> tuple[Optional[NoticeAssessment], Optional[NoticeRequirement]]:
        organization_id = str(claim.get("organization_id") or "") or None
        requirement = await self.requirements.current(
            organization_id, claim.get("project_id"), str(claim.get("type") or "other")
        )
        windows = await SlaService(self.db).notice_window_overrides_for(organization_id)
        return assess_notice(claim, organisation_windows=windows, requirement=requirement), requirement

    async def _clause_checks(
        self, claim: Dict[str, Any], head: ClaimHead
    ) -> tuple[bool, List[ClauseCheck], str]:
        organization_id = str(claim.get("organization_id") or "")
        project_id = str(claim.get("project_id") or "")
        if not organization_id or not project_id:
            return False, [ClauseCheck(clause_no=ref, found=False) for ref in head.clause_refs], "(none)"
        records = await self.requirements.clause_records(organization_id, project_id)
        by_no: Dict[str, List[Dict[str, Any]]] = {}
        for record in records:
            by_no.setdefault(str(record.get("clause_no") or "").strip(), []).append(record)
        checks: List[ClauseCheck] = []
        blocks: List[str] = []
        for ref in head.clause_refs:
            matches = by_no.get(ref, [])
            if not matches:
                checks.append(ClauseCheck(clause_no=ref, found=False))
                continue
            first = matches[0]
            checks.append(
                ClauseCheck(clause_no=ref, found=True, title=first.get("clause_title"), document_type=first.get("document_type"))
            )
            for match in matches[:3]:
                wording = str(match.get("cleaned_text") or match.get("text") or "")[:MAX_CLAUSE_CHARS]
                blocks.append(
                    f"[Clause {ref} | {match.get('document_type') or 'contract'} | {match.get('clause_title') or ''}]\n{wording}"
                )
        return bool(records), checks, "\n\n".join(blocks) or "(none of the cited clauses is in the clause index)"

    async def _skill_state(self, claim: Dict[str, Any]) -> BenchSkillState:
        version, _binding, reason = await self.registry.resolve(
            CLAIMS_SKILL_KEY, str(claim.get("organization_id") or "") or None, claim.get("project_id")
        )
        if version is not None:
            return BenchSkillState(available=True, version=version.version)
        if reason == "No published version":
            reason = "Awaiting legal approval"
        return BenchSkillState(available=False, reason=reason or "Not installed")

    async def _response(self, claim: Dict[str, Any], bench: Optional[ClaimBench]) -> ClaimBenchResponse:
        notice, requirement = await self._notice(claim)
        steps = await self.runner.list_for_parent(str(claim["_id"])) if bench else []
        # A step is current only while it is the latest test of a head that has
        # not been edited since.
        current = {head.step_id for head in (bench.heads if bench else []) if not head.test_stale}
        board = bench or ClaimBench()
        reason = self._read_only_reason(claim)
        return ClaimBenchResponse(
            claim_id=str(claim["_id"]),
            claim_ref=claim.get("claim_ref"),
            title=str(claim.get("title") or ""),
            claim_type=str(claim.get("type") or "other"),
            status=str(claim.get("status") or "draft"),
            organization_id=claim.get("organization_id"),
            project_id=claim.get("project_id"),
            exists=bench is not None,
            bench=board,
            gate=await self._gate(claim, board),
            steps=[
                BenchStepSummary(
                    step_id=step.step_id,
                    subject_id=step.subject_id,
                    skill_key=step.skill_key,
                    skill_version=step.skill_version,
                    skill_name=step.skill_name,
                    role=step.role,
                    degraded=step.degraded,
                    stale=step.stale or step.step_id not in current,
                    error=step.error,
                    model=step.model,
                    completed_at=step.completed_at,
                )
                for step in steps
            ],
            notice_assessment=notice,
            notice_requirement=requirement,
            perspective=await self._perspective(claim),
            skill=await self._skill_state(claim),
            read_only=reason is not None,
            read_only_reason=reason,
            arbitration_case_id=claim.get("arbitration_case_id"),
            rollout_mode=self.availability(str(claim.get("organization_id") or "") or None).rollout_mode,
        )

    async def _gate(self, claim: Dict[str, Any], bench: ClaimBench) -> BenchGate:
        """The lock gate: the five elements and classification of every head, plus
        any delay assessment that has been started and not finished."""
        gate = compute_gate(bench)
        # Imported here: the Delay Lab is built on this service.
        from .claim_delay_lab import ClaimDelayLab

        delay = await ClaimDelayLab(self.db, self).blockers(claim, bench)
        if delay:
            gate.blockers.extend(delay)
            gate.can_lock = False
        return gate

    # --------------------------------------------------------------- reads
    async def get(self, claim: Dict[str, Any]) -> ClaimBenchResponse:
        return await self._response(claim, self._bench_of(claim))

    async def get_step(self, claim: Dict[str, Any], step_id: str) -> SkillRunStep:
        step = await self.runner.get(step_id)
        if step is None or step.parent_id != str(claim["_id"]):
            raise HTTPException(status_code=404, detail="Step not found")
        return step

    # -------------------------------------------------------------- writes
    async def open(self, claim: Dict[str, Any], user_id: Optional[str]) -> ClaimBenchResponse:
        """Create the board with one head carried over from the register. Idempotent."""
        existing = self._bench_of(claim)
        if existing is not None:
            return await self._response(claim, existing)
        reason = self._read_only_reason(claim)
        if reason:
            raise HTTPException(status_code=409, detail=reason)
        bench = ClaimBench(heads=[initial_head(claim)])
        try:
            claim = await self._save(claim, bench, None, user_id)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            # Two opens raced; the other one created the board.
            fresh = await self.db.claims.find_one({"_id": claim["_id"]})
            raced = self._bench_of(fresh or {})
            if fresh is None or raced is None:
                raise
            return await self._response(fresh, raced)
        return await self._response(claim, bench)

    async def _change(
        self,
        claim: Dict[str, Any],
        client_version: int,
        user_id: Optional[str],
        change: Callable[[ClaimBench], None],
    ) -> ClaimBenchResponse:
        bench = self._require_bench(claim)
        self._ensure_writable(claim, bench, client_version)
        change(bench)
        claim = await self._save(claim, bench, client_version, user_id)
        return await self._response(claim, bench)

    @staticmethod
    def _unlock(bench: ClaimBench) -> None:
        if bench.stage == "locked":
            bench.stage = "heads"
            bench.locked_at = None
            bench.locked_by = None

    async def add_head(self, claim: Dict[str, Any], request: HeadCreateRequest, user_id: Optional[str]) -> ClaimBenchResponse:
        if request.kind not in _VALID_KINDS:
            raise HTTPException(status_code=422, detail="kind must be time, cost or time_and_cost")

        def _apply(bench: ClaimBench) -> None:
            if len(bench.heads) >= MAX_HEADS:
                raise HTTPException(status_code=422, detail=f"A claim can hold at most {MAX_HEADS} heads")
            bench.heads.append(
                ClaimHead(
                    head_id=next_head_id(bench),
                    title=request.title.strip(),
                    kind=request.kind,  # type: ignore[arg-type]
                    event_summary=(request.event_summary or "").strip() or None,
                    clause_refs=clean_clause_refs(request.clause_refs),
                    amount_claimed=request.amount_claimed,
                    eot_days_claimed=request.eot_days_claimed,
                )
            )
            self._unlock(bench)

        return await self._change(claim, request.board_version, user_id, _apply)

    async def patch_head(
        self, claim: Dict[str, Any], head_id: str, request: HeadPatchRequest, user_id: Optional[str]
    ) -> ClaimBenchResponse:
        if request.kind is not None and request.kind not in _VALID_KINDS:
            raise HTTPException(status_code=422, detail="kind must be time, cost or time_and_cost")
        if request.classification is not None and request.classification not in _VALID_CLASSIFICATIONS:
            raise HTTPException(status_code=422, detail="Unknown classification")
        stale_steps: List[str] = []

        def _apply(bench: ClaimBench) -> None:
            head = _find(bench, head_id)
            facts_changed = False
            if request.title is not None and request.title.strip() != head.title:
                head.title, facts_changed = request.title.strip(), True
            if request.kind is not None and request.kind != head.kind:
                head.kind, facts_changed = request.kind, True  # type: ignore[assignment]
            if request.event_summary is not None:
                summary = request.event_summary.strip() or None
                if summary != head.event_summary:
                    head.event_summary, facts_changed = summary, True
            if request.clause_refs is not None:
                refs = clean_clause_refs(request.clause_refs)
                if refs != head.clause_refs:
                    head.clause_refs, facts_changed = refs, True
            amount_change = request.clear_amount or (
                request.amount_claimed is not None and request.amount_claimed != head.amount_claimed
            )
            if amount_change and head.quantum_lines:
                # D-14: a head with a build-up takes its amount from the build-up.
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "head_amount_from_lines",
                        "user_message": (
                            "This head's amount is the total of its quantum lines. Change the lines on the "
                            "Quantum tab instead."
                        ),
                        "detail": None,
                    },
                )
            if request.clear_amount:
                facts_changed = facts_changed or head.amount_claimed is not None
                head.amount_claimed = None
            elif request.amount_claimed is not None and request.amount_claimed != head.amount_claimed:
                head.amount_claimed, facts_changed = request.amount_claimed, True
            # A figure awaiting re-entry is settled by entering it or by saying it is
            # not needed. Saving the head with the field still empty does neither.
            if request.amount_claimed is not None:
                _figure_settled(head, "amount_claimed")
            if request.eot_days_claimed is not None:
                _figure_settled(head, "eot_days_claimed")
            for field in request.figures_not_needed:
                _figure_settled(head, field)
            if request.clear_eot_days:
                facts_changed = facts_changed or head.eot_days_claimed is not None
                head.eot_days_claimed = None
            elif request.eot_days_claimed is not None and request.eot_days_claimed != head.eot_days_claimed:
                head.eot_days_claimed, facts_changed = request.eot_days_claimed, True
            decision_changed = False
            if request.clear_classification:
                decision_changed = head.classification is not None
                head.classification = None
            elif request.classification is not None and request.classification != head.classification:
                head.classification, decision_changed = request.classification, True  # type: ignore[assignment]
            if facts_changed and head.step_id:
                # The proposals describe the head as it was. Keep them visible, marked stale.
                head.test_stale = True
                stale_steps.append(head.step_id)
            if facts_changed or decision_changed:
                self._unlock(bench)

        response = await self._change(claim, request.board_version, user_id, _apply)
        await self.runner.mark_stale(stale_steps)
        return response

    async def remove_head(
        self, claim: Dict[str, Any], head_id: str, request: BoardVersionRequest, user_id: Optional[str]
    ) -> ClaimBenchResponse:
        removed: List[str] = []

        def _apply(bench: ClaimBench) -> None:
            head = _find(bench, head_id)
            if head.step_id:
                removed.append(head.step_id)
            bench.heads = [item for item in bench.heads if item.head_id != head_id]
            self._unlock(bench)

        response = await self._change(claim, request.board_version, user_id, _apply)
        await self.runner.mark_stale(removed)
        # The head's links go with it. They are marked removed, not deleted.
        await self.db.claim_head_links.update_many(
            {"claim_id": str(claim["_id"]), "head_id": head_id, "status": "active"},
            {"$set": {"status": "removed", "removed_by": user_id, "removed_at": now_utc()}},
        )
        return response

    async def decide_element(
        self, claim: Dict[str, Any], head_id: str, element_key: str, request: ElementDecisionRequest, user_id: Optional[str]
    ) -> ClaimBenchResponse:
        if element_key not in ELEMENT_KEYS:
            raise HTTPException(status_code=404, detail="Unknown element")
        if not request.clear and request.state not in _VALID_STATES:
            raise HTTPException(status_code=422, detail="state must be supported, weak, missing or not_applicable")

        def _apply(bench: ClaimBench) -> None:
            head = _find(bench, head_id)
            element = head.elements.get(element_key) or HeadElement()
            if request.clear:
                element.state, element.decided_by, element.decided_at = None, None, None
            else:
                element.state = request.state  # type: ignore[assignment]
                element.decided_by = user_id
                element.decided_at = now_utc()
            if request.note is not None:
                element.note = request.note.strip() or None
            head.elements[element_key] = element
            self._unlock(bench)

        return await self._change(claim, request.board_version, user_id, _apply)

    async def lock(self, claim: Dict[str, Any], request: BoardVersionRequest, user_id: Optional[str]) -> ClaimBenchResponse:
        perspective = await self._perspective(claim)
        current = self._require_bench(claim)
        delay_blockers = (await self._gate(claim, current)).blockers

        def _apply(bench: ClaimBench) -> None:
            gate = compute_gate(bench)
            # Delay blockers were worked out on the board as loaded; the version
            # check in _change refuses the lock if the board has moved since.
            gate.blockers = list(dict.fromkeys([*gate.blockers, *delay_blockers]))
            if gate.blockers:
                raise HTTPException(status_code=409, detail="; ".join(gate.blockers))
            bench.stage = "locked"
            bench.locked_at = now_utc()
            bench.locked_by = user_id
            bench.perspective = perspective or bench.perspective  # type: ignore[assignment]

        response = await self._change(claim, request.board_version, user_id, _apply)
        await self._audit(claim, "claim.bench.locked", user_id, request.board_version + 1)
        return response

    async def unlock(self, claim: Dict[str, Any], request: UnlockRequest, user_id: Optional[str]) -> ClaimBenchResponse:
        """Reopen a locked board. The route checks claim-manage rights; the reason is audited (D-22, AQ-9)."""
        reason = " ".join(request.reason.split())
        if len(reason.strip(" .,;:-")) < 10:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "bench_unlock_reason",
                    "user_message": "Say why the board is being reopened (at least 10 characters).",
                    "detail": None,
                },
            )
        bench = self._require_bench(claim)
        self._ensure_writable(claim, bench, request.board_version)  # frozen and version first
        if bench.stage != "locked":
            raise HTTPException(
                status_code=409,
                detail={"code": "bench_not_locked", "user_message": "The board is not locked.", "detail": None},
            )
        response = await self._change(claim, request.board_version, user_id, self._unlock)
        await self._audit(
            claim,
            "claim.bench.unlocked",
            user_id,
            request.board_version + 1,
            reason=reason,
            before={"stage": "locked", "locked_by": bench.locked_by, "locked_at": bench.locked_at},
        )
        return response

    async def _audit(
        self,
        claim: Dict[str, Any],
        action: str,
        user_id: Optional[str],
        board_version: int,
        *,
        reason: Optional[str] = None,
        before: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append-only: audit events are inserted, and no route edits or deletes them."""
        await AuditEventService(self.db).emit(
            action=action,
            actor_id=user_id,
            resource_type="claim",
            resource_id=str(claim["_id"]),
            organization_id=claim.get("organization_id"),
            project_id=claim.get("project_id"),
            reason=reason,
            before=before,
            metadata={"board_version": board_version},
        )

    async def test_head(
        self, claim: Dict[str, Any], head_id: str, request: HeadTestRequest, user_id: Optional[str]
    ) -> ClaimBenchResponse:
        """Test one head: record checks, then the skill if a published version exists."""
        bench = self._require_bench(claim)
        self._ensure_writable(claim, bench, request.board_version)
        head = _find(bench, head_id)
        perspective = await self._perspective(claim)
        if perspective is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "State which party this project acts for (Contractor, Engineer or Employer) "
                    "before testing a head. The test is run from that party's position and is never assumed."
                ),
            )
        notice, _requirement = await self._notice(claim)
        indexed, checks, clause_text = await self._clause_checks(claim, head)
        floor = record_checks(claim, head, notice, checks, indexed)
        facts = _facts_text(claim, head, notice, checks, indexed)
        step = await self.runner.run(
            skill_key=CLAIMS_SKILL_KEY,
            subject_type="claim_head",
            subject_id=f"{claim['_id']}:{head.head_id}",
            parent_id=str(claim["_id"]),
            organization_id=str(claim.get("organization_id") or "") or None,
            project_id=claim.get("project_id"),
            role=perspective,
            prompt_key=CLAIM_HEAD_TEST_PROMPT,
            inputs={
                "facts": facts,
                "title": head.title,
                "kind": head.kind,
                "event_summary": head.event_summary,
                "clauses": clause_text,
            },
            payload={
                "facts": facts,
                "title": head.title,
                "kind": head.kind.replace("_", " "),
                "event_summary": head.event_summary or "(no description recorded)",
                "clauses": clause_text,
            },
            fallback=lambda: floor,
            schema_key=CLAIM_HEAD_TEST_SCHEMA_KEY,
            user_id=user_id,
            force=request.force,
        )
        by_skill = not step.degraded
        output = step.output
        if by_skill:
            output = apply_fact_guards(ClaimHeadTestOutput(**step.output), floor).model_dump(mode="json")
        previous_step = head.step_id

        def _apply(fresh: ClaimBench) -> None:
            target = _find(fresh, head_id)
            apply_proposals(target, output, by_skill, step.step_id)
            fresh.perspective = perspective  # type: ignore[assignment]

        response = await self._change(claim, request.board_version, user_id, _apply)
        if previous_step and previous_step != step.step_id:
            await self.runner.mark_stale([previous_step])
        logger.info(
            "claim bench head test: claim=%s head=%s skill_used=%s degraded=%s",
            claim["_id"], head_id, by_skill, step.degraded,
        )
        return response


__all__ = [
    "CLAIM_HEAD_TEST_PROMPT",
    "BenchGate",
    "BoardVersionRequest",
    "ClaimBenchAvailability",
    "ClaimBenchResponse",
    "ClaimBenchService",
    "ClauseCheck",
    "ElementDecisionRequest",
    "HeadCreateRequest",
    "HeadPatchRequest",
    "HeadTestRequest",
    "UnlockRequest",
    "apply_fact_guards",
    "claim_bench_rollout",
    "compute_gate",
    "coerce_legacy_amount",
    "coerce_legacy_days",
    "initial_head",
    "unrecognised_status",
    "record_checks",
]
