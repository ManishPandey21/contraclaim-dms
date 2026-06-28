"""Evidence graph models for contract intelligence timelines."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class EvidenceEntityType(str, Enum):
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
    UNRESOLVED_REFERENCE = "unresolved_reference"


class ProjectEventType(str, Enum):
    LETTER = "letter"
    INSTRUCTION = "instruction"
    DELAY = "delay"
    PAYMENT = "payment"
    DRAWING = "drawing"
    MILESTONE = "milestone"
    MEETING = "meeting"
    CLAIM = "claim"
    VARIATION = "variation"
    BANK_GUARANTEE = "bank_guarantee"
    KEY_DATE = "key_date"
    OTHER = "other"


class ProjectEventStatus(str, Enum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    CLOSED = "closed"
    DISPUTED = "disputed"


class EventLinkStatus(str, Enum):
    AI_SUGGESTED = "ai_suggested"
    USER_VERIFIED = "user_verified"
    REJECTED = "rejected"
    APPROVED = "approved"


class EventRelationType(str, Enum):
    REFERS_TO = "refers_to"
    REPLIES_TO = "replies_to"
    SUPERSEDES = "supersedes"
    SUPPORTS_CLAIM = "supports_claim"
    REBUTS_CLAIM = "rebuts_claim"
    ISSUES_INSTRUCTION = "issues_instruction"
    RECORDS_DEFAULT = "records_default"
    SEEKS_INFORMATION = "seeks_information"
    ISSUED_THROUGH = "issued_through"
    CAUSES_DELAY = "causes_delay"
    CAUSES_VARIATION = "causes_variation"
    CLARIFIES_SCOPE = "clarifies_scope"
    SUPPORTS_MEASUREMENT = "supports_measurement"
    AFFECTS = "affects"
    GOVERNED_BY = "governed_by"
    RELATES_TO = "relates_to"


class AIExtractionStatus(str, Enum):
    CREATED = "created"
    APPLIED = "applied"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class SourceSpan(BaseModel):
    page: Optional[int] = None
    start: Optional[int] = None
    end: Optional[int] = None
    text: Optional[str] = None


class ProjectEventBase(BaseModel):
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    event_type: ProjectEventType = ProjectEventType.OTHER
    event_date: datetime
    event_end_date: Optional[datetime] = None
    title: str
    description: Optional[str] = None
    party: Optional[str] = None
    package: Optional[str] = None
    impact_area: Optional[str] = None
    source_entity_type: Optional[EvidenceEntityType] = None
    source_entity_id: Optional[str] = None
    status: ProjectEventStatus = ProjectEventStatus.OPEN
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    ai_extraction_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProjectEventCreate(ProjectEventBase):
    pass


class ProjectEvent(ProjectEventBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class EventLinkBase(BaseModel):
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    source_type: EvidenceEntityType
    source_id: str
    target_type: EvidenceEntityType
    target_id: str
    relation_type: EventRelationType
    status: EventLinkStatus = EventLinkStatus.AI_SUGGESTED
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    evidence_text: Optional[str] = None
    source_spans: List[SourceSpan] = Field(default_factory=list)
    ai_extraction_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EventLinkCreate(EventLinkBase):
    link_group_id: Optional[str] = None


class EventLinkDecision(BaseModel):
    note: Optional[str] = None


class EventLink(EventLinkBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    link_group_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    revision: int = 1
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(populate_by_name=True)


class AIExtractionBase(BaseModel):
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    source_document_id: str
    content_hash: str
    schema_version: str = "evidence_graph.v1"
    model: Optional[str] = None
    prompt_version: Optional[str] = None
    raw_output: Dict[str, Any] = Field(default_factory=dict)
    parsed_output: Dict[str, Any] = Field(default_factory=dict)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    status: AIExtractionStatus = AIExtractionStatus.CREATED
    error: Optional[str] = None


class AIExtractionCreate(AIExtractionBase):
    pass


class AIExtraction(AIExtractionBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(populate_by_name=True)


class TimelineEvent(ProjectEvent):
    links: List[EventLink] = Field(default_factory=list)


class TimelineSummary(BaseModel):
    total_events: int = 0
    graph_links: int = 0
    ai_suggested: int = 0
    user_verified: int = 0
    approved: int = 0
    rejected: int = 0
    awaiting_review: int = 0


class TimelineResponse(BaseModel):
    events: List[TimelineEvent]
    summary: TimelineSummary
    skip: int = 0
    limit: int = 100
    has_more: bool = False
