"""Quantum Builder: a head's amount as a build-up of lines, each with proof locks.

The arithmetic is plain addition. What this module adds is the rule that a
line does not count as supported until what the claim's own principles require
for that kind of cost is in place. A locked line stays on the board, with the
exact thing that would unlock it, and is left out of the supported total.

Locks, by category:

- **direct, disruption**: at least one supporting record.
- **prolongation**: a period; every day of it within the periods the Delay Lab's
  decisions make compensable; at least one supporting record.
- **overhead_formula** (Hudson, Emden, Eichleay): a formula chosen; a period
  within the compensable periods; a record of actual loss. A formula is never
  accepted on assertion.
- **loss_of_profit**: a record of work that was available and declined.
- **interest**: a stated basis (the clause or rate relied on).

Records are documents already linked to the claim. Every lock re-checks that a
record (and each formula input's record) is still linked, against the claim's
whole set of linked documents, not the viewer's: two people see the same locks.

Money is integer paise (4B-24, D-14). A Hudson, Emden or Eichleay line's amount
is computed from its inputs (services/quantum_formulas.py) and never typed.
"""

from __future__ import annotations

import itertools
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import claim_contract_scope as contract_scope
from ..models.claim import ClaimBench, ClaimHead
from ..models.claim_delay import FormulaInput, QuantumLine, QuantumRecord
from .quantum_formulas import FORMULA_CAUTIONS, FORMULA_INPUTS, compute_paise, delay_days, inconsistencies, missing_inputs, rupees, to_paise
from .claim_delay_engine import compensable_days, days_outside
from .claim_delay_lab import ClaimDelayLab, _day, _head, engine_events, head_treatments

MAX_LINES = 200
_CATEGORIES = {"direct", "prolongation", "disruption", "overhead_formula", "loss_of_profit", "interest"}
_FORMULAS = {"none", "hudson", "emden", "eichleay"}
_ROLES = {"support", "actual_loss", "work_declined"}
_PERIOD_CATEGORIES = {"prolongation", "overhead_formula"}


class QuantumLineInput(BaseModel):
    category: str
    description: str = Field(..., min_length=1, max_length=500)
    #: Rupees, read through its decimal text; or ``amount_paise``. Neither is sent for
    #: a formula line, whose amount is computed.
    amount: Optional[Decimal] = Field(default=None, ge=0)
    amount_paise: Optional[int] = Field(default=None, ge=0)
    formula_inputs: Dict[str, FormulaInput] = Field(default_factory=dict)
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    formula: str = "none"
    basis: Optional[str] = Field(default=None, max_length=1000)
    records: List[QuantumRecord] = Field(default_factory=list)


class QuantumLineRequest(QuantumLineInput):
    board_version: int = Field(..., ge=0)


CALCULATION_NOTE = (
    "Calculated from the figures entered. A calculation is not a finding that this amount is "
    "recoverable: entitlement under the contract, evidence that the overhead was in fact unabsorbed, "
    "and whether the formula's conditions are met are decided separately. The tribunal chooses the "
    "method and may insist on proof of actual loss."
)


class QuantumLineView(BaseModel):
    line: QuantumLine
    locked: bool
    # Each entry is one thing that would have to be true for the line to unlock.
    lock_reasons: List[str] = Field(default_factory=list)
    #: A formula line: its amount is arithmetic from the figures, not a determination.
    calculated: bool = False
    calculation_note: Optional[str] = None


class QuantumView(BaseModel):
    head_id: str
    currency: str = "INR"
    lines: List[QuantumLineView] = Field(default_factory=list)
    #: Totals in integer paise; the rupee figures are derived from them.
    claimed_total_paise: int = 0
    supported_total_paise: int = 0
    locked_total_paise: int = 0
    claimed_total: float = 0.0
    supported_total: float = 0.0
    locked_total: float = 0.0
    locked_count: int = 0
    #: Of the claimed total, the part calculated by a formula (a calculation, not a determination).
    calculated_total_paise: int = 0


_ROLE_WORDS = {"support": "supporting record", "actual_loss": "record of actual loss", "work_declined": "record of work declined"}


