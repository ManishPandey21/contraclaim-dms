"""Bank Guarantee Register models (Contract Controls).

Full BG lifecycle: type, amount, the contractual "required up to" date (which the
EOT/completion data can move), expiry, extension status, an immutable extension
history, and expiry alerts. Tenant-scoped by organization/project.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class BGType(str, Enum):
    PERFORMANCE = "performance"
    MOBILISATION_ADVANCE = "mobilisation_advance"
    PLANT_ADVANCE = "plant_advance"
    RETENTION = "retention"
    ADDITIONAL_PERFORMANCE = "additional_performance"
    SECURITY_DEPOSIT = "security_deposit"
    OTHER = "other"


class BGStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    VALID = "valid"
    EXTENSION_REQUIRED = "extension_required"
    EXTENDED = "extended"
    EXPIRED = "expired"
    RELEASED = "released"
    ENCASHMENT_UNDER_PROCESS = "encashment_under_process"
    ENCASHED = "encashed"


class BankGuaranteeBase(BaseModel):
    bg_type: BGType = BGType.PERFORMANCE
    bg_number: Optional[str] = None
    issuing_bank: Optional[str] = None
    branch: Optional[str] = None
    bg_amount: Optional[float] = None
    currency: str = "INR"
    submission_date: Optional[datetime] = None
    contractual_required_up_to: Optional[datetime] = None
    bg_expiry_date: Optional[datetime] = None
    claim_expiry_date: Optional[datetime] = None
    bg_status: BGStatus = BGStatus.VALID
    last_extension_date: Optional[datetime] = None
    remarks: Optional[str] = None
    contract_id: Optional[str] = None
    linked_document_ids: List[str] = Field(default_factory=list)
    organization_id: Optional[str] = None
    project_id: Optional[str] = None


class BankGuaranteeCreate(BankGuaranteeBase):
    project_id: str = Field(..., min_length=1)


class BankGuaranteeUpdate(BaseModel):
    bg_type: Optional[BGType] = None
    bg_number: Optional[str] = None
    issuing_bank: Optional[str] = None
    branch: Optional[str] = None
    bg_amount: Optional[float] = None
    currency: Optional[str] = None
    submission_date: Optional[datetime] = None
    contractual_required_up_to: Optional[datetime] = None
    bg_expiry_date: Optional[datetime] = None
    claim_expiry_date: Optional[datetime] = None
    bg_status: Optional[BGStatus] = None
    remarks: Optional[str] = None
    contract_id: Optional[str] = None
    linked_document_ids: Optional[List[str]] = None


class BGExtendRequest(BaseModel):
    revised_expiry_date: datetime
    revised_claim_expiry_date: Optional[datetime] = None
    revised_required_up_to: Optional[datetime] = None
    extension_letter_reference: Optional[str] = None
    extension_date: Optional[datetime] = None
    remarks: Optional[str] = None
    linked_document_ids: Optional[List[str]] = None


class BGReleaseRequest(BaseModel):
    release_letter_reference: Optional[str] = None
    release_date: Optional[datetime] = None
    remarks: Optional[str] = None


class BankGuarantee(BankGuaranteeBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    current_revision: int = 0
    # Derived for responses.
    extension_required: Optional[bool] = None
    days_to_expiry: Optional[int] = None
    next_alert_date: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class BGExtensionHistory(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    bg_id: str
    project_id: Optional[str] = None
    organization_id: Optional[str] = None
    revision_number: int
    previous_expiry_date: Optional[datetime] = None
    new_expiry_date: Optional[datetime] = None
    previous_required_up_to: Optional[datetime] = None
    new_required_up_to: Optional[datetime] = None
    claim_expiry_date: Optional[datetime] = None
    extension_letter_reference: Optional[str] = None
    extension_date: Optional[datetime] = None
    remarks: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(populate_by_name=True)


class BGSummary(BaseModel):
    total: int = 0
    total_bg_amount: float = 0.0
    valid: int = 0
    extension_required: int = 0
    expiring_45: int = 0
    expiring_30: int = 0
    expired: int = 0
    released: int = 0
