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

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class ConcurrencyFinding(BaseModel):
    """The treatment a person decided for one overlap, with its contractual basis."""

    treatment: OverlapTreatment
    days_to_eot: Optional[int] = Field(default=None, ge=0)
    basis: str = Field(..., min_length=10, max_length=2000)
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None


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


class QuantumLine(BaseModel):
    line_id: str
    category: QuantumCategory
    description: str = Field(..., min_length=1, max_length=500)
    amount: float = Field(..., ge=0)
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    formula: QuantumFormula = "none"
    basis: Optional[str] = Field(default=None, max_length=1000)
    records: List[QuantumRecord] = Field(default_factory=list)


__all__ = [
    "UNAVAILABLE_METHODS",
    "ClaimHeadLink",
    "ConcurrencyFinding",
    "Criticality",
    "DelayMethod",
    "EventDecision",
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
