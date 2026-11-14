"""Pure Letter Bench logic: deadlines, proposals, fallbacks, staleness, gates.

Everything here is deterministic and free of I/O so it can be tested directly
and reused by the service and the validators.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from ...models.letter_bench import (
    VERDICT_LABELS,
    AnalysedAssertion,
    Assertion,
    BenchGate,
    ChainLink,
    DeadlineInfo,
    LetterAnalysisOutput,
    PositionBoard,
    ReplyParagraph,
    ReplyParagraphOutput,
    SkillStep,
    VariationEvaluationOutput,
)
from ...models.letter_drafting import (
    IncomingLetterAnalysis,
    PlanningSheet,
    ReplyMatrixRow,
)
from .context import condense_text

_WITHIN_DAYS = re.compile(r"within\s+(\d{1,3})\s+days?", re.IGNORECASE)
_DAY_FIRST = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b")
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# Mirrors DraftValidator.CLAUSE_PATTERN: anything it would read as a citation.
_CLAUSE_MENTION = re.compile(
    r"\b(?:(?:GCC|SCC|PCC|COPA)\s*)?(?:sub-?clause|subclause|clause|article|section)\s+[0-9A-Za-z.\-()]+",
    re.IGNORECASE,
)
STALE_RUNNING_MINUTES = 15

RESERVATION_TEXT = {
    "contractor": (
        "This letter is issued without prejudice to, and the Contractor expressly reserves, "
        "all of its rights and remedies under the Contract and at law, including its "
        "entitlement to extension of time and additional payment."
    ),
    "employer": (
        "This letter is issued without prejudice to, and the Employer expressly reserves, "
        "all of its rights and remedies under the Contract and at law, including the right "
        "to levy liquidated damages and to recover costs where later justified."
    ),
    "engineer": (
        "Nothing in this letter relieves either Party of its obligations under the Contract. "
        "The rights of the Parties under the Contract are reserved, and this letter is "
        "without prejudice to any determination that may later be required."
    ),
}


# --------------------------------------------------------------------------- #
# Deadline
# --------------------------------------------------------------------------- #
def parse_date(value: Optional[str]) -> Optional[date]:
    """Parse an ISO or day-first (DD-MM-YYYY) date; None when it is not a date."""
    if not value:
        return None
    text = str(value)
    iso = _ISO.search(text)
    try:
        if iso:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        match = _DAY_FIRST.search(text)
        if match:
            year = int(match.group(3))
            if year < 100:
                year += 2000
            return date(year, int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None
    return None


def resolve_deadline(
    raw: Optional[str],
    letter_date: Optional[str],
    *,
    today: Optional[date] = None,
    risk: Optional[str] = None,
) -> DeadlineInfo:
    today = today or datetime.now(timezone.utc).date()
    if not raw:
        return DeadlineInfo(source="none")
    due: Optional[date] = None
    basis: Optional[str] = None
    within = _WITHIN_DAYS.search(raw)
    if within:
        start = parse_date(letter_date)
        if start:
            due = start + timedelta(days=int(within.group(1)))
            basis = f"{within.group(1)} days from the letter date"
        else:
            basis = f"{within.group(1)} days; letter date not identified"
    else:
        due = parse_date(raw)
        basis = "Date stated in the letter" if due else None
    return DeadlineInfo(
        raw=raw,
        due_date=due.isoformat() if due else None,
        days_remaining=(due - today).days if due else None,
        basis=basis,
        source="extracted",
        risk=risk if risk in {"low", "medium", "high"} else None,  # type: ignore[arg-type]
    )


def manual_deadline(due_date: str, basis: Optional[str], *, today: Optional[date] = None) -> DeadlineInfo:
    today = today or datetime.now(timezone.utc).date()
    due = date.fromisoformat(due_date)
    remaining = (due - today).days
    return DeadlineInfo(
        raw=due_date,
        due_date=due.isoformat(),
        days_remaining=remaining,
        basis=basis or "Entered by the drafter",
        source="manual",
        risk="high" if remaining <= 7 else "medium" if remaining <= 14 else "low",
    )


def refresh_deadline(deadline: DeadlineInfo, *, today: Optional[date] = None) -> DeadlineInfo:
    if not deadline.due_date:
        return deadline
    today = today or datetime.now(timezone.utc).date()
    return deadline.model_copy(
        update={"days_remaining": (date.fromisoformat(deadline.due_date) - today).days}
    )


# --------------------------------------------------------------------------- #
# Proposals and deterministic fallbacks
# --------------------------------------------------------------------------- #
def propose_verdict(assertion: Assertion) -> None:
    """Conservative deterministic proposal. Never proposes a concession or a rejection:
    without a model reading the record it can only say what is missing."""
    not_found = [check.clause_number for check in assertion.clause_checks if check.state == "not_found"]
    if not_found:
        assertion.proposed_verdict = "need_information"
        assertion.proposed_reason = (
            f"Clause {', '.join(not_found)} was not found in this project's clause index. "
            "Confirm the reference before taking a position."
        )
    elif not assertion.clause_refs:
        assertion.proposed_verdict = "need_information"
        assertion.proposed_reason = (
            "The sender cites no clause for this point. Ask for the contractual basis, "
            "or decide the position from the record."
        )
    else:
        assertion.proposed_verdict = None
        assertion.proposed_reason = (
            "No position proposed: the cited clause must be read against the facts. Decide manually."
        )


def fallback_analysis(
    analysis: Optional[IncomingLetterAnalysis], extracted: Sequence[Dict[str, Any]]
) -> LetterAnalysisOutput:
    analysis = analysis or IncomingLetterAnalysis()
    clauses = list(analysis.clauses_cited or [])
    short = [
        (
            f"From {analysis.sender or 'sender not identified'} to "
            f"{analysis.recipient or 'recipient not identified'}; "
            f"ref {analysis.letter_no or 'not identified'}, dated {analysis.letter_date or 'not identified'}."
        ),
        f"Purpose: {condense_text(analysis.main_request, 220) or 'not identified from the text'}.",
        f"Clauses invoked: {', '.join(clauses) if clauses else 'none identified'}.",
        f"Action sought: {condense_text(analysis.action_requested, 200) or 'not identified'}.",
        f"Response deadline: {analysis.response_deadline or 'not identified'}.",
    ]
    return LetterAnalysisOutput(
        short_summary=short,
        basic_particulars={
            "from": analysis.sender,
            "to": analysis.recipient,
            "date": analysis.letter_date,
            "reference": analysis.letter_no,
            "subject": analysis.subject,
        },
        purpose_and_nature=f"Issue type: {analysis.issue_type}.",
        key_facts_asserted=[condense_text(item["quoted_text"], 300) or "" for item in extracted][:10],
        contractual_basis_claimed=[
            f"Clause {ev.clause_number}: "
            + (
                "found in this contract"
                if ev.exists_in_contract
                else "not found in this contract"
                if ev.exists_in_contract is False
                else "not checked"
            )
            for ev in analysis.cited_clause_evaluations
        ],
        relief_sought=[item for item in [analysis.action_requested, analysis.amount_claimed] if item],
        strengths_and_weaknesses=[
            "Not assessed: the language model was unavailable. Review notice timing, form and evidence manually."
        ],
        implications_and_risks=[
            item
            for item in [
                analysis.drafter_alert,
                "Silence may be treated as acceptance of the facts asserted; reply to every point.",
            ]
            if item
        ],
        recommended_response_strategy=[
            "Reply point by point and reserve all rights.",
            *([analysis.recommended_immediate_action] if analysis.recommended_immediate_action else []),
        ],
        immediate_action=analysis.recommended_immediate_action,
        assertions=[
            AnalysedAssertion(quoted_text=item["quoted_text"], clause_refs=item.get("clause_refs", []))
            for item in extracted
        ],
    )


VARIATION_REQUIRED_DATA = [
    "BOQ rates and prices for the item(s) concerned",
    "Original scope for the work: drawings, specification or Employer's Requirements",
    "The instruction relied upon, with its reference and date",
    "The Contractor's proposal under the variation procedure, if one was requested",
    "Site records showing the quantity and conditions of the work executed",
]


def fallback_variation(assertion: Optional[Assertion]) -> VariationEvaluationOutput:
    """The skill requires this outcome when data is missing: stop at 'insufficient
    information' and list exactly what is required."""
    refs = list(assertion.clause_refs) if assertion else []
    return VariationEvaluationOutput(
        description=condense_text(assertion.quoted_text, 400) if assertion else "",
        classification="insufficient_information",
        contractual_basis=[f"Sender relies on clause {ref}" for ref in refs],
        valuation_limb="not_determined",
        valuation_justification=(
            "The valuation hierarchy cannot be applied until the contract rates and the "
            "original scope are on record."
        ),
        time_impact="Not assessed: critical-path effect requires the programme and site records.",
        recommended_wording=None,
        residual_risks=[
            "Scope ambiguity until the original scope is compared with the work claimed.",
            "Risk of a constructive variation if work proceeded without a formal instruction.",
        ],
        missing_information=list(VARIATION_REQUIRED_DATA),
        suggested_verdict="need_information",
    )


