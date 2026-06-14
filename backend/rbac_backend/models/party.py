from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .representative import Representative

_EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")


def _validate_email(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    if not _EMAIL_RE.match(value):
        raise ValueError("Invalid email format")
    return value


def _validate_phone(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    digits = re.sub(r"\D", "", value)
    if not 10 <= len(digits) <= 15:
        raise ValueError("Phone number must be between 10 and 15 digits")
    return value


def _validate_pin(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    if not re.fullmatch(r"\d{6}", value):
        raise ValueError("PIN code must be 6 digits")
    return value


class PartyType(str, Enum):
    ORGANIZATION = "Organization"
    INDIVIDUAL = "Individual"


class Party(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda value: value.isoformat()},
        json_schema_extra={
            "example": {
                "name": "Example Corp",
                "type": "Organization",
                "contactEmail": "contact@example.com",
                "contactPhone": "123-456-7890",
                "representatives": [
                    {
                        "name": "John Doe",
                        "email": "john.doe@example.com",
                        "contact_number": "987-654-3210",
                        "designation": "CEO",
                        "is_primary": True,
                    }
                ],
                "projects": ["proj_123", "proj_456"],
                "address": "123 Main St",
                "city": "Anytown",
                "state": "CA",
                "pinCode": "123456",
                "country": "USA",
            }
        },
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    name: str
    type: PartyType
    organization_id: Optional[str] = Field(default=None, alias="organizationId")
    contact_email: Optional[str] = Field(default=None, alias="contactEmail")
    contact_phone: Optional[str] = Field(default=None, alias="contactPhone")
    created_at: datetime = Field(default_factory=datetime.utcnow, alias="createdAt")
    representatives: List[Representative] = Field(default_factory=list)
    projects: List[str] = Field(default_factory=list)
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    pin_code: Optional[str] = Field(default=None, alias="pinCode")
    country: Optional[str] = None
    is_active: Optional[bool] = True
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_ids(cls, data):
        if isinstance(data, dict):
            d = dict(data)
            if isinstance(d.get("_id"), ObjectId):
                d["_id"] = str(d["_id"])
            if isinstance(d.get("organizationId"), ObjectId):
                d["organizationId"] = str(d["organizationId"])
            if isinstance(d.get("projects"), list):
                d["projects"] = [str(item) if isinstance(item, ObjectId) else item for item in d["projects"]]
            return d
        return data


class PartyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    type: PartyType
    contact_email: Optional[str] = Field(None, max_length=255)
    contact_phone: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = Field(None, max_length=500)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    pin_code: Optional[str] = Field(None, max_length=6)
    country: Optional[str] = Field(None, max_length=100)
    organization_id: Optional[str] = None
    projects: Optional[List[str]] = Field(default_factory=list)
    representatives: Optional[List[Representative]] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Party name cannot be empty")
        return stripped

    _validate_email_fields = field_validator("contact_email")(_validate_email)
    _validate_phone_fields = field_validator("contact_phone")(_validate_phone)
    _validate_pin_code = field_validator("pin_code")(_validate_pin)


class PartyUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    type: Optional[PartyType] = None
    contact_email: Optional[str] = Field(None, max_length=255)
    contact_phone: Optional[str] = Field(None, max_length=50)
    address: Optional[str] = Field(None, max_length=500)
    city: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    pin_code: Optional[str] = Field(None, max_length=6, alias="pinCode")
    country: Optional[str] = Field(None, max_length=100)
    organization_id: Optional[str] = Field(None, alias="organizationId")
    representatives: Optional[List[Representative]] = None
    projects: Optional[List[str]] = None

    @field_validator("name")
    @classmethod
    def _validate_name_optional(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            stripped = value.strip()
            if not stripped:
                raise ValueError("Party name cannot be empty")
            return stripped
        return value

    _validate_email_fields = field_validator("contact_email")(_validate_email)
    _validate_phone_fields = field_validator("contact_phone")(_validate_phone)
    _validate_pin_code = field_validator("pin_code")(_validate_pin)


class PartyResponse(Party):
    model_config = ConfigDict(json_encoders={datetime: lambda value: value.isoformat()})

    project_count: Optional[int] = None
    contract_count: Optional[int] = None
    representative_count: Optional[int] = None


class PartyListResponse(BaseModel):
    parties: List[PartyResponse]
    total: int
    page: int
    limit: int
    has_next: bool = False
    has_prev: bool = False

    @model_validator(mode="after")
    def _compute_pagination(self) -> "PartyListResponse":
        self.has_next = (self.page * self.limit) < self.total
        self.has_prev = self.page > 1
        return self
