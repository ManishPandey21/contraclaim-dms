from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..utils.datetime import now_utc


DraftMode = Literal["background", "strategy", "draft", "review"]
DraftType = Literal["reply", "fresh"]
DraftRole = Literal["contractor", "engineer", "employer"]
LetterCategory = Literal[
    "claim_reply",
    "eot_reply",
    "variation",
    "payment_ipc",
    "advance_recovery",
    "completion",
    "ncr_quality",
    "delay_progress",
    "records_request",
    "dispute",
    "general",
]
DraftTone = Literal[
    "firm",
    "neutral",
    "advisory",
    "conciliatory",
    "firm_contractual",
]
IssueType = Literal[
    "claim",
    "delay",
    "variation",
    "payment",
    "approval",
    "dispute",
    "notice",
    "contractual_compliance",
    "request_for_information",
    "other",
]
IssueTypeSource = Literal["manual", "ai", "system_default"]
DraftRunStatus = Literal[
    "queued",
    "running",
    "awaiting_user_direction",
    "awaiting_strategy_confirmation",
    "cancel_requested",
    "cancelled",
    "completed",
    "blocked",
    "needs_attention",
    "failed",
    "approved",
    "exported",
    "issued",
]
DraftLifecycleEventType = Literal[
    "created",
    "reviewer_assigned",
    "comment_added",
    "analysis_confirmed",
    "user_direction_provided",
    "legal_risk_reviewed",
    "paragraphs_locked",
    "drafter_approved",
    "reviewer_approved",
    "final_approved",
    "plan_confirmed",
    "plan_accepted",
    "draft_accepted",
    "validated",
    "critiqued",
    "revised",
    "approved",
    "returned_for_correction",
    "exported",
    "issued",
    "failed",
    "queued",
    "resumed",
    "cancel_requested",
    "cancelled",
    "fallback_started",
]
DraftEngine = Literal["v2", "langgraph_v3"]
DraftExecutionStatus = Literal[
    "queued",
    "running",
    "awaiting_user_direction",
    "awaiting_strategy_confirmation",
    "completed",
    "failed",
    "cancel_requested",
    "cancelled",
]
DraftNextAction = Literal[
    "none",
    "poll",
    "answer_questions",
    "confirm_strategy",
    "approve",
    "cancelled",
]
ReplyMatrixStatus = Literal["supported", "needs_confirmation", "unsupported"]
RevisionAction = Literal[
    "make_firmer",
    "make_more_polite",
    "add_contractual_reasoning",
    "add_clause_reference",
    "make_short",
    "make_detailed",
    "convert_to_employer_submission",
    "convert_to_contractor_letter",
    "regenerate",
    "custom_instruction",
]
SourceType = Literal[
    "current_input",
    "contract_clause",
    "context_document",
    "prior_correspondence",
    "graph_thread",
    "comment",
    "other",
]
SourceUse = Literal["fact", "style_continuity", "history_only", "clause", "comment"]
ValidationLevel = Literal["warning", "error"]
AssertionSupportStatus = Literal["supported", "user_provided", "unsupported", "needs_confirmation"]


LegalRiskCategory = Literal["admission", "waiver", "contradiction", "entitlement"]
LegalRiskSeverity = Literal["info", "caution", "high"]


class LegalRiskFlag(BaseModel):
    """One potential admission/waiver/contradiction/entitlement in a draft.

    Flags never block a run — the human decides the contractual position;
    the system records the evidence.
    """

    flag_id: str
    category: LegalRiskCategory
    severity: LegalRiskSeverity = "caution"
    excerpt: str
    explanation: str


class LegalRiskReport(BaseModel):
    flags: List[LegalRiskFlag] = Field(default_factory=list)
    human_review_required: bool = False
    reviewed_at: Optional[datetime] = None


class LegalRiskReviewRequest(BaseModel):
    comment: Optional[str] = Field(default=None, max_length=2000)


