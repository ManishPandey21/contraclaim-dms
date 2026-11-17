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

Safety (D-18, plan 4B-36, C-10). An escalation run:

1. refuses a filed or archived case, and checks that every claim not yet frozen
   can be frozen, before it writes anything (nothing unfreezes a claim);
2. for a new case, takes its idempotency key (a unique ``_id`` in
   ``claim_escalation_keys``): a second run with the same key waits for the
   first run's case and extends it, so two clicks never make two cases;
3. reserves each claim with one conditional update on ``{arbitration_case_id:
   null, status}`` that moves the status through the lifecycle and records what
   it changed (prior status, freeze state, case, key) under ``escalation``. A
   claim already in this case is left as it is (a re-run is a no-op); a claim
   taken by another case meanwhile undoes this run's reservations, discards a
   case this run created, and answers 409;
4. freezes each claim's evidence through ``DocumentRelationshipService.freeze``;
5. seeds the rows. Migration 20261120_0001 (M-05) makes (case, origin) unique,
   and a duplicate-key refusal counts as "already carried";
6. audits each claim's move and the case.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from fastapi import HTTPException
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from . import claim_contract_scope as contract_scope
from .claim_delay_lab import ClaimDelayLab
from ..models.arbitration_drafting import ArbitrationCaseCreate, ArbitrationMatrixRowCreate
from ..models.claim import ALLOWED_STATUS_TRANSITIONS, ELEMENT_KEYS, ClaimBench, ClaimStatus, HeadElement
from ..utils.datetime import now_utc
from .arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService
from .arbitration_drafting.matrix_registry import MATRIX_COLLECTIONS
from .audit_event_service import AuditEventService
from .claim_bench import initial_head
from .claim_submission import ClaimSubmissions, SubmissionRecord, format_paise, head_amount, head_days
from .quantum_formulas import rupees
from .document_relationship_service import DocumentRelationshipError, DocumentRelationshipService

logger = logging.getLogger(__name__)

MAX_CLAIMS = 20
FREEZE_REASON = "Escalated to arbitration"
KEYS = "claim_escalation_keys"
#: A case in these states takes no more claims.
CLOSED_CASE_STATUSES = {"filed", "archived"}
#: How long a run waits for another run holding the same idempotency key.
KEY_WAIT_SECONDS = 10.0
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
    # Required with new_case: one key per "Escalate" action, so a repeated click or a
    # retried request extends the case the first one created (D-18).
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    # D-18 (4B-40): claims whose board is not locked and whose record is weak are
    # escalated only when the user has acknowledged what is weak for each, by id.
    acknowledged_weak: List[str] = Field(default_factory=list, max_length=MAX_CLAIMS)


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "user_message": message, "detail": None})


class UndoRequest(BaseModel):
    claim_id: str = Field(..., min_length=1, max_length=120)
    case_id: str = Field(..., min_length=1, max_length=120)
    reason: str = Field(..., min_length=10, max_length=1000)


