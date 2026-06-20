"""Variation Register models (Contract Controls).

Tracks contract variations (positive/negative/neutral) with submitted/approved
amounts, and the contract-value roll-up: cumulative approved variation, revised
contract value and percentage variation against the original contract value.
Tenant-scoped by organization/project, consistent with the claims module.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class VariationType(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class VariationStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    RECOMMENDED = "recommended"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class VariationBase(BaseModel):
    variation_number: Optional[str] = None
    variation_type: VariationType = VariationType.POSITIVE
    description: Optional[str] = None
    letter_reference: Optional[str] = None
    submitted_amount: Optional[float] = None
    approved_amount: Optional[float] = None
    original_contract_value: Optional[float] = None
    status: VariationStatus = VariationStatus.DRAFT
    approval_date: Optional[datetime] = None
    remarks: Optional[str] = None
    contract_id: Optional[str] = None
    linked_document_ids: List[str] = Field(default_factory=list)
    organization_id: Optional[str] = None
    project_id: Optional[str] = None


class VariationCreate(VariationBase):
    project_id: str = Field(..., min_length=1)


class VariationUpdate(BaseModel):
    variation_number: Optional[str] = None
    variation_type: Optional[VariationType] = None
    description: Optional[str] = None
    letter_reference: Optional[str] = None
    submitted_amount: Optional[float] = None
    approved_amount: Optional[float] = None
    original_contract_value: Optional[float] = None
    status: Optional[VariationStatus] = None
    approval_date: Optional[datetime] = None
    remarks: Optional[str] = None
    contract_id: Optional[str] = None
    linked_document_ids: Optional[List[str]] = None


class Variation(VariationBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    # Derived for responses.
    difference_amount: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class VariationSummary(BaseModel):
    original_contract_value: float = 0.0
    total_submitted_amount: float = 0.0
    total_approved_amount: float = 0.0
    cumulative_approved_variation: float = 0.0
    revised_contract_value: float = 0.0
    percentage_variation: float = 0.0
    pending_variation_count: int = 0
    approved_variation_count: int = 0
    rejected_variation_count: int = 0
