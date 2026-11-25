"""Delay Lab and Quantum Builder models for a head of claim (Phase 2).

The head owns the *assessment*. The hindrance register and the key-date
submissions and determinations stay the authoritative records of their own
facts: a head links to them, pinned to the version it was linked at, and holds
only what a person decided about them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..utils.datetime import now_utc

LinkTargetType = Literal["hindrance", "eot_submission", "eot_determination"]
LinkStatus = Literal["active", "removed"]
RiskOwner = Literal["employer", "contractor", "neutral"]
Criticality = Literal["critical", "not_critical", "unproven"]
DelayMethod = Literal["as_planned_vs_as_built", "windows"]
OverlapTreatment = Literal[
    "eot_granted_no_cost", "eot_granted_with_cost", "apportioned", "no_eot", "not_true_concurrency"
]
QuantumCategory = Literal[
    "direct", "prolongation", "disruption", "overhead_formula", "loss_of_profit", "interest"
]
QuantumFormula = Literal["none", "hudson", "emden", "eichleay"]
RecordRole = Literal["support", "actual_loss", "work_declined"]

# Methods the product cannot offer, and why. Shown, never silently omitted.
UNAVAILABLE_METHODS: Dict[str, str] = {
    "time_impact_analysis": "Needs a programme network (activities and logic). Only milestones are held.",
    "collapsed_as_built": "Needs an as-built programme network. Only milestones are held.",
}


class ClaimHeadLink(BaseModel):
    """A head's link to a record another register owns, pinned to what it was when linked.

    Never edited in place. Removing a link marks it removed; linking again, or
    refreshing to the record's latest version, writes a new link.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    organization_id: str
    project_id: str
    claim_id: str
    head_id: str
    target_type: LinkTargetType
    target_id: str
    # Hash of the fields the assessment relies on, taken when the link was made.
    target_revision: str
    target_label: str
    # Those fields as they were. The register stays authoritative; this is what
    # the assessment was made against, so it can be told when the record moved on.
    snapshot: Dict[str, Any] = Field(default_factory=dict)
    detail: Dict[str, Any] = Field(default_factory=dict)
    status: LinkStatus = "active"
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)
    removed_by: Optional[str] = None
    removed_at: Optional[datetime] = None
    superseded_by_id: Optional[str] = None

    @field_validator("id", mode="before")
    @classmethod
    def _stringify_id(cls, value: Any) -> str:
        return str(value) if value is not None else str(uuid.uuid4())


class EventDecision(BaseModel):
    """What a person decided about one delay event, beside what was proposed."""

    proposed_risk_owner: Optional[RiskOwner] = None
    proposed_criticality: Optional[Criticality] = None
    proposed_reason: Optional[str] = None
    proposed_by: Optional[Literal["skill", "register"]] = None
    risk_owner: Optional[RiskOwner] = None
    criticality: Optional[Criticality] = None
    note: Optional[str] = Field(default=None, max_length=2000)
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None


class CostBasis(BaseModel):
    """What makes an overlap's days carry cost (D-16, AQ-4). Nothing is compensable without it.

    Recorded by one person and reviewed by another who holds the claim's determination
    authority by name (or, once 4B-07 lands, a delay_planning_expert assignment).
    """

    contractual_basis: str = Field(..., min_length=10, max_length=2000)
    causation: str = Field(..., min_length=10, max_length=2000)
    # Documents linked to the claim that evidence the cost.
    evidence_document_ids: List[str] = Field(..., min_length=1, max_length=50)
    recorded_by: Optional[str] = None
    recorded_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None


class ConcurrencyFinding(BaseModel):
    """The treatment a person decided for one overlap, with its contractual basis."""

    treatment: OverlapTreatment
    days_to_eot: Optional[int] = Field(default=None, ge=0)
    basis: str = Field(..., min_length=10, max_length=2000)
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    # The events the finding was made on (D-16): a change to them orphans it.
    event_ids: List[str] = Field(default_factory=list)
    cost_basis: Optional[CostBasis] = None

    def cost_established(self) -> bool:
        cost = self.cost_basis
        return bool(
            cost
            and cost.evidence_document_ids
            and cost.reviewed_by
            and cost.recorded_by
            and cost.reviewed_by != cost.recorded_by
        )


class HeadDelay(BaseModel):
    method: Optional[DelayMethod] = None
    # The date the analysis is made up to. An event with no end date runs to it.
    as_of_date: Optional[datetime] = None
    event_decisions: Dict[str, EventDecision] = Field(default_factory=dict)
    findings: Dict[str, ConcurrencyFinding] = Field(default_factory=dict)
    # Whether this contract gives time for neutral events, as a person recorded it.
    include_neutral: bool = False
    neutral_basis: Optional[str] = Field(default=None, max_length=2000)


class QuantumRecord(BaseModel):
    document_id: str
    role: RecordRole = "support"


class FormulaInput(BaseModel):
    """One typed input to a head-office overhead formula, and the record it is taken from (4B-24)."""

    #: Decimal text, never a binary float: "12500000.50", "7.5", "730".
    value: str = Field(..., min_length=1, max_length=40)
    document_id: Optional[str] = None

    @field_validator("value")
    @classmethod
    def _decimal_text(cls, value: str) -> str:
        from decimal import Decimal, InvalidOperation

        text = str(value).replace(",", "").strip()
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("must be a number") from exc
        if not number.is_finite() or number < 0:
            raise ValueError("must be a positive number")
        return text


class QuantumLine(BaseModel):
    line_id: str
    category: QuantumCategory
    description: str = Field(..., min_length=1, max_length=500)
    #: Money is held in integer paise (D-14, 4B-24). ``amount`` is the rupee figure
    #: derived from it, kept for display and older readers; nothing sums it.
    amount_paise: Optional[int] = Field(default=None, ge=0)
    amount: float = Field(default=0.0, ge=0)
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    formula: QuantumFormula = "none"
    #: Hudson, Emden or Eichleay inputs, by key (services/quantum_formulas.FORMULA_INPUTS).
    formula_inputs: Dict[str, FormulaInput] = Field(default_factory=dict)
    basis: Optional[str] = Field(default=None, max_length=1000)
    records: List[QuantumRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _paise_are_authoritative(self) -> "QuantumLine":
        from ..services.quantum_formulas import rupees, to_paise

        # A line stored before 4B-24 has only the rupee float; read it through its
        # decimal text. It is written in paise on its next save.
        if self.amount_paise is None:
            self.amount_paise = to_paise(repr(float(self.amount)))
        self.amount = rupees(self.amount_paise)
        return self


__all__ = [
    "UNAVAILABLE_METHODS",
    "CostBasis",
    "ClaimHeadLink",
    "ConcurrencyFinding",
    "Criticality",
    "DelayMethod",
    "EventDecision",
    "FormulaInput",
    "HeadDelay",
    "LinkTargetType",
    "OverlapTreatment",
    "QuantumCategory",
    "QuantumFormula",
    "QuantumLine",
    "QuantumRecord",
    "RecordRole",
    "RiskOwner",
]