class LockParagraphsRequest(BaseModel):
    """Human-approved paragraphs the AI must not change on redraft."""

    locked_paragraphs: List[str] = Field(default_factory=list)


ApprovalStage = Literal["drafter", "reviewer", "final"]


class ApprovalStep(BaseModel):
    """One completed step of the drafter -> reviewer -> final approval chain."""

    stage: ApprovalStage
    approved_by: Optional[str] = None
    approved_at: datetime = Field(default_factory=now_utc)
    comment: Optional[str] = Field(default=None, max_length=2000)


class ApproveStageRequest(BaseModel):
    stage: ApprovalStage
    comment: Optional[str] = Field(default=None, max_length=2000)


class ProbingQuestion(BaseModel):
    """A question the User Direction agent asks before planning/drafting."""

    question_id: str
    question: str
    category: Literal[
        "position", "deadline", "clause", "amount", "missing_input", "scope"
    ] = "scope"
    why: Optional[str] = None
    question_version: int = Field(default=1, ge=1)
    required: bool = True


class UserDirectionAnswer(BaseModel):
    question_id: Optional[str] = None
    answer: str = Field(..., min_length=1, max_length=4000)
    question_version: Optional[int] = Field(default=None, ge=1)


class UserDirectionRequest(BaseModel):
    """User's line of action: structured answers and/or free-text direction."""

    answers: List[UserDirectionAnswer] = Field(default_factory=list)
    directions: Optional[str] = Field(default=None, max_length=8000)


class DraftRunCreateRequest(BaseModel):
    mode: DraftMode = Field(default="draft")
    draft_type: DraftType = Field(default="reply")
    letter_category: LetterCategory = Field(default="general")
    contract_package: Optional[str] = Field(default=None, max_length=120)
    role: Optional[DraftRole] = None
    recipient_focus: Optional[str] = None
    subject: Optional[str] = Field(default=None, max_length=500)
    recipient: Optional[str] = Field(default=None, max_length=500)
    requirements: Optional[str] = Field(default=None, max_length=8000)
    points: Optional[str] = Field(default=None, max_length=8000)
    purpose: Optional[str] = Field(default=None, max_length=2000)
    desired_position: Optional[str] = Field(default=None, max_length=1000)
    required_action: Optional[str] = Field(default=None, max_length=2000)
    background_facts: Optional[str] = Field(default=None, max_length=8000)
    trigger_event: Optional[str] = Field(default=None, max_length=2000)
    tone: DraftTone = Field(default="firm_contractual")
    timeline_days: Optional[int] = Field(default=None, ge=0, le=3650)
    incoming_document_id: Optional[str] = None
    incoming_letter_id: Optional[str] = None
    clauses_to_consider: List[str] = Field(default_factory=list)
    attachments: List[str] = Field(default_factory=list)
    document_ids: List[str] = Field(default_factory=list)
    include_letter_codes: List[str] = Field(default_factory=list)
    exclude_letter_codes: List[str] = Field(default_factory=list)
    plan_override: Optional[str] = Field(default=None, max_length=12000)
    issue_type: Optional[IssueType] = None
    response_deadline: Optional[str] = Field(default=None, max_length=120)
    max_iterations: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Maximum cyclic retrieval/critique/redraft iterations for draft mode.",
    )
    finalized: bool = Field(
        default=False,
        description="Allows Learning Update extraction when a user explicitly finalizes/approves the draft.",
    )


class DraftRunResumeRequest(BaseModel):
    """Versioned human input used to resume an interrupted v3 run."""

    answers: List[UserDirectionAnswer] = Field(default_factory=list)
    directions: Optional[str] = Field(default=None, max_length=8000)
    strategy_approved: bool = False
    expected_state_version: int = Field(..., ge=0)


class DraftRunCancelRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=1000)
    expected_state_version: int = Field(..., ge=0)


