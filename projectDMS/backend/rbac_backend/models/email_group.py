from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from bson import ObjectId
from pydantic import BaseModel, EmailStr, Field, model_validator


class EmailGroup(BaseModel):
    """Persisted email distribution group."""

    id: Optional[str] = Field(default=None, alias="_id")
    name: str
    description: Optional[str] = Field(default=None, max_length=1000)
    emails: List[EmailStr] = Field(default_factory=list)
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    category: Optional[str] = None
    is_active: bool = Field(default=True)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="before")
    @classmethod
    def _normalize_ids(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            for key in ("_id", "organization_id", "project_id", "created_by", "updated_by"):
                value = data.get(key)
                if isinstance(value, ObjectId):
                    data[key] = str(value)
            return data
        return data

    class Config:
        populate_by_name = True
        json_encoders = {datetime: lambda value: value.isoformat()}


class EmailGroupCreate(BaseModel):
    """Payload for creating an email group."""

    name: str
    description: Optional[str] = Field(default=None, max_length=1000)
    emails: List[EmailStr] = Field(default_factory=list)
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    category: Optional[str] = None


class EmailGroupUpdate(BaseModel):
    """Mutable email group fields."""

    name: Optional[str] = None
    description: Optional[str] = Field(default=None, max_length=1000)
    emails: Optional[List[EmailStr]] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    category: Optional[str] = None
    is_active: Optional[bool] = None


class EmailGroupListResponse(BaseModel):
    """Paginated email group response."""

    groups: List[EmailGroup]
    total: int
    page: int
    limit: int
    has_next: bool = Field(default=False)
    has_prev: bool = Field(default=False)

    @model_validator(mode="after")
    def _set_flags(self) -> "EmailGroupListResponse":
        pages = 0
        if self.limit:
            pages = (self.total + self.limit - 1) // self.limit
        self.has_prev = self.page > 1
        self.has_next = self.page < max(pages, 1)
        return self
