from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _validate_email(value: str) -> str:
    value = value.strip()
    if not _EMAIL_REGEX.match(value):
        raise ValueError("Invalid email format")
    return value.lower()


def _validate_password_strength(value: str) -> str:
    value = value.strip()
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not re.search(r"[A-Z]", value):
        raise ValueError("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", value):
        raise ValueError("Password must contain at least one lowercase letter")
    if not re.search(r"\d", value):
        raise ValueError("Password must contain at least one digit")
    return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"email": "user@example.com", "password": "SecurePassword123!"}
        }
    )

    email: str = Field(..., description="User email address")
    password: str = Field(..., min_length=8, max_length=200, description="User password")

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return _validate_email(value)

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Password must be at least 8 characters long")
        return value


class UserInfo(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "user_123",
                "username": "john_doe",
                "email": "john@example.com",
                "roles": ["user", "orguser"],
                "organization_id": "org_456",
                "projects": ["proj_789"],
                "disabled": False,
            }
        }
    )

    id: str
    username: str
    email: str
    roles: List[str] = Field(default_factory=list)
    organization_id: Optional[str] = None
    projects: List[str] = Field(default_factory=list)
    disabled: bool = False


class LoginResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "token",
                "token_type": "bearer",
                "expires_in": 3600,
                "user": {
                    "id": "user_123",
                    "username": "john_doe",
                    "email": "john@example.com",
                    "roles": ["user", "orguser"],
                    "organization_id": "org_456",
                    "projects": ["proj_789"],
                    "disabled": False,
                },
            }
        }
    )

    access_token: str
    token_type: str = Field(default="bearer")
    expires_in: int
    user: UserInfo


class TokenResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"access_token": "token", "token_type": "bearer", "expires_in": 3600}
        }
    )

    access_token: str
    token_type: str = Field(default="bearer")
    expires_in: int


class RefreshTokenRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"refresh_token": "refresh_token_value"}}
    )

    refresh_token: str

    @field_validator("refresh_token")
    @classmethod
    def _strip_refresh_token(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Refresh token cannot be empty")
        return value


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "current_password": "OldPassword123!",
                "new_password": "NewSecurePassword456!",
                "confirm_password": "NewSecurePassword456!",
            }
        }
    )

    current_password: str
    new_password: str = Field(..., min_length=8, max_length=200)
    confirm_password: str

    @field_validator("new_password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        return _validate_password_strength(value)

    @model_validator(mode="after")
    def _check_confirmation(self) -> "PasswordChangeRequest":
        if self.confirm_password != self.new_password:
            raise ValueError("Passwords do not match")
        return self


class ForgotPasswordRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"email": "user@example.com"}}
    )

    email: str

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return _validate_email(value)


class ResetPasswordRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "token": "reset_token_123456",
                "new_password": "NewSecurePassword456!",
                "confirm_password": "NewSecurePassword456!",
            }
        }
    )

    token: str
    new_password: str = Field(..., min_length=8, max_length=200)
    confirm_password: str

    @field_validator("new_password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        return _validate_password_strength(value)

    @model_validator(mode="after")
    def _check_confirmation(self) -> "ResetPasswordRequest":
        if self.confirm_password != self.new_password:
            raise ValueError("Passwords do not match")
        return self


class SessionInfo(BaseModel):
    model_config = ConfigDict(
        json_encoders={datetime: lambda value: value.isoformat()},
        json_schema_extra={
            "example": {
                "session_id": "session_123",
                "user_id": "user_456",
                "created_at": "2024-01-01T12:00:00Z",
                "expires_at": "2024-01-01T13:00:00Z",
                "ip_address": "192.168.1.100",
                "user_agent": "Mozilla/5.0...",
                "is_active": True,
            }
        },
    )

    session_id: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    ip_address: str
    user_agent: str
    is_active: bool = True


class AuthResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"message": "Operation completed", "success": True}}
    )

    message: str
    success: bool = True


__all__ = [
    "LoginRequest",
    "UserInfo",
    "LoginResponse",
    "TokenResponse",
    "RefreshTokenRequest",
    "PasswordChangeRequest",
    "ForgotPasswordRequest",
    "ResetPasswordRequest",
    "SessionInfo",
    "AuthResponse",
]