class ForceV2FallbackRequest(BaseModel):
    reason: str = Field(..., min_length=3, max_length=1000)
    expected_state_version: int = Field(..., ge=0)


class CitedClauseEvaluation(BaseModel):
    clause_number: str
    clause_title: Optional[str] = None
    quoted_text: Optional[str] = None
    exists_in_contract: Optional[bool] = None
    quote_matches_contract: Optional[bool] = None
    applicable_to_issue: Optional[bool] = None
    sender_reliance_assessment: Optional[str] = None
    counter_clauses: List[str] = Field(default_factory=list)
    effect_on_sender_position: Optional[Literal["supports", "weakens", "neutral", "unknown"]] = None
    legal_or_commercial_review_required: bool = False
    drafter_comment: Optional[str] = None


class IncomingLetterAnalysis(BaseModel):
    letter_no: Optional[str] = None
    letter_date: Optional[str] = None
    sender: Optional[str] = None
    sender_role_or_party_type: Optional[str] = None
    recipient: Optional[str] = None
    subject: Optional[str] = None
    contract_project_reference: Optional[str] = None
    subject_matches_requested_matter: Optional[bool] = None
    issue_type: IssueType = "other"
    issue_type_source: IssueTypeSource = "system_default"
    issue_type_editable: bool = True
    main_request: Optional[str] = None
    # Points the AI metadata pipeline extracted as "to be addressed while
    # responding" on the incoming document — seeds the reply matrix.
    key_reply_points: List[str] = Field(default_factory=list)
    # Reference letters linked on the incoming document's stored metadata.
    linked_references: List[str] = Field(default_factory=list)
    clauses_cited: List[str] = Field(default_factory=list)
    cited_clause_evaluations: List[CitedClauseEvaluation] = Field(default_factory=list)
    amount_claimed: Optional[str] = None
    time_extension_requested: Optional[str] = None
    documents_submitted: List[str] = Field(default_factory=list)
    action_requested: Optional[str] = None
    response_required: Optional[bool] = None
    response_deadline: Optional[str] = None
    contractual_response_period: Optional[str] = None
    deadline_risk: Optional[Literal["low", "medium", "high"]] = None
    recommended_immediate_action: Optional[str] = None
    priority_flag: bool = False
    drafter_alert: Optional[str] = None
    reviewer_alert: Optional[str] = None
    approval_urgency: Optional[Literal["normal", "urgent"]] = None
    workflow_due_date: Optional[str] = None
    contractual_risk: Optional[str] = None
    extraction_confidence: Optional[float] = Field(default=None, ge=0, le=1)
    confirmed_by_user: bool = False
    confirmed_at: Optional[datetime] = None


class PlanningSheet(BaseModel):
    draft_type: DraftType = "reply"
    letter_category: LetterCategory = "general"
    letter_purpose: Optional[str] = None
    subject: Optional[str] = None
    recipient: Optional[str] = None
    sender_role: DraftRole = "contractor"
    trigger_event: Optional[str] = None
    contractual_basis: List[str] = Field(default_factory=list)
    factual_basis: List[str] = Field(default_factory=list)
    previous_correspondence: List[str] = Field(default_factory=list)
    issue_type: IssueType = "other"
    response_deadline: Optional[str] = None
    deadline_risk: Optional[Literal["low", "medium", "high"]] = None
    cited_clause_evaluations: List[CitedClauseEvaluation] = Field(default_factory=list)
    recommended_position: Optional[str] = None
    required_action: Optional[str] = None
    timeline: Optional[str] = None
    tone: DraftTone = "firm_contractual"
    risk_level: Literal["low", "medium", "high"] = "medium"
    rights_reservation_required: bool = True
    missing_inputs: List[str] = Field(default_factory=list)
    user_confirmed: bool = False


class ReplyMatrixRow(BaseModel):
    incoming_point: str
    proposed_reply: str
    source_ids: List[str] = Field(default_factory=list)
    clause_refs: List[str] = Field(default_factory=list)
    risk_note: Optional[str] = None
    status: ReplyMatrixStatus = "needs_confirmation"