def _point_reference(assertion: Assertion) -> str:
    """Refer to the sender's point without repeating its clause numbers.

    Template wording is used when no model read the sources, so it must not
    restate a citation the source ledger may be unable to support.
    """
    text = _CLAUSE_MENTION.sub("the provision cited", " ".join(assertion.quoted_text.split()))
    words = text.split()
    if len(words) > 22:
        text = " ".join(words[:22]) + "..."
    return text.strip().strip(".")


def fallback_paragraph(assertion: Assertion, role: str) -> ReplyParagraphOutput:
    verdict = assertion.verdict or "need_information"
    opening = f"In respect of your statement that \"{_point_reference(assertion)}\":"
    direction = f" {assertion.user_direction.strip()}" if (assertion.user_direction or "").strip() else ""
    body = {
        "entitlement": (
            "this is accepted, subject to the contractual procedure being followed. "
            "[CONFIRM: the contractual provision under which the entitlement arises]"
        ),
        "partial": (
            "this is accepted in part only. [CONFIRM: the part accepted, the part not accepted, "
            "and the contractual provision relied upon for each]"
        ),
        "none": (
            "this is not accepted. [CONFIRM: the contractual reason and the provision relied upon]"
        ),
        "need_information": (
            "the position cannot be determined on the information provided. Please furnish "
            "[CONFIRM: the particulars and records required] within [CONFIRM: number] days "
            "of this letter."
        ),
    }[verdict]
    needs = ""
    if verdict == "need_information" and assertion.needs:
        needs = " The following are required: " + "; ".join(assertion.needs[:6]) + "."
    return ReplyParagraphOutput(
        text=f"{opening} {body}{needs}{direction}".strip(),
        clause_refs=[],
        internal_note=(
            f"{assertion.assertion_id}: template wording used because the language model was "
            "unavailable. Complete every [CONFIRM] before issue."
        ),
        residual_risks=["Template wording: position is stated but not reasoned."],
    )