def line_lock_reasons(line: QuantumLine, compensable: Set[date], linked: Optional[Set[str]] = None) -> List[str]:
    """Why a line is locked. Empty means it counts as supported.

    ``linked`` is the claim's whole set of linked documents. A record that is no
    longer in it does not count, and the line says which one to link again.
    """
    reasons: List[str] = []
    live = [record for record in line.records if linked is None or record.document_id in linked]
    for position, record in enumerate(line.records, start=1):
        if linked is not None and record.document_id not in linked:
            reasons.append(
                f"Record {position} on this line ({_ROLE_WORDS.get(record.role, 'record')}) is no longer linked to "
                "this claim. Link it to the claim again, or remove it from the line."
            )
    if line.formula in FORMULA_INPUTS:
        for spec in FORMULA_INPUTS[line.formula]:
            entry = line.formula_inputs.get(spec.key)
            if entry is None or not entry.document_id:
                reasons.append(f"Link the record that \"{spec.label}\" is taken from. A formula input is not accepted on assertion.")
            elif linked is not None and entry.document_id not in linked:
                reasons.append(
                    f"The record for \"{spec.label}\" is no longer linked to this claim. Link it to the claim again."
                )
    roles = {record.role for record in live}
    if line.category in {"direct", "disruption", "prolongation"} and not live:
        reasons.append("Link at least one record that supports this cost (invoice, timesheet, plant or site record).")
    if line.category in _PERIOD_CATEGORIES:
        start, end = _day(line.period_start), _day(line.period_end)
        if start is None or end is None:
            reasons.append("State the period this cost was incurred over.")
        elif end < start:
            reasons.append("The period ends before it starts.")
        else:
            outside, total = days_outside(start, end, compensable)
            if outside:
                reasons.append(
                    f"{outside} of the {total} days in this period are not in a period decided as compensable "
                    "in the delay assessment (Employer critical delay, not concurrent or decided as carrying cost)."
                )
    if line.category == "overhead_formula":
        if line.formula == "none":
            reasons.append("Choose the formula used (Hudson, Emden or Eichleay).")
        if "actual_loss" not in roles:
            reasons.append(
                "Link a record of actual loss: unabsorbed overhead or lost opportunity, shown by contemporaneous records. "
                "A formula is not accepted on assertion."
            )
    if line.category == "loss_of_profit" and "work_declined" not in roles:
        reasons.append("Link a record of work that was available and was declined because of the delay.")
    if line.category == "interest" and not (line.basis or "").strip():
        reasons.append("State the basis for interest: the clause or rate relied on.")
    return reasons


def build_quantum_view(
    head: ClaimHead, compensable: Set[date], currency: str = "INR", linked: Optional[Set[str]] = None
) -> QuantumView:
    views = []
    overlaps = formula_overlaps(head.quantum_lines)
    for line in head.quantum_lines:
        reasons = line_lock_reasons(line, compensable, linked) + overlaps.get(line.line_id, [])
        calculated = line.formula in FORMULA_INPUTS
        views.append(
            QuantumLineView(
                line=line, locked=bool(reasons), lock_reasons=reasons,
                calculated=calculated,
                calculation_note=f"{CALCULATION_NOTE} {FORMULA_CAUTIONS[line.formula]}" if calculated else None,
            )
        )
    claimed = sum(int(view.line.amount_paise or 0) for view in views)
    locked = sum(int(view.line.amount_paise or 0) for view in views if view.locked)
    return QuantumView(
        head_id=head.head_id,
        currency=currency,
        lines=views,
        claimed_total_paise=claimed,
        supported_total_paise=claimed - locked,
        locked_total_paise=locked,
        claimed_total=rupees(claimed),
        supported_total=rupees(claimed - locked),
        locked_total=rupees(locked),
        locked_count=sum(1 for view in views if view.locked),
        calculated_total_paise=sum(int(view.line.amount_paise or 0) for view in views if view.calculated),
    )


def formula_overlaps(lines: List[QuantumLine]) -> Dict[str, List[str]]:
    """Head-office overhead recovered twice: formula lines whose periods share days.

    Every such pair locks both lines, each naming the other, until one period is
    changed or one line removed. Days are counted with both ends included."""
    formula_lines = [
        (index, line) for index, line in enumerate(lines, start=1)
        if line.formula in FORMULA_INPUTS and line.period_start is not None and line.period_end is not None
    ]
    found: Dict[str, List[str]] = {}
    for (i, first), (j, second) in itertools.combinations(formula_lines, 2):
        start = max(_day(first.period_start), _day(second.period_start))
        end = min(_day(first.period_end), _day(second.period_end))
        if start is None or end is None or end < start:
            continue
        days = (end - start).days + 1
        for (own, other_index, other) in ((first, j, second), (second, i, first)):
            found.setdefault(own.line_id, []).append(
                f"Overlaps line {other_index} ({other.formula.title()}) for {days} day{'s' if days != 1 else ''}: "
                "head-office overhead would be recovered twice for those days. Change one period or remove one line."
            )
    return found