class SourceIntegritySummary(BaseModel):
    documents_relied_upon: List[str] = Field(default_factory=list)
    clauses_relied_upon: List[str] = Field(default_factory=list)
    user_provided_facts: List[str] = Field(default_factory=list)
    prior_correspondence_used: List[str] = Field(default_factory=list)
    placeholders_requiring_confirmation: List[str] = Field(default_factory=list)
    unsupported_points_excluded: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class DraftingStartRequest(BaseModel):
    draft_type: DraftType = "reply"
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    contract_package: Optional[str] = None
    letter_category: LetterCategory = "general"
    sender_role: DraftRole = "contractor"
    recipient: Optional[str] = None
    subject: Optional[str] = None
    incoming_document_id: Optional[str] = None
    incoming_letter_id: Optional[str] = None


class DraftingStartResponse(BaseModel):
    session_id: str
    letter_id: str
    next_step: str


class ConfirmAnalysisRequest(BaseModel):
    analysis: IncomingLetterAnalysis


class ConfirmPlanRequest(BaseModel):
    planning_sheet: Optional[PlanningSheet] = None
    reply_matrix: List[ReplyMatrixRow] = Field(default_factory=list)


class ReviseDraftRequest(BaseModel):
    revision_action: RevisionAction
    custom_instruction: Optional[str] = Field(default=None, max_length=2000)
    additional_requirements: Optional[str] = Field(default=None, max_length=4000)
    # None = inherit the source run's locks; [] = clear all locks.
    locked_paragraphs: Optional[List[str]] = None


class AssignReviewerRequest(BaseModel):
    reviewer_user_id: str = Field(..., min_length=1, max_length=200)
    due_at: Optional[datetime] = None
    note: Optional[str] = Field(default=None, max_length=2000)


class DraftCommentRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
    visibility: Literal["internal", "reviewer", "approver"] = "reviewer"


class ReturnForCorrectionRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=4000)
    required_changes: List[str] = Field(default_factory=list)


class SourceEvidence(BaseModel):
    source_id: str
    source_type: SourceType
    allowed_use: SourceUse
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    label: str
    text: Optional[str] = None
    snippet: Optional[str] = None
    document_id: Optional[str] = None
    letter_id: Optional[str] = None
    clause_number: Optional[str] = None
    clause_title: Optional[str] = None
    page_numbers: List[int] = Field(default_factory=list)
    score: Optional[float] = None
    source_hash: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DraftContextBundle(BaseModel):
    active_workspace: Dict[str, Optional[str]] = Field(default_factory=dict)
    current_materials: List[str] = Field(default_factory=list)
    selected_document_ids: List[str] = Field(default_factory=list)
    prior_correspondence_ids: List[str] = Field(default_factory=list)
    graph_thread_codes: List[str] = Field(default_factory=list)
    comments: List[str] = Field(default_factory=list)
    threshold_inputs: Dict[str, bool] = Field(default_factory=dict)


class DraftArtifact(BaseModel):
    draft_letter: str = ""
    source_integrity_notes: str = ""
    learning_update: Optional[str] = None
    raw_model_output: str = ""
    model_name: Optional[str] = None
    prompt_version: Optional[int] = None


class ValidationFinding(BaseModel):
    level: ValidationLevel
    code: str
    message: str
    evidence: Optional[str] = None


class ValidationReport(BaseModel):
    blocking: bool = False
    findings: List[ValidationFinding] = Field(default_factory=list)


