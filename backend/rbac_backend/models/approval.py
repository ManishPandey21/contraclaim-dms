"""Generalised approval workflow models (Phase 4 / Module 3).

A reusable review/approval state machine bound to any ``(resource_type,
resource_id)`` — generalising the letter draft-approval pattern so claims (and
future approvable entities) get the same lifecycle:

    draft → assigned → in_review → approved
                          ↘ returned ↗ (re-submittable)

Tenant-scoped by organization/project. The drafter (the resource owner) can
move the work forward but can never approve their own — that gate lives in the
service.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ApprovalState(str, Enum):
    DRAFT = "draft"
    ASSIGNED = "assigned"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    RETURNED = "returned"


class ApprovalEvent(BaseModel):
    action: str  # assigned | submitted | approved | returned
    actor_id: Optional[str] = None
    at: datetime = Field(default_factory=datetime.utcnow)
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    comment: Optional[str] = None


class ApprovalRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    resource_type: str
    resource_id: str
    organization_id: Optional[str] = None
    project_id: Optional[str] = None

    state: ApprovalState = ApprovalState.DRAFT
    drafter_id: Optional[str] = None

    reviewer_id: Optional[str] = None
    assigned_by: Optional[str] = None
    assigned_at: Optional[datetime] = None
    due_at: Optional[datetime] = None

    submitted_by: Optional[str] = None
    submitted_at: Optional[datetime] = None

    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision_comment: Optional[str] = None

    history: List[ApprovalEvent] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(populate_by_name=True)


# --- request bodies -------------------------------------------------------


class AssignBody(BaseModel):
    reviewer_id: str = Field(..., min_length=1)
    due_at: Optional[datetime] = None
    note: Optional[str] = None


class DecisionBody(BaseModel):
    comment: Optional[str] = None
