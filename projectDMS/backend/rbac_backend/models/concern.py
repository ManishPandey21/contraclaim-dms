from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from bson import ObjectId
from pydantic import BaseModel, EmailStr, Field, model_validator


class ConcernStatus(str, Enum):
    """Lifecycle state for a concern."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class ConcernPriority(str, Enum):
    """Priority levels for concerns."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Concern(BaseModel):
    """Persisted concern record."""

    id: Optional[str] = Field(default=None, alias="_id")
    name: Optional[str] = Field(default=None, max_length=200)
    email: Optional[EmailStr] = None
    description: Optional[str] = Field(default=None, max_length=2000)
    party_id: Optional[str] = Field(default=None, alias="partyId")
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    status: ConcernStatus = Field(default=ConcernStatus.OPEN)
    priority: ConcernPriority = Field(default=ConcernPriority.MEDIUM)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, alias="createdAt")
    updated_at: datetime = Field(default_factory=datetime.utcnow, alias="updatedAt")
    created_by: Optional[str] = None
    updated_by: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_ids(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            for key in ("_id", "party_id", "partyId", "organization_id", "project_id", "created_by", "updated_by"):
                value = data.get(key)
                if isinstance(value, ObjectId):
                    data[key] = str(value)
            return data
        return data

    class Config:
        populate_by_name = True
        json_encoders = {datetime: lambda value: value.isoformat()}


class ConcernCreate(BaseModel):
    """Payload for creating a concern."""

    name: Optional[str] = Field(default=None, max_length=200)
    email: Optional[EmailStr] = None
    description: Optional[str] = Field(default=None, max_length=2000)
    party_id: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    status: Optional[ConcernStatus] = None
    priority: Optional[ConcernPriority] = None


class ConcernUpdate(BaseModel):
    """Mutable concern fields."""

    name: Optional[str] = Field(default=None, max_length=200)
    email: Optional[EmailStr] = None
    description: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[ConcernStatus] = None
    priority: Optional[ConcernPriority] = None
    is_active: Optional[bool] = None


class ConcernListResponse(BaseModel):
    """Paginated concern response."""

    concerns: List[Concern]
    total: int
    page: int
    limit: int
    has_next: bool = Field(default=False)
    has_prev: bool = Field(default=False)

    @model_validator(mode="after")
    def _set_flags(self) -> "ConcernListResponse":
        pages = 0
        if self.limit:
            pages = (self.total + self.limit - 1) // self.limit
        self.has_prev = self.page > 1
        self.has_next = self.page < max(pages, 1)
        return self
