"""Structured outputs of the claim, EOT, pleadings and hearing skill packs.

One schema per pack is defined here: the one its manifest names. Further
schemas are added with the step that produces them. Every default is the
"nothing established" value, so a deterministic fallback built with no
arguments is valid and never asserts an entitlement, a date or an amount.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from .skill_output_schemas import register_schema

ElementState = Literal["supported", "weak", "missing", "not_applicable"]
HeadClassification = Literal["excusable", "compensable", "contractor_risk", "undetermined"]
RiskOwner = Literal["employer", "contractor", "neutral", "undetermined"]
Criticality = Literal["critical", "not_critical", "unproven"]
Audience = Literal["internal", "tribunal"]


class ElementFinding(BaseModel):
    state: ElementState = "missing"
    reason: str = "Not assessed."
    source_ids: List[str] = Field(default_factory=list)


class ClaimHeadTestOutput(BaseModel):
    """`indian-infra-claims`: one head of claim tested on its five elements."""

    event: ElementFinding = Field(default_factory=ElementFinding)
    notice: ElementFinding = Field(default_factory=ElementFinding)
    contractual_basis: ElementFinding = Field(default_factory=ElementFinding)
    causation: ElementFinding = Field(default_factory=ElementFinding)
    quantum: ElementFinding = Field(default_factory=ElementFinding)
    classification: HeadClassification = "undetermined"
    clause_refs: List[str] = Field(default_factory=list)
    residual_risks: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)


class DelayEventClassificationOutput(BaseModel):
    """`indian-infra-eot-claims`: who carries the risk of one delay event, and is it proven critical.

    Carries no day counts: durations and overlaps are computed from records.
    """

    risk_owner: RiskOwner = "undetermined"
    risk_owner_reason: str = "Not assessed."
    criticality: Criticality = "unproven"
    criticality_reason: str = "Not assessed."
    clause_refs: List[str] = Field(default_factory=list)
    notice_concerns: List[str] = Field(default_factory=list)
    missing_records: List[str] = Field(default_factory=list)
    source_ids: List[str] = Field(default_factory=list)


class PleadingHeadOutput(BaseModel):
    """`indian-infra-arbitration-pleadings`: the four elements every pleaded head must carry."""

    contractual_provision: str = ""
    factual_event: str = ""
    causal_link: str = ""
    relief_sought: str = ""
    notice_pleaded: bool = False
    clause_refs: List[str] = Field(default_factory=list)
    exhibit_refs: List[str] = Field(default_factory=list)
    evidence_required: List[str] = Field(default_factory=list)


class HearingPoint(BaseModel):
    text: str = ""
    exhibit_refs: List[str] = Field(default_factory=list)
    clause_refs: List[str] = Field(default_factory=list)


class HearingDeliverableOutput(BaseModel):
    """`indian-infra-arbitration-hearing`: one section of one hearing deliverable."""

    title: str = ""
    audience: Audience = "internal"
    points: List[HearingPoint] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)
    speaking_note: Optional[str] = None


FindingResult = Literal["full", "partial", "none", "insufficient_information"]


class ClaimDeterminationOutput(BaseModel):
    """`indian-infra-claims`: a finding on one head, for a determination or a simulation of one.

    Carries no days and no amount: figures are entered by a person from the
    delay assessment and the quantum build-up. The default is the "cannot yet
    be determined" value.
    """

    result: FindingResult = "insufficient_information"
    findings: str = "Not assessed."
    residual_risks: List[str] = Field(default_factory=list)
    information_required: List[str] = Field(default_factory=list)
    clause_refs: List[str] = Field(default_factory=list)


class SubmissionSectionOutput(BaseModel):
    """`indian-infra-claims`: one section of a claim submission, as paragraphs."""

    paragraphs: List[str] = Field(default_factory=list)


class StrategyNoteOutput(BaseModel):
    """`indian-infra-claims`: the claimant's internal view of the claim. Never issued."""

    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    residual_risks: List[str] = Field(default_factory=list)


CLAIM_HEAD_TEST_SCHEMA_KEY = "claim_head_test.v1"
CLAIM_DETERMINATION_SCHEMA_KEY = "claim_determination.v1"
SUBMISSION_SECTION_SCHEMA_KEY = "submission_section.v1"
STRATEGY_NOTE_SCHEMA_KEY = "strategy_note.v1"
DELAY_EVENT_SCHEMA_KEY = "delay_event_classification.v1"
PLEADING_HEAD_SCHEMA_KEY = "pleading_head.v1"
HEARING_DELIVERABLE_SCHEMA_KEY = "hearing_deliverable.v1"

register_schema(CLAIM_HEAD_TEST_SCHEMA_KEY, ClaimHeadTestOutput)
register_schema(CLAIM_DETERMINATION_SCHEMA_KEY, ClaimDeterminationOutput)
register_schema(SUBMISSION_SECTION_SCHEMA_KEY, SubmissionSectionOutput)
register_schema(STRATEGY_NOTE_SCHEMA_KEY, StrategyNoteOutput)
register_schema(DELAY_EVENT_SCHEMA_KEY, DelayEventClassificationOutput)
register_schema(PLEADING_HEAD_SCHEMA_KEY, PleadingHeadOutput)
register_schema(HEARING_DELIVERABLE_SCHEMA_KEY, HearingDeliverableOutput)

__all__ = [
    "CLAIM_DETERMINATION_SCHEMA_KEY",
    "CLAIM_HEAD_TEST_SCHEMA_KEY",
    "STRATEGY_NOTE_SCHEMA_KEY",
    "SUBMISSION_SECTION_SCHEMA_KEY",
    "ClaimDeterminationOutput",
    "StrategyNoteOutput",
    "SubmissionSectionOutput",
    "DELAY_EVENT_SCHEMA_KEY",
    "HEARING_DELIVERABLE_SCHEMA_KEY",
    "PLEADING_HEAD_SCHEMA_KEY",
    "ClaimHeadTestOutput",
    "DelayEventClassificationOutput",
    "ElementFinding",
    "HearingDeliverableOutput",
    "HearingPoint",
    "PleadingHeadOutput",
]