# --------------------------------------------------------------------------- #
# Framing, assembly, plan
# --------------------------------------------------------------------------- #
def framing_paragraphs(board: PositionBoard, existing: Sequence[ReplyParagraph]) -> Dict[str, ReplyParagraph]:
    """The deterministic parts of the letters skill's structure. A part the user
    has edited is kept as they left it."""
    meta = board.incoming_meta or {}
    kept = {p.part: p for p in existing if p.part != "point" and p.edited_by_user}
    reference = meta.get("letter_no") or "[CONFIRM: your letter reference]"
    dated = meta.get("letter_date") or "[CONFIRM: date]"
    subject = meta.get("subject") or "[CONFIRM: subject]"
    party = {"contractor": "Contractor", "employer": "Employer", "engineer": "Engineer"}[board.role]
    need_info = [a for a in board.assertions if a.verdict == "need_information"]
    parts = {
        "header": (
            f"To: {meta.get('sender') or '[CONFIRM: addressee]'}\n"
            f"Subject: Reply to your letter {reference} dated {dated} - {subject}"
        ),
        "opening": (
            f"We refer to your letter {reference} dated {dated}. The {party} replies to each "
            "point raised in that letter below."
        ),
        "request": (
            "You are requested to provide the particulars and records identified above within "
            "[CONFIRM: number] days of this letter."
            if need_info
            else "You are requested to note the positions set out above and to act accordingly."
        ),
        "reservations": RESERVATION_TEXT[board.role],
        "closing": f"Yours faithfully,\n\nFor and on behalf of the {party}\n[CONFIRM: authorised signatory]",
    }
    result: Dict[str, ReplyParagraph] = {}
    for part, text in parts.items():
        result[part] = kept.get(part) or ReplyParagraph(paragraph_id=f"P-{part}", part=part, text=text)  # type: ignore[arg-type]
    return result


