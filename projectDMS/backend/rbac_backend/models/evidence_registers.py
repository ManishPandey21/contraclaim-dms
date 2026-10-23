"""Domain registers that feed the evidence graph timeline."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _reject_explicit_nulls(model: BaseModel, fields: FrozenSet[str]) -> None:
    """PATCH semantics: an omitted field is untouched, an explicit null clears.

    Some fields have no meaningful "cleared" state (a record without a title or
    a start date is not a record). For those an explicit null is refused with a
    422 here, rather than stored and turned into a 500 on the next read.
    """
    for name in fields:
        if name in model.model_fields_set and getattr(model, name) is None:
            raise ValueError(f"{name} cannot be null")


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
    """Every status a stored `delay_events` row may carry.

    `open`, `under_review`, `resolved` and `closed` are the operational
    lifecycle. `claimed`, `assessed` and `rejected` predate the register: they
    describe the claim, not the event, and are kept so legacy rows and the
    legacy API stay valid. The register API records claim progress in
    `claim_status` instead and refuses them as a status (see `HindranceStatus`).
    """

    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    CLAIMED = "claimed"
    ASSESSED = "assessed"
    REJECTED = "rejected"
    CLOSED = "closed"


class HindranceStatus(str, Enum):
    """Operational lifecycle accepted by the register API."""

    OPEN = "open"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    CLOSED = "closed"


class HindranceEventType(str, Enum):
    """What kind of register entry this is.

    A hindrance or constraint is not presumed to be a delay, let alone an EOT
    entitlement; only `delay_event` carries that presumption. Rows written
    before the register existed have no stored type and read as `delay_event`:
    the delay-event schema was the only one that could have produced them.
    """

    HINDRANCE = "hindrance"
    CONSTRAINT = "constraint"
    DELAY_EVENT = "delay_event"


class HindranceCategory(str, Enum):
    """Controlled taxonomy, drawn from the document-metadata vocabulary."""

    SITE_ACCESS = "site_access"
    LAND_HANDOVER = "land_handover"
    DESIGN_INFORMATION = "design_information"
    DRAWING_APPROVAL = "drawing_approval"
    UTILITY_DIVERSION = "utility_diversion"
    TRAFFIC_DIVERSION = "traffic_diversion"
    TREE_CUTTING = "tree_cutting"
    STATUTORY_APPROVAL = "statutory_approval"
    LOCAL_RESTRICTIONS = "local_restrictions"
    ADVERSE_WEATHER = "adverse_weather"
    FORCE_MAJEURE = "force_majeure"
    VARIATION_INSTRUCTION = "variation_instruction"
    PAYMENT = "payment"
    EMPLOYER_SUPPLIED_ITEMS = "employer_supplied_items"
    SUSPENSION = "suspension"
    INTERFACE = "interface"
    OTHER = "other"


class HindranceClaimStatus(str, Enum):
    """Claim progress, kept apart from the operational lifecycle."""

    NOT_ASSESSED = "not_assessed"
    NOTIFIED = "notified"
    CLAIMED = "claimed"
    ASSESSED = "assessed"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    NOT_APPLICABLE = "not_applicable"


class TimelineSyncStatus(str, Enum):
    """State of the derived ProjectEvent. Mongo stays canonical either way."""

    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"


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

    @model_validator(mode="after")
    def _non_nullable(self) -> "DrawingReferenceUpdate":
        _reject_explicit_nulls(self, frozenset({"title", "status", "metadata"}))
        return self


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
    event_type: HindranceEventType = HindranceEventType.DELAY_EVENT
    # User-entered legacy reference. The register's own reference is the
    # server-generated, immutable `hindrance_ref` on `DelayEvent`.
    delay_ref: Optional[str] = None
    title: str
    description: Optional[str] = None
    category: Optional[HindranceCategory] = None
    start_date: datetime
    end_date: Optional[datetime] = None
    duration_days: Optional[float] = None
    responsibility: DelayResponsibility = DelayResponsibility.UNDER_REVIEW
    responsible_party: Optional[str] = None
    affected_party: Optional[str] = None
    cause: Optional[str] = None
    location: Optional[str] = None
    # Free text kept for compatibility; structured links live in
    # `delay_event_links` (programme activities, key dates, EOT submissions).
    programme_activity: Optional[str] = None
    impact_description: Optional[str] = None
    critical_path_impact: bool = False
    float_consumed_days: Optional[float] = None
    claimed_days: Optional[float] = None
    assessed_days: Optional[float] = None
    entitlement: Optional[str] = None
    status: DelayEventStatus = DelayEventStatus.OPEN
    claim_status: Optional[HindranceClaimStatus] = None
    evidence_link_ids: List[str] = Field(default_factory=list)
    linked_document_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DelayEventCreate(DelayEventBase):
    pass


_DELAY_EVENT_NON_NULLABLE = frozenset(
    {
        "title",
        "start_date",
        "responsibility",
        "status",
        "critical_path_impact",
        "evidence_link_ids",
        "linked_document_ids",
        "metadata",
    }
)


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

    @model_validator(mode="after")
    def _non_nullable(self) -> "DelayEventUpdate":
        _reject_explicit_nulls(self, _DELAY_EVENT_NON_NULLABLE)
        return self


class DelayEvent(DelayEventBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    hindrance_ref: Optional[str] = None
    archived_at: Optional[datetime] = None
    archived_by: Optional[str] = None
    archive_reason: Optional[str] = None
    timeline_sync_status: Optional[TimelineSyncStatus] = None
    timeline_sync_error: Optional[str] = None
    timeline_event_id: Optional[str] = None
    timeline_synced_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


# ---------------------------------------------------------------------------
# Register API (/api/hindrances). Same collection, stricter contract: no
# client-supplied reference, no scope change, operational status only.
# ---------------------------------------------------------------------------

_TEXT = 4000


class HindranceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: Optional[str] = None
    project_id: str = Field(..., min_length=1)
    event_type: HindranceEventType = HindranceEventType.HINDRANCE
    title: str = Field(..., min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=_TEXT)
    category: Optional[HindranceCategory] = None
    start_date: datetime
    end_date: Optional[datetime] = None
    responsibility: DelayResponsibility = DelayResponsibility.UNDER_REVIEW
    responsible_party: Optional[str] = Field(default=None, max_length=200)
    affected_party: Optional[str] = Field(default=None, max_length=200)
    cause: Optional[str] = Field(default=None, max_length=_TEXT)
    location: Optional[str] = Field(default=None, max_length=300)
    programme_activity: Optional[str] = Field(default=None, max_length=300)
    impact_description: Optional[str] = Field(default=None, max_length=_TEXT)
    critical_path_impact: bool = False
    float_consumed_days: Optional[float] = Field(default=None, ge=0)
    claimed_days: Optional[float] = Field(default=None, ge=0)
    assessed_days: Optional[float] = Field(default=None, ge=0)
    entitlement: Optional[str] = Field(default=None, max_length=_TEXT)
    status: HindranceStatus = HindranceStatus.OPEN
    claim_status: Optional[HindranceClaimStatus] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _dates_in_order(self) -> "HindranceCreate":
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        return self


HINDRANCE_NON_NULLABLE = frozenset(
    {"event_type", "title", "start_date", "responsibility", "status", "critical_path_impact", "metadata"}
)


class HindranceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: Optional[HindranceEventType] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=_TEXT)
    category: Optional[HindranceCategory] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    responsibility: Optional[DelayResponsibility] = None
    responsible_party: Optional[str] = Field(default=None, max_length=200)
    affected_party: Optional[str] = Field(default=None, max_length=200)
    cause: Optional[str] = Field(default=None, max_length=_TEXT)
    location: Optional[str] = Field(default=None, max_length=300)
    programme_activity: Optional[str] = Field(default=None, max_length=300)
    impact_description: Optional[str] = Field(default=None, max_length=_TEXT)
    critical_path_impact: Optional[bool] = None
    float_consumed_days: Optional[float] = Field(default=None, ge=0)
    claimed_days: Optional[float] = Field(default=None, ge=0)
    assessed_days: Optional[float] = Field(default=None, ge=0)
    entitlement: Optional[str] = Field(default=None, max_length=_TEXT)
    status: Optional[HindranceStatus] = None
    claim_status: Optional[HindranceClaimStatus] = None
    metadata: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _non_nullable(self) -> "HindranceUpdate":
        _reject_explicit_nulls(self, HINDRANCE_NON_NULLABLE)
        return self


class HindranceReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)


class HindranceListResponse(BaseModel):
    items: List[DelayEvent]
    total: int
    skip: int
    limit: int


class HindranceHistoryEntry(BaseModel):
    id: Optional[str] = None
    action: str
    actor_id: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    result: Optional[str] = None
    reason: Optional[str] = None
    changed_fields: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None


class HindranceHistoryResponse(BaseModel):
    entries: List[HindranceHistoryEntry]


class HindranceLinkTargetType(str, Enum):
    PROGRAMME_MILESTONE = "programme_milestone"
    KEY_DATE = "key_date"
    EOT_SUBMISSION = "eot_submission"


class HindranceLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: HindranceLinkTargetType
    target_id: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=1000)


class HindranceLinkTarget(BaseModel):
    label: str
    title: Optional[str] = None
    status: Optional[str] = None
    date: Optional[datetime] = None
    kind: Optional[str] = None
    route: Optional[str] = None


class HindranceLink(BaseModel):
    id: str = Field(alias="_id")
    delay_event_id: str
    organization_id: str
    project_id: str
    target_type: HindranceLinkTargetType
    target_id: str
    relationship_role: str
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    removed_at: Optional[datetime] = None
    removed_by: Optional[str] = None
    removal_reason: Optional[str] = None
    revision: int = Field(default=1, alias="_revision")
    target: Optional[HindranceLinkTarget] = None
    # False when the linked record no longer resolves (for example a deleted
    # key date); True with `target_restricted` when the caller may not view it.
    target_available: bool = True
    target_restricted: bool = False

    model_config = ConfigDict(populate_by_name=True)


class HindranceLinkListResponse(BaseModel):
    links: List[HindranceLink]


class HindranceAffectingItem(BaseModel):
    link: HindranceLink
    hindrance: DelayEvent


class HindranceAffectingResponse(BaseModel):
    items: List[HindranceAffectingItem]


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

    @model_validator(mode="after")
    def _non_nullable(self) -> "ProgrammeMilestoneUpdate":
        _reject_explicit_nulls(
            self,
            frozenset({"title", "milestone_type", "planned_date", "status", "linked_document_ids", "metadata"}),
        )
        return self


class ProgrammeMilestone(ProgrammeMilestoneBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)
