"""Reviewer role assignments (plan 4B-07, D-06, AQ-2).

A reviewer role (for example ``senior_legal_approver``) is an identity, not a
permission bundle. A person holds one only through a record here, at one scope:
the platform, one organisation or one project. The record says who granted it,
why, and until when, and it is revoked, never deleted.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..core.permissions import (
    REVIEWER_ROLE_KEYS,
    REVIEWER_SCOPE_ORGANIZATION,
    REVIEWER_SCOPE_PLATFORM,
    REVIEWER_SCOPE_PROJECT,
    SKILL_REVIEWER_ROLE_KEYS,
)

REVIEWER_ROLE_ASSIGNMENTS = "reviewer_role_assignments"

ReviewerScopeType = Literal["platform", "organization", "project"]

#: Organisation and project assignments always expire. This is the default and
#: the longest period a single grant may run.
DEFAULT_EXPIRY_DAYS = 365
MAX_EXPIRY_DAYS = 366

REVIEWER_ROLE_LABELS = {
    "chief_contract_expert": "Chief contract expert",
    "delay_planning_expert": "Delay and planning expert",
    "arbitration_case_preparation_expert": "Arbitration case preparation expert",
    "senior_legal_approver": "Senior legal approver",
    "legal_reviewer": "Legal reviewer",
    "contract_reviewer": "Contract reviewer",
    "delay_reviewer": "Delay reviewer",
    "quantum_reviewer": "Quantum reviewer",
    "export_authorizer": "Export authoriser",
}


class ReviewerAssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(..., min_length=1, max_length=64)
    role_key: str = Field(..., min_length=1, max_length=64)
    scope_type: ReviewerScopeType
    organization_id: Optional[str] = Field(default=None, max_length=64)
    project_id: Optional[str] = Field(default=None, max_length=64)
    reason: str = Field(..., min_length=5, max_length=500)
    expires_at: Optional[datetime] = None

    @field_validator("role_key")
    @classmethod
    def _known_role(cls, value: str) -> str:
        key = value.strip().lower()
        if key not in REVIEWER_ROLE_KEYS:
            raise ValueError("role_key is not a reviewer role")
        return key

    @field_validator("reason")
    @classmethod
    def _reason_has_text(cls, value: str) -> str:
        text = value.strip()
        if len(text) < 5:
            raise ValueError("reason must say why the role is granted")
        return text

    @model_validator(mode="after")
    def _scope_is_coherent(self) -> "ReviewerAssignmentCreate":
        if self.scope_type == REVIEWER_SCOPE_PLATFORM:
            if self.organization_id or self.project_id:
                raise ValueError("a platform assignment names no organisation or project")
            if self.role_key not in SKILL_REVIEWER_ROLE_KEYS:
                raise ValueError("only skill reviewer roles are held at platform scope")
        elif self.scope_type == REVIEWER_SCOPE_ORGANIZATION:
            if not self.organization_id or self.project_id:
                raise ValueError("an organisation assignment names one organisation and no project")
        elif self.scope_type == REVIEWER_SCOPE_PROJECT:
            if not self.project_id:
                raise ValueError("a project assignment names one project")
        return self


class ReviewerAssignmentRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=5, max_length=500)

    @field_validator("reason")
    @classmethod
    def _reason_has_text(cls, value: str) -> str:
        text = value.strip()
        if len(text) < 5:
            raise ValueError("reason must say why the role is withdrawn")
        return text


class ReviewerAssignment(BaseModel):
    """One assignment as stored and as returned."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    user_id: str
    role_key: str
    scope_type: ReviewerScopeType
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    granted_by: str
    reason: str
    granted_at: datetime
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    revoke_reason: Optional[str] = None


class ReviewerAssignmentView(BaseModel):
    """An assignment with the words a person reads."""

    id: str
    user_id: str
    user_name: str
    role_key: str
    role_label: str
    scope_type: ReviewerScopeType
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    granted_by: str
    reason: str
    granted_at: datetime
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    revoke_reason: Optional[str] = None
    #: "active", "expired" or "revoked".
    state: str


class ReviewerAssignmentList(BaseModel):
    items: List[ReviewerAssignmentView] = Field(default_factory=list)


class MyReviewerRoles(BaseModel):
    """What the caller holds, by scope. Used by the client to show reviewer views."""

    platform: List[str] = Field(default_factory=list)
    assignments: List[ReviewerAssignmentView] = Field(default_factory=list)


class LegacyReviewerRoleHolder(BaseModel):
    user_id: str
    user_name: str
    role_key: str
    #: True when a live assignment already covers this role for the reported scope.
    has_assignment: bool


class LegacyReviewerRoleReport(BaseModel):
    """People holding a reviewer role through their role list, not an assignment.

    Read-only. Nothing is converted automatically: an administrator grants an
    explicit assignment for each person who should keep the role.
    """

    organization_id: Optional[str] = None
    holders: List[LegacyReviewerRoleHolder] = Field(default_factory=list)
    counts_by_role: dict = Field(default_factory=dict)
