"""Insurance Register models (Contract Controls).

Insurance policies submitted by contractors under a contract: type (from the
master list), policy number, issue/expiry dates, sum insured, the policy file,
and a date-derived status (active / expiring_soon / expired). Tenant-scoped by
organization/project. Mirrors the Bank Guarantee register's shape and lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class InsuranceStatus(str, Enum):
    ACTIVE = "active"
    EXPIRING_SOON = "expiring_soon"
    EXPIRED = "expired"


# Canonical master list seeded per organization. Admins may add/edit/deactivate.
DEFAULT_INSURANCE_TYPES: List[str] = [
    "Professional Indemnity Insurance",
    "Workmen's Compensation Policy",
    "Erection All Risks (EAR) Insurance",
    "Marine Cargo Insurance",
    "Contractors' All Risks (CAR) Insurance with Third-Party Liability",
    "Plant & Machinery / Tools Insurance",
    "Marine Cargo Open Policy",
    "Group Personal Accident Policy",
    "Commercial General Liability (CGL) / Third-Party Liability Insurance",
    "Labour Licence",
]

# Number of days before expiry at which a policy is flagged "expiring soon".
EXPIRING_SOON_DAYS = 30


class InsuranceBase(BaseModel):
    contract_id: Optional[str] = None
    contractor_name: Optional[str] = None
    insurance_type: Optional[str] = None
    insurance_company: Optional[str] = None
    policy_number: Optional[str] = None
    sum_insured: Optional[float] = None
    currency: str = "INR"
    date_of_issue: Optional[datetime] = None
    date_of_expiry: Optional[datetime] = None
    # Primary policy file (document/upload id) plus any additional linked docs.
    document_id: Optional[str] = None
    linked_document_ids: List[str] = Field(default_factory=list)
    remarks: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None


class InsuranceCreate(InsuranceBase):
    project_id: str = Field(..., min_length=1)
    insurance_type: str = Field(..., min_length=1)
    policy_number: str = Field(..., min_length=1)


class InsuranceUpdate(BaseModel):
    contract_id: Optional[str] = None
    contractor_name: Optional[str] = None
    insurance_type: Optional[str] = None
    insurance_company: Optional[str] = None
    policy_number: Optional[str] = None
    sum_insured: Optional[float] = None
    currency: Optional[str] = None
    date_of_issue: Optional[datetime] = None
    date_of_expiry: Optional[datetime] = None
    document_id: Optional[str] = None
    linked_document_ids: Optional[List[str]] = None
    remarks: Optional[str] = None
    contract_id_set: Optional[bool] = None  # reserved


class Insurance(InsuranceBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    # Derived for responses.
    status: Optional[InsuranceStatus] = None
    days_remaining: Optional[int] = None
    next_alert_date: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    created_by_name: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class InsuranceSummary(BaseModel):
    total: int = 0
    active: int = 0
    expiring_soon: int = 0
    expired: int = 0
    missing: int = 0
    total_sum_insured: float = 0.0


# --- Insurance Type master (admin-managed) --------------------------------


class InsuranceTypeBase(BaseModel):
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    is_active: bool = True
    organization_id: Optional[str] = None


class InsuranceTypeCreate(InsuranceTypeBase):
    pass


class InsuranceTypeUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class InsuranceType(InsuranceTypeBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    is_default: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)
