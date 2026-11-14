"""Letter Bench models: the position board, the skill chain and skill steps.

The board is the Bench's working state for one letter. It is a planning
surface, not a second source of truth for the letter: locking positions writes
an ordinary ``StrategyVersion`` and composing writes an ordinary ``DraftRun``,
so validation, approval, export, issue and rollback are unchanged.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..utils.datetime import now_utc
from .letter_drafting import DraftRole, ProbingQuestion

# The letters skill's own four-way classification of an assertion.
Verdict = Literal["entitlement", "partial", "none", "need_information"]
AssertionState = Literal["open", "decided", "locked"]
VerdictSource = Literal["ai", "user"]
ClauseCheckState = Literal["found", "not_found", "not_checked"]
StepStatus = Literal["queued", "running", "completed", "failed"]
ChainLinkStatus = Literal["pending", "queued", "running", "done", "stale", "failed", "unavailable"]
BoardStage = Literal["dissecting", "positions", "locked", "composed"]
VariationClass = Literal[
    "instructed_variation",
    "value_engineering",
    "constructive_variation",
    "not_a_variation",
    "insufficient_information",
]
ValuationLimb = Literal["contract_rates", "derived_rates", "cost_plus", "daywork", "not_determined"]

VERDICT_LABELS: Dict[str, str] = {
    "entitlement": "Valid entitlement",
    "partial": "Partial entitlement",
    "none": "No entitlement",
    "need_information": "Insufficient information",
}


class TextSpan(BaseModel):
    start: int = Field(..., ge=0)
    end: int = Field(..., ge=0)
    page: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> "TextSpan":
        if self.end <= self.start:
            raise ValueError("span end must be after start")
        return self


class ClauseCheck(BaseModel):
    clause_number: str
    state: ClauseCheckState = "not_checked"
    title: Optional[str] = None
    note: Optional[str] = None

    @property
    def label(self) -> str:
        return {
            "found": "Found in this contract",
            "not_found": "Not found in this contract",
            "not_checked": "Not checked - no clause index for this project",
        }[self.state]


class Assertion(BaseModel):
    assertion_id: str
    order: int = 0
    quoted_text: str
    span: Optional[TextSpan] = None
    clause_refs: List[str] = Field(default_factory=list)
    clause_checks: List[ClauseCheck] = Field(default_factory=list)
    proposed_verdict: Optional[Verdict] = None
    proposed_reason: Optional[str] = None
    verdict: Optional[Verdict] = None
    verdict_source: Optional[VerdictSource] = None
    user_direction: Optional[str] = Field(default=None, max_length=2000)
    source_ids: List[str] = Field(default_factory=list)
    routed_skill_key: Optional[str] = None
    step_ids: List[str] = Field(default_factory=list)
    needs: List[str] = Field(default_factory=list)
    reply_paragraph_id: Optional[str] = None
    state: AssertionState = "open"
    origin: Literal["ai", "user"] = "ai"


class ChainLink(BaseModel):
    link_id: str
    skill_key: str
    label: str
    kind: Literal["analysis", "evaluation", "drafting"] = "evaluation"
    # Empty target = the whole letter.
    target_assertion_ids: List[str] = Field(default_factory=list)
    skill_version: Optional[str] = None
    status: ChainLinkStatus = "pending"
    step_id: Optional[str] = None
    reason: Optional[str] = None
    added_by: Literal["router", "user"] = "router"


class ReplyParagraph(BaseModel):
    paragraph_id: str
    # None for framing parts (opening, reservations, closing).
    assertion_id: Optional[str] = None
    part: Literal["header", "opening", "point", "request", "reservations", "attachments", "closing"] = "point"
    heading: Optional[str] = None
    text: str = ""
    step_id: Optional[str] = None
    stale: bool = False
    edited_by_user: bool = False
    degraded: bool = False
    clause_refs: List[str] = Field(default_factory=list)
    source_ids: List[str] = Field(default_factory=list)


class BoardQuestion(BaseModel):
    """A run-level probing question surfaced on the Bench as a 'need information' card."""

    question: ProbingQuestion
    answer: Optional[str] = Field(default=None, max_length=4000)


class DeadlineInfo(BaseModel):
    raw: Optional[str] = None
    due_date: Optional[str] = None  # ISO date when it could be resolved
    days_remaining: Optional[int] = None
    basis: Optional[str] = None
    source: Literal["extracted", "manual", "none"] = "none"
    risk: Optional[Literal["low", "medium", "high"]] = None


class PositionBoard(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = Field(default=None, alias="_id")
    letter_id: str
    organization_id: str
    project_id: Optional[str] = None
    board_version: int = Field(default=0, ge=0)
    stage: BoardStage = "dissecting"
    role: DraftRole = "contractor"
    incoming_document_id: Optional[str] = None
    trigger: Optional[Dict[str, Any]] = None
    incoming_text: str = ""
    incoming_meta: Dict[str, Any] = Field(default_factory=dict)
    issue_type: Optional[str] = None
    deadline: DeadlineInfo = Field(default_factory=DeadlineInfo)
    assertions: List[Assertion] = Field(default_factory=list)
    chain: List[ChainLink] = Field(default_factory=list)
    questions: List[BoardQuestion] = Field(default_factory=list)
    reply_paragraphs: List[ReplyParagraph] = Field(default_factory=list)
    internal_note: Optional[str] = None
    analysis_step_id: Optional[str] = None
    locked_strategy_version: Optional[int] = None
    locked_board_version: Optional[int] = None
    locked_at: Optional[datetime] = None
    locked_by: Optional[str] = None
    strategy_run_id: Optional[str] = None
    draft_run_id: Optional[str] = None
    composing: bool = False
    last_error: Optional[str] = None
    # Set when the incoming text tripped the injection scan: no step on this
    # board calls the language model until the analysis is run again cleanly.
    model_blocked_reason: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=now_utc)

    @field_validator("id", mode="before")
    @classmethod
    def _stringify_id(cls, value: Any) -> Optional[str]:
        return None if value is None else str(value)


# --------------------------------------------------------------------------- #
# Structured skill outputs. The packs are prose; these schemas are ours and are
# enforced around the skill.
# --------------------------------------------------------------------------- #
class AnalysedAssertion(BaseModel):
    quoted_text: str
    clause_refs: List[str] = Field(default_factory=list)
    proposed_verdict: Optional[Verdict] = None
    reason: Optional[str] = None


class LetterAnalysisOutput(BaseModel):
    """`indian-infra-letter-analysis`: short summary, parts A-H, immediate action."""

    short_summary: List[str] = Field(default_factory=list)
    basic_particulars: Dict[str, Optional[str]] = Field(default_factory=dict)  # A
    purpose_and_nature: Optional[str] = None  # B
    key_facts_asserted: List[str] = Field(default_factory=list)  # C
    contractual_basis_claimed: List[str] = Field(default_factory=list)  # D
    relief_sought: List[str] = Field(default_factory=list)  # E
    strengths_and_weaknesses: List[str] = Field(default_factory=list)  # F
    implications_and_risks: List[str] = Field(default_factory=list)  # G
    recommended_response_strategy: List[str] = Field(default_factory=list)  # H
    immediate_action: Optional[str] = None
    suggested_next_skills: List[str] = Field(default_factory=list)
    assertions: List[AnalysedAssertion] = Field(default_factory=list)


class VariationEvaluationOutput(BaseModel):
    """`indian-infra-variations`: the five-part Variation Evaluation Report."""

    description: str = ""
    classification: VariationClass = "insufficient_information"
    contractual_basis: List[str] = Field(default_factory=list)
    valuation_limb: ValuationLimb = "not_determined"
    valuation_justification: Optional[str] = None
    valuation_amount: Optional[str] = None
    time_impact: Optional[str] = None
    recommended_wording: Optional[str] = None
    residual_risks: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    suggested_verdict: Optional[Verdict] = None


class ReplyParagraphOutput(BaseModel):
    """`indian-infra-contract-letters`: the answer to one assertion."""

    text: str = ""
    clause_refs: List[str] = Field(default_factory=list)
    internal_note: Optional[str] = None
    residual_risks: List[str] = Field(default_factory=list)


OUTPUT_SCHEMAS: Dict[str, type[BaseModel]] = {
    "letter_analysis.v1": LetterAnalysisOutput,
    "variation_evaluation.v1": VariationEvaluationOutput,
    "reply_paragraph.v1": ReplyParagraphOutput,
}


class SkillStep(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = Field(default=None, alias="_id")
    step_id: str
    letter_id: str
    run_id: Optional[str] = None
    skill_key: str
    skill_version: str
    skill_hash: str
    skill_name: Optional[str] = None
    role: Optional[str] = None
    overlay_key: Optional[str] = None
    target_assertion_ids: List[str] = Field(default_factory=list)
    input_hash: str
    output: Dict[str, Any] = Field(default_factory=dict)
    output_schema_key: str
    status: StepStatus = "queued"
    stale: bool = False
    degraded: bool = False
    error: Optional[str] = None
    parent_step_ids: List[str] = Field(default_factory=list)
    model: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)
    completed_at: Optional[datetime] = None

    @field_validator("id", mode="before")
    @classmethod
    def _stringify_id(cls, value: Any) -> Optional[str]:
        return None if value is None else str(value)


class SkillStepSummary(BaseModel):
    step_id: str
    skill_key: str
    skill_version: str
    skill_hash: str
    skill_name: Optional[str] = None
    target_assertion_ids: List[str] = Field(default_factory=list)
    output_schema_key: str
    status: StepStatus
    stale: bool = False
    degraded: bool = False
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


# --------------------------------------------------------------------------- #
# Requests / responses
# --------------------------------------------------------------------------- #
class BenchOpenRequest(BaseModel):
    incoming_document_id: Optional[str] = None
    trigger_type: Optional[Literal["variation", "key_date", "hindrance"]] = None
    trigger_id: Optional[str] = None
    project_id: Optional[str] = None
    organization_id: Optional[str] = None
    role: Optional[DraftRole] = None

    @model_validator(mode="after")
    def _one_origin(self) -> "BenchOpenRequest":
        has_trigger = bool(self.trigger_type and self.trigger_id)
        if bool(self.incoming_document_id) == has_trigger:
            raise ValueError("Provide either incoming_document_id or a trigger record, not both")
        return self


class BenchOpenResponse(BaseModel):
    letter_id: str
    created: bool
    board_version: int


class AssertionPatchRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    verdict: Optional[Verdict] = None
    clear_verdict: bool = False
    user_direction: Optional[str] = Field(default=None, max_length=2000)
    routed_skill_key: Optional[str] = Field(default=None, max_length=80)
    clear_routed_skill: bool = False
    # Structural edits. ``split_at`` is an offset inside quoted_text.
    split_at: Optional[int] = Field(default=None, ge=1)
    merge_with: Optional[str] = None
    remove: bool = False


class AssertionCreateRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    span: Optional[TextSpan] = None
    quoted_text: Optional[str] = Field(default=None, max_length=4000)


class ChainLinkInput(BaseModel):
    link_id: Optional[str] = None
    skill_key: str = Field(..., min_length=2, max_length=80)
    target_assertion_ids: List[str] = Field(default_factory=list)


class ChainUpdateRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    links: List[ChainLinkInput] = Field(..., max_length=40)


class StepRunRequest(BaseModel):
    skill_key: str = Field(..., min_length=2, max_length=80)
    target_assertion_ids: List[str] = Field(default_factory=list, max_length=50)
    force: bool = False


class QuestionAnswerRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    question_id: str
    answer: str = Field(..., min_length=1, max_length=4000)


class BoardSettingsRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    role: Optional[DraftRole] = None
    deadline_date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    deadline_basis: Optional[str] = Field(default=None, max_length=200)


class ParagraphPatchRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    text: str = Field(..., max_length=20000)


class LockPositionsRequest(BaseModel):
    board_version: int = Field(..., ge=0)


class ComposeRequest(BaseModel):
    board_version: int = Field(..., ge=0)
    # Limit regeneration to these paragraphs; default = everything stale or missing.
    paragraph_ids: List[str] = Field(default_factory=list)


class BenchAccepted(BaseModel):
    letter_id: str
    operation: Literal["dissect", "step", "compose"]
    step_id: Optional[str] = None
    poll_url: str


class BenchGate(BaseModel):
    undecided: int = 0
    unanswered_questions: int = 0
    stale_steps: int = 0
    running_steps: int = 0
    can_lock: bool = False
    can_compose: bool = False
    blockers: List[str] = Field(default_factory=list)


class BenchResponse(BaseModel):
    letter_id: str
    letter_title: Optional[str] = None
    letter_status: Optional[str] = None
    board: PositionBoard
    steps: List[SkillStepSummary] = Field(default_factory=list)
    gate: BenchGate = Field(default_factory=BenchGate)
    rollout_mode: str = "off"
    read_only: bool = False


class BenchAvailability(BaseModel):
    enabled: bool
    rollout_mode: str


