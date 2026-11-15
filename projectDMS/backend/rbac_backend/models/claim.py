"""Claim register models (Phase 4 / Module 1).

A claim is the spine of the claims-market layer: EOT / variation / payment /
loss-and-expense entries linked to project correspondence, with value, dates,
contract clauses, and a lifecycle status. Tenant-scoped by organization/project.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ClaimType(str, Enum):
    EOT = "eot"
    VARIATION = "variation"
    PAYMENT_IPC = "payment_ipc"
    LOSS_EXPENSE = "loss_expense"
    ACCELERATION = "acceleration"
    DEFECT = "defect"
    OTHER = "other"


class ClaimStatus(str, Enum):
    # Raised from a signal (a hindrance, an incoming letter) before anyone has
    # decided to pursue it. Its notice period is already running.
    POTENTIAL = "potential"
    DRAFT = "draft"
    NOTIFIED = "notified"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    AGREED = "agreed"
    REJECTED = "rejected"
    DISPUTED = "disputed"
    CLOSED = "closed"


# Moves a claim may make. Anything else is refused: a status is a statement
# about where the claim stands with the other party, not a free label.
ALLOWED_STATUS_TRANSITIONS: Dict[ClaimStatus, frozenset] = {
    # Taken up (draft, or straight to notified once notice is served) or dropped.
    ClaimStatus.POTENTIAL: frozenset({ClaimStatus.DRAFT, ClaimStatus.NOTIFIED, ClaimStatus.CLOSED}),
    ClaimStatus.DRAFT: frozenset({ClaimStatus.NOTIFIED, ClaimStatus.SUBMITTED, ClaimStatus.CLOSED}),
    ClaimStatus.NOTIFIED: frozenset({ClaimStatus.SUBMITTED, ClaimStatus.CLOSED}),
    ClaimStatus.SUBMITTED: frozenset(
        {
            ClaimStatus.UNDER_REVIEW,
            ClaimStatus.AGREED,
            ClaimStatus.REJECTED,
            ClaimStatus.DISPUTED,
            ClaimStatus.CLOSED,
        }
    ),
    ClaimStatus.UNDER_REVIEW: frozenset(
        {
            # Back to submitted: further particulars were requested and supplied.
            ClaimStatus.SUBMITTED,
            ClaimStatus.AGREED,
            ClaimStatus.REJECTED,
            ClaimStatus.DISPUTED,
            ClaimStatus.CLOSED,
        }
    ),
    ClaimStatus.AGREED: frozenset({ClaimStatus.CLOSED}),
    ClaimStatus.REJECTED: frozenset(
        {ClaimStatus.SUBMITTED, ClaimStatus.DISPUTED, ClaimStatus.CLOSED}
    ),
    ClaimStatus.DISPUTED: frozenset(
        {ClaimStatus.UNDER_REVIEW, ClaimStatus.AGREED, ClaimStatus.REJECTED, ClaimStatus.CLOSED}
    ),
    # A closed claim is reopened only as a live review or a dispute, never as a draft.
    ClaimStatus.CLOSED: frozenset({ClaimStatus.UNDER_REVIEW, ClaimStatus.DISPUTED}),
}


def allowed_next_statuses(current: Any) -> List[ClaimStatus]:
    """Statuses a claim in ``current`` may move to, in lifecycle order."""
    try:
        status = ClaimStatus(str(getattr(current, "value", current) or ClaimStatus.DRAFT.value))
    except ValueError:
        return []
    allowed = ALLOWED_STATUS_TRANSITIONS.get(status, frozenset())
    return [item for item in ClaimStatus if item in allowed]


# --------------------------------------------------------------------------- #
# Claim Bench: a claim as a board of heads, each tested on five elements
# --------------------------------------------------------------------------- #
ELEMENT_KEYS = ("event", "notice", "contractual_basis", "causation", "quantum")
ElementKey = Literal["event", "notice", "contractual_basis", "causation", "quantum"]
ElementState = Literal["supported", "weak", "missing", "not_applicable"]
HeadKind = Literal["time", "cost", "time_and_cost"]
HeadClassification = Literal["excusable", "compensable", "contractor_risk", "undetermined"]
BenchStage = Literal["heads", "locked"]
Perspective = Literal["contractor", "engineer", "employer"]


class HeadElement(BaseModel):
    """One of the five things a head of claim must establish.

    ``proposed_*`` is what the last test proposed (the skill, or the record
    checks when no skill ran). ``state`` is the call a person made. Nothing is
    decided until a person decides it.
    """

    proposed_state: Optional[ElementState] = None
    proposed_reason: Optional[str] = None
    proposed_by: Optional[Literal["skill", "record_check"]] = None
    source_ids: List[str] = Field(default_factory=list)
    state: Optional[ElementState] = None
    note: Optional[str] = Field(default=None, max_length=2000)
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None


def _empty_elements() -> Dict[str, HeadElement]:
    return {key: HeadElement() for key in ELEMENT_KEYS}


class ClaimHead(BaseModel):
    head_id: str
    title: str = Field(..., min_length=1, max_length=300)
    kind: HeadKind = "time_and_cost"
    event_summary: Optional[str] = Field(default=None, max_length=4000)
    clause_refs: List[str] = Field(default_factory=list)
    amount_claimed: Optional[float] = Field(default=None, ge=0)
    eot_days_claimed: Optional[int] = Field(default=None, ge=0)
    elements: Dict[str, HeadElement] = Field(default_factory=_empty_elements)
    proposed_classification: Optional[HeadClassification] = None
    classification: Optional[HeadClassification] = None
    residual_risks: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    # The step that produced the current proposals, and whether the head has
    # been edited since (the proposals then describe an earlier version of it).
    step_id: Optional[str] = None
    test_stale: bool = False
    tested_at: Optional[datetime] = None


class ClaimBench(BaseModel):
    """Board state stored on the claim under ``bench``."""

    board_version: int = 0
    stage: BenchStage = "heads"
    heads: List[ClaimHead] = Field(default_factory=list)
    # The party this board was worked for, recorded when it was last tested or locked.
    perspective: Optional[Perspective] = None
    locked_at: Optional[datetime] = None
    locked_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None


class ClaimOrigin(BaseModel):
    """The record a potential claim was raised from. One claim per origin."""

    type: Literal["hindrance", "letter_bench"]
    id: str
    label: Optional[str] = None


class ClaimBase(BaseModel):
    claim_ref: Optional[str] = None
    type: ClaimType = ClaimType.OTHER
    title: str
    description: Optional[str] = None
    status: ClaimStatus = ClaimStatus.DRAFT
    event_date: Optional[datetime] = None
    # When the claiming party became aware (or should have) of the event. Notice
    # periods normally run from awareness, which can be later than the event.
    awareness_date: Optional[datetime] = None
    notice_date: Optional[datetime] = None
    submission_date: Optional[datetime] = None
    response_due_date: Optional[datetime] = None
    amount_claimed: Optional[float] = None
    amount_agreed: Optional[float] = None
    currency: str = "INR"
    eot_days_claimed: Optional[int] = None
    eot_days_granted: Optional[int] = None
    responsible_party_id: Optional[str] = None
    contract_clauses: List[str] = Field(default_factory=list)
    linked_document_ids: List[str] = Field(default_factory=list)
    linked_letter_ids: List[str] = Field(default_factory=list)
    # Type-specific structured detail (delay events for EOT, CE/VO refs +
    # valuation for variation, IPC line items for payment, etc.).
    type_metadata: Dict[str, Any] = Field(default_factory=dict)
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    origin: Optional[ClaimOrigin] = None


class ClaimCreate(ClaimBase):
    title: str = Field(..., min_length=1, max_length=300)


class ClaimUpdate(BaseModel):
    claim_ref: Optional[str] = None
    type: Optional[ClaimType] = None
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[ClaimStatus] = None
    event_date: Optional[datetime] = None
    awareness_date: Optional[datetime] = None
    notice_date: Optional[datetime] = None
    submission_date: Optional[datetime] = None
    response_due_date: Optional[datetime] = None
    amount_claimed: Optional[float] = None
    amount_agreed: Optional[float] = None
    currency: Optional[str] = None
    eot_days_claimed: Optional[int] = None
    eot_days_granted: Optional[int] = None
    responsible_party_id: Optional[str] = None
    contract_clauses: Optional[List[str]] = None
    linked_document_ids: Optional[List[str]] = None
    linked_letter_ids: Optional[List[str]] = None
    type_metadata: Optional[Dict[str, Any]] = None


class ClaimStatusUpdate(BaseModel):
    status: ClaimStatus


class Claim(ClaimBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None
    evidence_frozen_at: Optional[datetime] = None
    evidence_frozen_by: Optional[str] = None
    evidence_freeze_reason: Optional[str] = None
    bench: Optional[ClaimBench] = None

    model_config = ConfigDict(populate_by_name=True)


NoticeBasis = Literal["awareness", "event"]
NoticePeriodSource = Literal[
    # Read from a clause of this project's contract and confirmed by a person.
    "contract_verified",
    # Read from a clause of this project's contract; nobody has confirmed it yet.
    "contract_unverified",
    "organisation_rule",
    "assumed_default",
]
NoticeState = Literal["open", "due_soon", "overdue", "served_in_time", "served_late"]


class NoticeAssessment(BaseModel):
    """Where a claim stands against its notice period. Computed, never stored.

    ``period_source`` says where the period came from. Only
    ``contract_verified`` is the contract's own period, confirmed by a person
    against the quoted clause; ``period_verified`` is true for that source
    alone. ``contract_unverified`` was read from a clause but not confirmed,
    ``organisation_rule`` is a figure the organisation set, and
    ``assumed_default`` is a typical figure for the claim type. For anything
    but a verified period, a "late" result is an indication to check, not a
    finding that the notice was out of time under the contract.
    """

    basis: NoticeBasis
    basis_date: datetime
    period_days: int
    period_source: NoticePeriodSource
    period_verified: bool = False
    # Where a contract-derived period was read from.
    clause_no: Optional[str] = None
    requirement_id: Optional[str] = None
    due_date: datetime
    served_on: Optional[datetime] = None
    days_remaining: Optional[int] = None
    served_late_by_days: Optional[int] = None
    state: NoticeState


class ClaimRead(Claim):
    """A claim as returned by the API, with fields derived at read time."""

    notice_assessment: Optional[NoticeAssessment] = None
    allowed_status_transitions: List[ClaimStatus] = Field(default_factory=list)
    # Summary of the board for the register; the board itself is read through the Bench.
    head_count: int = 0
    bench_stage: Optional[BenchStage] = None


class ClaimAssessment(BaseModel):
    """A clause-grounded AI assessment of a claim (Phase 4 / Module 5).

    Produced by running the citation-enforced contract-QA engine over the
    project's contract scoped to the claim. Citations + the iteration trace are
    persisted for traceability — every assertion is anchored to a contract clause
    or the engine answers "Information not found".
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    claim_id: str
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    claim_type: Optional[str] = None
    query: Optional[str] = None
    answer: str = ""
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    trace: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class SlaItem(BaseModel):
    """A derived correspondence deadline (Phase 4 / Module 2)."""

    claim_id: str
    claim_ref: Optional[str] = None
    title: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None
    kind: str  # "response" | "time_bar"
    due_date: Optional[datetime] = None
    days_remaining: int
    state: str  # "ok" | "approaching" | "breached"
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    responsible_party_id: Optional[str] = None
