from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


SmtpEncryption = Literal["none", "starttls", "ssl_tls"]
SmtpScope = Literal["organization", "project"]


def normalize_encryption(value: str | None) -> str:
    raw = str(value or "starttls").strip().lower().replace("-", "_").replace("/", "_")
    aliases = {
        "": "starttls",
        "none": "none",
        "no": "none",
        "false": "none",
        "tls": "starttls",
        "starttls": "starttls",
        "start_tls": "starttls",
        "ssl": "ssl_tls",
        "ssl_tls": "ssl_tls",
        "ssltls": "ssl_tls",
    }
    normalized = aliases.get(raw)
    if not normalized:
        raise ValueError("Encryption type must be one of: none, starttls, ssl_tls")
    return normalized


class SmtpSettingsBase(BaseModel):
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)
    username: str = Field(..., min_length=1, max_length=255)
    sender_email: EmailStr
    sender_name: Optional[str] = Field(default=None, max_length=255)
    encryption: SmtpEncryption = "starttls"
    is_active: bool = True

    @field_validator("host", "username", "sender_name", mode="before")
    @classmethod
    def _strip_text(cls, value):
        if value is None:
            return value
        stripped = str(value).strip()
        return stripped or None

    @field_validator("host", "username")
    @classmethod
    def _required_text(cls, value: str | None) -> str:
        if not value:
            raise ValueError("SMTP host and username are required")
        return value

    @field_validator("encryption", mode="before")
    @classmethod
    def _normalize_encryption(cls, value):
        return normalize_encryption(value)


class SmtpSettingsUpsert(SmtpSettingsBase):
    password: str = Field(..., min_length=1, max_length=4096)


class SmtpSettingsUpdate(BaseModel):
    host: Optional[str] = Field(default=None, min_length=1, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    username: Optional[str] = Field(default=None, min_length=1, max_length=255)
    password: Optional[str] = Field(default=None, min_length=1, max_length=4096)
    sender_email: Optional[EmailStr] = None
    sender_name: Optional[str] = Field(default=None, max_length=255)
    encryption: Optional[SmtpEncryption] = None
    is_active: Optional[bool] = None

    @field_validator("host", "username", "sender_name", mode="before")
    @classmethod
    def _strip_text(cls, value):
        if value is None:
            return value
        stripped = str(value).strip()
        return stripped or None

    @field_validator("encryption", mode="before")
    @classmethod
    def _normalize_encryption(cls, value):
        if value is None:
            return value
        return normalize_encryption(value)

    @model_validator(mode="after")
    def _ensure_payload(self) -> "SmtpSettingsUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one SMTP setting field is required")
        return self


class SmtpSettingsResponse(SmtpSettingsBase):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    scope_type: SmtpScope
    organization_id: str
    project_id: Optional[str] = None
    password_configured: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class SmtpSettingsTestResponse(BaseModel):
    ok: bool
    source: Optional[str] = None
    message: str
