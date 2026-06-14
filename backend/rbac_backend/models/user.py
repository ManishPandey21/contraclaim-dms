from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class PyObjectId(ObjectId):
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler):
        from pydantic_core import core_schema

        return core_schema.str_schema()


class Preferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emailNotifications: Optional[bool] = True
    sharingAlerts: Optional[bool] = True
    theme: Optional[str] = "light"
    language: Optional[str] = "en"


def _validate_password_strength(value: str) -> str:
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not re.search(r"[A-Z]", value):
        raise ValueError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", value):
        raise ValueError("Password must contain at least one lowercase letter")
    if not re.search(r"\d", value):
        raise ValueError("Password must contain at least one digit")
    return value


class UserBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    roles: List[str] = Field(default_factory=list)
    organization_id: Optional[str] = None
    organizations: List[str] = Field(default_factory=list)
    projects: List[str] = Field(default_factory=list)
    permissions: Optional[List[str]] = Field(default_factory=list)
    account_type: str = Field(default="client_user")
    preferences: Preferences = Field(default_factory=Preferences)


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        return _validate_password_strength(value)


class User(UserBase):
    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={
            ObjectId: str,
            datetime: lambda value: value.isoformat(),
        },
    )

    id: PyObjectId = Field(default_factory=ObjectId, alias="_id")
    hashed_password: str = Field(..., exclude=True)
    is_active: bool = True
    is_verified: bool = False
    disabled: Optional[bool] = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_login: Optional[datetime] = None
    login_count: int = 0
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    organization_name: Optional[str] = None
    project_names: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_role_based_fields(self) -> "User":
        roles = self.roles or []

        if "superadmin" in roles:
            if self.organization_id is not None:
                raise ValueError("Super Admin cannot have an Organization ID")
            if self.projects:
                raise ValueError("Super Admin cannot have Projects")
            if self.organizations:
                raise ValueError("Super Admin cannot have Organizations")
        elif "superuser" in roles:
            if not self.organizations:
                raise ValueError("Super User must have at least one assigned organization")
            if self.organization_id and str(self.organization_id) not in {str(o) for o in self.organizations}:
                raise ValueError("organization_id must be one of the assigned organizations for Super User")
        elif "orgadmin" in roles or "orguser" in roles:
            if not self.organization_id:
                raise ValueError("Organization Admin or user must have an Organization ID")
        elif "projectadmin" in roles or "projectuser" in roles:
            if not self.organization_id:
                raise ValueError("Project Admin or user must have an Organization ID")
            if not self.projects:
                raise ValueError("Project Admin or user must have Projects")
        return self


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class UserResponse(BaseModel):
    model_config = ConfigDict(json_encoders={datetime: lambda value: value.isoformat()})

    id: str
    username: str
    email: EmailStr
    first_name: str
    last_name: str
    roles: List[str]
    organization_id: Optional[str]
    organizations: List[str]
    projects: List[str]
    permissions: List[str]
    account_type: str = "client_user"
    is_active: bool
    is_verified: bool
    preferences: Preferences
    created_at: datetime
    last_login: Optional[datetime]
    organization_name: Optional[str] = None
    project_names: List[str] = Field(default_factory=list)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class UserUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    hashed_password: Optional[str] = None
    first_name: Optional[str] = Field(None, min_length=1, max_length=50)
    last_name: Optional[str] = Field(None, min_length=1, max_length=50)
    roles: Optional[List[str]] = None
    permissions: Optional[List[str]] = None
    account_type: Optional[str] = None
    disabled: Optional[bool] = None
    organization_id: Optional[str] = None
    organizations: Optional[List[str]] = None
    projects: Optional[List[str]] = None
    is_active: Optional[bool] = None
    preferences: Optional[Preferences] = None


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        return _validate_password_strength(value)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordReset(BaseModel):
    reset_token: str
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        return _validate_password_strength(value)