class DraftAssertionSupport(BaseModel):
    assertion_id: str
    text: str
    support_status: AssertionSupportStatus = "needs_confirmation"
    source_ids: List[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "medium"


class CyclicIterationTrace(BaseModel):
    iteration: int
    critique_blocking: bool = False
    finding_codes: List[str] = Field(default_factory=list)
    refinement_queries: List[str] = Field(default_factory=list)
    retrieved_source_ids: List[str] = Field(default_factory=list)
    regenerated: bool = False
    notes: Optional[str] = None


class DraftConfidenceScores(BaseModel):
    clause_confidence: float = Field(default=0.0, ge=0, le=1)
    factual_support: float = Field(default=0.0, ge=0, le=1)
    tone_suitability: float = Field(default=0.0, ge=0, le=1)
    overall: float = Field(default=0.0, ge=0, le=1)
    risk_level: Literal["low", "medium", "high"] = "medium"


class DraftLifecycleEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = Field(default=None, alias="_id")
    event_id: str
    letter_id: str
    run_id: str
    event_type: DraftLifecycleEventType
    actor_user_id: Optional[str] = None
    status: Optional[str] = None
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)

    @field_validator("id", mode="before")
    @classmethod
    def _stringify_id(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)


class DraftReviewAssignment(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = Field(default=None, alias="_id")
    assignment_id: str
    letter_id: str
    run_id: str
    reviewer_user_id: str
    assigned_by: Optional[str] = None
    due_at: Optional[datetime] = None
    status: Literal["assigned", "completed", "cancelled"] = "assigned"
    note: Optional[str] = None
    notified: bool = False
    overdue_notified: bool = False
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    @field_validator("id", mode="before")
    @classmethod
    def _stringify_id(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)


class DraftReviewComment(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = Field(default=None, alias="_id")
    comment_id: str
    letter_id: str
    run_id: str
    body: str
    visibility: Literal["internal", "reviewer", "approver"] = "reviewer"
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)

    @field_validator("id", mode="before")
    @classmethod
    def _stringify_id(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)


class DraftGovernanceResponse(BaseModel):
    letter_id: str
    run_id: str
    assignments: List[DraftReviewAssignment] = Field(default_factory=list)
    comments: List[DraftReviewComment] = Field(default_factory=list)
    events: List[DraftLifecycleEvent] = Field(default_factory=list)


class DraftMetricKpi(BaseModel):
    key: str
    label: str
    value: float
    formatted_value: str
    target: Optional[str] = None
    status: Literal["good", "watch", "risk"] = "good"
    detail: Optional[str] = None


class DraftMetricBreakdownItem(BaseModel):
    label: str
    count: int
    percentage: float = 0.0


class DraftMetricTrendPoint(BaseModel):
    date: str
    runs: int = 0
    approved: int = 0
    blocking: int = 0
    unsupported_rate: float = 0.0
    average_confidence: float = 0.0


class DraftMetricBottleneck(BaseModel):
    stage: str
    count: int
    average_age_hours: float = 0.0


class DraftQualityRiskItem(BaseModel):
    run_id: str
    letter_id: str
    status: str
    risk: str
    detail: str
    created_at: Optional[datetime] = None


class DraftQualityDashboardResponse(BaseModel):
    generated_at: datetime = Field(default_factory=now_utc)
    window_days: int = 30
    total_runs: int = 0
    active_runs: int = 0
    approved_runs: int = 0
    exported_runs: int = 0
    issued_runs: int = 0
    average_cycle_hours: float = 0.0
    average_iterations: float = 0.0
    average_confidence: float = 0.0
    first_review_approval_rate: float = 0.0
    unsupported_claim_rate: float = 0.0
    blocking_validation_rate: float = 0.0
    source_integrity_rate: float = 0.0
    average_sources_per_run: float = 0.0
    review_return_rate: float = 0.0
    issue_artifact_compliance_rate: float = 0.0
    overdue_review_count: int = 0
    kpis: List[DraftMetricKpi] = Field(default_factory=list)
    status_breakdown: List[DraftMetricBreakdownItem] = Field(default_factory=list)
    quality_trends: List[DraftMetricTrendPoint] = Field(default_factory=list)
    bottlenecks: List[DraftMetricBottleneck] = Field(default_factory=list)
    recent_risks: List[DraftQualityRiskItem] = Field(default_factory=list)


class DraftContextPack(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = Field(default=None, alias="_id")
    context_pack_id: str
    letter_id: str
    run_id: str
    project: Dict[str, Any] = Field(default_factory=dict)
    draft_request: Dict[str, Any] = Field(default_factory=dict)
    facts: List[str] = Field(default_factory=list)
    contractual_basis: List[Dict[str, Any]] = Field(default_factory=list)
    prior_correspondence: List[Dict[str, Any]] = Field(default_factory=list)
    required_actions: List[str] = Field(default_factory=list)
    risk_flags: List[str] = Field(default_factory=list)
    missing_confirmations: List[str] = Field(default_factory=list)
    source_notes: List[str] = Field(default_factory=list)
    source_ids: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)

    @field_validator("id", mode="before")
    @classmethod
    def _stringify_id(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)


class SourceLedgerResponse(BaseModel):
    letter_id: str
    run_id: str
    source_count: int = 0
    sources: List[SourceEvidence] = Field(default_factory=list)


class ExactClauseSearchRequest(BaseModel):
    organization_id: str
    project_id: str
    clause_number: str = Field(..., min_length=1, max_length=120)
    document_id: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=50)


