"""Domain registers that feed the evidence graph timeline."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DrawingStatus(str, Enum):
    DRAFT = "draft"
    FOR_REVIEW = "for_review"
    FOR_CONSTRUCTION = "for_construction"
    SUPERSEDED = "superseded"
    AS_BUILT = "as_built"
    CANCELLED = "cancelled"


class DelayResponsibility(str, Enum):
    EMPLOYER = "employer"
    CONTRACTOR = "contractor"
    CONCURRENT = "concurrent"
    NEUTRAL = "neutral"
    UNDER_REVIEW = "under_review"


class DelayEventStatus(str, Enum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    CLAIMED = "claimed"
    ASSESSED = "assessed"
    REJECTED = "rejected"
    CLOSED = "closed"


class ProgrammeMilestoneType(str, Enum):
    PROGRAMME_ACTIVITY = "programme_activity"
    INTERFACE = "interface"
    SECTIONAL_COMPLETION = "sectional_completion"
    CONTRACTUAL_KEY_DATE = "contractual_key_date"


class ProgrammeMilestoneStatus(str, Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    DELAYED = "delayed"
    ACHIEVED = "achieved"
    SUPERSEDED = "superseded"


class DrawingReferenceBase(BaseModel):
    organization_id: Optional[str] = None
    project_id: str
    drawing_number: str
    title: str
    revision: Optional[str] = None
    status: DrawingStatus = DrawingStatus.FOR_REVIEW
    discipline: Optional[str] = None
    location: Optional[str] = None
    issue_date: Optional[datetime] = None
    received_date: Optional[datetime] = None
    issued_by: Optional[str] = None
    received_from: Optional[str] = None
    superseded_drawing_id: Optional[str] = None
    linked_letter_id: Optional[str] = None
    linked_variation_id: Optional[str] = None
    linked_delay_event_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DrawingReferenceCreate(DrawingReferenceBase):
    pass


class DrawingReferenceUpdate(BaseModel):
    title: Optional[str] = None
    revision: Optional[str] = None
    status: Optional[DrawingStatus] = None
    discipline: Optional[str] = None
    location: Optional[str] = None
    issue_date: Optional[datetime] = None
    received_date: Optional[datetime] = None
    issued_by: Optional[str] = None
    received_from: Optional[str] = None
    superseded_drawing_id: Optional[str] = None
    linked_letter_id: Optional[str] = None
    linked_variation_id: Optional[str] = None
    linked_delay_event_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class DrawingReference(DrawingReferenceBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class DelayEventBase(BaseModel):
    organization_id: Optional[str] = None
    project_id: str
    delay_ref: str
    title: str
    description: Optional[str] = None
    start_date: datetime
    end_date: Optional[datetime] = None
    duration_days: Optional[float] = None
    responsibility: DelayResponsibility = DelayResponsibility.UNDER_REVIEW
    cause: Optional[str] = None
    location: Optional[str] = None
    programme_activity: Optional[str] = None
    critical_path_impact: bool = False
    float_consumed_days: Optional[float] = None
    claimed_days: Optional[float] = None
    assessed_days: Optional[float] = None
    entitlement: Optional[str] = None
    status: DelayEventStatus = DelayEventStatus.OPEN
    evidence_link_ids: List[str] = Field(default_factory=list)
    linked_document_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DelayEventCreate(DelayEventBase):
    pass


class DelayEventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    duration_days: Optional[float] = None
    responsibility: Optional[DelayResponsibility] = None
    cause: Optional[str] = None
    location: Optional[str] = None
    programme_activity: Optional[str] = None
    critical_path_impact: Optional[bool] = None
    float_consumed_days: Optional[float] = None
    claimed_days: Optional[float] = None
    assessed_days: Optional[float] = None
    entitlement: Optional[str] = None
    status: Optional[DelayEventStatus] = None
    evidence_link_ids: Optional[List[str]] = None
    linked_document_ids: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class DelayEvent(DelayEventBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class ProgrammeMilestoneBase(BaseModel):
    organization_id: Optional[str] = None
    project_id: str
    milestone_ref: str
    title: str
    description: Optional[str] = None
    milestone_type: ProgrammeMilestoneType = ProgrammeMilestoneType.PROGRAMME_ACTIVITY
    planned_date: datetime
    forecast_date: Optional[datetime] = None
    actual_date: Optional[datetime] = None
    package: Optional[str] = None
    discipline: Optional[str] = None
    location: Optional[str] = None
    status: ProgrammeMilestoneStatus = ProgrammeMilestoneStatus.PLANNED
    linked_key_date_id: Optional[str] = None
    linked_document_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProgrammeMilestoneCreate(ProgrammeMilestoneBase):
    pass


class ProgrammeMilestoneUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    milestone_type: Optional[ProgrammeMilestoneType] = None
    planned_date: Optional[datetime] = None
    forecast_date: Optional[datetime] = None
    actual_date: Optional[datetime] = None
    package: Optional[str] = None
    discipline: Optional[str] = None
    location: Optional[str] = None
    status: Optional[ProgrammeMilestoneStatus] = None
    linked_key_date_id: Optional[str] = None
    linked_document_ids: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class ProgrammeMilestone(ProgrammeMilestoneBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)
