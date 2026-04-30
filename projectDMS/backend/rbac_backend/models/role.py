from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RoleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    permissions: Optional[List[str]] = Field(default_factory=list)
    scope: Optional[str] = Field(default=None)
    organization_id: Optional[str] = None
    project_id: Optional[str] = None

    @field_validator("scope")
    @classmethod
    def _validate_scope_base(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in {"system", "organization", "project"}:
            raise ValueError("scope must be one of: system, organization, project")
        return normalized


class RoleCreate(RoleBase):
    is_system: Optional[bool] = False

    @field_validator("name")
    @classmethod
    def _ensure_valid_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Role name cannot be empty")
        return stripped


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    permissions: Optional[List[str]] = None
    is_system: Optional[bool] = None
    scope: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None

    @field_validator("scope")
    @classmethod
    def _validate_optional_scope(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in {"system", "organization", "project"}:
            raise ValueError("scope must be one of: system, organization, project")
        return normalized

    @field_validator("name")
    @classmethod
    def _ensure_optional_name(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            stripped = value.strip()
            if not stripped:
                raise ValueError("Role name cannot be empty")
            return stripped
        return value


class Role(RoleBase):
    model_config = ConfigDict(json_encoders={datetime: lambda value: value.isoformat()})

    id: str
    is_system: bool = False
    is_active: bool = True
    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in {"system", "organization", "project"}:
            raise ValueError("scope must be one of: system, organization, project")
        return normalized


class RoleResponse(Role):
    permission_count: Optional[int] = None
    user_count: Optional[int] = None


class RoleListResponse(BaseModel):
    roles: List[RoleResponse]
    total: int
    page: int
    limit: int
    has_next: bool = False
    has_prev: bool = False

    @model_validator(mode="after")
    def _compute_pagination(self) -> "RoleListResponse":
        self.has_next = (self.page * self.limit) < self.total
        self.has_prev = self.page > 1
        return self


class RolePermissionAssignment(BaseModel):
    permission_ids: List[str] = Field(..., min_items=0)

    @field_validator("permission_ids")
    @classmethod
    def _dedupe_permissions(cls, value: List[str]) -> List[str]:
        seen = set()
        unique: List[str] = []
        for permission_id in value:
            if permission_id not in seen:
                seen.add(permission_id)
                unique.append(permission_id)
        return unique


class RoleStats(BaseModel):
    id: str
    name: str
    user_count: int
    permission_count: int
    is_system: bool
    is_active: bool


class RoleSummary(BaseModel):
    total_roles: int
    active_roles: int
    system_roles: int
    custom_roles: int
    most_assigned_roles: List[RoleStats]
