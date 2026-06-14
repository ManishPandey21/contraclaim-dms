from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TemplateSection(BaseModel):
    model_config = ConfigDict(
        json_encoders={datetime: lambda value: value.isoformat()},
        json_schema_extra={
            "example": {
                "id": "header",
                "name": "Letter Header",
                "type": "header",
                "enabled": True,
                "content": "<p>Header content</p>",
                "order": 0,
            }
        },
    )

    id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    type: str = Field(..., min_length=1, max_length=50)
    enabled: bool = True
    content: str = ""
    order: int = 0

    @field_validator("id", "name", "type")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Section field cannot be empty")
        return stripped

    @field_validator("order")
    @classmethod
    def _validate_order(cls, value: int) -> int:
        if value < 0:
            raise ValueError("order must be >= 0")
        return value


class TemplateVersion(BaseModel):
    model_config = ConfigDict(
        json_encoders={datetime: lambda value: value.isoformat()},
        json_schema_extra={
            "example": {
                "version": 1,
                "updated_at": "2025-01-01T12:00:00Z",
                "updated_by": "user_123",
                "sections": [],
            }
        },
    )

    version: int = Field(..., ge=1)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    updated_by: Optional[str] = None
    description: Optional[str] = None
    sections: List[TemplateSection] = Field(default_factory=list)


class LetterTemplate(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={datetime: lambda value: value.isoformat()},
        json_schema_extra={
            "example": {
                "name": "Contractor Request Response",
                "code": "LET-JVTI-CPM",
                "category": "Contract Management",
                "description": "Template for responding to contractor requests",
                "status": "active",
                "visibility": "organization",
                "organization_id": "org_123",
                "project_id": None,
                "version": 1,
            }
        },
    )

    id: Optional[str] = Field(alias="_id", default=None)
    name: str = Field(..., min_length=1, max_length=200)
    code: str = Field(..., min_length=1, max_length=100)
    category: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=1000)
    status: str = Field(default="draft")
    visibility: str = Field(default="organization")
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    sections: List[TemplateSection] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)
    versions: List[TemplateVersion] = Field(default_factory=list)
    usage_count: int = Field(default=0, ge=0)
    is_active: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None

    @field_validator("name", "code", "category")
    @classmethod
    def _strip_required_fields(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Field cannot be empty")
        return stripped

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"active", "draft", "archived"}:
            raise ValueError("status must be one of: active, draft, archived")
        return value

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"global", "organization", "project"}:
            raise ValueError("visibility must be one of: global, organization, project")
        return value

    @model_validator(mode="after")
    def _validate_visibility_constraints(self) -> "LetterTemplate":
        if self.visibility == "global":
            if self.organization_id is not None:
                raise ValueError("Global templates cannot have an organization_id")
            if self.project_id is not None:
                raise ValueError("Global templates cannot have a project_id")
        elif self.visibility == "organization":
            if self.organization_id is None:
                raise ValueError("Organization templates must have an organization_id")
            if self.project_id is not None:
                raise ValueError("Organization templates cannot have a project_id")
        elif self.visibility == "project":
            if self.project_id is None:
                raise ValueError("Project templates must have a project_id")
            if self.organization_id is None:
                raise ValueError("Project templates must have an organization_id")
        return self


class LetterTemplateCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Contractor Request Response",
                "code": "LET-JVTI-CPM",
                "category": "Contract Management",
                "description": "Template for responding to contractor requests",
                "status": "draft",
                "organization_id": "org_123",
                "project_id": "proj_456",
                "sections": [],
            }
        }
    )

    name: str = Field(..., min_length=1, max_length=200)
    code: str = Field(..., min_length=1, max_length=100)
    category: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=1000)
    status: Optional[str] = Field(default="draft")
    visibility: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    sections: List[TemplateSection] = Field(default_factory=list)

    @field_validator("name", "code", "category")
    @classmethod
    def _strip_required_fields(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Field cannot be empty")
        return stripped

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in {"active", "draft", "archived"}:
            raise ValueError("status must be one of: active, draft, archived")
        return normalized

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in {"global", "organization", "project"}:
            raise ValueError("visibility must be one of: global, organization, project")
        return normalized


class LetterTemplateUpdate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Updated Template",
                "description": "Updated description",
                "status": "active",
                "sections": [],
            }
        }
    )

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    code: Optional[str] = Field(default=None, min_length=1, max_length=100)
    category: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=1000)
    status: Optional[str] = None
    visibility: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    sections: Optional[List[TemplateSection]] = None

    @field_validator("name", "code", "category")
    @classmethod
    def _strip_optional_fields(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("Field cannot be empty")
        return stripped

    @field_validator("status")
    @classmethod
    def _validate_update_status(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in {"active", "draft", "archived"}:
            raise ValueError("status must be one of: active, draft, archived")
        return normalized

    @field_validator("visibility")
    @classmethod
    def _validate_update_visibility(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in {"global", "organization", "project"}:
            raise ValueError("visibility must be one of: global, organization, project")
        return normalized


class LetterTemplateListResponse(BaseModel):
    templates: List[LetterTemplate]
    total: int
    page: int
    limit: int
    has_next: bool = False
    has_prev: bool = False

    @model_validator(mode="after")
    def _compute_pagination(self) -> "LetterTemplateListResponse":
        self.has_next = (self.page * self.limit) < self.total
        self.has_prev = self.page > 1
        return self


__all__ = [
    "TemplateSection",
    "TemplateVersion",
    "LetterTemplate",
    "LetterTemplateCreate",
    "LetterTemplateUpdate",
    "LetterTemplateListResponse",
]
