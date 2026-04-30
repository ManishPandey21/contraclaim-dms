from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import Enum
from typing import List, Optional

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


_PHONE_RE = re.compile(r"\D")


def _validate_phone(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    digits = _PHONE_RE.sub("", value)
    if not 10 <= len(digits) <= 15:
        raise ValueError("Phone number must be between 10 and 15 digits")
    return value


class RepresentativeLevel(str, Enum):
    ORGANIZATION = "organization"
    PROJECT = "project"
    PARTY = "party"


class Representative(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda value: value.isoformat()},
        json_schema_extra={
            "example": {
                "name": "John Doe",
                "email": "john.doe@example.com",
                "contact_number": "987-654-3210",
                "designation": "CEO",
                "is_primary": True,
                "level": "organization",
                "use_head_office": False,
                "organization_id": "org_123",
                "project_id": None,
            }
        },
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    party_id: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    name: str
    email: EmailStr
    contact_number: Optional[str] = None
    designation: Optional[str] = None
    is_primary: bool = False
    level: RepresentativeLevel = RepresentativeLevel.ORGANIZATION
    use_head_office: bool = False
    is_active: Optional[bool] = True
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_ids(cls, data):
        if isinstance(data, dict):
            result = dict(data)
            for key in ("_id", "party_id", "organization_id", "project_id"):
                value = result.get(key)
                if isinstance(value, ObjectId):
                    result[key] = str(value)
            return result
        return data

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Representative name cannot be empty")
        return stripped

    _validate_contact = field_validator("contact_number")(_validate_phone)


class RepresentativeCreate(BaseModel):
    party_id: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    name: str
    email: EmailStr
    contact_number: Optional[str] = None
    designation: Optional[str] = None
    is_primary: bool = False
    level: RepresentativeLevel = RepresentativeLevel.ORGANIZATION
    use_head_office: bool = False

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Representative name cannot be empty")
        return stripped

    _validate_contact = field_validator("contact_number")(_validate_phone)

    model_config = ConfigDict(populate_by_name=True)


class RepresentativeUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    contact_number: Optional[str] = None
    designation: Optional[str] = None
    is_primary: Optional[bool] = None
    level: Optional[RepresentativeLevel] = None
    use_head_office: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def _strip_optional_name(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            stripped = value.strip()
            if not stripped:
                raise ValueError("Representative name cannot be empty")
            return stripped
        return value

    _validate_contact = field_validator("contact_number")(_validate_phone)

    model_config = ConfigDict(populate_by_name=True)


class RepresentativeResponse(Representative):
    party_name: Optional[str] = None
    organization_name: Optional[str] = None
    project_name: Optional[str] = None


class RepresentativeListResponse(BaseModel):
    representatives: List[RepresentativeResponse]
    total: int
    page: int
    limit: int
    has_next: bool = False
    has_prev: bool = False

    @model_validator(mode="after")
    def _compute_pagination(self) -> "RepresentativeListResponse":
        self.has_next = (self.page * self.limit) < self.total
        self.has_prev = self.page > 1
        return self
