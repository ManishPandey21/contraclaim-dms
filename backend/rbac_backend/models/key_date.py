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


class KeyDateMilestone(KeyDateMilestoneBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    eot_status: Optional[str] = None  # latest EOT lifecycle state
    current_revision: int = 0
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