def ordered_paragraphs(board: PositionBoard, paragraphs: Sequence[ReplyParagraph]) -> List[ReplyParagraph]:
    framing = {p.part: p for p in paragraphs if p.part != "point"}
    points = {p.assertion_id: p for p in paragraphs if p.part == "point" and p.assertion_id}
    ordered: List[ReplyParagraph] = []
    for part in ("header", "opening"):
        if part in framing:
            ordered.append(framing[part])
    for assertion in sorted(board.assertions, key=lambda item: item.order):
        if assertion.assertion_id in points:
            ordered.append(points[assertion.assertion_id])
    for part in ("request", "reservations", "attachments", "closing"):
        if part in framing:
            ordered.append(framing[part])
    return ordered


def assemble_letter(board: PositionBoard) -> str:
    numbered = 0
    blocks: List[str] = []
    for paragraph in ordered_paragraphs(board, board.reply_paragraphs):
        text = (paragraph.text or "").strip()
        if not text:
            continue
        if paragraph.part == "point":
            numbered += 1
            text = f"{numbered}. {text}"
        blocks.append(text)
    return "\n\n".join(blocks)


def build_reply_matrix(board: PositionBoard) -> List[ReplyMatrixRow]:
    rows: List[ReplyMatrixRow] = []
    for assertion in sorted(board.assertions, key=lambda item: item.order):
        label = VERDICT_LABELS.get(assertion.verdict or "", "Undecided")
        reason = (assertion.user_direction or "").strip() or (
            assertion.proposed_reason if assertion.verdict_source == "ai" else ""
        )
        checks = assertion.clause_checks
        if assertion.verdict == "need_information" or not assertion.verdict:
            status = "needs_confirmation"
        elif checks and all(check.state == "found" for check in checks):
            status = "supported"
        else:
            status = "needs_confirmation"
        rows.append(
            ReplyMatrixRow(
                incoming_point=condense_text(assertion.quoted_text, 500) or assertion.quoted_text,
                proposed_reply=f"{label}. {reason}".strip(),
                source_ids=list(assertion.source_ids),
                clause_refs=list(assertion.clause_refs),
                risk_note=(
                    "; ".join(
                        f"Clause {check.clause_number}: {check.label.lower()}" for check in checks
                    )
                    or None
                ),
                status=status,  # type: ignore[arg-type]
            )
        )
    return rows


def build_planning_sheet(board: PositionBoard, analysis: Optional[Dict[str, Any]]) -> PlanningSheet:
    stored = IncomingLetterAnalysis(**analysis) if analysis else None
    meta = board.incoming_meta or {}
    counts: Dict[str, int] = {}
    for assertion in board.assertions:
        counts[assertion.verdict or "undecided"] = counts.get(assertion.verdict or "undecided", 0) + 1
    position = "; ".join(
        f"{VERDICT_LABELS.get(key, key)}: {value}" for key, value in sorted(counts.items())
    )
    issue_type = stored.issue_type if stored else "other"
    return PlanningSheet(
        draft_type="reply" if board.incoming_document_id else "fresh",
        letter_purpose=f"Point-by-point reply to {meta.get('letter_no') or 'the incoming letter'}",
        subject=meta.get("subject"),
        recipient=meta.get("sender"),
        sender_role=board.role,
        trigger_event=condense_text(stored.main_request, 400) if stored else None,
        contractual_basis=sorted({ref for a in board.assertions for ref in a.clause_refs}),
        issue_type=issue_type,
        response_deadline=board.deadline.due_date or board.deadline.raw,
        deadline_risk=board.deadline.risk,
        cited_clause_evaluations=stored.cited_clause_evaluations if stored else [],
        recommended_position=position,
        required_action="Reply to every assertion as decided on the Letter Bench.",
        rights_reservation_required=True,
        user_confirmed=True,
    )


