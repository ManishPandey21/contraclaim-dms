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

Records are documents already linked to the claim.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException
from pydantic import BaseModel, Field

from ..models.claim import ClaimBench, ClaimHead
from ..models.claim_delay import QuantumLine, QuantumRecord
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
    amount: float = Field(..., ge=0)
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    formula: str = "none"
    basis: Optional[str] = Field(default=None, max_length=1000)
    records: List[QuantumRecord] = Field(default_factory=list)


class QuantumLineRequest(QuantumLineInput):
    board_version: int = Field(..., ge=0)


class QuantumLineView(BaseModel):
    line: QuantumLine
    locked: bool
    # Each entry is one thing that would have to be true for the line to unlock.
    lock_reasons: List[str] = Field(default_factory=list)


class QuantumView(BaseModel):
    head_id: str
    currency: str = "INR"
    lines: List[QuantumLineView] = Field(default_factory=list)
    claimed_total: float = 0.0
    supported_total: float = 0.0
    locked_total: float = 0.0
    locked_count: int = 0


def line_lock_reasons(line: QuantumLine, compensable: Set[date]) -> List[str]:
    """Why a line is locked. Empty means it counts as supported."""
    reasons: List[str] = []
    roles = {record.role for record in line.records}
    if line.category in {"direct", "disruption", "prolongation"} and not line.records:
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


def build_quantum_view(head: ClaimHead, compensable: Set[date], currency: str = "INR") -> QuantumView:
    views = []
    for line in head.quantum_lines:
        reasons = line_lock_reasons(line, compensable)
        views.append(QuantumLineView(line=line, locked=bool(reasons), lock_reasons=reasons))
    claimed = sum(view.line.amount for view in views)
    locked = sum(view.line.amount for view in views if view.locked)
    return QuantumView(
        head_id=head.head_id,
        currency=currency,
        lines=views,
        claimed_total=round(claimed, 2),
        supported_total=round(claimed - locked, 2),
        locked_total=round(locked, 2),
        locked_count=sum(1 for view in views if view.locked),
    )


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

    async def view(self, claim: Dict[str, Any], head_id: str) -> QuantumView:
        bench = self.bench._require_bench(claim)
        head = _head(bench, head_id)
        return build_quantum_view(head, await self._compensable(claim, head), str(claim.get("currency") or "INR"))

    def _build(self, line_id: str, payload: QuantumLineInput, allowed_documents: Set[str]) -> QuantumLine:
        if payload.category not in _CATEGORIES:
            raise HTTPException(status_code=422, detail="Unknown cost category")
        if payload.formula not in _FORMULAS:
            raise HTTPException(status_code=422, detail="Unknown formula")
        if payload.formula != "none" and payload.category != "overhead_formula":
            raise HTTPException(status_code=422, detail="A formula applies only to an overhead-by-formula line")
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
            amount=payload.amount,
            period_start=payload.period_start,
            period_end=payload.period_end,
            formula=payload.formula,  # type: ignore[arg-type]
            basis=(payload.basis or "").strip() or None,
            records=records,
        )

    @staticmethod
    def _roll_up(head: ClaimHead) -> None:
        # Once a head has a build-up, its amount is the build-up, not a typed figure.
        if head.quantum_lines:
            head.amount_claimed = round(sum(line.amount for line in head.quantum_lines), 2)

    async def add_line(
        self, claim: Dict[str, Any], head_id: str, request: QuantumLineRequest, allowed_documents: Set[str], user_id: Optional[str]
    ) -> QuantumView:
        def _apply(fresh: ClaimBench) -> None:
            head = _head(fresh, head_id)
            if len(head.quantum_lines) >= MAX_LINES:
                raise HTTPException(status_code=422, detail=f"A head can hold at most {MAX_LINES} quantum lines")
            head.quantum_lines.append(self._build(_next_line_id(head), request, allowed_documents))
            self._roll_up(head)
            self.bench._unlock(fresh)

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
        def _apply(fresh: ClaimBench) -> None:
            head = _head(fresh, head_id)
            for index, line in enumerate(head.quantum_lines):
                if line.line_id == line_id:
                    head.quantum_lines[index] = self._build(line_id, request, allowed_documents)
                    break
            else:
                raise HTTPException(status_code=404, detail="Quantum line not found")
            self._roll_up(head)
            self.bench._unlock(fresh)

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
            self.bench._unlock(fresh)

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
