from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _validate_email(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    if not re.fullmatch(r"[^@]+@[^@]+\.[^@]+", value):
        raise ValueError("Invalid email format")
    return value


def _validate_phone(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    digits = re.sub(r"\D", "", value)
    if not 10 <= len(digits) <= 15:
        raise ValueError("Phone number must be between 10 and 15 digits")
    return value


def _validate_pan(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    if not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", value):
        raise ValueError("Invalid PAN number format")
    return value


def _validate_gst(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    if not re.fullmatch(r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]", value):
        raise ValueError("Invalid GST number format")
    return value


def _validate_pin(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    if not re.fullmatch(r"\d{6}", value):
        raise ValueError("PIN code must be 6 digits")
    return value


class Organization(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda value: value.isoformat()},
        json_schema_extra={
            "example": {
                "name": "Acme Corporation",
                "email": "info@acmecorp.com",
                "phone": "123-456-7890",
                "panNumber": "ABCDE1234F",
                "gstNumber": "12ABCDE1234F1Z5",
                "address": "123 Business St",
                "city": "Business City",
                "state": "Business State",
                "pinCode": "123456",
                "adminName": "John Admin",
                "adminEmail": "admin@acmecorp.com",
                "adminContact": "987-654-3210",
            }
        },
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    name: str
    shortName: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    panNumber: Optional[str] = None
    gstNumber: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pinCode: Optional[str] = None
    adminName: Optional[str] = None
    adminEmail: Optional[str] = None
    adminContact: Optional[str] = None
    billingEnabled: Optional[bool] = False
    is_active: Optional[bool] = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None


class OrganizationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    shortName: Optional[str] = Field(None, max_length=10)
    email: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=15)
    panNumber: Optional[str] = Field(None, max_length=10)
    gstNumber: Optional[str] = Field(None, max_length=15)
    address: Optional[str] = Field(None, max_length=500)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    pinCode: Optional[str] = Field(None, max_length=6)
    adminName: Optional[str] = Field(None, max_length=200)
    adminEmail: Optional[str] = Field(None, max_length=100)
    adminContact: Optional[str] = Field(None, max_length=15)
    billingEnabled: Optional[bool] = False


class OrganizationCreate(OrganizationBase):
    # Subscription configuration at registration time (optional)
    plan_code: Optional[str] = Field(
        None,
        max_length=100,
        description="Plan code to assign on creation. If omitted, 'no_service_override' is used.",
    )
    subscription_status: Optional[str] = Field(
        None,
        description="Initial subscription status (e.g. 'trial', 'active'). Defaults to 'trial' when plan_code is provided.",
    )
    trial_days: Optional[int] = Field(
        None,
        ge=0,
        le=365,
        description="Number of trial days. Only used when subscription_status is 'trial'.",
    )

    @field_validator("name")
    @classmethod
    def _ensure_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Organization name cannot be empty")
        return stripped

    @field_validator("panNumber")
    @classmethod
    def _validate_pan_number(cls, value: Optional[str]) -> Optional[str]:
        return _validate_pan(value)

    @field_validator("gstNumber")
    @classmethod
    def _validate_gst_number(cls, value: Optional[str]) -> Optional[str]:
        return _validate_gst(value)

    @field_validator("email", "adminEmail")
    @classmethod
    def _validate_email_fields(cls, value: Optional[str]) -> Optional[str]:
        return _validate_email(value)

    @field_validator("phone", "adminContact")
    @classmethod
    def _validate_phone_fields(cls, value: Optional[str]) -> Optional[str]:
        return _validate_phone(value)

    @field_validator("pinCode")
    @classmethod
    def _validate_pin_code(cls, value: Optional[str]) -> Optional[str]:
        return _validate_pin(value)


class OrganizationUpdate(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        json_schema_extra={
            "example": {
                "name": "Updated Example Corp",
                "contactEmail": "updated.contact@example.com",
            }
        },
    )

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    shortName: Optional[str] = Field(None, max_length=10)
    email: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=15)
    panNumber: Optional[str] = Field(None, max_length=10)
    gstNumber: Optional[str] = Field(None, max_length=15)
    address: Optional[str] = Field(None, max_length=500)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    pinCode: Optional[str] = Field(None, max_length=6)
    adminName: Optional[str] = Field(None, max_length=200)
    adminEmail: Optional[str] = Field(None, max_length=100)
    adminContact: Optional[str] = Field(None, max_length=15)
    billingEnabled: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def _validate_optional_name(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            stripped = value.strip()
            if not stripped:
                raise ValueError("Organization name cannot be empty")
            return stripped
        return value

    _validate_pan_number = field_validator("panNumber")(_validate_pan)
    _validate_gst_number = field_validator("gstNumber")(_validate_gst)
    _validate_email_fields = field_validator("email", "adminEmail")(_validate_email)
    _validate_phone_fields = field_validator("phone", "adminContact")(_validate_phone)
    _validate_pin_code = field_validator("pinCode")(_validate_pin)


class OrganizationResponse(OrganizationBase):
    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda value: value.isoformat()},
    )

    id: str = Field(alias="_id")
    is_active: bool = True
    # Make timestamps optional to accommodate legacy records that may not have these fields set
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None
    # Optional aggregate counts; may not be present in base collection docs
    user_count: Optional[int] = None
    project_count: Optional[int] = None
    document_count: Optional[int] = None
    projectsCount: Optional[int] = None
    employeesCount: Optional[int] = None
    lettersCount: Optional[int] = None

    @model_validator(mode="after")
    def _sync_count_aliases(self) -> "OrganizationResponse":
        """Mirror snake_case counts into camelCase aliases and vice versa."""
        if self.projectsCount is None and self.project_count is not None:
            self.projectsCount = self.project_count
        elif self.project_count is None and self.projectsCount is not None:
            self.project_count = self.projectsCount

        if self.employeesCount is None and self.user_count is not None:
            self.employeesCount = self.user_count
        elif self.user_count is None and self.employeesCount is not None:
            self.user_count = self.employeesCount

        if self.lettersCount is None and self.document_count is not None:
            self.lettersCount = self.document_count
        elif self.document_count is None and self.lettersCount is not None:
            self.document_count = self.lettersCount

        return self


class OrganizationListResponse(BaseModel):
    organizations: List[OrganizationResponse]
    total: int
    page: int
    limit: int
    has_next: bool = False
    has_prev: bool = False

    @model_validator(mode="after")
    def _compute_pagination(self) -> "OrganizationListResponse":
        self.has_next = (self.page * self.limit) < self.total
        self.has_prev = self.page > 1
        return self


class OrganizationStats(BaseModel):
    id: str
    name: str
    users_count: int
    projects_count: int
    documents_count: int
    contracts_count: int
    created_at: datetime
    last_activity: Optional[datetime] = None


class OrganizationValidation(BaseModel):
    is_valid: bool
    issues: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    validated_at: datetime = Field(default_factory=datetime.utcnow)


class OrganizationSummary(BaseModel):
    total_organizations: int
    active_organizations: int
    inactive_organizations: int
    organizations_with_billing: int
    top_cities: List[Dict[str, str]]
    top_states: List[Dict[str, str]]
