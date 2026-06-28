"""Models for arbitration pleadings drafting."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArbitrationDraftType(str, Enum):
    STATEMENT_OF_CLAIM = "statement_of_claim"
    STATEMENT_OF_DEFENCE = "statement_of_defence"
    REJOINDER = "rejoinder"
    COUNTERCLAIM = "counterclaim"


class ArbitrationPartyRole(str, Enum):
    CLAIMANT = "claimant"
    RESPONDENT = "respondent"


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
    MANUAL_FACT = "manual_fact"


class ArbitrationSourceUse(str, Enum):
    FACT = "fact"
    CLAUSE = "clause"
    CHRONOLOGY = "chronology"
    QUANTUM = "quantum"
    ANNEXURE = "annexure"
    BACKGROUND = "background"


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
    source_pleading_type: PleadingSourceType
    source_paragraph_number: str
    source_paragraph_text: str
    response_type: ParagraphResponseType = ParagraphResponseType.REQUIRE_PROOF
    response_text: Optional[str] = None
    response_reason: Optional[str] = None
    supporting_source_ids: List[str] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationDraftBase(BaseModel):
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

    model_config = ConfigDict(use_enum_values=True)


class ArbitrationDraftCreate(ArbitrationDraftBase):
    selected_references: List[ArbitrationSelectedReferenceCreate] = Field(default_factory=list)
    claim_heads: List[ArbitrationClaimHeadCreate] = Field(default_factory=list)


class ArbitrationDraftUpdate(BaseModel):
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
    ai_prompt_version: Optional[str] = None
    model: Optional[str] = None
    generation_run_id: Optional[str] = None
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


class PleadingImportRequest(BaseModel):
    source_pleading_document_id: Optional[str] = None
    source_pleading_type: PleadingSourceType
    text: str = Field(..., min_length=1)

    model_config = ConfigDict(use_enum_values=True)

    @field_validator("text")
    @classmethod
    def _trim_text(cls, value: str) -> str:
        return value.strip()


class ReturnForRevisionRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=4000)

