from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class AccountType(str, Enum):
    CLIENT_USER = "client_user"
    CONTRACLAIM_STAFF = "contraclaim_staff"
    SYSTEM_SERVICE = "system_service"


class AssignmentRole(str, Enum):
    DRAFTER = "drafter"
    REVIEWER = "reviewer"
    SENIOR_REVIEWER = "senior_reviewer"
    DRAFTING_MANAGER = "drafting_manager"


class AssignmentStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    EXPIRED = "expired"
    REVOKED = "revoked"


class SubscriptionStatus(str, Enum):
    TRIAL = "trial"
    PILOT = "pilot"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    OFFBOARDING = "offboarding"
    ARCHIVE = "archive"


class PlanFamily(str, Enum):
    DMS_SAAS = "dms_saas"
    DRAFTING_BUNDLE = "drafting_bundle"
    ARCHIVE = "archive"
    OFFBOARDING = "offboarding"
    NO_SERVICE = "no_service"


class ExpertAllocationBase(BaseModel):
    expert_user_id: str
    organization_id: str
    project_id: str
    package_id: Optional[str] = None
    drafting_request_id: Optional[str] = None
    letter_id: Optional[str] = None
    assignment_role: AssignmentRole
    permissions_granted: List[str] = Field(default_factory=list)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: AssignmentStatus = AssignmentStatus.ACTIVE
    allocation_reason: Optional[str] = None
    work_order_reference: Optional[str] = None


class ExpertAllocationCreate(ExpertAllocationBase):
    pass


class ExpertAllocationUpdate(BaseModel):
    package_id: Optional[str] = None
    drafting_request_id: Optional[str] = None
    letter_id: Optional[str] = None
    assignment_role: Optional[AssignmentRole] = None
    permissions_granted: Optional[List[str]] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    status: Optional[AssignmentStatus] = None
    allocation_reason: Optional[str] = None
    work_order_reference: Optional[str] = None


class ExpertAllocation(ExpertAllocationBase):
    model_config = ConfigDict(json_encoders={datetime: lambda value: value.isoformat()})

    id: str
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    audit_correlation_id: Optional[str] = None


class PlanBase(BaseModel):
    code: str
    name: str
    family: PlanFamily
    billing_cadence: str = "monthly"
    currency: str = "INR"
    base_price_minor: int = 0
    features: Dict[str, Any] = Field(default_factory=dict)
    default_limits: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class PlanCreate(PlanBase):
    pass


class PlanUpdate(BaseModel):
    name: Optional[str] = None
    family: Optional[PlanFamily] = None
    billing_cadence: Optional[str] = None
    currency: Optional[str] = None
    base_price_minor: Optional[int] = None
    features: Optional[Dict[str, Any]] = None
    default_limits: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class Plan(PlanBase):
    model_config = ConfigDict(json_encoders={datetime: lambda value: value.isoformat()})

    id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class SubscriptionBase(BaseModel):
    organization_id: str
    project_id: Optional[str] = None
    package_id: Optional[str] = None
    plan_code: str
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    billing_status: str = "active"
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    trial: bool = False
    pilot: bool = False
    entitlement_overrides: Dict[str, Any] = Field(default_factory=dict)


class SubscriptionCreate(SubscriptionBase):
    pass


class SubscriptionUpdate(BaseModel):
    project_id: Optional[str] = None
    package_id: Optional[str] = None
    plan_code: Optional[str] = None
    status: Optional[SubscriptionStatus] = None
    billing_status: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    trial: Optional[bool] = None
    pilot: Optional[bool] = None
    entitlement_overrides: Optional[Dict[str, Any]] = None


class PlanSettingsMode(str, Enum):
    INHERIT = "inherit"
    PLAN = "plan"
    NO_SERVICE = "no_service"


class PlanSettingsScopeUpdate(BaseModel):
    organization_id: str
    project_id: Optional[str] = None
    mode: PlanSettingsMode
    plan_code: Optional[str] = None
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE


class Subscription(SubscriptionBase):
    model_config = ConfigDict(json_encoders={datetime: lambda value: value.isoformat()})

    id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class UsageEventCreate(BaseModel):
    organization_id: str
    project_id: Optional[str] = None
    package_id: Optional[str] = None
    event_type: str
    quantity: int = 1
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BillingRecordCreate(BaseModel):
    organization_id: str
    project_id: Optional[str] = None
    subscription_id: Optional[str] = None
    line_items: List[Dict[str, Any]] = Field(default_factory=list)
    currency: str = "INR"
    status: str = "draft"
    metadata: Dict[str, Any] = Field(default_factory=dict)
