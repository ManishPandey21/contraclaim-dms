"""Escalate claims into an arbitration case.

Escalation carries what the claim record establishes into the case's matrices,
explicitly and by rule. It does not draft anything and it approves nothing.

What is carried, and where:

- each head of claim            -> claim matrix
- the notice position           -> notice compliance
- cost lines with proof linked  -> quantum annexures
- delay events and key dates    -> chronology matrix
- documents linked to the claim -> document index
- documents linked with the role "notice" -> each head's notice records, and
  the notice-compliance row (4B-34)
- overlaps with no decided treatment -> issue matrix, as open issues with no figure

What is never carried: a notice period that has not been verified against the
contract, a cost line whose proof is not linked, and anything privileged (a
simulation, the strategy note). This module does not read privileged material
at all.

Every row is created through the case workspace's own row path, so it starts
as ``needs_review`` like any other row and passes the same human review before
it can support a pleading. Each carries ``origin`` (claim and head) so a
second escalation of the same claim adds nothing twice.

Escalating marks the claim's evidence as frozen and records the case on the
claim. Adding rows withdraws any readiness approval already given on the
case, because the case workspace does that for every new row. Authorisation is
the router's job.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..models.arbitration_drafting import ArbitrationCaseCreate, ArbitrationMatrixRowCreate
from ..models.claim import ALLOWED_STATUS_TRANSITIONS, ELEMENT_KEYS, ClaimBench, ClaimStatus, HeadElement
from ..utils.datetime import now_utc
from .arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService
from .arbitration_drafting.matrix_registry import MATRIX_COLLECTIONS
from .audit_event_service import AuditEventService
from .claim_bench import initial_head
from .claim_submission import ClaimSubmissions, SubmissionRecord, format_amount, head_amount, head_days

logger = logging.getLogger(__name__)

MAX_CLAIMS = 20
FREEZE_REASON = "Escalated to arbitration"
_ELEMENT_LABEL = {
    "event": "event",
    "notice": "notice",
    "contractual_basis": "contractual basis",
    "causation": "causation",
    "quantum": "quantum",
}
MATRIX_ORDER = [
    "claim-matrix",
    "notice-compliance",
    "quantum-annexures",
    "chronology-matrix",
    "document-index",
    "issue-matrix",
]


class NewCase(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    case_reference: Optional[str] = Field(default=None, max_length=120)
    party_perspective: str = "claimant"


class EscalationRequest(BaseModel):
    claim_ids: List[str] = Field(..., min_length=1, max_length=MAX_CLAIMS)
    case_id: Optional[str] = None
    new_case: Optional[NewCase] = None


class ClaimCarry(BaseModel):
    claim_id: str
    claim_ref: Optional[str] = None
    title: str = ""
    status: str = ""
    board_locked: bool = False
    has_board: bool = False
    already_in_case_id: Optional[str] = None
    # Rows this claim would add, by matrix.
    rows: Dict[str, int] = Field(default_factory=dict)
    # What is carried but is weak on the record.
    weak: List[str] = Field(default_factory=list)
    # What is deliberately left behind, and why.
    not_carried: List[str] = Field(default_factory=list)
    status_after: str = ""


class EscalationPreview(BaseModel):
    project_id: Optional[str] = None
    claims: List[ClaimCarry] = Field(default_factory=list)
    totals: Dict[str, int] = Field(default_factory=dict)
    blockers: List[str] = Field(default_factory=list)
    can_escalate: bool = False


class EscalationResult(BaseModel):
    case_id: str
    case_title: str = ""
    created_case: bool = False
    rows_created: Dict[str, int] = Field(default_factory=dict)
    rows_already_present: int = 0
    claims: List[ClaimCarry] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Pure: what one claim carries
# --------------------------------------------------------------------------- #
def _origin(claim_id: str, part: str) -> Dict[str, Any]:
    return {"origin_key": f"claim:{claim_id}:{part}"}


def status_after(current: str) -> str:
    """A claim becomes disputed where its lifecycle allows that move; otherwise it stays as it is."""
    try:
        allowed = ALLOWED_STATUS_TRANSITIONS.get(ClaimStatus(current), frozenset())
    except ValueError:
        return current
    return ClaimStatus.DISPUTED.value if ClaimStatus.DISPUTED in allowed or current == ClaimStatus.DISPUTED.value else current


def build_carry(
    claim: Dict[str, Any],
    record: SubmissionRecord,
    bench: Optional[ClaimBench],
    notice_ids: Iterable[str] = (),
) -> tuple[ClaimCarry, List[tuple[str, Dict[str, Any]]]]:
    """The rows a claim adds to a case, with what is weak and what is left behind.

    ``notice_ids`` are the documents linked to the claim with the role "notice"
    that the caller may see (4B-34). The notice is given for the claim, so every
    head carries them; a reviewer still approves each row before it is pleaded.
    """
    claim_id = str(claim["_id"])
    notices = sorted({str(item) for item in notice_ids if item})
    reference = str(claim.get("claim_ref") or claim_id)
    rows: List[tuple[str, Dict[str, Any]]] = []
    weak: List[str] = []
    left: List[str] = []
    if bench is None:
        weak.append("The claim has not been worked head by head; one head is carried from its register entry.")
    elif bench.stage != "locked":
        weak.append("The board is not locked: some elements or heads are not decided.")

    for head in record.heads:
        days, assessed = head_days(record, head)
        amount, built = head_amount(record, head)
        states = {key: (head.elements.get(key) or HeadElement()).state for key in ELEMENT_KEYS}
        for key, state in states.items():
            if state in (None, "weak", "missing"):
                weak.append(f"{head.head_id}: {_ELEMENT_LABEL[key]} is {state or 'not decided'}")
        causation = head.elements.get("causation") or HeadElement()
        relief = []
        if days is not None:
            relief.append(f"Extension of time of {days} day{'' if days == 1 else 's'}")
        if amount is not None:
            relief.append(f"Payment of {format_amount(amount, record.currency)}")
        view = record.quantum.get(head.head_id)
        evidence_ids = sorted(
            {proof.document_id for item in (view.lines if view else []) if not item.locked for proof in item.line.records}
        )
        if days is not None and not assessed:
            weak.append(f"{head.head_id}: the days are the figure entered on the head, with no delay assessment behind them")
        if amount is not None and not built:
            weak.append(f"{head.head_id}: the amount is the figure entered on the head, with no build-up behind it")
        rows.append(
            (
                "claim-matrix",
                {
                    **_origin(claim_id, f"head:{head.head_id}"),
                    "origin": {"claim_id": claim_id, "head_id": head.head_id},
                    "source_claim_id": claim_id,
                    "claim_no": f"{reference}/{head.head_id}",
                    "claim_head": head.title,
                    "amount_or_days": (amount if amount is not None else (f"{days} days" if days is not None else None)),
                    "amount": amount,
                    "eot_days": days,
                    "currency": record.currency,
                    "clause_ids": list(head.clause_refs),
                    "facts": head.event_summary,
                    # The claim's notice letters (role "notice"), as linked by a person on the claim.
                    "notice_ids": list(notices),
                    "evidence_ids": evidence_ids,
                    "causation": causation.note if causation.state == "supported" else None,
                    "relief": "; ".join(relief) or None,
                    "classification": head.classification,
                    "element_states": states,
                },
            )
        )
        for item in view.lines if view else []:
            if item.locked:
                continue
            rows.append(
                (
                    "quantum-annexures",
                    {
                        **_origin(claim_id, f"head:{head.head_id}:line:{item.line.line_id}"),
                        "origin": {"claim_id": claim_id, "head_id": head.head_id, "line_id": item.line.line_id},
                        "claim_no": f"{reference}/{head.head_id}",
                        "cost_head": item.line.category,
                        "calculation_type": item.line.category,
                        "description": item.line.description,
                        "amount": item.line.amount,
                        "currency": record.currency,
                        "period_start": item.line.period_start,
                        "period_end": item.line.period_end,
                        "formula": item.line.formula if item.line.formula != "none" else None,
                        "basis": item.line.basis,
                        "evidence_ids": [proof.document_id for proof in item.line.records],
                    },
                )
            )
        if view and view.locked_count:
            left.append(
                f"{head.head_id}: {view.locked_count} cost line{'' if view.locked_count == 1 else 's'} "
                f"({format_amount(view.locked_total, record.currency)}) whose proof is not linked"
            )
        delay = record.delay.get(head.head_id)
        for overlap in delay.overlaps if delay else []:
            if overlap.finding is not None:
                continue
            rows.append(
                (
                    "issue-matrix",
                    {
                        **_origin(claim_id, f"head:{head.head_id}:overlap:{overlap.overlap_id}"),
                        "origin": {"claim_id": claim_id, "head_id": head.head_id},
                        "issue": (
                            f"Concurrent delay under {reference}/{head.head_id}: "
                            f"{'Neutral' if getattr(overlap, 'kind', '') == 'neutral_contractor' else 'Employer'} and "
                            "Contractor delay ran together and the treatment of the period has not been decided."
                        ),
                        "issue_type": "concurrent_delay",
                        "period_start": overlap.start_date,
                        "period_end": overlap.end_date,
                        "open": True,
                    },
                )
            )
            weak.append(f"{head.head_id}: an overlap of concurrent delay has no decided treatment")

    notice = record.notice
    if record.heads and not notices:
        weak.append(
            "No notice letter is linked to the claim with the role Notice, so each head will be missing its notice record."
        )
    if notice is None or notice.served_on is None:
        weak.append("No notice is recorded as served.")
    else:
        row: Dict[str, Any] = {
            **_origin(claim_id, "notice"),
            "origin": {"claim_id": claim_id},
            "claim_no": reference,
            "event": record.title,
            "event_date": record.event_date,
            "awareness_date": record.awareness_date,
            "notice_date": notice.served_on,
            "notice_document_ids": list(notices) or None,
        }
        if notice.period_source == "contract_verified":
            row.update(
                {
                    "notice_period_days": notice.period_days,
                    "notice_clause": notice.clause_no,
                    "counted_from": notice.basis,
                    "compliant": notice.state != "served_late",
                    "period_status": "verified against the contract",
                }
            )
        else:
            row["period_status"] = "not verified against the contract"
            left.append(
                f"The {notice.period_days}-day notice period, which has not been verified against the contract. "
                "Only the dates are carried; timeliness is left to be established in the case."
            )
        rows.append(("notice-compliance", row))

    dated: List[tuple[str, datetime, str]] = []
    if record.event_date:
        dated.append(("event", record.event_date, f"{reference}: the event giving rise to the claim occurred."))
    if record.awareness_date:
        dated.append(("awareness", record.awareness_date, f"{reference}: the claimant became aware of the event."))
    if notice is not None and notice.served_on is not None:
        dated.append(("notice", notice.served_on, f"{reference}: notice of the claim was given."))
    for key, moment, text in dated:
        rows.append(
            (
                "chronology-matrix",
                {**_origin(claim_id, f"date:{key}"), "origin": {"claim_id": claim_id}, "date": moment, "event": text, "event_type": key},
            )
        )
    seen: Set[str] = set()
    for head in record.heads:
        delay = record.delay.get(head.head_id)
        for event in delay.events if delay else []:
            if event.event_id in seen:
                continue
            seen.add(event.event_id)
            rows.append(
                (
                    "chronology-matrix",
                    {
                        **_origin(claim_id, f"delay:{event.event_id}"),
                        "origin": {"claim_id": claim_id, "head_id": head.head_id},
                        "date": event.start_date,
                        "end_date": event.end_date,
                        "event": f"{event.label}: {event.title}",
                        "event_type": "delay_event",
                        "source_record_id": event.event_id,
                        "risk_owner": event.decision.risk_owner,
                        "criticality": event.decision.criticality,
                    },
                )
            )
    for item in record.evidence:
        rows.append(
            (
                "document-index",
                {
                    **_origin(claim_id, f"document:{item.document_id}"),
                    "origin": {"claim_id": claim_id},
                    "document_id": item.document_id,
                    "source_id": item.document_id,
                    "title": item.title,
                    "reference_no": item.reference,
                    "document_date": item.date,
                },
            )
        )
    if not record.evidence:
        weak.append("No document is linked to the claim.")

    counts: Dict[str, int] = {}
    for slug, _row in rows:
        counts[slug] = counts.get(slug, 0) + 1
    carry = ClaimCarry(
        claim_id=claim_id,
        claim_ref=claim.get("claim_ref"),
        title=str(claim.get("title") or ""),
        status=str(claim.get("status") or ""),
        board_locked=bool(bench and bench.stage == "locked"),
        has_board=bench is not None,
        already_in_case_id=claim.get("arbitration_case_id"),
        rows=counts,
        weak=weak,
        not_carried=left,
        status_after=status_after(str(claim.get("status") or "")),
    )
    return carry, rows


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class ClaimEscalation:
    def __init__(self, db: Any, bench: Any) -> None:
        self.db = db
        self.bench = bench
        self.cases = ArbitrationCaseWorkspaceService(db)

    async def _carry(self, claim: Dict[str, Any], document_ids: Set[str], notice_ids: Iterable[str] = ()):
        bench = self.bench._bench_of(claim)
        working = bench or ClaimBench(heads=[initial_head(claim)])
        record = await ClaimSubmissions(self.db, self.bench).gather(claim, working, document_ids)
        # A notice letter is carried only if it is also among the documents the caller may see.
        return build_carry(claim, record, bench, [item for item in notice_ids if item in document_ids])

    @staticmethod
    def _blockers(claims: List[Dict[str, Any]], case: Optional[Dict[str, Any]]) -> List[str]:
        blockers: List[str] = []
        projects = {str(claim.get("project_id") or "") for claim in claims}
        if len(projects) > 1 or "" in projects:
            blockers.append("Claims escalated together must belong to one project.")
        if len({str(claim["_id"]) for claim in claims}) != len(claims):
            blockers.append("A claim is listed twice.")
        target = str(case["_id"]) if case else None
        for claim in claims:
            held = claim.get("arbitration_case_id")
            if held and held != target:
                blockers.append(f"{claim.get('claim_ref') or claim['_id']} is already in another arbitration case.")
            if str(claim.get("status") or "") in {ClaimStatus.CLOSED.value, ClaimStatus.AGREED.value, ClaimStatus.POTENTIAL.value}:
                blockers.append(
                    f"{claim.get('claim_ref') or claim['_id']} is {claim.get('status')}; a claim in that state is not escalated."
                )
        if case is not None and projects and str(case.get("project_id") or "") not in projects:
            blockers.append("The case belongs to a different project from the claims.")
        return blockers

    async def preview(
        self,
        claims: List[Dict[str, Any]],
        documents: Dict[str, Set[str]],
        case: Optional[Dict[str, Any]] = None,
        notices: Optional[Dict[str, Set[str]]] = None,
    ) -> EscalationPreview:
        notices = notices or {}
        carries = [
            (await self._carry(claim, documents.get(str(claim["_id"]), set()), notices.get(str(claim["_id"]), set())))[0]
            for claim in claims
        ]
        totals: Dict[str, int] = {}
        for carry in carries:
            for slug, count in carry.rows.items():
                totals[slug] = totals.get(slug, 0) + count
        blockers = self._blockers(claims, case)
        return EscalationPreview(
            project_id=str(claims[0].get("project_id") or "") or None,
            claims=carries,
            totals={slug: totals[slug] for slug in MATRIX_ORDER if slug in totals},
            blockers=blockers,
            can_escalate=not blockers,
        )

    async def escalate(
        self,
        claims: List[Dict[str, Any]],
        documents: Dict[str, Set[str]],
        request: EscalationRequest,
        current_user: Any,
        case: Optional[Dict[str, Any]] = None,
        notices: Optional[Dict[str, Set[str]]] = None,
    ) -> EscalationResult:
        notices = notices or {}
        blockers = self._blockers(claims, case)
        if blockers:
            raise HTTPException(status_code=409, detail="; ".join(blockers))
        if case is None and request.new_case is None:
            raise HTTPException(status_code=422, detail="Choose an existing case or give the new case a title.")
        user_id = str(getattr(current_user, "id", "") or "") or None
        created = case is None
        if case is None:
            case = await self.cases.create_case(
                ArbitrationCaseCreate(
                    organization_id=claims[0].get("organization_id"),
                    project_id=str(claims[0].get("project_id")),
                    title=request.new_case.title.strip(),  # type: ignore[union-attr]
                    case_reference=request.new_case.case_reference,  # type: ignore[union-attr]
                    party_perspective=request.new_case.party_perspective,  # type: ignore[union-attr,arg-type]
                ),
                current_user,
            )
        case_id = str(case["_id"])
        created_rows: Dict[str, int] = {}
        present = 0
        carries: List[ClaimCarry] = []
        for claim in claims:
            carry, rows = await self._carry(
                claim, documents.get(str(claim["_id"]), set()), notices.get(str(claim["_id"]), set())
            )
            for slug, row in rows:
                collection = self.db[MATRIX_COLLECTIONS[slug]]
                if await collection.find_one({"case_id": case_id, "origin_key": row["origin_key"]}):
                    present += 1
                    continue
                payload = {key: value for key, value in row.items() if value is not None}
                await self.cases.create_matrix_row(case_id, slug, ArbitrationMatrixRowCreate(**payload), current_user)
                created_rows[slug] = created_rows.get(slug, 0) + 1
            update: Dict[str, Any] = {
                "arbitration_case_id": case_id,
                "status": carry.status_after,
                "updated_at": datetime.utcnow(),
                "updated_by": user_id,
            }
            if not claim.get("evidence_frozen_at"):
                update.update(
                    {"evidence_frozen_at": now_utc(), "evidence_frozen_by": user_id, "evidence_freeze_reason": FREEZE_REASON}
                )
            await self.db.claims.update_one({"_id": claim["_id"]}, {"$set": update})
            carries.append(carry)
        claim_ids = list(dict.fromkeys([*(case.get("claim_ids") or []), *[str(claim["_id"]) for claim in claims]]))
        await self.db.arbitration_cases.update_one(
            {"_id": case["_id"]}, {"$set": {"claim_ids": claim_ids, "updated_at": datetime.utcnow(), "updated_by": user_id}}
        )
        # Creating a row through the case workspace withdraws any readiness approval
        # already given on the case, as it does for a row added by hand.
        await AuditEventService(self.db).emit(
            action="claim.escalated_to_arbitration",
            actor_id=user_id,
            resource_type="arbitration_case",
            resource_id=case_id,
            organization_id=case.get("organization_id"),
            project_id=case.get("project_id"),
            after={"claim_ids": [str(claim["_id"]) for claim in claims], "rows_created": created_rows, "created_case": created},
        )
        logger.info("claims escalated: case=%s claims=%s rows=%s", case_id, len(claims), created_rows)
        return EscalationResult(
            case_id=case_id,
            case_title=str(case.get("title") or ""),
            created_case=created,
            rows_created={slug: created_rows[slug] for slug in MATRIX_ORDER if slug in created_rows},
            rows_already_present=present,
            claims=carries,
        )


__all__ = [
    "ClaimCarry",
    "ClaimEscalation",
    "EscalationPreview",
    "EscalationRequest",
    "EscalationResult",
    "NewCase",
    "build_carry",
    "status_after",
]