def build_plan_text(board: PositionBoard, rows: Sequence[ReplyMatrixRow]) -> str:
    chain = " -> ".join(
        f"{link.label}{' v' + link.skill_version if link.skill_version else ''}"
        + (" (unavailable)" if link.status == "unavailable" else "")
        for link in board.chain
    )
    lines = [
        "1. Drafting posture",
        f"- Profile: {board.role}",
        f"- Response deadline: {board.deadline.due_date or board.deadline.raw or 'Not identified'}",
        f"- Skill chain: {chain or 'none'}",
        "2. Positions locked on the Letter Bench",
    ]
    for assertion, row in zip(sorted(board.assertions, key=lambda item: item.order), rows):
        lines.append(f"- {assertion.assertion_id}: {row.incoming_point}")
        lines.append(f"  Position: {row.proposed_reply}")
        if assertion.clause_refs:
            lines.append(f"  Clauses relied on by the sender: {', '.join(assertion.clause_refs)}")
    lines += [
        "3. Required outcome",
        "- Answer every assertion as decided above and reserve all rights.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Staleness
# --------------------------------------------------------------------------- #
def descendants(steps: Iterable[SkillStep], root_ids: Iterable[str]) -> Set[str]:
    """Ids of every step that depends, directly or not, on any root step."""
    children: Dict[str, List[str]] = {}
    for step in steps:
        for parent in step.parent_step_ids:
            children.setdefault(parent, []).append(step.step_id)
    found: Set[str] = set()
    stack = list(root_ids)
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def steps_stale_after_position_change(
    steps: Iterable[SkillStep], assertion_id: str, *, include_evaluations: bool
) -> Set[str]:
    """A verdict or direction change touches only that assertion's reply paragraph.
    A routing change also invalidates that assertion's evaluation and what follows it."""
    steps = list(steps)
    roots: Set[str] = set()
    for step in steps:
        if assertion_id not in step.target_assertion_ids:
            continue
        is_paragraph = step.output_schema_key == "reply_paragraph.v1"
        if is_paragraph or include_evaluations:
            roots.add(step.step_id)
    return roots | descendants(steps, roots)


def apply_stale_to_board(board: PositionBoard, stale_step_ids: Set[str], steps: Iterable[SkillStep]) -> None:
    by_id = {step.step_id: step for step in steps}
    for paragraph in board.reply_paragraphs:
        if paragraph.step_id in stale_step_ids:
            paragraph.stale = True
    for link in board.chain:
        if link.step_id in stale_step_ids and link.status == "done":
            link.status = "stale"
            continue
        if link.kind == "evaluation" and link.status == "done":
            for step_id in stale_step_ids:
                step = by_id.get(step_id)
                if (
                    step
                    and step.skill_key == link.skill_key
                    and set(step.target_assertion_ids) & set(link.target_assertion_ids)
                ):
                    link.status = "stale"


# --------------------------------------------------------------------------- #
# Gate
# --------------------------------------------------------------------------- #
def compute_gate(board: PositionBoard, steps: Sequence[SkillStep]) -> BenchGate:
    undecided = sum(1 for assertion in board.assertions if not assertion.verdict)
    unanswered = sum(
        1 for item in board.questions if item.question.required and not (item.answer or "").strip()
    )
    running = sum(1 for step in steps if step.status in {"queued", "running"})
    stale_evaluations = sum(
        1 for link in board.chain if link.kind == "evaluation" and link.status in {"stale", "failed"}
    )
    blockers: List[str] = []
    if not board.assertions:
        blockers.append("No assertions yet. Select text in the letter to add one.")
    if undecided:
        blockers.append(f"{undecided} position{'s' if undecided != 1 else ''} still need your call")
    if unanswered:
        blockers.append(f"{unanswered} question{'s' if unanswered != 1 else ''} need an answer")
    if running:
        blockers.append("A skill is still running")
    if stale_evaluations:
        blockers.append("An evaluation is out of date. Re-run it or remove it from the chain.")
    can_lock = not blockers and board.stage in {"positions", "locked", "composed"}
    positions_current = (
        board.locked_strategy_version is not None
        and all(assertion.state == "locked" for assertion in board.assertions)
    )
    stale_paragraphs = sum(1 for paragraph in board.reply_paragraphs if paragraph.stale)
    return BenchGate(
        undecided=undecided,
        unanswered_questions=unanswered,
        stale_steps=stale_evaluations + stale_paragraphs,
        running_steps=running,
        can_lock=can_lock and not positions_current,
        can_compose=positions_current and not blockers and not board.composing,
        blockers=blockers,
    )


def link_for(chain: Sequence[ChainLink], skill_key: str, assertion_id: Optional[str] = None) -> Optional[ChainLink]:
    for link in chain:
        if link.skill_key != skill_key:
            continue
        if assertion_id is None or not link.target_assertion_ids or assertion_id in link.target_assertion_ids:
            return link
    return None
