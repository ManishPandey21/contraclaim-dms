"""Claim register models (Phase 4 / Module 1).

A claim is the spine of the claims-market layer: EOT / variation / payment /
loss-and-expense entries linked to project correspondence, with value, dates,
contract clauses, and a lifecycle status. Tenant-scoped by organization/project.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ClaimType(str, Enum):
    EOT = "eot"
    VARIATION = "variation"
    PAYMENT_IPC = "payment_ipc"
    LOSS_EXPENSE = "loss_expense"
    ACCELERATION = "acceleration"
    DEFECT = "defect"
    OTHER = "other"


class ClaimStatus(str, Enum):
    DRAFT = "draft"
    NOTIFIED = "notified"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    AGREED = "agreed"
    REJECTED = "rejected"
    DISPUTED = "disputed"
    CLOSED = "closed"


class ClaimBase(BaseModel):
    claim_ref: Optional[str] = None
    type: ClaimType = ClaimType.OTHER
    title: str
    description: Optional[str] = None
    status: ClaimStatus = ClaimStatus.DRAFT
    event_date: Optional[datetime] = None
    notice_date: Optional[datetime] = None
    submission_date: Optional[datetime] = None
    response_due_date: Optional[datetime] = None
    amount_claimed: Optional[float] = None
    amount_agreed: Optional[float] = None
    currency: str = "INR"
    eot_days_claimed: Optional[int] = None
    eot_days_granted: Optional[int] = None
    responsible_party_id: Optional[str] = None
    contract_clauses: List[str] = Field(default_factory=list)
    linked_document_ids: List[str] = Field(default_factory=list)
    linked_letter_ids: List[str] = Field(default_factory=list)
    organization_id: Optional[str] = None
    project_id: Optional[str] = None


class ClaimCreate(ClaimBase):
    title: str = Field(..., min_length=1, max_length=300)


class ClaimUpdate(BaseModel):
    claim_ref: Optional[str] = None
    type: Optional[ClaimType] = None
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[ClaimStatus] = None
    event_date: Optional[datetime] = None
    notice_date: Optional[datetime] = None
    submission_date: Optional[datetime] = None
    response_due_date: Optional[datetime] = None
    amount_claimed: Optional[float] = None
    amount_agreed: Optional[float] = None
    currency: Optional[str] = None
    eot_days_claimed: Optional[int] = None
    eot_days_granted: Optional[int] = None
    responsible_party_id: Optional[str] = None
    contract_clauses: Optional[List[str]] = None
    linked_document_ids: Optional[List[str]] = None
    linked_letter_ids: Optional[List[str]] = None


class ClaimStatusUpdate(BaseModel):
    status: ClaimStatus


class Claim(ClaimBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)