def _refusal(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "user_message": message, "detail": message})


def _next_line_id(head: ClaimHead) -> str:
    used = {line.line_id for line in head.quantum_lines}
    index = len(head.quantum_lines) + 1
    while f"Q{index}" in used:
        index += 1
    return f"Q{index}"


class ClaimQuantum:
    def __init__(self, db: Any, bench: Any) -> None:
        self.db = db
        self.bench = bench
        self.delay = ClaimDelayLab(db, bench)

    async def _compensable(self, claim: Dict[str, Any], head: ClaimHead) -> Set[date]:
        links = [link for link in await self.delay.links_for_claim(str(claim["_id"])) if link.head_id == head.head_id]
        return compensable_days(
            engine_events(head, links), _day(head.delay.as_of_date), head_treatments(head.delay),
            include_neutral=head.delay.include_neutral,
        )

    async def linked_documents(self, claim: Dict[str, Any]) -> Set[str]:
        """Every document linked to the claim, whoever is looking (4B-24).

        Locks are judged against this set, not the viewer's filtered one, so two
        people with different document access see the same locks. Only ids are read.
        """
        rows = await self.db.entity_document_links.find(
            {"target_type": "claim", "target_id": str(claim["_id"]), "removed_at": None}, {"document_id": 1}
        ).to_list(length=None)
        linked = {str(row.get("document_id")) for row in rows if row.get("document_id")}
        linked.update(str(item) for item in (claim.get("linked_document_ids") or []) if item)
        return linked

    async def view(self, claim: Dict[str, Any], head_id: str) -> QuantumView:
        bench = self.bench._require_bench(claim)
        head = _head(bench, head_id)
        return build_quantum_view(
            head, await self._compensable(claim, head), str(claim.get("currency") or "INR"), await self.linked_documents(claim)
        )

    @staticmethod
    def _amount_paise(payload: QuantumLineInput) -> int:
        """The line's amount: computed for a formula line, typed otherwise (4B-24, D-14)."""
        if payload.formula in FORMULA_INPUTS:
            if payload.amount is not None or payload.amount_paise is not None:
                raise _refusal(
                    422, "quantum_formula_amount_computed",
                    "A formula line's amount is computed from its inputs; it is not typed.",
                )
            unknown = sorted(set(payload.formula_inputs) - {spec.key for spec in FORMULA_INPUTS[payload.formula]})
            if unknown:
                raise _refusal(422, "quantum_formula_inputs_missing", "Unknown formula input: " + ", ".join(unknown))
            missing = missing_inputs(payload.formula, payload.formula_inputs)
            if missing:
                raise _refusal(
                    422, "quantum_formula_inputs_missing",
                    "Enter every input the formula needs, as a positive figure: " + "; ".join(missing) + ".",
                )
            days = delay_days(payload.period_start, payload.period_end)
            if days is None:
                raise _refusal(
                    422, "quantum_formula_period_required",
                    "State the period of delay, start and end, for the formula: it is the number of days it multiplies.",
                )
            inconsistent = inconsistencies(payload.formula, payload.formula_inputs, days)
            if inconsistent:
                raise _refusal(422, "quantum_formula_inputs_inconsistent", " ".join(inconsistent))
            return compute_paise(payload.formula, payload.formula_inputs, days)
        if payload.formula_inputs:
            raise _refusal(422, "quantum_formula_inputs_missing", "Formula inputs apply only to a Hudson, Emden or Eichleay line.")
        if payload.amount_paise is not None:
            return int(payload.amount_paise)
        if payload.amount is None:
            raise _refusal(422, "quantum_amount_required", "Enter the amount of this line.")
        return to_paise(payload.amount)

    def _build(self, line_id: str, payload: QuantumLineInput, allowed_documents: Set[str]) -> QuantumLine:
        if payload.category not in _CATEGORIES:
            raise HTTPException(status_code=422, detail="Unknown cost category")
        if payload.formula not in _FORMULAS:
            raise HTTPException(status_code=422, detail="Unknown formula")
        if payload.formula != "none" and payload.category != "overhead_formula":
            raise HTTPException(status_code=422, detail="A formula applies only to an overhead-by-formula line")
        amount_paise = self._amount_paise(payload)
        for entry in payload.formula_inputs.values():
            if entry.document_id and entry.document_id not in allowed_documents:
                raise HTTPException(
                    status_code=422,
                    detail="A formula input's record must be a document already linked to this claim",
                )
        seen: Set[tuple] = set()
        records: List[QuantumRecord] = []
        for record in payload.records:
            if record.role not in _ROLES:
                raise HTTPException(status_code=422, detail="Unknown record role")
            if record.document_id not in allowed_documents:
                raise HTTPException(
                    status_code=422,
                    detail="A quantum record must be a document already linked to this claim",
                )
            key = (record.document_id, record.role)
            if key not in seen:
                seen.add(key)
                records.append(record)
        return QuantumLine(
            line_id=line_id,
            category=payload.category,  # type: ignore[arg-type]
            description=payload.description.strip(),
            amount_paise=amount_paise,
            formula_inputs=dict(payload.formula_inputs),
            period_start=payload.period_start,
            period_end=payload.period_end,
            formula=payload.formula,  # type: ignore[arg-type]
            basis=(payload.basis or "").strip() or None,
            records=records,
        )

    async def _refuse_foreign_records(
        self, claim: Dict[str, Any], request: QuantumLineRequest, allowed_documents: Set[str]
    ) -> None:
        """4B-23 (D-23): a quantum line never cites a record of another contract."""
        cited = {record.document_id for record in request.records if record.document_id in allowed_documents}
        cited |= {entry.document_id for entry in request.formula_inputs.values() if entry.document_id in allowed_documents}
        foreign = await contract_scope.evidence_conflicts(self.db, claim, [], cited)
        if foreign:
            raise contract_scope.refusal(claim, foreign)

    @staticmethod
    def _roll_up(head: ClaimHead) -> None:
        # Once a head has a build-up, its amount is the build-up, not a typed figure.
        # Removing the last line clears it: the old total is no longer supported by
        # anything (D-14, 4B-17, C-07). Only line operations call this.
        if head.quantum_lines:
            # Summed in paise; the head's rupee figure is derived from the sum (4B-24).
            total = sum(int(line.amount_paise or 0) for line in head.quantum_lines)
            head.amount_claimed_paise = total
            head.amount_claimed = rupees(total)
            # The build-up is the amount now; a legacy figure awaiting re-entry is superseded (4B-19).
            head.figures_to_reenter = [item for item in head.figures_to_reenter if item.field != "amount_claimed"]
        else:
            head.amount_claimed_paise = None
            head.amount_claimed = None

    async def add_line(
        self, claim: Dict[str, Any], head_id: str, request: QuantumLineRequest, allowed_documents: Set[str], user_id: Optional[str]
    ) -> QuantumView:
        await self._refuse_foreign_records(claim, request, allowed_documents)

        def _apply(fresh: ClaimBench) -> None:
            head = _head(fresh, head_id)
            if len(head.quantum_lines) >= MAX_LINES:
                raise HTTPException(status_code=422, detail=f"A head can hold at most {MAX_LINES} quantum lines")
            head.quantum_lines.append(self._build(_next_line_id(head), request, allowed_documents))
            self._roll_up(head)

        await self.bench._change(claim, request.board_version, user_id, _apply)
        return await self.view(await self.delay._reload(claim), head_id)

    async def update_line(
        self,
        claim: Dict[str, Any],
        head_id: str,
        line_id: str,
        request: QuantumLineRequest,
        allowed_documents: Set[str],
        user_id: Optional[str],
    ) -> QuantumView:
        await self._refuse_foreign_records(claim, request, allowed_documents)

        def _apply(fresh: ClaimBench) -> None:
            head = _head(fresh, head_id)
            for index, line in enumerate(head.quantum_lines):
                if line.line_id == line_id:
                    head.quantum_lines[index] = self._build(line_id, request, allowed_documents)
                    break
            else:
                raise HTTPException(status_code=404, detail="Quantum line not found")
            self._roll_up(head)

        await self.bench._change(claim, request.board_version, user_id, _apply)
        return await self.view(await self.delay._reload(claim), head_id)

    async def remove_line(
        self, claim: Dict[str, Any], head_id: str, line_id: str, board_version: int, user_id: Optional[str]
    ) -> QuantumView:
        def _apply(fresh: ClaimBench) -> None:
            head = _head(fresh, head_id)
            if not any(line.line_id == line_id for line in head.quantum_lines):
                raise HTTPException(status_code=404, detail="Quantum line not found")
            head.quantum_lines = [line for line in head.quantum_lines if line.line_id != line_id]
            self._roll_up(head)

        await self.bench._change(claim, board_version, user_id, _apply)
        return await self.view(await self.delay._reload(claim), head_id)


__all__ = [
    "ClaimQuantum",
    "QuantumLineInput",
    "QuantumLineRequest",
    "QuantumLineView",
    "QuantumView",
    "build_quantum_view",
    "line_lock_reasons",
]
