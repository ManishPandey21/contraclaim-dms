"""Key Date / Milestone Tracker models.

Project-level contractual key dates / milestones with EOT (extension-of-time)
applications, an immutable extension history (the original key date is never
overwritten), and actual-achievement records. Tenant-scoped by organization /
project, consistent with the claims and contract-appraisal modules.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class MilestoneStatus(str, Enum):
    NOT_STARTED = "not_started"
    UPCOMING = "upcoming"
    DUE_SOON = "due_soon"
    DUE_TODAY = "due_today"
    OVERDUE = "overdue"
    ACHIEVED = "achieved"
    EOT_SUBMITTED = "eot_submitted"
    EOT_UNDER_REVIEW = "eot_under_review"
    EXTENSION_APPROVED = "extension_approved"
    EXTENSION_REJECTED = "extension_rejected"


class EOTStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class BaselineStatus(str, Enum):
    DRAFT = "draft"
    FROZEN = "frozen"


class EOTSubmissionStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    LOCKED = "locked"
    WITHDRAWN = "withdrawn"
    SUPERSEDED = "superseded"


class EOTDeterminationStatus(str, Enum):
    NOT_STARTED = "not_started"
    UNDER_REVIEW = "under_review"
    PENDING = "pending"
    GRANTED = "granted"
    PARTIALLY_GRANTED = "partially_granted"
    REJECTED = "rejected"
    NO_EXTENSION = "no_extension"
    SUPERSEDED = "superseded"


class EOTDeterminationResult(str, Enum):
    GRANTED = "granted"
    PARTIALLY_GRANTED = "partially_granted"
    REJECTED = "rejected"
    NO_CHANGE = "no_change"
    PENDING = "pending"


# --- milestone ------------------------------------------------------------


class KeyDateMilestoneBase(BaseModel):
    milestone_ref: Optional[str] = None
    title: str
    description: Optional[str] = None
    contractual_week_number: int = Field(..., ge=1)
    # Baseline planned date (defaults to the calculated date); never overwritten.
    original_planned_key_date: Optional[datetime] = None
    calculated_key_date: Optional[datetime] = None
    # The date in force: original, or the latest approved revised date.
    current_approved_key_date: Optional[datetime] = None
    responsible_party_id: Optional[str] = None
    remarks: Optional[str] = None
    linked_document_ids: List[str] = Field(default_factory=list)
    linked_letter_ids: List[str] = Field(default_factory=list)
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    contract_id: str = "primary"


class KeyDateMilestoneCreate(KeyDateMilestoneBase):
    title: str = Field(..., min_length=1, max_length=300)
    project_id: str = Field(..., min_length=1)
    # Used for the date calculation when the project record carries no start date.
    project_start_date: Optional[datetime] = None


class KeyDateMilestoneUpdate(BaseModel):
    milestone_ref: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    contractual_week_number: Optional[int] = Field(None, ge=1)
    responsible_party_id: Optional[str] = None
    remarks: Optional[str] = None
    linked_document_ids: Optional[List[str]] = None
    linked_letter_ids: Optional[List[str]] = None
    project_start_date: Optional[datetime] = None  # triggers a recalculation


class MilestoneRevision(BaseModel):
    """One approved EOT revision, surfaced as a column on the register (CM-4b)."""
    revision_number: int
    approved_revised_key_date: Optional[datetime] = None
    eot_letter_reference: Optional[str] = None
    approval_letter_reference: Optional[str] = None
    approval_date: Optional[datetime] = None
    status: str = "approved"


class KeyDateMilestone(KeyDateMilestoneBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    eot_status: Optional[str] = None  # latest EOT lifecycle state
    current_revision: int = 0
    latest_eot_submission_label: Optional[str] = None
    latest_eot_submitted_date: Optional[datetime] = None
    latest_eot_status: Optional[str] = None
    pending_eot_count: int = 0
    # Derived: one entry per approved EOT (original date + these = the per-EOT columns).
    revisions: List[MilestoneRevision] = Field(default_factory=list)
    # Achievement summary (full record also stored in key_date_achievements).
    actual_achievement_date: Optional[datetime] = None
    achieved_by: Optional[str] = None
    achieved_on_time: Optional[bool] = None
    delay_days: Optional[int] = None
    early_completion_days: Optional[int] = None
    achievement_remarks: Optional[str] = None
    client_notification_required: bool = False
    client_notification_ref: Optional[str] = None
    client_notification_date: Optional[datetime] = None
    final_status: Optional[str] = None
    # Derived for responses (not persisted authoritatively).
    status: Optional[str] = None
    days_remaining: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


# --- EOT application ------------------------------------------------------


class EOTApplicationCreate(BaseModel):
    application_date: Optional[datetime] = None
    eot_letter_reference: Optional[str] = None
    requested_extension_days: int = Field(..., ge=1)
    requested_revised_key_date: Optional[datetime] = None
    reason: Optional[str] = None
    linked_document_ids: List[str] = Field(default_factory=list)
    submit: bool = True  # False persists a draft; True requires the letter ref


class EOTReview(BaseModel):
    decision: str  # approved | rejected | withdrawn | under_review
    approved_extension_days: Optional[int] = Field(None, ge=0)
    approved_revised_key_date: Optional[datetime] = None
    approval_letter_reference: Optional[str] = None
    approval_date: Optional[datetime] = None
    approving_authority: Optional[str] = None
    approval_remarks: Optional[str] = None
    linked_document_ids: Optional[List[str]] = None


class EOTApplication(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    milestone_id: str
    project_id: Optional[str] = None
    organization_id: Optional[str] = None
    application_date: Optional[datetime] = None
    eot_letter_reference: Optional[str] = None
    requested_extension_days: Optional[int] = None
    requested_revised_key_date: Optional[datetime] = None
    reason: Optional[str] = None
    linked_document_ids: List[str] = Field(default_factory=list)
    status: EOTStatus = EOTStatus.DRAFT
    submitted_by: Optional[str] = None
    submitted_date: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    reviewed_date: Optional[datetime] = None
    approved_extension_days: Optional[int] = None
    approved_revised_key_date: Optional[datetime] = None
    approval_letter_reference: Optional[str] = None
    approval_date: Optional[datetime] = None
    approving_authority: Optional[str] = None
    remarks: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(populate_by_name=True)


# --- extension history (immutable revisions) ------------------------------


class ExtensionHistory(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    milestone_id: str
    project_id: Optional[str] = None
    organization_id: Optional[str] = None
    revision_number: int
    original_key_date: Optional[datetime] = None
    previous_key_date: Optional[datetime] = None
    requested_revised_key_date: Optional[datetime] = None
    approved_revised_key_date: Optional[datetime] = None
    requested_extension_days: Optional[int] = None
    approved_extension_days: Optional[int] = None
    eot_letter_reference: Optional[str] = None
    approval_letter_reference: Optional[str] = None
    approval_date: Optional[datetime] = None
    status: str = "approved"  # approved | rejected
    remarks: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(populate_by_name=True)


# --- achievement ----------------------------------------------------------


class AchievementRecord(BaseModel):
    actual_achievement_date: datetime
    achieved_by: Optional[str] = None
    achievement_remarks: Optional[str] = None
    linked_document_ids: List[str] = Field(default_factory=list)
    client_notification_required: bool = False
    client_notification_ref: Optional[str] = None
    client_notification_date: Optional[datetime] = None
    final_status: Optional[str] = None


# --- dashboard ------------------------------------------------------------


class KeyDateDashboard(BaseModel):
    total: int = 0
    achieved: int = 0
    pending: int = 0
    overdue: int = 0
    due_30: int = 0
    due_15: int = 0
    due_10: int = 0
    due_1: int = 0
    eot_submitted: int = 0
    eot_under_review: int = 0
    eot_approved: int = 0
    eot_rejected: int = 0
    achieved_late: int = 0
    achieved_early: int = 0


# --- project/contract baseline + successive EOT workflow -----------------


class KeyDateWorkflowScope(BaseModel):
    project_id: str = Field(..., min_length=1)
    contract_id: str = Field(default="primary", min_length=1)
    organization_id: Optional[str] = None


class BaselineFreezeRequest(KeyDateWorkflowScope):
    confirmation: bool = True


class KeyDateBaseline(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    organization_id: Optional[str] = None
    project_id: str
    contract_id: str = "primary"
    status: BaselineStatus = BaselineStatus.DRAFT
    revision_number: int = 0
    frozen_at: Optional[datetime] = None
    frozen_by: Optional[str] = None
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    next_revision_number: int = 0
    items: List[dict] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class EOTSubmissionItemInput(BaseModel):
    milestone_ref: str = Field(..., min_length=1, max_length=100)
    eot_submitted_date: datetime
    claimed_extension_days: Optional[int] = Field(None, ge=0)
    remarks: Optional[str] = None


class EOTSubmissionCreate(KeyDateWorkflowScope):
    eot_reference: Optional[str] = None
    contractor_submission_date: Optional[datetime] = None
    contractor_letter_reference: Optional[str] = None
    claim_cutoff_date: Optional[datetime] = None
    remarks: Optional[str] = None
    status: EOTSubmissionStatus = EOTSubmissionStatus.DRAFT
    items: List[EOTSubmissionItemInput] = Field(default_factory=list)


class EOTSubmissionUpdate(BaseModel):
    eot_reference: Optional[str] = None
    contractor_submission_date: Optional[datetime] = None
    contractor_letter_reference: Optional[str] = None
    claim_cutoff_date: Optional[datetime] = None
    remarks: Optional[str] = None
    status: Optional[EOTSubmissionStatus] = None
    items: Optional[List[EOTSubmissionItemInput]] = None


class EOTSubmissionItem(EOTSubmissionItemInput):
    id: Optional[str] = Field(default=None, alias="_id")
    eot_submission_id: str
    key_date_id: str
    contractual_date_at_submission: Optional[datetime] = None
    original_contractual_date: Optional[datetime] = None
    description: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class EOTSubmission(BaseModel):
    id: str = Field(alias="_id")
    organization_id: Optional[str] = None
    project_id: str
    contract_id: str = "primary"
    revision_number: int
    revision_label: str
    eot_reference: Optional[str] = None
    contractor_submission_date: Optional[datetime] = None
    contractor_letter_reference: Optional[str] = None
    claim_cutoff_date: Optional[datetime] = None
    status: EOTSubmissionStatus
    remarks: Optional[str] = None
    created_at: datetime
    created_by: Optional[str] = None
    locked_at: Optional[datetime] = None
    locked_by: Optional[str] = None
    items: List[EOTSubmissionItem] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class EOTDeterminationItemInput(BaseModel):
    milestone_ref: str = Field(..., min_length=1, max_length=100)
    eot_granted_date: Optional[datetime] = None
    granted_extension_days: Optional[int] = Field(None, ge=0)
    determination_result: EOTDeterminationResult = EOTDeterminationResult.PENDING
    remarks: Optional[str] = None


class EOTDeterminationCreate(KeyDateWorkflowScope):
    eot_submission_ids: List[str] = Field(..., min_length=1)
    determination_reference: Optional[str] = None
    determination_date: Optional[datetime] = None
    approval_grant_reference: Optional[str] = None
    approved_by: Optional[str] = None
    status: EOTDeterminationStatus = EOTDeterminationStatus.UNDER_REVIEW
    remarks: Optional[str] = None
    supersedes_determination_ids: List[str] = Field(default_factory=list)
    items: List[EOTDeterminationItemInput] = Field(default_factory=list)


class EOTDeterminationUpdate(BaseModel):
    determination_reference: Optional[str] = None
    determination_date: Optional[datetime] = None
    approval_grant_reference: Optional[str] = None
    approved_by: Optional[str] = None
    status: Optional[EOTDeterminationStatus] = None
    remarks: Optional[str] = None
    supersedes_determination_ids: Optional[List[str]] = None
    items: Optional[List[EOTDeterminationItemInput]] = None


class EOTDeterminationItem(EOTDeterminationItemInput):
    id: Optional[str] = Field(default=None, alias="_id")
    determination_id: str
    key_date_id: str
    contractual_date_before_determination: Optional[datetime] = None
    submitted_date: Optional[datetime] = None
    claimed_extension_days: Optional[int] = None
    description: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class EOTDetermination(BaseModel):
    id: str = Field(alias="_id")
    organization_id: Optional[str] = None
    project_id: str
    contract_id: str = "primary"
    eot_submission_ids: List[str]
    covered_revision_labels: List[str] = Field(default_factory=list)
    determination_reference: Optional[str] = None
    determination_date: Optional[datetime] = None
    approval_grant_reference: Optional[str] = None
    approved_by: Optional[str] = None
    status: EOTDeterminationStatus
    remarks: Optional[str] = None
    supersedes_determination_ids: List[str] = Field(default_factory=list)
    created_at: datetime
    created_by: Optional[str] = None
    frozen_at: Optional[datetime] = None
    frozen_by: Optional[str] = None
    items: List[EOTDeterminationItem] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class KeyDateWorkflowSummary(BaseModel):
    project_id: str
    contract_id: str = "primary"
    baseline_status: BaselineStatus = BaselineStatus.DRAFT
    baseline_frozen_at: Optional[datetime] = None
    baseline_frozen_by: Optional[str] = None
    current_contractual_baseline: str = "Original"
    latest_eot_submission: Optional[str] = None
    pending_determinations: int = 0
    open_eot_submissions: int = 0
    oldest_pending_submission: Optional[str] = None
    submissions: List[EOTSubmission] = Field(default_factory=list)
    determinations: List[EOTDetermination] = Field(default_factory=list)