class UndoResult(BaseModel):
    claim_id: str
    case_id: str
    status: str
    rows_archived: int = 0
    freeze_lifted: bool = False
    case_discarded: bool = False


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
    #: Evidence whose contract is not confirmed, or that names none (owner review of
    #: 4B-23). Shown before escalating; nothing here blocks.
    evidence_notes: List[str] = Field(default_factory=list)
    #: OD-1: records awaiting a person's confirmation of their contract, per claim.
    unconfirmed_evidence: List[Dict[str, Any]] = Field(default_factory=list)


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
        amount_paise, built = head_amount(record, head)
        # The matrix row keeps rupees (its existing shape), derived from the paise held.
        amount = rupees(amount_paise) if amount_paise is not None else None
        states = {key: (head.elements.get(key) or HeadElement()).state for key in ELEMENT_KEYS}
        for key, state in states.items():
            if state in (None, "weak", "missing"):
                weak.append(f"{head.head_id}: {_ELEMENT_LABEL[key]} is {state or 'not decided'}")
        causation = head.elements.get("causation") or HeadElement()
        relief = []
        if days is not None:
            relief.append(f"Extension of time of {days} day{'' if days == 1 else 's'}")
        if amount is not None:
            relief.append(f"Payment of {format_paise(amount_paise, record.currency)}")
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
                    "amount_paise": amount_paise,
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
                f"({format_paise(view.locked_total_paise, record.currency)}) whose proof is not linked"
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
    def __init__(self, db: Any, bench: Any, relationships: Any = None) -> None:
        self.db = db
        self.bench = bench
        self.cases = ArbitrationCaseWorkspaceService(db)
        self.relationships = relationships or DocumentRelationshipService(db)

    async def _carry(self, claim: Dict[str, Any], document_ids: Set[str], notice_ids: Iterable[str] = ()):
        bench = self.bench._bench_of(claim)
        working = bench or ClaimBench(heads=[initial_head(claim)])
        record = await ClaimSubmissions(self.db, self.bench).gather(claim, working, document_ids)
        # A notice letter is carried only if it is also among the documents the caller may see.
        return build_carry(claim, record, bench, [item for item in notice_ids if item in document_ids])

    async def _foreign_evidence(self, claim: Dict[str, Any], document_ids: Set[str]) -> List[str]:
        links = await ClaimDelayLab(self.db, self.bench).links_for_claim(str(claim["_id"]))
        return await contract_scope.evidence_conflicts(self.db, claim, links, document_ids)

    async def _contract_review(
        self, claims: List[Dict[str, Any]], documents: Dict[str, Set[str]], unconfirmed: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[List[str], List[str]]:
        """Blockers (records of another contract) and notes (records not confirmed against
        the Contract Master, or naming no contract) for each claim."""
        blockers: List[str] = []
        notes: List[str] = []
        lab = ClaimDelayLab(self.db, self.bench)
        for claim in claims:
            links = await lab.links_for_claim(str(claim["_id"]))
            review = await contract_scope.identity_review(self.db, claim, links, documents.get(str(claim["_id"]), set()))
            reference = str(claim.get("claim_ref") or claim["_id"])
            if review.conflicts:
                blockers.append(f"{reference}: {contract_scope.message(claim, review.conflicts)}")
            waiting = contract_scope.unconfirmed_reason(claim, review)
            if waiting:
                blockers.append(f"{reference}: {waiting}")
                if unconfirmed is not None:
                    unconfirmed.extend({"claim_id": str(claim["_id"]), **vars(item)} for item in review.unconfirmed)
            notes.extend(f"{reference}: {note}" for note in contract_scope.identity_notes(claim, review))
        return blockers, notes

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
        if case is not None and str(case.get("status") or "") in CLOSED_CASE_STATUSES:
            blockers.append(f"The case is {case.get('status')}; claims cannot be added to it.")
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
        unconfirmed: List[Dict[str, Any]] = []
        contract_blockers, notes = await self._contract_review(claims, documents, unconfirmed)
        blockers = self._blockers(claims, case) + contract_blockers
        return EscalationPreview(
            project_id=str(claims[0].get("project_id") or "") or None,
            claims=carries,
            totals={slug: totals[slug] for slug in MATRIX_ORDER if slug in totals},
            blockers=blockers,
            can_escalate=not blockers,
            evidence_notes=notes,
            unconfirmed_evidence=unconfirmed,
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
        # 4B-23 (D-23): nothing of another contract is carried into a case; OD-1: nothing
        # whose contract a person has not confirmed.
        lab = ClaimDelayLab(self.db, self.bench)
        for claim in claims:
            review = await contract_scope.identity_review(
                self.db, claim, await lab.links_for_claim(str(claim["_id"])), documents.get(str(claim["_id"]), set())
            )
            if review.conflicts:
                raise contract_scope.refusal(claim, review.conflicts)
            waiting = contract_scope.unconfirmed_reason(claim, review)
            if waiting:
                raise HTTPException(
                    status_code=409,
                    detail={"code": contract_scope.UNCONFIRMED_CODE, "user_message": waiting, "detail": waiting},
                )
        if case is None and request.new_case is None:
            raise HTTPException(status_code=422, detail="Choose an existing case or give the new case a title.")
        if case is None and not request.idempotency_key:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "escalation_key_required",
                    "user_message": "This request is missing its idempotency key. Reload the page and try again.",
                    "detail": None,
                },
            )
        user_id = str(getattr(current_user, "id", "") or "") or None

        # 0. D-18: an unlocked board with weak points goes only with the user's acknowledgement.
        unacknowledged: List[str] = []
        for claim in claims:
            carry, _rows = await self._carry(
                claim, documents.get(str(claim["_id"]), set()), notices.get(str(claim["_id"]), set())
            )
            if carry.weak and not carry.board_locked and str(claim["_id"]) not in set(request.acknowledged_weak):
                unacknowledged.append(str(claim.get("claim_ref") or claim["_id"]))
        if unacknowledged:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "escalation_weak_unacknowledged",
                    "user_message": (
                        "Acknowledge what is weak before escalating a claim whose board is not locked: "
                        + ", ".join(unacknowledged)
                        + "."
                    ),
                    "detail": {"claims": unacknowledged},
                },
            )

        # 1. Nothing unfreezes a claim, so every freeze is checked before anything is written.
        for claim in claims:
            if not claim.get("evidence_frozen_at"):
                await self.relationships.freeze_preflight(current_user, "claim", str(claim["_id"]))

        # 2. The case: the chosen one, or the one this request's key created.
        created = False
        if case is None:
            case, created = await self._new_case(claims, request, current_user, user_id)
            blockers = self._blockers(claims, case)
            if blockers:
                raise HTTPException(status_code=409, detail="; ".join(blockers))
        case_id = str(case["_id"])

        # 3. Reserve every claim for this case, or none.
        reserved: List[Dict[str, Any]] = []
        for claim in claims:
            outcome = await self._reserve(claim, case_id, created, request.idempotency_key, user_id)
            if outcome == "conflict":
                await self._release(reserved, case_id)
                if created:
                    await self._discard_case(case, request.idempotency_key)
                raise _conflict(
                    "escalation_claim_taken",
                    f"{claim.get('claim_ref') or claim['_id']} was changed or escalated by someone else at the same "
                    "time. Nothing was escalated; reload and try again.",
                )
            if outcome == "reserved":
                reserved.append(claim)

        # 4. Freeze the evidence (checked in step 1). What each link pointed at before is
        # recorded first, so an undo (4B-37) can give it back exactly.
        for claim in reserved:
            if not claim.get("evidence_frozen_at"):
                await self._record_links_before(claim, case_id)
        for claim in claims:
            await self._freeze(claim, current_user)
        for claim in reserved:
            if not claim.get("evidence_frozen_at"):
                frozen = await self.db.claims.find_one({"_id": claim["_id"]}) or {}
                await self.db.claims.update_one(
                    {"_id": claim["_id"], "escalation.case_id": case_id},
                    {"$set": {"escalation.frozen_at": frozen.get("evidence_frozen_at")}},
                )

        # 5. Seed the rows; a row already carried is refused by the unique index (M-05).
        created_rows: Dict[str, int] = {}
        present = 0
        carries: List[ClaimCarry] = []
        for claim in claims:
            carry, rows = await self._carry(
                claim, documents.get(str(claim["_id"]), set()), notices.get(str(claim["_id"]), set())
            )
            seeded: List[Dict[str, str]] = []
            for slug, row in rows:
                collection = self.db[MATRIX_COLLECTIONS[slug]]
                if await collection.find_one({"case_id": case_id, "origin_key": row["origin_key"]}):
                    present += 1
                    continue
                payload = {key: value for key, value in row.items() if value is not None}
                try:
                    stored = await self.cases.create_matrix_row(
                        case_id, slug, ArbitrationMatrixRowCreate(**payload), current_user
                    )
                except DuplicateKeyError:
                    present += 1  # a concurrent run carried it first
                    continue
                created_rows[slug] = created_rows.get(slug, 0) + 1
                seeded.append({"matrix": slug, "row_id": str((stored or {}).get("_id") or "")})
            if seeded:
                await self.db.claims.update_one(
                    {"_id": claim["_id"], "escalation.case_id": case_id},
                    {"$push": {"escalation.seeded_rows": {"$each": seeded}}},
                )
            carries.append(carry)

        # 6. The case lists its claims; each move is audited.
        await self.db.arbitration_cases.update_one(
            {"_id": case["_id"]},
            {
                "$addToSet": {"claim_ids": {"$each": [str(claim["_id"]) for claim in claims]}},
                "$set": {"updated_at": datetime.utcnow(), "updated_by": user_id},
            },
        )
        audit = AuditEventService(self.db)
        for claim in reserved:
            await audit.emit(
                action="claim.escalated",
                actor_id=user_id,
                resource_type="claim",
                resource_id=str(claim["_id"]),
                organization_id=claim.get("organization_id"),
                project_id=claim.get("project_id"),
                before={"status": claim.get("status"), "arbitration_case_id": None},
                after={"status": status_after(str(claim.get("status") or "")), "arbitration_case_id": case_id},
                metadata={"created_case": created, "idempotency_key": request.idempotency_key},
            )
        # Creating a row through the case workspace withdraws any readiness approval
        # already given on the case, as it does for a row added by hand.
        await audit.emit(
            action="claim.escalated_to_arbitration",
            actor_id=user_id,
            resource_type="arbitration_case",
            resource_id=case_id,
            organization_id=case.get("organization_id"),
            project_id=case.get("project_id"),
            after={
                "claim_ids": [str(claim["_id"]) for claim in claims],
                "claims_reserved": [str(claim["_id"]) for claim in reserved],
                "rows_created": created_rows,
                "created_case": created,
            },
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

    # ------------------------------------------------------------ helpers
    async def _new_case(
        self, claims: List[Dict[str, Any]], request: EscalationRequest, current_user: Any, user_id: Optional[str]
    ) -> tuple[Dict[str, Any], bool]:
        """The case this request's idempotency key names: created once, found by every repeat."""
        organization_id = claims[0].get("organization_id")
        claim_ids = sorted(str(claim["_id"]) for claim in claims)
        key_id = f"{organization_id}:{request.idempotency_key}"
        try:
            await self.db[KEYS].insert_one(
                {"_id": key_id, "case_id": None, "claim_ids": claim_ids, "created_by": user_id, "created_at": now_utc()}
            )
        except DuplicateKeyError:
            return await self._case_for_key(key_id, claim_ids), False
        try:
            case = await self.cases.create_case(
                ArbitrationCaseCreate(
                    organization_id=organization_id,
                    project_id=str(claims[0].get("project_id")),
                    title=request.new_case.title.strip(),  # type: ignore[union-attr]
                    case_reference=request.new_case.case_reference,  # type: ignore[union-attr]
                    party_perspective=request.new_case.party_perspective,  # type: ignore[union-attr,arg-type]
                ),
                current_user,
            )
        except BaseException:
            await self.db[KEYS].delete_one({"_id": key_id, "case_id": None})
            raise
        await self.db[KEYS].update_one({"_id": key_id}, {"$set": {"case_id": str(case["_id"])}})
        return case, True

    async def _case_for_key(self, key_id: str, claim_ids: List[str]) -> Dict[str, Any]:
        waited = 0.0
        while True:
            held = await self.db[KEYS].find_one({"_id": key_id})
            if held is None:
                raise _conflict("escalation_key_released", "The earlier attempt did not finish. Try again.")
            if held.get("claim_ids") != claim_ids:
                raise _conflict(
                    "escalation_key_reused", "This escalation key was used for different claims. Reload and try again."
                )
            if held.get("case_id"):
                return await self.cases.get_case(str(held["case_id"]))
            if waited >= KEY_WAIT_SECONDS:
                raise _conflict("escalation_in_progress", "This escalation is still running. Try again in a moment.")
            await asyncio.sleep(0.1)
            waited += 0.1

    async def _reserve(
        self, claim: Dict[str, Any], case_id: str, created_case: bool, key: Optional[str], user_id: Optional[str]
    ) -> str:
        """``reserved``, ``already`` (in this case: a re-run) or ``conflict``."""
        prior = str(claim.get("status") or "")
        result = await self.db.claims.update_one(
            {"_id": claim["_id"], "arbitration_case_id": None, "status": claim.get("status")},
            {
                "$set": {
                    "arbitration_case_id": case_id,
                    "status": status_after(prior),
                    "updated_at": datetime.utcnow(),
                    "updated_by": user_id,
                    "escalation": {
                        "case_id": case_id,
                        "prior_status": prior,
                        "status_after": status_after(prior),
                        "was_frozen": bool(claim.get("evidence_frozen_at")),
                        "created_case": created_case,
                        "idempotency_key": key,
                        "escalated_by": user_id,
                        "escalated_at": now_utc(),
                        "seeded_rows": [],
                    },
                }
            },
        )
        if getattr(result, "matched_count", 0):
            return "reserved"
        fresh = await self.db.claims.find_one({"_id": claim["_id"]}) or {}
        return "already" if fresh.get("arbitration_case_id") == case_id else "conflict"

    async def _freeze(self, claim: Dict[str, Any], current_user: Any) -> None:
        try:
            await self.relationships.freeze(
                current_user, "claim", str(claim["_id"]), reason=FREEZE_REASON, allow_already_frozen=True
            )
        except DocumentRelationshipError:
            # A concurrent run of the same escalation froze it a moment earlier: that is the
            # state this run wants. Anything else is a real refusal.
            fresh = await self.db.claims.find_one({"_id": claim["_id"]}) or {}
            if not fresh.get("evidence_frozen_at"):
                raise

    async def _record_links_before(self, claim: Dict[str, Any], case_id: str) -> None:
        links = await self.db.entity_document_links.find(
            {"target_type": "claim", "target_id": str(claim["_id"]), "removed_at": None}
        ).to_list(length=None)
        await self.db.claims.update_one(
            {"_id": claim["_id"], "escalation.case_id": case_id},
            {
                "$set": {
                    "escalation.links_before": [
                        {"link_id": str(link.get("_id")), "document_version_id": link.get("document_version_id")}
                        for link in links
                    ]
                }
            },
        )

    # ------------------------------------------------------------ undo (4B-37)
    async def pleading_versions(self, case_id: str) -> int:
        """Pleading versions in the case: versions of any draft carrying this case's id
        (deleted drafts included: their versions existed). Scratch drafts never count."""
        draft_ids = await self.db.arbitration_drafts.distinct("_id", {"case_id": case_id})
        if not draft_ids:
            return 0
        return await self.db.arbitration_draft_versions.count_documents({"draft_id": {"$in": draft_ids}})

    async def undo(
        self, claim: Dict[str, Any], case: Dict[str, Any], request: "UndoRequest", current_user: Any
    ) -> "UndoResult":
        """Reverse one claim's escalation, exactly, until the case's first pleading version (D-18, AQ-6)."""
        reason = " ".join(request.reason.split())
        if len(reason.strip(" .,;:-")) < 10:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "escalation_undo_reason",
                    "user_message": "Say why the escalation is being undone (at least 10 characters).",
                    "detail": None,
                },
            )
        case_id = str(case["_id"])
        record = claim.get("escalation") or {}
        if claim.get("arbitration_case_id") != case_id or record.get("case_id") != case_id:
            raise _conflict("escalation_not_found", "This claim was not escalated into this case, so there is nothing to undo.")
        if await self.pleading_versions(case_id):
            raise _conflict(
                "escalation_undo_after_pleading",
                "A pleading version exists in this case, so the escalation can no longer be undone.",
            )
        user_id = str(getattr(current_user, "id", "") or "") or None
        now = now_utc()
        # 1. The claim goes back to the status it had, if it has not moved since.
        result = await self.db.claims.update_one(
            {
                "_id": claim["_id"],
                "arbitration_case_id": case_id,
                "escalation.case_id": case_id,
                "status": record.get("status_after", claim.get("status")),
            },
            {
                "$set": {
                    "arbitration_case_id": None,
                    "status": record.get("prior_status") or claim.get("status"),
                    "updated_at": datetime.utcnow(),
                    "updated_by": user_id,
                },
                "$unset": {"escalation": ""},
                "$push": {
                    "escalation_history": {**record, "undone_at": now, "undone_by": user_id, "undo_reason": reason}
                },
            },
        )
        if not getattr(result, "matched_count", 0):
            raise _conflict(
                "escalation_claim_moved",
                "The claim has changed since it was escalated (its status moved on), so the escalation is not undone.",
            )
        # 2. The freeze this escalation made is lifted; an earlier freeze is kept.
        freeze_lifted = False
        if not record.get("was_frozen") and record.get("frozen_at"):
            await self.relationships.lift_freeze(
                current_user,
                "claim",
                str(claim["_id"]),
                frozen_at=record["frozen_at"],
                versions_before={
                    str(item.get("link_id")): item.get("document_version_id")
                    for item in record.get("links_before") or []
                },
                reason=f"Escalation undone: {reason}",
            )
            freeze_lifted = True
        # 3. The rows it seeded are archived with the reason; their origin is released so
        # a later escalation can carry the claim again (M-05 indexes live rows only by origin).
        archived = 0
        for item in record.get("seeded_rows") or []:
            collection = self.db[MATRIX_COLLECTIONS[item["matrix"]]]
            row = await collection.find_one({"_id": item["row_id"], "case_id": case_id, "deleted_at": {"$exists": False}})
            if not row:
                continue
            await collection.update_one(
                {"_id": row["_id"], "deleted_at": {"$exists": False}},
                {
                    "$set": {
                        "deleted_at": now,
                        "deleted_by": user_id,
                        "delete_reason": f"Escalation undone: {reason}",
                        "archived_origin_key": row.get("origin_key"),
                    },
                    "$unset": {"origin_key": ""},
                },
            )
            archived += 1
        # 4. The case loses the claim; a case this escalation created and nothing else uses goes too.
        await self.db.arbitration_cases.update_one(
            {"_id": case["_id"]},
            {"$pull": {"claim_ids": str(claim["_id"])}, "$set": {"updated_at": datetime.utcnow(), "updated_by": user_id}},
        )
        if archived:
            await self.cases.invalidate_readiness_approvals(case_id, "escalation_undone", current_user)
        discarded = False
        if record.get("created_case"):
            fresh = await self.db.arbitration_cases.find_one({"_id": case["_id"]}) or {}
            live_rows = 0
            for name in set(MATRIX_COLLECTIONS.values()):
                live_rows += await self.db[name].count_documents({"case_id": case_id, "deleted_at": {"$exists": False}})
            drafts = await self.db.arbitration_drafts.count_documents({"case_id": case_id})
            if not fresh.get("claim_ids") and not live_rows and not drafts:
                await self.db.arbitration_cases.update_one(
                    {"_id": case["_id"], "deleted_at": None},
                    {"$set": {"deleted_at": now, "deleted_reason": f"Escalation undone: {reason}"}},
                )
                discarded = True
        audit = AuditEventService(self.db)
        await audit.emit(
            action="claim.escalation_undone",
            actor_id=user_id,
            resource_type="claim",
            resource_id=str(claim["_id"]),
            organization_id=claim.get("organization_id"),
            project_id=claim.get("project_id"),
            reason=reason,
            before={"status": claim.get("status"), "arbitration_case_id": case_id},
            after={"status": record.get("prior_status"), "arbitration_case_id": None},
            metadata={"rows_archived": archived, "freeze_lifted": freeze_lifted, "case_discarded": discarded},
        )
        await audit.emit(
            action="arbitration_case.claim_escalation_undone",
            actor_id=user_id,
            resource_type="arbitration_case",
            resource_id=case_id,
            organization_id=case.get("organization_id"),
            project_id=case.get("project_id"),
            reason=reason,
            after={"claim_id": str(claim["_id"]), "rows_archived": archived, "case_discarded": discarded},
        )
        return UndoResult(
            claim_id=str(claim["_id"]),
            case_id=case_id,
            status=str(record.get("prior_status") or claim.get("status") or ""),
            rows_archived=archived,
            freeze_lifted=freeze_lifted,
            case_discarded=discarded,
        )

    async def _release(self, reserved: List[Dict[str, Any]], case_id: str) -> None:
        for claim in reserved:
            await self.db.claims.update_one(
                {"_id": claim["_id"], "arbitration_case_id": case_id, "escalation.case_id": case_id},
                {"$set": {"arbitration_case_id": None, "status": claim.get("status")}, "$unset": {"escalation": ""}},
            )

    async def _discard_case(self, case: Dict[str, Any], key: Optional[str]) -> None:
        """A case this run created and could not use: soft-deleted, and its key released."""
        await self.db.arbitration_cases.update_one(
            {"_id": case["_id"]},
            {"$set": {"deleted_at": now_utc(), "deleted_reason": "Escalation did not complete; no claim was added"}},
        )
        if key:
            await self.db[KEYS].delete_one({"_id": f"{case.get('organization_id')}:{key}"})


__all__ = [
    "ClaimCarry",
    "ClaimEscalation",
    "EscalationPreview",
    "EscalationRequest",
    "EscalationResult",
    "NewCase",
    "UndoRequest",
    "UndoResult",
    "build_carry",
    "status_after",
]
