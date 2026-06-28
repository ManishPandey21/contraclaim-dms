"""Models for matter chronology and pleading chronology integration."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChronologyType(str, Enum):
    GENERAL_DISPUTE = "general_dispute"
    EOT_DELAY = "eot_delay"
    VARIATION = "variation"
    PAYMENT = "payment"
    TERMINATION = "termination"
    FORCE_MAJEURE = "force_majeure"
    DEFECT_DLP = "defect_dlp"
    BANK_GUARANTEE_RETENTION = "bank_guarantee_retention"
    COUNTERCLAIM = "counterclaim"
    OTHER = "other"


class ChronologyPartyPerspective(str, Enum):
    CLAIMANT = "claimant"
    RESPONDENT = "respondent"
    NEUTRAL = "neutral"


class ChronologyStatus(str, Enum):
    DRAFT = "draft"
    EXTRACTING = "extracting"
    REVIEW = "review"
    VERIFIED = "verified"
    EXPORTED = "exported"
    ARCHIVED = "archived"
    FAILED = "failed"


class ChronologyDateType(str, Enum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    INFERRED = "inferred"
    RANGE = "range"
    UNDATED = "undated"


class ChronologySupportsParty(str, Enum):
    CLAIMANT = "claimant"
    RESPONDENT = "respondent"
    NEUTRAL = "neutral"
    BOTH = "both"
    UNKNOWN = "unknown"


class ChronologyEventClassification(str, Enum):
    ADMITTED_FACT = "admitted_fact"
    DISPUTED_FACT = "disputed_fact"
    NOTICE = "notice"
    BREACH = "breach"
    MITIGATION = "mitigation"
    DELAY = "delay"
    PAYMENT = "payment"
    VARIATION = "variation"
    CONTRACTUAL_TRIGGER = "contractual_trigger"
    EVIDENCE_ONLY = "evidence_only"
    OTHER = "other"


class ChronologyImpactType(str, Enum):
    TIME = "time"
    COST = "cost"
    SCOPE = "scope"
    QUALITY = "quality"
    COMPLIANCE = "compliance"
    LEGAL = "legal"
    NONE = "none"
    UNKNOWN = "unknown"


class ChronologyVerificationStatus(str, Enum):
    AI_SUGGESTED = "ai_suggested"
    VERIFIED = "verified"
    EDITED_VERIFIED = "edited_verified"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    NEEDS_REVIEW = "needs_review"


class ChronologyPleadingUse(str, Enum):
    SOC_BACKGROUND = "soc_background"
    SOC_BREACH = "soc_breach"
    SOC_QUANTUM = "soc_quantum"
    SOD_DEFENCE = "sod_defence"
    SOD_OBJECTION = "sod_objection"
    REJOINDER_REPLY = "rejoinder_reply"
    COUNTERCLAIM = "counterclaim"
    ANNEXURE = "annexure"
    NONE = "none"


class ChronologyRevisionAction(str, Enum):
    CREATED = "created"
    EDITED = "edited"
    VERIFIED = "verified"
    REJECTED = "rejected"
    MARKED_DUPLICATE = "marked_duplicate"
    RESTORED = "restored"
    LINKED = "linked"
    EXPORTED = "exported"


class ChronologyExportType(str, Enum):
    DOCX = "docx"
    XLSX = "xlsx"
    PDF = "pdf"
    EVIDENCE_INDEX = "evidence_index"
    SOC_SECTION = "soc_section"
    SOD_SECTION = "sod_section"
    REJOINDER_SECTION = "rejoinder_section"
    HEARING_BUNDLE = "hearing_bundle"


class ChronologySourceSpan(BaseModel):
    page: Optional[int] = None
    paragraph: Optional[str] = None
    start: Optional[int] = None
    end: Optional[int] = None
    text: Optional[str] = None


class MatterChronologyBase(BaseModel):
    organization_id: Optional[str] = None
    project_id: str
    contract_id: Optional[str] = None
    matter_id: Optional[str] = None
    claim_id: Optional[str] = None
    title: str
    chronology_type: ChronologyType = ChronologyType.GENERAL_DISPUTE
    party_perspective: ChronologyPartyPerspective = ChronologyPartyPerspective.NEUTRAL
    selected_source_ids: List[str] = Field(default_factory=list)
    selected_source_types: List[str] = Field(default_factory=list)
    settings: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(use_enum_values=True)


class MatterChronologyCreate(MatterChronologyBase):
    pass


class MatterChronologyUpdate(BaseModel):
    contract_id: Optional[str] = None
    matter_id: Optional[str] = None
    claim_id: Optional[str] = None
    title: Optional[str] = None
    chronology_type: Optional[ChronologyType] = None
    party_perspective: Optional[ChronologyPartyPerspective] = None
    status: Optional[ChronologyStatus] = None
    selected_source_ids: Optional[List[str]] = None
    selected_source_types: Optional[List[str]] = None
    settings: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(use_enum_values=True)


class MatterChronology(MatterChronologyBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    status: ChronologyStatus = ChronologyStatus.DRAFT
    summary_counts: Dict[str, int] = Field(default_factory=dict)
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class MatterChronologyEventBase(BaseModel):
    chronology_id: str
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    contract_id: Optional[str] = None
    matter_id: Optional[str] = None
    claim_id: Optional[str] = None
    event_date: Optional[datetime] = None
    event_end_date: Optional[datetime] = None
    date_text: Optional[str] = None
    date_type: ChronologyDateType = ChronologyDateType.EXACT
    title: str
    description: Optional[str] = None
    source_document_id: Optional[str] = None
    source_document_name: Optional[str] = None
    source_page: Optional[int] = None
    source_paragraph: Optional[str] = None
    source_spans: List[ChronologySourceSpan] = Field(default_factory=list)
    letter_no: Optional[str] = None
    from_party: Optional[str] = None
    to_party: Optional[str] = None
    contract_clauses: List[str] = Field(default_factory=list)
    issue_tags: List[str] = Field(default_factory=list)
    claim_heads: List[str] = Field(default_factory=list)
    responsible_party: Optional[str] = None
    supports_party: ChronologySupportsParty = ChronologySupportsParty.UNKNOWN
    event_classification: ChronologyEventClassification = ChronologyEventClassification.OTHER
    impact_type: ChronologyImpactType = ChronologyImpactType.UNKNOWN
    impact_days: Optional[float] = None
    impact_amount: Optional[float] = None
    confidence_score: Optional[float] = Field(default=None, ge=0, le=1)
    verification_status: ChronologyVerificationStatus = ChronologyVerificationStatus.AI_SUGGESTED
    manual_notes: Optional[str] = None
    legal_relevance: Optional[str] = None
    pleading_use: ChronologyPleadingUse = ChronologyPleadingUse.NONE
    related_event_ids: List[str] = Field(default_factory=list)
    related_document_ids: List[str] = Field(default_factory=list)
    project_event_id: Optional[str] = None
    event_link_ids: List[str] = Field(default_factory=list)
    ai_extraction_id: Optional[str] = None
    annexure_no: Optional[str] = None
    duplicate_of_event_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(use_enum_values=True)


class MatterChronologyEventCreate(MatterChronologyEventBase):
    pass


class MatterChronologyEventUpdate(BaseModel):
    event_date: Optional[datetime] = None
    event_end_date: Optional[datetime] = None
    date_text: Optional[str] = None
    date_type: Optional[ChronologyDateType] = None
    title: Optional[str] = None
    description: Optional[str] = None
    source_document_id: Optional[str] = None
    source_document_name: Optional[str] = None
    source_page: Optional[int] = None
    source_paragraph: Optional[str] = None
    source_spans: Optional[List[ChronologySourceSpan]] = None
    letter_no: Optional[str] = None
    from_party: Optional[str] = None
    to_party: Optional[str] = None
    contract_clauses: Optional[List[str]] = None
    issue_tags: Optional[List[str]] = None
    claim_heads: Optional[List[str]] = None
    responsible_party: Optional[str] = None
    supports_party: Optional[ChronologySupportsParty] = None
    event_classification: Optional[ChronologyEventClassification] = None
    impact_type: Optional[ChronologyImpactType] = None
    impact_days: Optional[float] = None
    impact_amount: Optional[float] = None
    confidence_score: Optional[float] = Field(default=None, ge=0, le=1)
    verification_status: Optional[ChronologyVerificationStatus] = None
    manual_notes: Optional[str] = None
    legal_relevance: Optional[str] = None
    pleading_use: Optional[ChronologyPleadingUse] = None
    related_event_ids: Optional[List[str]] = None
    related_document_ids: Optional[List[str]] = None
    annexure_no: Optional[str] = None
    duplicate_of_event_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(use_enum_values=True)


class MatterChronologyEvent(MatterChronologyEventBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class MatterChronologyEventRevision(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    chronology_id: str
    event_id: str
    revision: int = 1
    action: ChronologyRevisionAction
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None
    note: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


class ChronologyExtractRequest(BaseModel):
    source_document_ids: List[str] = Field(default_factory=list)
    include_existing_project_events: bool = True
    max_documents: int = Field(default=100, ge=1, le=1000)


class ChronologyDecisionRequest(BaseModel):
    note: Optional[str] = None


class ChronologyDuplicateRequest(BaseModel):
    duplicate_of_event_id: str
    note: Optional[str] = None


class ChronologyLinkRequest(BaseModel):
    related_event_ids: List[str] = Field(default_factory=list)
    related_document_ids: List[str] = Field(default_factory=list)
    note: Optional[str] = None


class ChronologyPleadingContext(BaseModel):
    chronology_id: str
    source_ledger: List[Dict[str, Any]] = Field(default_factory=list)
    missing_evidence: List[str] = Field(default_factory=list)
    summary: Dict[str, int] = Field(default_factory=dict)


class AttachChronologyRequest(BaseModel):
    chronology_id: str
    include_unverified: bool = False
    limit: int = Field(default=200, ge=1, le=500)

