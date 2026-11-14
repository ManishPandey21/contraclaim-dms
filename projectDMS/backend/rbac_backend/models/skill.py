"""Skill registry models.

A *skill* is a versioned method document (``SKILL.md`` plus reference notes)
that tells the drafting engine HOW to analyse, evaluate or draft. Skills are
method, never evidence: skill text must not enter the source ledger and cannot
support a clause citation.

``SkillManifest`` is authored by us per skill version. It is not parsed from
the prose, because the packs carry no input/output schema of their own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..utils.datetime import now_utc

SkillKind = Literal["analysis", "evaluation", "drafting"]
SkillVersionStatus = Literal["draft", "published", "deprecated"]
SkillRole = Literal["contractor", "engineer", "employer"]

SYSTEM_SCOPE = "system"
SEMVER_PATTERN = r"^\d+\.\d+\.\d+$"
SKILL_KEY_PATTERN = r"^[a-z0-9][a-z0-9-]{1,63}$"


class SkillChainRule(BaseModel):
    """A hand-off a skill declares: run ``skill_key`` when the condition holds."""

    skill_key: str
    label: Optional[str] = None
    # Condition. Any populated field must match; ``always`` matches everything.
    issue_types: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    always: bool = False


class SkillManifest(BaseModel):
    roles_supported: List[SkillRole] = Field(
        default_factory=lambda: ["contractor", "engineer", "employer"]
    )
    # The role the prose was written for. Any other supported role needs an overlay.
    native_role: Optional[SkillRole] = None
    contract_forms: List[str] = Field(default_factory=list)
    output_schema_key: str
    required_inputs: List[str] = Field(default_factory=list)
    chains_to: List[SkillChainRule] = Field(default_factory=list)
    validators: List[str] = Field(default_factory=list)
    use_when: Optional[str] = None


class SkillReference(BaseModel):
    path: str
    text: str
    content_hash: str


class SkillEvalReport(BaseModel):
    """Result of the golden-set evaluation that gates publishing."""

    suite: str = Field(..., min_length=1, max_length=200)
    score: float = Field(..., ge=0, le=1)
    threshold: float = Field(..., ge=0, le=1)
    case_count: int = Field(..., ge=0)
    signed_off_by: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=2000)
    recorded_at: datetime = Field(default_factory=now_utc)

    @property
    def passed(self) -> bool:
        return self.case_count > 0 and self.score >= self.threshold


class SkillVersion(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = Field(default=None, alias="_id")
    skill_key: str = Field(..., pattern=SKILL_KEY_PATTERN)
    version: str = Field(..., pattern=SEMVER_PATTERN)
    owner_scope: str = SYSTEM_SCOPE
    name: str
    description: str = ""
    kind: SkillKind
    content_hash: str = Field(..., min_length=64, max_length=64)
    skill_md: str
    references: List[SkillReference] = Field(default_factory=list)
    manifest: SkillManifest
    status: SkillVersionStatus = "draft"
    bundle_file_id: Optional[str] = None
    eval_report: Optional[SkillEvalReport] = None
    guardrail_findings: List[Dict[str, Any]] = Field(default_factory=list)
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)
    published_by: Optional[str] = None
    published_at: Optional[datetime] = None
    deprecated_by: Optional[str] = None
    deprecated_at: Optional[datetime] = None

    @field_validator("id", mode="before")
    @classmethod
    def _stringify_id(cls, value: Any) -> Optional[str]:
        return None if value is None else str(value)


class Skill(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = Field(default=None, alias="_id")
    skill_key: str
    owner_scope: str = SYSTEM_SCOPE
    name: str
    description: str = ""
    kind: SkillKind
    status: Literal["active", "retired"] = "active"
    latest_published_version: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)

    @field_validator("id", mode="before")
    @classmethod
    def _stringify_id(cls, value: Any) -> Optional[str]:
        return None if value is None else str(value)


class SkillBinding(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    organization_id: str
    project_id: Optional[str] = None
    skill_key: str
    pinned_version: Optional[str] = None
    enabled: bool = True
    role_overlay_key: Optional[str] = None
    updated_by: Optional[str] = None
    updated_at: datetime = Field(default_factory=now_utc)


class SkillBindingRequest(BaseModel):
    organization_id: str = Field(..., min_length=1)
    project_id: Optional[str] = None
    skill_key: str = Field(..., pattern=SKILL_KEY_PATTERN)
    pinned_version: Optional[str] = Field(default=None, pattern=SEMVER_PATTERN)
    enabled: bool = True
    role_overlay_key: Optional[str] = Field(default=None, max_length=80)


class SkillPublishRequest(BaseModel):
    eval_report: SkillEvalReport


class SkillImportOptions(BaseModel):
    """Form fields accompanying an uploaded bundle."""

    version: str = Field(..., pattern=SEMVER_PATTERN)
    kind: SkillKind
    manifest: SkillManifest


class EffectiveSkill(BaseModel):
    """A skill as resolved for one organisation/project, safe to list to drafters."""

    skill_key: str
    name: str
    description: str = ""
    kind: SkillKind
    use_when: Optional[str] = None
    version: Optional[str] = None
    content_hash: Optional[str] = None
    roles_supported: List[SkillRole] = Field(default_factory=list)
    native_role: Optional[SkillRole] = None
    contract_forms: List[str] = Field(default_factory=list)
    output_schema_key: Optional[str] = None
    chains_to: List[SkillChainRule] = Field(default_factory=list)
    required_inputs: List[str] = Field(default_factory=list)
    enabled: bool = True
    pinned: bool = False
    available: bool = True
    unavailable_reason: Optional[str] = None


class SkillVersionSummary(BaseModel):
    skill_key: str
    version: str
    name: str
    kind: SkillKind
    status: SkillVersionStatus
    content_hash: str
    owner_scope: str = SYSTEM_SCOPE
    eval_report: Optional[SkillEvalReport] = None
    created_at: Optional[datetime] = None
    published_at: Optional[datetime] = None


class SkillAdminListResponse(BaseModel):
    skills: List[Skill] = Field(default_factory=list)
    versions: List[SkillVersionSummary] = Field(default_factory=list)
    bindings: List[SkillBinding] = Field(default_factory=list)


__all__ = [
    "SYSTEM_SCOPE",
    "EffectiveSkill",
    "Skill",
    "SkillAdminListResponse",
    "SkillBinding",
    "SkillBindingRequest",
    "SkillChainRule",
    "SkillEvalReport",
    "SkillImportOptions",
    "SkillKind",
    "SkillManifest",
    "SkillPublishRequest",
    "SkillReference",
    "SkillRole",
    "SkillVersion",
    "SkillVersionStatus",
    "SkillVersionSummary",
]