class ExactReferenceSearchRequest(BaseModel):
    organization_id: str
    project_id: str
    reference: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=50)


class DraftAuditResponse(BaseModel):
    letter_id: str
    run_id: str
    events: List[DraftLifecycleEvent] = Field(default_factory=list)


class DraftInputSnapshot(BaseModel):
    """Immutable create/resume input record for reproducible execution."""

    snapshot_id: str
    letter_id: str
    run_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    payload_hash: str
    created_at: datetime = Field(default_factory=now_utc)


class DraftEvidenceSnapshot(BaseModel):
    """Immutable, source-minimised evidence ledger for one run."""

    snapshot_id: str
    letter_id: str
    run_id: str
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    context_hash: str
    created_at: datetime = Field(default_factory=now_utc)


class DraftExecutionEffect(BaseModel):
    effect_id: str
    effect_key: str
    run_id: str
    effect_type: str
    status: Literal["pending", "completed", "failed"] = "pending"
    payload_hash: str
    created_at: datetime = Field(default_factory=now_utc)
    completed_at: Optional[datetime] = None


class DraftOutboxEvent(BaseModel):
    event_id: str
    run_id: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "processing", "completed", "failed"] = "pending"
    attempt_count: int = 0
    created_at: datetime = Field(default_factory=now_utc)
    processed_at: Optional[datetime] = None


