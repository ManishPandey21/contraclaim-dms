from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from bson import ObjectId
from pydantic import BaseModel, Field, ConfigDict, model_validator


class InputRequestStatus(str, Enum):
    """Workflow status for input requests."""

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    FULFILLED = "fulfilled"
    CLOSED = "closed"


class InputRequest(BaseModel):
    """Persisted input request."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda value: value.isoformat()},
    )

    id: Optional[str] = Field(default=None, alias="_id")
    letter_id: str
    requested_by: Optional[str] = None
    requested_from: str
    details: str
    key_points: Optional[str] = None
    due_date: Optional[datetime] = None
    reference_letter_id: Optional[str] = None
    status: InputRequestStatus = Field(default=InputRequestStatus.OPEN)
    responses: List[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_ids(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            for key in ("_id", "letter_id", "requested_by", "reference_letter_id"):
                value = data.get(key)
                if isinstance(value, ObjectId):
                    data[key] = str(value)
            return data
        return data


class InputRequestCreate(BaseModel):
    """Payload for creating an input request."""

    requested_from: str
    details: str
    key_points: Optional[str] = None
    due_date: Optional[datetime] = None
    reference_letter_id: Optional[str] = None


class InputRequestUpdate(BaseModel):
    """Mutable input request fields."""

    requested_from: Optional[str] = None
    details: Optional[str] = None
    key_points: Optional[str] = None
    due_date: Optional[datetime] = None
    status: Optional[InputRequestStatus] = None


class InputRequestResponse(BaseModel):
    """Response payload for fulfilling an input request."""

    message: str


class InputRequestListResponse(BaseModel):
    """Paginated input request response."""

    requests: List[InputRequest]
    total: int
    page: int
    limit: int
    has_next: bool = Field(default=False)
    has_prev: bool = Field(default=False)

    @model_validator(mode="after")
    def _set_flags(self) -> "InputRequestListResponse":
        pages = 0
        if self.limit:
            pages = (self.total + self.limit - 1) // self.limit
        self.has_prev = self.page > 1
        self.has_next = self.page < max(pages, 1)
        return self


class SuggestedKeyPoints(BaseModel):
    """AI-suggested key points response."""

    letter_id: str
    key_points: List[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
