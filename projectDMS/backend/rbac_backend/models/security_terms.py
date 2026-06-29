from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SecurityTermsVersionCreate(BaseModel):
    version: str = Field(..., min_length=1, max_length=80)
    title: str = Field(default="Security, Privacy & Anti-Piracy Terms", max_length=200)
    body: str = Field(..., min_length=20, max_length=200000)
    effective_date: datetime
    is_active: bool = True


class SecurityTermsVersion(SecurityTermsVersionCreate):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    terms_hash: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    activated_at: Optional[datetime] = None
    activated_by: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class SecurityTermsAcceptance(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    user_id: str
    org_id: Optional[str] = None
    terms_version: str
    accepted_at: datetime
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    terms_hash: str
    acceptance_method: str = "checkbox_accept_continue"

    model_config = ConfigDict(populate_by_name=True)


class SecurityTermsStatus(BaseModel):
    requires_acceptance: bool
    active_version: SecurityTermsVersion
    accepted_acceptance: Optional[SecurityTermsAcceptance] = None


class SecurityTermsAcceptRequest(BaseModel):
    accepted: bool
    acceptance_method: str = Field(default="checkbox_accept_continue", max_length=80)


class SecurityTermsAcceptancesResponse(BaseModel):
    acceptances: List[SecurityTermsAcceptance] = Field(default_factory=list)
