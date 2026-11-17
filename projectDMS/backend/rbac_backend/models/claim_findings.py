"""Findings on a claim: determinations, simulations, the strategy note and the submission.

Two record types hold findings, and they are deliberately not one type with a
flag:

- ``ClaimDetermination`` is an **official** finding. It is either authored in
  a workspace that acts as Engineer or Employer, by a person holding
  determination authority, and approved by a second person; or it is a
  transcription of a determination received from the other side, tied to the
  document it was received in. Once approved or recorded it does not change; a
  change is a new revision.
- ``ClaimSimulation`` is an **internal analysis** of how another party is
  likely to respond. It is privileged, lives in its own collection with the
  strategy note, and is never exported, pleaded or treated as a finding.

Neither can write a contractual fact. They read the board; they do not change it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..utils.datetime import now_utc

FindingResult = Literal["full", "partial", "none", "insufficient_information"]
DeterminationOrigin = Literal["authored", "received"]
DeterminationStatus = Literal["draft", "in_review", "returned", "approved", "recorded"]
Party = Literal["contractor", "engineer", "employer"]
SectionKey = Literal[
    "executive_summary",
    "contractual_basis",
    "chronology_and_evidence",
    "delay_analysis",
    "quantum",
    "relief_and_reservation",
]

SECTION_ORDER: List[str] = [
    "executive_summary",
    "contractual_basis",
    "chronology_and_evidence",
    "delay_analysis",
    "quantum",
    "relief_and_reservation",
]
SECTION_TITLES: Dict[str, str] = {
    "executive_summary": "Executive summary",
    "contractual_basis": "Contractual basis",
    "chronology_and_evidence": "Chronology and evidence",
    "delay_analysis": "Delay analysis",
    "quantum": "Quantum",
    "relief_and_reservation": "Relief sought and reservation of rights",
}

INTERNAL_ANALYSIS_LABEL = "Internal analysis — not a determination"
INTERNAL_NOTE_LABEL = "Internal — not for issue"


class InformationRequest(BaseModel):
    """Something the finding cannot be made without, and what follows if it is not supplied."""

    item: str = Field(..., min_length=1, max_length=500)
    deadline: Optional[datetime] = None
    consequence: Optional[str] = Field(default=None, max_length=500)


class FindingProposal(BaseModel):
    """What the skill, or the record check when no skill is published, put forward."""

    result: FindingResult = "insufficient_information"
    findings: str = ""
    residual_risks: List[str] = Field(default_factory=list)
    information_required: List[str] = Field(default_factory=list)
    proposed_by: Literal["skill", "record_check"] = "record_check"
    step_id: Optional[str] = None


class HeadFinding(BaseModel):
    head_id: str
    head_title: str = ""
    result: Optional[FindingResult] = None
    # Entered by a person. Figures on the record are offered beside them; the
    # skill never supplies a number of days or an amount.
    days: Optional[int] = Field(default=None, ge=0)
    amount: Optional[float] = Field(default=None, ge=0)
    findings: str = Field(default="", max_length=6000)
    residual_risks: List[str] = Field(default_factory=list)
    information_required: List[InformationRequest] = Field(default_factory=list)
    proposal: Optional[FindingProposal] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None


class DeterminationEvent(BaseModel):
    action: str
    actor_id: Optional[str] = None
    at: datetime = Field(default_factory=now_utc)
    comment: Optional[str] = None


class ClaimDetermination(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    claim_id: str
    origin: DeterminationOrigin
    revision: int = 1
    supersedes_id: Optional[str] = None
    # The party whose finding this is. For an authored determination it is the
    # party the project acts for; for a received one, the party that issued it.
    issued_by: Literal["engineer", "employer"]
    status: DeterminationStatus = "draft"
    heads: List[HeadFinding] = Field(default_factory=list)
    # The board the findings were made against.
    board_version: Optional[int] = None
    # Received determinations only: the document it arrived in.
    source_document_id: Optional[str] = None
    reference: Optional[str] = Field(default=None, max_length=200)
    determination_date: Optional[datetime] = None
    reviewer_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: Optional[datetime] = None
    submitted_by: Optional[str] = None
    submitted_at: Optional[datetime] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    history: List[DeterminationEvent] = Field(default_factory=list)
    # Compare-and-set counter for edits to a draft.
    version: int = 0

    model_config = ConfigDict(populate_by_name=True)


class SimulatedHead(BaseModel):
    head_id: str
    head_title: str = ""
    likely_result: FindingResult = "insufficient_information"
    reasoning: str = ""
    likely_challenges: List[str] = Field(default_factory=list)
    information_likely_requested: List[str] = Field(default_factory=list)
    produced_by: Literal["skill", "record_check"] = "record_check"
    step_id: Optional[str] = None


class ClaimSimulation(BaseModel):
    """Privileged. Not a finding of any party."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    kind: Literal["simulation"] = "simulation"
    label: str = INTERNAL_ANALYSIS_LABEL
    privileged: bool = True
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    claim_id: str
    # Whose likely response is being modelled, and for whom.
    simulated_party: Party
    prepared_for: Party
    heads: List[SimulatedHead] = Field(default_factory=list)
    board_version: Optional[int] = None
    degraded: bool = False
    degraded_reason: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)

    model_config = ConfigDict(populate_by_name=True)


