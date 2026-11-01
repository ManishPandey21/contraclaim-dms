from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Tag(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda value: value.isoformat()},
        json_schema_extra={
            "example": {
                "name": "Contract Type",
                "description": "Tags for different types of contracts",
                "color": "#3498db",
                "organization_id": "org_123",
                "project_id": "proj_456",
                "visibility": "organization"
            }
        },
    )

    id: Optional[str] = Field(alias="_id", default=None)
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    visibility: Optional[str] = Field(default="organization")
    is_active: Optional[bool] = Field(default=True)
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be empty")
        return value

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, value: Optional[str]) -> str:
        if value is None:
            return "organization"
        if value not in ["global", "organization", "project"]:
            raise ValueError("visibility must be one of: global, organization, project")
        return value

    @model_validator(mode="after")
    def _validate_visibility_constraints(self) -> "Tag":
        """Validate visibility constraints based on organization_id and project_id."""
        if self.visibility == "global":
            if self.organization_id is not None:
                raise ValueError("Global tags cannot have an organization_id")
            if self.project_id is not None:
                raise ValueError("Global tags cannot have a project_id")
        elif self.visibility == "organization":
            if self.organization_id is None:
                raise ValueError("Organization-level tags must have an organization_id")
            if self.project_id is not None:
                raise ValueError("Organization-level tags cannot have a project_id")
        elif self.visibility == "project":
            if self.project_id is None:
                raise ValueError("Project-level tags must have a project_id")
            if self.organization_id is None:
                raise ValueError("Project-level tags must have an organization_id for consistency")
        return self


class TagCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Contract Type",
                "description": "Tags for different types of contracts",
                "color": "#3498db",
            }
        }
    )

    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    organization_id: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Tag name cannot be empty")
        return value


class TagUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Updated Contract Type",
                "description": "Updated description",
                "color": "#e74c3c",
            }
        }
    )

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    color: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")

    @field_validator("name")
    @classmethod
    def _validate_optional_name(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            stripped = value.strip()
            if not stripped:
                raise ValueError("Tag name cannot be empty")
            return stripped
        return value


class TagResponse(Tag):
    model_config = ConfigDict(json_encoders={datetime: lambda value: value.isoformat()})

    subtag_count: Optional[int] = None
    usage_count: Optional[int] = None
    organization_name: Optional[str] = None
    project_name: Optional[str] = None
    created_by_label: Optional[str] = None


class TagListResponse(BaseModel):
    tags: List[TagResponse]
    total: int
    page: int
    limit: int
    has_next: bool = False
    has_prev: bool = False

    @model_validator(mode="after")
    def _compute_pagination(self) -> "TagListResponse":
        self.has_next = (self.page * self.limit) < self.total
        self.has_prev = self.page > 1
        return self


class Subtag(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda value: value.isoformat()},
        json_schema_extra={
            "example": {
                "name": "Service Agreement",
                "description": "Subtag for service agreements",
                "tag_id": "tag_123",
            }
        },
    )

    id: Optional[str] = Field(alias="_id", default=None)
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    tag_id: str
    is_active: Optional[bool] = Field(default=True)
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("subtag name must not be empty")
        return value


class SubtagCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Service Agreement",
                "description": "Subtag for service agreements",
            }
        }
    )

    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Subtag name cannot be empty")
        return value


class SubtagUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Updated Service Agreement",
                "description": "Updated description",
            }
        }
    )

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _validate_optional_name(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            stripped = value.strip()
            if not stripped:
                raise ValueError("Subtag name cannot be empty")
            return stripped
        return value


class SubtagResponse(Subtag):
    model_config = ConfigDict(json_encoders={datetime: lambda value: value.isoformat()})

    tag_name: Optional[str] = None
    usage_count: Optional[int] = None
    created_by_label: Optional[str] = None


class SubtagListResponse(BaseModel):
    subtags: List[SubtagResponse]
    total: int
    page: int
    limit: int
    has_next: bool = False
    has_prev: bool = False

    @model_validator(mode="after")
    def _compute_pagination(self) -> "SubtagListResponse":
        self.has_next = (self.page * self.limit) < self.total
        self.has_prev = self.page > 1
        return self


class SubtagLookupItem(BaseModel):
    """The fields a display lookup needs: no usage counts, no enrichment."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    name: str
    tag_id: str


class SubtagBatchResponse(BaseModel):
    """Subtags of the requested tags the caller may see.

    A requested tag the caller cannot see is simply absent: the response never
    says whether it exists.
    """

    subtags: List[SubtagLookupItem]
    truncated: bool = False


__all__ = [
    "Tag",
    "TagCreate",
    "TagUpdate",
    "TagResponse",
    "TagListResponse",
    "Subtag",
    "SubtagCreate",
    "SubtagUpdate",
    "SubtagResponse",
    "SubtagListResponse",
    "SubtagLookupItem",
    "SubtagBatchResponse",
]
