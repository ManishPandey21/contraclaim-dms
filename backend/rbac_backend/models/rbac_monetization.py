from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

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


class BillingPeriod(str, Enum):
    """Supported billing cadence options."""
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMI_ANNUAL = "semi_annual"
    ANNUAL = "annual"


class AddOnType(str, Enum):
    """Classification of plan add-ons."""
    FEATURE = "feature"       # Unlocks a feature (e.g. API webhooks)
    CAPACITY = "capacity"     # Increases a quota (e.g. +100 OCR pages)
    SUPPORT = "support"       # Support tier upgrade (e.g. priority support)


class SubscriptionChangeType(str, Enum):
    """Type of subscription state transition for history tracking."""
    CREATED = "created"
    UPGRADE = "upgrade"
    DOWNGRADE = "downgrade"
    ADDON_ADD = "addon_add"
    ADDON_REMOVE = "addon_remove"
    PERIOD_CHANGE = "period_change"
    RENEWAL = "renewal"
    TRIAL_START = "trial_start"
    TRIAL_CONVERT = "trial_convert"
    TRIAL_EXPIRE = "trial_expire"
    CANCELLATION = "cancellation"
    REACTIVATION = "reactivation"
    PAUSE = "pause"
    RESUME = "resume"
    ENTITLEMENT_OVERRIDE = "entitlement_override"


# ---------------------------------------------------------------------------
# Expert Allocation Models (unchanged)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Add-On Models
# ---------------------------------------------------------------------------

class AddOnBase(BaseModel):
    """An optional capability or capacity boost purchasable on top of a plan."""
    code: str
    name: str
    description: Optional[str] = None
    add_on_type: AddOnType = AddOnType.FEATURE
    price_minor: int = 0
    billing_cadence: str = "monthly"
    currency: str = "INR"
    features: Dict[str, Any] = Field(default_factory=dict)
    limits: Dict[str, Any] = Field(default_factory=dict)
    compatible_plans: List[str] = Field(default_factory=list)
    is_active: bool = True


class AddOnCreate(AddOnBase):
    pass


class AddOnUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    add_on_type: Optional[AddOnType] = None
    price_minor: Optional[int] = None
    billing_cadence: Optional[str] = None
    currency: Optional[str] = None
    features: Optional[Dict[str, Any]] = None
    limits: Optional[Dict[str, Any]] = None
    compatible_plans: Optional[List[str]] = None
    is_active: Optional[bool] = None


class AddOn(AddOnBase):
    model_config = ConfigDict(json_encoders={datetime: lambda value: value.isoformat()})

    id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


# ---------------------------------------------------------------------------
# Plan Models (enhanced)
# ---------------------------------------------------------------------------

class PlanBase(BaseModel):
    code: str
    name: str
    description: Optional[str] = None
    family: PlanFamily
    tier: int = 0
    billing_cadence: str = "monthly"
    currency: str = "INR"
    base_price_minor: int = 0
    # Per-period pricing: {"monthly": 2500000, "quarterly": 7000000, "annual": 25000000}
    pricing_tiers: Dict[str, int] = Field(default_factory=dict)
    # Per-period discount percentages: {"quarterly": 6.7, "annual": 16.7}
    discount_percentages: Dict[str, float] = Field(default_factory=dict)
    features: Dict[str, Any] = Field(default_factory=dict)
    default_limits: Dict[str, Any] = Field(default_factory=dict)
    # Available add-on codes for this plan
    available_add_ons: List[str] = Field(default_factory=list)
    # Trial configuration: {"enabled": true, "days": 14, "features": {...}}
    trial_config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    display_order: int = 0
    highlight: bool = False
    max_users: Optional[int] = None
    max_storage_gb: Optional[float] = None


class PlanCreate(PlanBase):
    pass


class PlanUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    family: Optional[PlanFamily] = None
    tier: Optional[int] = None
    billing_cadence: Optional[str] = None
    currency: Optional[str] = None
    base_price_minor: Optional[int] = None
    pricing_tiers: Optional[Dict[str, int]] = None
    discount_percentages: Optional[Dict[str, float]] = None
    features: Optional[Dict[str, Any]] = None
    default_limits: Optional[Dict[str, Any]] = None
    available_add_ons: Optional[List[str]] = None
    trial_config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None
    display_order: Optional[int] = None
    highlight: Optional[bool] = None
    max_users: Optional[int] = None
    max_storage_gb: Optional[float] = None