class StrategyNote(BaseModel):
    """Privileged. The claimant's own view of where the claim is strong and weak."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    kind: Literal["strategy_note"] = "strategy_note"
    label: str = INTERNAL_NOTE_LABEL
    privileged: bool = True
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    claim_id: str
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    residual_risks: List[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=8000)
    board_version: Optional[int] = None
    produced_by: Literal["skill", "record_check", "person"] = "record_check"
    degraded: bool = False
    degraded_reason: Optional[str] = None
    version: int = 0
    updated_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=now_utc)

    model_config = ConfigDict(populate_by_name=True)


class ParagraphSource(BaseModel):
    """Where a paragraph's content comes from on the record."""

    kind: Literal["claim", "head", "clause", "notice", "delay", "quantum", "document", "standard_wording"]
    ref: str = ""
    label: str = ""


class SubmissionParagraph(BaseModel):
    text: str
    sources: List[ParagraphSource] = Field(default_factory=list)
    #: 4B-32: the text was changed by a person; its sources are those of the composed paragraph.
    edited: bool = False


class EvidenceItem(BaseModel):
    number: str
    document_id: str
    title: str = ""
    reference: Optional[str] = None
    date: Optional[datetime] = None


class SubmissionSection(BaseModel):
    key: SectionKey
    title: str
    paragraphs: List[SubmissionParagraph] = Field(default_factory=list)
    # Not part of the document: things the reader of the workspace should know
    # (a cost line left out because its proof is not linked, for example).
    internal_notes: List[str] = Field(default_factory=list)
    input_hash: str = ""
    produced_by: Literal["skill", "record_composition"] = "record_composition"
    degraded: bool = False
    degraded_reason: Optional[str] = None
    step_id: Optional[str] = None
    composed_at: datetime = Field(default_factory=now_utc)
    #: 4B-32: who last edited the section by hand, when, and the record (input hash) they edited against.
    edited_by: Optional[str] = None
    edited_at: Optional[datetime] = None
    edit_basis_hash: Optional[str] = None


class ClaimSubmission(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    claim_id: str
    sections: List[SubmissionSection] = Field(default_factory=list)
    evidence: List[EvidenceItem] = Field(default_factory=list)
    #: 4B-27: each evidence document's revision when composed (document id -> revision
    #: fingerprint). A document replaced or re-dated afterwards makes the evidence stale.
    evidence_snapshot: Dict[str, str] = Field(default_factory=dict)
    board_version: Optional[int] = None
    version: int = 0
    composed_by: Optional[str] = None
    composed_at: datetime = Field(default_factory=now_utc)

    model_config = ConfigDict(populate_by_name=True)


__all__ = [
    "INTERNAL_ANALYSIS_LABEL",
    "INTERNAL_NOTE_LABEL",
    "SECTION_ORDER",
    "SECTION_TITLES",
    "ClaimDetermination",
    "ClaimSimulation",
    "ClaimSubmission",
    "DeterminationEvent",
    "EvidenceItem",
    "FindingProposal",
    "HeadFinding",
    "InformationRequest",
    "ParagraphSource",
    "SimulatedHead",
    "StrategyNote",
    "SubmissionParagraph",
    "SubmissionSection",
]
