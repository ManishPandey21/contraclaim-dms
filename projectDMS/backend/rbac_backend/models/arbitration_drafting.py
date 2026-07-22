"""Models for arbitration pleadings drafting."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PRIVILEGED_MATRIX_FIELDS = {
    "approved",
    "approved_at",
    "approved_by",
    "approval_log",
    "approval_status",
    "human_approval_status",
    "last_review_action",
    "last_reviewed_at",
    "last_reviewed_by",
    "permission_approved_at",
    "permission_approved_by",
    "permission_obtained",
    "permission_source_id",
    "projection_hash",
    "projection_read_only",
    "projection_source",
    "readiness_status",
    "rejected_at",
    "rejected_by",
    "review_completed_roles",
    "review_status",
    "source_paragraph_response_id",
    "verified",
    "verification_status",
}

ALLOWED_AGENT_OPTIONS = {
    "acknowledgement_dates",
    "agent_mode",
    "agent_model",
    "amendment_leave_required",
    "cause_of_action_date",
    "chronology_limit",
    "claim_limit",
    "clause_limit",
    "clause_vector_limit",
    "document_limit",
    "exhibit_prefix",
    "final_bill_date",
    "include_review_sources",
    "interest_from",
    "interest_period_days",
    "interest_rate",
    "interest_to",
    "limitation_period_years",
    "pleading_timetable",
    "pre_arbitration_steps",
    "quantum_limit",
    "rejection_date",
    "sequence",
}


class ArbitrationDraftType(str, Enum):
    STATEMENT_OF_CLAIM = "statement_of_claim"
    STATEMENT_OF_DEFENCE = "statement_of_defence"
    REJOINDER = "rejoinder"
    COUNTERCLAIM = "counterclaim"


class ArbitrationPartyRole(str, Enum):
    CLAIMANT = "claimant"
    RESPONDENT = "respondent"


class ArbitrationCasePartyPerspective(str, Enum):
    CLAIMANT = "claimant"
    RESPONDENT = "respondent"
    BOTH = "both"
    NEUTRAL = "neutral"


class ArbitrationCaseStatus(str, Enum):
    INTAKE = "intake"
    MATRIX_PREPARATION = "matrix_preparation"
    READY_FOR_DRAFTING = "ready_for_drafting"
    DRAFTING = "drafting"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    FILED = "filed"
    ARCHIVED = "archived"


class ArbitrationDisputeType(str, Enum):
    EOT_DELAY = "eot_delay"
    PROLONGATION_COST = "prolongation_cost"
    PRICE_VARIATION = "price_variation"
    VARIATION_CHANGE_ORDER = "variation_change_order"
    PAYMENT_DISPUTE = "payment_dispute"
    TERMINATION = "termination"
    FORCE_MAJEURE = "force_majeure"
    DEFECT_DLP = "defect_dlp"
    BANK_GUARANTEE_RETENTION = "bank_guarantee_retention"
    COUNTERCLAIM = "counterclaim"
    OTHER = "other"


class ArbitrationDraftStatus(str, Enum):
    DRAFT = "draft"
    GENERATING = "generating"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    EXPORTED = "exported"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class ArbitrationSourceType(str, Enum):
    LETTER = "letter"
    DOCUMENT = "document"
    CLAUSE = "clause"
    DRAWING = "drawing"
    PAYMENT_EVENT = "payment_event"
    PROGRAMME_MILESTONE = "programme_milestone"
    KEY_DATE = "key_date"
    DELAY_EVENT = "delay_event"
    CLAIM = "claim"
    VARIATION = "variation"
    BANK_GUARANTEE = "bank_guarantee"
    PROJECT_EVENT = "project_event"
    EVENT_LINK = "event_link"
    CHRONOLOGY_EVENT = "chronology_event"
    MANUAL_FACT = "manual_fact"
    EXPERT_REPORT = "expert_report"


class ArbitrationSourceUse(str, Enum):
    FACT = "fact"
    CLAUSE = "clause"
    CHRONOLOGY = "chronology"
    QUANTUM = "quantum"
    ANNEXURE = "annexure"
    BACKGROUND = "background"
    EXPERT = "expert"


class ClaimHeadType(str, Enum):
    TIME = "time"
    COST = "cost"
    VARIATION = "variation"
    PAYMENT = "payment"
    DEFECT = "defect"
    TERMINATION = "termination"
    SETOFF = "setoff"
    INTEREST = "interest"
    OTHER = "other"


class SupportStatus(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    EVIDENCE_REQUIRED = "evidence_required"


class PleadingSourceType(str, Enum):
    STATEMENT_OF_CLAIM = "statement_of_claim"
    STATEMENT_OF_DEFENCE = "statement_of_defence"
    COUNTERCLAIM = "counterclaim"


class ParagraphResponseType(str, Enum):
    ADMIT = "admit"
    DENY = "deny"
    REQUIRE_PROOF = "require_proof"
    PART_ADMIT_PART_DENY = "part_admit_part_deny"
    NOT_ADMITTED = "not_admitted"
    MISCONCEIVED = "misconceived"
    INCORRECT = "incorrect"
    MISLEADING = "misleading"


class GenerationRunType(str, Enum):
    FULL_DRAFT = "full_draft"
    SECTION_REGENERATION = "section_regeneration"
    PARAGRAPH_RESPONSE = "paragraph_response"
    EVIDENCE_REFRESH = "evidence_refresh"


class GenerationRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ArbitrationBundleFormat(str, Enum):
    ZIP = "zip"
    DOCX = "docx"
    PDF = "pdf"


class ArbitrationWorkflowStatus(str, Enum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    AWAITING_DOCUMENT_SELECTION = "awaiting_document_selection"
    AWAITING_USER_DIRECTION = "awaiting_user_direction"
    AWAITING_MATRIX_REVIEW = "awaiting_matrix_review"
    AWAITING_READINESS_APPROVAL = "awaiting_readiness_approval"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    AWAITING_LEGAL_REVIEW = "awaiting_legal_review"
    AWAITING_DRAFT_APPROVAL = "awaiting_draft_approval"
    AWAITING_EXPORT_AUTHORIZATION = "awaiting_export_authorization"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    FALLBACK_V2 = "fallback_v2"


class MatrixApprovalStatus(str, Enum):
    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class MatrixReviewActionType(str, Enum):
    ASSIGN = "assign"
    COMMENT = "comment"
    REQUEST_CHANGES = "request_changes"
    APPROVE = "approve"
    REJECT = "reject"


class MatrixReviewerRole(str, Enum):
    LEGAL = "legal"
    CONTRACTS = "contracts"
    TECHNICAL = "technical"
    DELAY = "delay"
    QUANTUM = "quantum"
    COMMERCIAL = "commercial"
    REVIEWER = "reviewer"


class ReadinessCheckStatus(str, Enum):
    READY = "ready"
    NEEDS_EVIDENCE = "needs_evidence"
    NEEDS_CLAUSE_SUPPORT = "needs_clause_support"
    NEEDS_QUANTUM_SUPPORT = "needs_quantum_support"
    NEEDS_LEGAL_REVIEW = "needs_legal_review"
    NEEDS_USER_CONFIRMATION = "needs_user_confirmation"
    BLOCKED = "blocked"


class ArbitrationCaseBase(BaseModel):
    organization_id: Optional[str] = None
    project_id: str
    contract_id: Optional[str] = None
    title: str
    case_reference: Optional[str] = None
    party_perspective: ArbitrationCasePartyPerspective = ArbitrationCasePartyPerspective.NEUTRAL
    tribunal_details: Optional[str] = None
    institutional_rules: Optional[str] = None
    seat: Optional[str] = None
    venue: Optional[str] = None
    language: Optional[str] = None
    governing_law: Optional[str] = None
    arbitration_clause_source_id: Optional[str] = None
    arbitration_clause: Optional[str] = None
    case_summary: Optional[str] = None

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationCaseCreate(ArbitrationCaseBase):
    pass


class ArbitrationCaseUpdate(BaseModel):
    contract_id: Optional[str] = None
    title: Optional[str] = None
    case_reference: Optional[str] = None
    party_perspective: Optional[ArbitrationCasePartyPerspective] = None
    tribunal_details: Optional[str] = None
    institutional_rules: Optional[str] = None
    seat: Optional[str] = None
    venue: Optional[str] = None
    language: Optional[str] = None
    governing_law: Optional[str] = None
    arbitration_clause_source_id: Optional[str] = None
    arbitration_clause: Optional[str] = None
    case_summary: Optional[str] = None
    status: Optional[ArbitrationCaseStatus] = None

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationCase(ArbitrationCaseBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    status: ArbitrationCaseStatus = ArbitrationCaseStatus.INTAKE
    readiness_score: int = 0
    readiness_blockers: List[Dict[str, Any]] = Field(default_factory=list)
    readiness_approved_by: Optional[str] = None
    readiness_approved_at: Optional[datetime] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class ArbitrationMatrixRowCreate(BaseModel):
    draft_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _reject_privileged_review_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            supplied = PRIVILEGED_MATRIX_FIELDS.intersection(value)
            if "status" in value and str(value.get("status") or "").lower() in {
                "approved",
                "ready",
                "verified",
            }:
                supplied.add("status")
            if supplied:
                raise ValueError(
                    "Matrix review fields are server-controlled; use the dedicated review endpoint: "
                    + ", ".join(sorted(supplied))
                )
        return value


class ArbitrationMatrixRowUpdate(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    draft_id: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _reject_privileged_review_fields(cls, value: Any) -> Any:
        return ArbitrationMatrixRowCreate._reject_privileged_review_fields(value)


class ArbitrationMatrixReviewRequest(BaseModel):
    action: MatrixReviewActionType
    reviewer_role: MatrixReviewerRole = MatrixReviewerRole.LEGAL
    reviewer_user_id: Optional[str] = None
    required_roles: List[MatrixReviewerRole] = Field(default_factory=list)
    comment: Optional[str] = Field(default=None, max_length=5000)
    due_at: Optional[datetime] = None

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationRejoinderPermissionRequest(BaseModel):
    permission_source_id: str = Field(..., min_length=1, max_length=200)
    permission_notes: Optional[str] = Field(default=None, max_length=4000)


class ArbitrationMatrixRow(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    case_id: str
    draft_id: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    contract_id: Optional[str] = None
    approval_status: Optional[MatrixApprovalStatus] = None
    verification_status: Optional[str] = None
    readiness_status: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True, extra="allow")


class ArbitrationReadinessCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    case_id: str
    draft_id: Optional[str] = None
    check_key: str
    check_group: str
    status: ReadinessCheckStatus
    message: str
    linked_matrix_row_id: Optional[str] = None
    assigned_to: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class ArbitrationReadinessResponse(BaseModel):
    case_id: str
    draft_id: Optional[str] = None
    readiness_score: int
    status: str
    blockers: List[ArbitrationReadinessCheck] = Field(default_factory=list)
    checks: List[ArbitrationReadinessCheck] = Field(default_factory=list)

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationReadinessApprovalRequest(BaseModel):
    draft_id: Optional[str] = None
    draft_type: Optional[ArbitrationDraftType] = None
    reviewer_role: str = Field(default="senior_legal_approver", min_length=1, max_length=100)
    comment: Optional[str] = Field(default=None, max_length=4000)

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationApprovalReceipt(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    gate: str
    case_id: str
    draft_id: Optional[str] = None
    draft_type: ArbitrationDraftType
    organization_id: Optional[str] = None
    project_id: str
    matrix_revision_set_id: str
    matrix_revision_hash: str
    evidence_snapshot_hash: str
    artifact_hash: str
    decision: str = "approved"
    receipt_status: str = "pending"
    approver_id: str
    approver_role: str
    comment: Optional[str] = None
    approved_at: datetime = Field(default_factory=datetime.utcnow)
    committed_at: Optional[datetime] = None
    invalidated_at: Optional[datetime] = None
    invalidated_by: Optional[str] = None
    invalidation_reason: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class ArbitrationAgentRunRequest(BaseModel):
    draft_id: Optional[str] = None
    options: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("options")
    @classmethod
    def _reject_privileged_or_unknown_options(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        options = dict(value or {})
        if "auto_approve" in options:
            raise ValueError("options.auto_approve is prohibited; agents cannot approve arbitration artifacts")
        unknown = sorted(set(options).difference(ALLOWED_AGENT_OPTIONS))
        if unknown:
            raise ValueError("Unsupported arbitration agent options: " + ", ".join(unknown))
        return options


class ArbitrationAgentRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    case_id: str
    draft_id: Optional[str] = None
    agent_type: str
    status: str = "queued"
    input_hash: Optional[str] = None
    source_ids: List[str] = Field(default_factory=list)
    output_summary: Optional[str] = None
    created_records: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    prompt_version: Optional[str] = None
    model: Optional[str] = None
    background_job_id: Optional[str] = None
    started_at: Optional[datetime] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(populate_by_name=True)


class ArbitrationBundleExportRequest(BaseModel):
    format: ArbitrationBundleFormat = ArbitrationBundleFormat.ZIP

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationBundleExport(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    case_id: str
    format: ArbitrationBundleFormat
    status: str = "queued"
    background_job_id: Optional[str] = None
    effect_key: Optional[str] = None
    attempts: int = 0
    content_type: Optional[str] = None
    filename: Optional[str] = None
    content_length: int = 0
    error: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class ArbitrationSelectedReference(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    draft_id: Optional[str] = None
    source_type: ArbitrationSourceType
    source_id: str
    label: str
    citation: Optional[str] = None
    snippet: Optional[str] = None
    page_numbers: List[int] = Field(default_factory=list)
    clause_number: Optional[str] = None
    letter_no: Optional[str] = None
    event_date: Optional[datetime] = None
    allowed_use: ArbitrationSourceUse = ArbitrationSourceUse.FACT
    selected_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class ArbitrationSelectedReferenceCreate(BaseModel):
    source_type: ArbitrationSourceType
    source_id: str
    label: str
    citation: Optional[str] = None
    snippet: Optional[str] = None
    page_numbers: List[int] = Field(default_factory=list)
    clause_number: Optional[str] = None
    letter_no: Optional[str] = None
    event_date: Optional[datetime] = None
    allowed_use: ArbitrationSourceUse = ArbitrationSourceUse.FACT
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationClaimHead(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    draft_id: Optional[str] = None
    head_type: ClaimHeadType = ClaimHeadType.OTHER
    description: str
    amount: Optional[float] = None
    currency: Optional[str] = None
    calculation_basis: Optional[str] = None
    supporting_source_ids: List[str] = Field(default_factory=list)
    status: SupportStatus = SupportStatus.EVIDENCE_REQUIRED
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class ArbitrationClaimHeadCreate(BaseModel):
    head_type: ClaimHeadType = ClaimHeadType.OTHER
    description: str
    amount: Optional[float] = None
    currency: Optional[str] = None
    calculation_basis: Optional[str] = None
    supporting_source_ids: List[str] = Field(default_factory=list)
    status: SupportStatus = SupportStatus.EVIDENCE_REQUIRED

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationParagraphResponse(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    draft_id: Optional[str] = None
    source_pleading_document_id: Optional[str] = None
    source_pleading_version_id: Optional[str] = None
    source_pleading_version_hash: Optional[str] = None
    source_pleading_type: PleadingSourceType
    source_paragraph_number: str
    source_paragraph_text: str
    response_type: ParagraphResponseType = ParagraphResponseType.REQUIRE_PROOF
    response_text: Optional[str] = None
    response_reason: Optional[str] = None
    supporting_source_ids: List[str] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class ArbitrationParagraphResponseCreate(BaseModel):
    source_pleading_document_id: Optional[str] = None
    source_pleading_version_id: Optional[str] = None
    source_pleading_type: PleadingSourceType
    source_paragraph_number: str
    source_paragraph_text: str
    response_type: ParagraphResponseType = ParagraphResponseType.REQUIRE_PROOF
    response_text: Optional[str] = None
    response_reason: Optional[str] = None
    supporting_source_ids: List[str] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationParagraphResponseUpdate(BaseModel):
    response_type: ParagraphResponseType
    response_text: Optional[str] = Field(default=None, max_length=8000)
    response_reason: Optional[str] = Field(default=None, max_length=8000)
    supporting_source_ids: List[str] = Field(default_factory=list, max_length=200)
    missing_evidence: List[str] = Field(default_factory=list, max_length=200)

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationDraftBase(BaseModel):
    case_id: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: str
    contract_id: Optional[str] = None
    draft_type: ArbitrationDraftType
    party_role: ArbitrationPartyRole
    dispute_type: ArbitrationDisputeType = ArbitrationDisputeType.OTHER
    title: str
    case_details: Dict[str, Any] = Field(default_factory=dict)
    tribunal_details: Optional[str] = None
    arbitration_clause: Optional[str] = None
    governing_law: Optional[str] = None
    relief_sought: Optional[str] = None
    manual_facts: Optional[str] = None
    claim_amount: Optional[float] = None
    currency: Optional[str] = None
    interest_rate: Optional[float] = None
    # Register-source controls (audit P2): registers join the ledger by default,
    # but the user can turn them off wholesale or exclude specific rows.
    include_register_sources: bool = True
    excluded_register_ids: List[str] = Field(default_factory=list)

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationDraftCreate(ArbitrationDraftBase):
    selected_references: List[ArbitrationSelectedReferenceCreate] = Field(default_factory=list)
    claim_heads: List[ArbitrationClaimHeadCreate] = Field(default_factory=list)


class ArbitrationDraftUpdate(BaseModel):
    case_id: Optional[str] = None
    contract_id: Optional[str] = None
    draft_type: Optional[ArbitrationDraftType] = None
    party_role: Optional[ArbitrationPartyRole] = None
    dispute_type: Optional[ArbitrationDisputeType] = None
    title: Optional[str] = None
    case_details: Optional[Dict[str, Any]] = None
    tribunal_details: Optional[str] = None
    arbitration_clause: Optional[str] = None
    governing_law: Optional[str] = None
    relief_sought: Optional[str] = None
    manual_facts: Optional[str] = None
    claim_amount: Optional[float] = None
    currency: Optional[str] = None
    interest_rate: Optional[float] = None
    include_register_sources: Optional[bool] = None
    excluded_register_ids: Optional[List[str]] = None
    status: Optional[ArbitrationDraftStatus] = None

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationDraft(ArbitrationDraftBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    status: ArbitrationDraftStatus = ArbitrationDraftStatus.DRAFT
    is_locked: bool = False
    current_version: int = 0
    latest_generation_run_id: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    approved_version_id: Optional[str] = None
    approved_version: Optional[int] = None
    approved_version_hash: Optional[str] = None
    readiness_approval_receipt_id: Optional[str] = None
    exported_by: Optional[str] = None
    exported_at: Optional[datetime] = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class ArbitrationDraftVersion(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    draft_id: str
    version: int
    status: ArbitrationDraftStatus = ArbitrationDraftStatus.DRAFT
    sections: List[Dict[str, Any]] = Field(default_factory=list)
    full_markdown: str = ""
    structured_output: Dict[str, Any] = Field(default_factory=dict)
    source_ledger: List[Dict[str, Any]] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    paragraph_responses: List[Dict[str, Any]] = Field(default_factory=list)
    claim_heads: List[Dict[str, Any]] = Field(default_factory=list)
    annexures: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validation_status: str = "not_checked"
    ai_prompt_version: Optional[str] = None
    model: Optional[str] = None
    generation_run_id: Optional[str] = None
    parent_version_id: Optional[str] = None
    parent_version: Optional[int] = None
    version_hash: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class ArbitrationGenerationRun(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    draft_id: str
    run_type: GenerationRunType = GenerationRunType.FULL_DRAFT
    section_key: Optional[str] = None
    status: GenerationRunStatus = GenerationRunStatus.QUEUED
    input_hash: Optional[str] = None
    retrieval_queries: List[str] = Field(default_factory=list)
    source_ids: List[str] = Field(default_factory=list)
    prompt_version: Optional[str] = None
    model: Optional[str] = None
    raw_output: str = ""
    parsed_output: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    error_message: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class ArbitrationDraftDetail(ArbitrationDraft):
    selected_references: List[ArbitrationSelectedReference] = Field(default_factory=list)
    claim_heads: List[ArbitrationClaimHead] = Field(default_factory=list)
    paragraph_responses: List[ArbitrationParagraphResponse] = Field(default_factory=list)
    latest_version: Optional[ArbitrationDraftVersion] = None


class ArbitrationEvidenceSearchRequest(BaseModel):
    query: str = Field(default="", max_length=1000)
    source_types: List[ArbitrationSourceType] = Field(default_factory=list)
    include_unverified_graph_links: bool = False
    limit: int = Field(default=20, ge=1, le=100)

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationEvidenceSearchResponse(BaseModel):
    results: List[ArbitrationSelectedReferenceCreate] = Field(default_factory=list)


class ArbitrationGenerateRequest(BaseModel):
    section_key: Optional[str] = None
    include_unverified_graph_links: bool = False
    additional_instruction: Optional[str] = Field(default=None, max_length=2000)
    # audit item 6: "deterministic" (default) or "llm" prose generation. LLM mode
    # falls back to deterministic when no model client is configured.
    draft_mode: Optional[str] = None


class PleadingImportRequest(BaseModel):
    source_pleading_document_id: Optional[str] = None
    source_pleading_version_id: Optional[str] = None
    source_pleading_type: PleadingSourceType
    text: str = Field(..., min_length=1)

    model_config = ConfigDict(use_enum_values=True)

    @field_validator("text")
    @classmethod
    def _trim_text(cls, value: str) -> str:
        return value.strip()


class ReturnForRevisionRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=4000)


class ArbitrationOpponentPleadingSelection(BaseModel):
    draft_id: str
    version_id: str


class ArbitrationWorkflowCreateRequest(BaseModel):
    draft_id: Optional[str] = None
    pleading_type: ArbitrationDraftType
    selected_document_ids: List[str] = Field(default_factory=list, max_length=500)
    opponent_draft_id: Optional[str] = None
    opponent_version_id: Optional[str] = None
    opponent_pleadings: List[ArbitrationOpponentPleadingSelection] = Field(default_factory=list, max_length=4)
    requested_engine: Optional[str] = None

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationWorkflowAccepted(BaseModel):
    run_id: str
    case_id: str
    draft_id: Optional[str] = None
    engine: str
    rollout_mode: str
    rollout_policy_version: Optional[str] = None
    rollout_decision_reason: Optional[str] = None
    rollout_decision_hash: Optional[str] = None
    acceptance_receipt_sha256: Optional[str] = None
    v2_compatibility_mode: Optional[str] = None
    status: ArbitrationWorkflowStatus
    current_node: str
    next_action: str
    state_version: int
    created_at: datetime

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationWorkflowStateResponse(ArbitrationWorkflowAccepted):
    pleading_type: ArbitrationDraftType
    graph_version: str
    state_schema_version: int
    progress: int = Field(default=0, ge=0, le=100)
    blockers: List[Dict[str, Any]] = Field(default_factory=list)
    required_human_role: Optional[str] = None
    fallback_available: bool = False
    last_checkpoint_at: Optional[datetime] = None
    checkpoint_sync_status: Optional[str] = None
    checkpoint_sync_state_version: Optional[int] = None
    updated_at: Optional[datetime] = None
    document_manifest_hash: Optional[str] = None
    opponent_pleading_snapshot_hash: Optional[str] = None
    evidence_snapshot_hash: Optional[str] = None
    analysis_artifact_set_id: Optional[str] = None
    analysis_artifact_set_hash: Optional[str] = None
    matrix_revision_set_id: Optional[str] = None
    matrix_revision_hash: Optional[str] = None
    readiness_artifact_hash: Optional[str] = None
    plan_id: Optional[str] = None
    plan_hash: Optional[str] = None
    draft_version_id: Optional[str] = None
    draft_version_hash: Optional[str] = None
    validation_status: Optional[str] = None
    validation_blockers: List[Dict[str, Any]] = Field(default_factory=list)
    validation_warnings: List[Dict[str, Any]] = Field(default_factory=list)
    validation_artifact_set_id: Optional[str] = None
    validation_artifact_set_hash: Optional[str] = None
    validation_report_id: Optional[str] = None
    validation_report_hash: Optional[str] = None
    validation_route: Optional[str] = None
    remediation_artifact_id: Optional[str] = None
    remediation_artifact_hash: Optional[str] = None
    remediation_cycle: int = 0
    approval_receipt_ids: Dict[str, str] = Field(default_factory=dict)
    targeted_questions: List[Dict[str, Any]] = Field(default_factory=list)
    fallback_reason: Optional[str] = None
    fallback_from_engine: Optional[str] = None
    fallback_input_snapshot_id: Optional[str] = None
    fallback_input_snapshot_hash: Optional[str] = None


class ArbitrationWorkflowResumeRequest(BaseModel):
    state_version: int = Field(..., ge=1)
    gate: str = Field(..., min_length=1, max_length=100)
    decision: str = Field(default="continue", max_length=100)
    artifact_hash: Optional[str] = Field(default=None, max_length=128)
    selected_document_ids: List[str] = Field(default_factory=list, max_length=500)
    answers: Dict[str, str] = Field(default_factory=dict)
    directions: Optional[str] = Field(default=None, max_length=8000)


class ArbitrationWorkflowApprovalRequest(BaseModel):
    state_version: int = Field(..., ge=1)
    decision: str = Field(default="approved", pattern="^(approved|rejected|returned)$")
    artifact_hash: str = Field(..., min_length=16, max_length=128)
    reviewer_role: str = Field(..., min_length=1, max_length=100)
    comment: Optional[str] = Field(default=None, max_length=4000)


class ArbitrationWorkflowCancelRequest(BaseModel):
    state_version: int = Field(..., ge=1)
    reason: str = Field(..., min_length=1, max_length=1000)


class ArbitrationWorkflowFallbackRequest(ArbitrationWorkflowCancelRequest):
    pass


class ArbitrationProductionAcceptanceRequest(BaseModel):
    criteria: Dict[str, str]
    evidence_hashes: Dict[str, str]
    stakeholder_signoffs: List[str] = Field(..., min_length=2, max_length=20)
    organization_ids: List[str] = Field(..., min_length=1, max_length=100)
    project_ids: List[str] = Field(default_factory=list, max_length=500)
    expires_at: datetime
    notes: Optional[str] = Field(default=None, max_length=8000)

    @model_validator(mode="after")
    def _validate_complete_acceptance(self):
        required = {str(index) for index in range(1, 15)}
        if set(self.criteria) != required or any(value != "passed" for value in self.criteria.values()):
            raise ValueError("All 14 production acceptance criteria must be explicitly passed")
        if set(self.evidence_hashes) != required or any(
            not re.fullmatch(r"[0-9a-fA-F]{64}", str(value or ""))
            for value in self.evidence_hashes.values()
        ):
            raise ValueError("Every production acceptance criterion requires a SHA-256 evidence hash")
        if len(set(self.stakeholder_signoffs)) < 2:
            raise ValueError("At least two distinct stakeholder signoffs are required")
        return self


class ArbitrationPlan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    run_id: str
    case_id: str
    draft_id: Optional[str] = None
    version: int = 1
    plan_hash: str
    status: str = "needs_review"
    issues: List[Dict[str, Any]] = Field(default_factory=list)
    positions: List[Dict[str, Any]] = Field(default_factory=list)
    paragraph_mapping: List[Dict[str, Any]] = Field(default_factory=list)
    claim_theory: List[Dict[str, Any]] = Field(default_factory=list)
    legal_basis: List[Dict[str, Any]] = Field(default_factory=list)
    burden_of_proof: List[Dict[str, Any]] = Field(default_factory=list)
    anticipated_arguments: List[Dict[str, Any]] = Field(default_factory=list)
    causation_theory: List[Dict[str, Any]] = Field(default_factory=list)
    quantum_theory: List[Dict[str, Any]] = Field(default_factory=list)
    evidentiary_gaps: List[Dict[str, Any]] = Field(default_factory=list)
    relief_requested: List[Dict[str, Any]] = Field(default_factory=list)
    section_structure: List[Dict[str, Any]] = Field(default_factory=list)
    section_source_mapping: List[Dict[str, Any]] = Field(default_factory=list)
    source_mapping: List[Dict[str, Any]] = Field(default_factory=list)
    decisions: Dict[str, Any] = Field(default_factory=dict)
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(populate_by_name=True)