class Plan(PlanBase):
    model_config = ConfigDict(json_encoders={datetime: lambda value: value.isoformat()})

    id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


# ---------------------------------------------------------------------------
# Subscription Models (enhanced)
# ---------------------------------------------------------------------------

class SubscriptionBase(BaseModel):
    organization_id: str
    project_id: Optional[str] = None
    package_id: Optional[str] = None
    plan_code: str
    billing_period: str = "monthly"
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    billing_status: str = "active"
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    trial: bool = False
    trial_ends_at: Optional[datetime] = None
    pilot: bool = False
    auto_renew: bool = True
    active_add_ons: List[str] = Field(default_factory=list)
    entitlement_overrides: Dict[str, Any] = Field(default_factory=dict)
    payment_gateway_subscription_id: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    cancellation_reason: Optional[str] = None


class SubscriptionCreate(SubscriptionBase):
    pass


class SubscriptionUpdate(BaseModel):
    project_id: Optional[str] = None
    package_id: Optional[str] = None
    plan_code: Optional[str] = None
    billing_period: Optional[str] = None
    status: Optional[SubscriptionStatus] = None
    billing_status: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    trial: Optional[bool] = None
    trial_ends_at: Optional[datetime] = None
    pilot: Optional[bool] = None
    auto_renew: Optional[bool] = None
    active_add_ons: Optional[List[str]] = None
    entitlement_overrides: Optional[Dict[str, Any]] = None
    payment_gateway_subscription_id: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    cancellation_reason: Optional[str] = None


class Subscription(SubscriptionBase):
    model_config = ConfigDict(json_encoders={datetime: lambda value: value.isoformat()})

    id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


# ---------------------------------------------------------------------------
# Subscription History
# ---------------------------------------------------------------------------

class SubscriptionHistoryCreate(BaseModel):
    """Records a single state transition in a subscription's lifecycle."""
    subscription_id: str
    organization_id: str
    project_id: Optional[str] = None
    change_type: SubscriptionChangeType
    from_plan_code: Optional[str] = None
    to_plan_code: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    from_billing_period: Optional[str] = None
    to_billing_period: Optional[str] = None
    proration_amount_minor: Optional[int] = None
    add_on_code: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    changed_by: Optional[str] = None


class SubscriptionHistory(SubscriptionHistoryCreate):
    model_config = ConfigDict(json_encoders={datetime: lambda value: value.isoformat()})

    id: str
    changed_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Plan Settings (unchanged)
# ---------------------------------------------------------------------------

class PlanSettingsMode(str, Enum):
    INHERIT = "inherit"
    PLAN = "plan"
    NO_SERVICE = "no_service"


class PlanSettingsScopeUpdate(BaseModel):
    organization_id: str
    project_id: Optional[str] = None
    mode: PlanSettingsMode
    plan_code: Optional[str] = None
    billing_period: Optional[str] = None
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE


# ---------------------------------------------------------------------------
# Usage & Billing (enhanced)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Request / Response payloads for subscription lifecycle endpoints
# ---------------------------------------------------------------------------

class UpgradeDowngradeRequest(BaseModel):
    """Payload for upgrading or downgrading a subscription."""
    new_plan_code: str
    billing_period: Optional[str] = None
    effective_immediately: bool = True


class ChangeBillingPeriodRequest(BaseModel):
    """Payload for switching billing cadence."""
    new_billing_period: str  # monthly, quarterly, annual


class AddOnActionRequest(BaseModel):
    """Payload for adding or removing an add-on."""
    add_on_code: str


class StartTrialRequest(BaseModel):
    """Payload for starting a trial subscription."""
    organization_id: str
    project_id: Optional[str] = None
    plan_code: str
    trial_days: Optional[int] = None  # Override plan's default trial_days


class ConvertTrialRequest(BaseModel):
    """Payload for converting a trial into a paid subscription."""
    billing_period: str = "monthly"
    add_on_codes: List[str] = Field(default_factory=list)


class CancelSubscriptionRequest(BaseModel):
    """Payload for cancelling a subscription."""
    reason: Optional[str] = None
    immediate: bool = False  # If True, cancel now; if False, cancel at period end