class DraftRun(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = Field(default=None, alias="_id")
    run_id: str
    letter_id: str
    draft_type: DraftType = "reply"
    mode: DraftMode
    letter_category: LetterCategory = "general"
    contract_package: Optional[str] = None
    status: DraftRunStatus
    role: DraftRole
    recipient_focus: Optional[str] = None
    inputs: Dict[str, Any] = Field(default_factory=dict)
    incoming_analysis: Optional[IncomingLetterAnalysis] = None
    probing_questions: List[ProbingQuestion] = Field(default_factory=list)
    user_directions: List[UserDirectionAnswer] = Field(default_factory=list)
    planning_sheet: Optional[PlanningSheet] = None
    reply_matrix: List[ReplyMatrixRow] = Field(default_factory=list)
    context_bundle: DraftContextBundle = Field(default_factory=DraftContextBundle)
    sources: List[SourceEvidence] = Field(default_factory=list)
    context_pack_id: Optional[str] = None
    plan: Optional[str] = None
    draft_artifact: Optional[DraftArtifact] = None
    source_integrity_summary: Optional[SourceIntegritySummary] = None
    validation_report: ValidationReport = Field(default_factory=ValidationReport)
    legal_risk_report: Optional[LegalRiskReport] = None
    locked_paragraphs: List[str] = Field(default_factory=list)
    cyclic_trace: List[CyclicIterationTrace] = Field(default_factory=list)
    assertion_support: List[DraftAssertionSupport] = Field(default_factory=list)
    confidence_scores: Optional[DraftConfidenceScores] = None
    iteration_count: int = 0
    revision_of_run_id: Optional[str] = None
    revision_action: Optional[RevisionAction] = None
    approval_status: Optional[str] = None
    approvals: List[ApprovalStep] = Field(default_factory=list)
    assigned_reviewer_id: Optional[str] = None
    returned_reason: Optional[str] = None
    required_changes: List[str] = Field(default_factory=list)
    issued_document_id: Optional[str] = None
    exported_file_id: Optional[str] = None
    exported_pdf_file_id: Optional[str] = None
    exported_docx_file_id: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    exported_by: Optional[str] = None
    exported_at: Optional[datetime] = None
    issued_by: Optional[str] = None
    issued_at: Optional[datetime] = None
    last_validated_at: Optional[datetime] = None
    warnings: List[str] = Field(default_factory=list)
    # Engine and execution metadata.  These optional/defaulted fields keep all
    # pre-migration v2 records readable while making the v3 contract explicit.
    engine: DraftEngine = "v2"
    engine_version: str = "v2"
    graph_version: Optional[str] = None
    state_schema_version: int = 1
    thread_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    request_hash: Optional[str] = None
    attempt_number: int = Field(default=1, ge=1)
    parent_run_id: Optional[str] = None
    fallback_of_run_id: Optional[str] = None
    fallback_reason: Optional[str] = None
    shadow_of_run_id: Optional[str] = None
    context_snapshot_id: Optional[str] = None
    input_snapshot_id: Optional[str] = None
    input_snapshot_hash: Optional[str] = None
    context_snapshot_hash: Optional[str] = None
    execution_status: DraftExecutionStatus = "completed"
    next_action: DraftNextAction = "none"
    state_version: int = Field(default=0, ge=0)
    last_checkpoint_id: Optional[str] = None
    queue_job_id: Optional[str] = None
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    cancellation_requested_at: Optional[datetime] = None
    cancellation_reason: Optional[str] = None
    resumed_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    trace: List[Dict[str, Any]] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=now_utc)
    completed_at: datetime = Field(default_factory=now_utc)
    created_by: Optional[str] = None

    @field_validator("id", mode="before")
    @classmethod
    def _stringify_id(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)


class DraftRunResponse(DraftRun):
    pass


class DraftRunAccepted(BaseModel):
    """Asynchronous creation response; legacy/off mode still returns DraftRun."""

    run_id: str
    letter_id: str
    engine: DraftEngine
    execution_status: DraftExecutionStatus = "queued"
    next_action: DraftNextAction = "poll"
    state_version: int = 0
    poll_url: str


class DraftRunStateResponse(BaseModel):
    run_id: str
    letter_id: str
    engine: DraftEngine
    execution_status: DraftExecutionStatus
    next_action: DraftNextAction
    state_version: int
    last_checkpoint_id: Optional[str] = None
    cancellation_requested_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    probing_questions: List[ProbingQuestion] = Field(default_factory=list)


class DraftCheckpointResponse(BaseModel):
    checkpoint_id: str
    run_id: str
    thread_id: str
    node: str
    state_schema_version: int
    created_at: datetime
    redacted_state: Dict[str, Any] = Field(default_factory=dict)


class PromptTemplateRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = Field(default=None, alias="_id")
    prompt_key: str
    version: int
    supported_payload_schema: List[str] = Field(default_factory=list)
    template: str
    enabled: bool = True
    created_at: datetime = Field(default_factory=now_utc)

    @field_validator("id", mode="before")
    @classmethod
    def _stringify_id(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(value)
