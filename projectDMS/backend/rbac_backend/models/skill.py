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


SkillApprovalKind = Literal["technical", "legal"]
APPROVER_ROLE_PATTERN = r"^[a-z][a-z0-9_]{2,63}$"


class SkillApprovalRequirement(BaseModel):
    """One review a version must hold before it can be published."""

    kind: SkillApprovalKind
    reviewer_role: str = Field(..., pattern=APPROVER_ROLE_PATTERN)


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
    # Reviews that must exist, bound to this exact text and its evaluation,
    # before the version can be published. Empty keeps the evaluation-only gate.
    required_approvals: List[SkillApprovalRequirement] = Field(default_factory=list)


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
    # User id of whoever ran the evaluation. Required when the version needs
    # approvals: the evaluator, each approver and the publisher are separate people.
    recorded_by: Optional[str] = Field(default=None, max_length=200)

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
    # Serialisation point for publication decisions (plan D-05a, 4B-05): every
    # approval recorded or revoked, and every publish, increments or checks it
    # inside one transaction. Absent on versions stored before 4B-05 (reads as 0).
    approval_revision: int = 0
    # Hash of the live approval ids after the last decision. Display and audit only.
    approvals_digest: Optional[str] = None
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


SkillSubjectType = Literal["claim", "claim_head", "arbitration_case", "arbitration_draft", "hearing"]
SkillRunStatus = Literal["completed", "failed"]


class SkillRunStep(BaseModel):
    """One skill step run against a subject other than a letter.

    The Letter Bench keeps its own step records. Everything else (claims now,
    arbitration later) records steps here, keyed by what the step was about.
    ``skill_version`` and ``skill_hash`` are empty when no published version of
    the skill existed: the output is then the deterministic fallback, the step
    is ``degraded`` and ``error`` says why.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = Field(default=None, alias="_id")
    step_id: str
    subject_type: SkillSubjectType
    subject_id: str
    # The record the subject belongs to, for listing (a head's claim, say).
    parent_id: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    skill_key: str
    skill_version: str = ""
    skill_hash: str = ""
    skill_name: Optional[str] = None
    role: Optional[str] = None
    overlay_key: Optional[str] = None
    input_hash: str
    output: Dict[str, Any] = Field(default_factory=dict)
    output_schema_key: str
    status: SkillRunStatus = "completed"
    stale: bool = False
    degraded: bool = False
    error: Optional[str] = None
    model: Optional[str] = None
    prompt_key: Optional[str] = None
    prompt_version: Optional[int] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)
    completed_at: Optional[datetime] = None

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


class SkillApproval(BaseModel):
    """A reviewer's decision on one skill version.

    Bound to the exact text (``content_hash``) and evaluation result
    (``eval_report_hash``) the reviewer saw. A re-imported draft or a re-run
    evaluation produces different hashes, so an earlier approval stops matching.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = Field(default=None, alias="_id")
    skill_key: str = Field(..., pattern=SKILL_KEY_PATTERN)
    version: str = Field(..., pattern=SEMVER_PATTERN)
    content_hash: str = Field(..., min_length=64, max_length=64)
    # Hash of the version's manifest (its policy: required reviews, schema,
    # chains, validators). An approval given before the manifest changed stops
    # matching. Absent on approvals recorded before 4B-04, which therefore count
    # as stale.
    policy_hash: Optional[str] = Field(default=None, min_length=64, max_length=64)
    eval_suite: str
    eval_report_hash: str = Field(..., min_length=64, max_length=64)
    # The result the reviewer saw, kept readable alongside its hash.
    eval_score: Optional[float] = None
    eval_threshold: Optional[float] = None
    eval_case_count: Optional[int] = None
    eval_recorded_by: Optional[str] = None
    kind: SkillApprovalKind
    reviewer_role: str = Field(..., pattern=APPROVER_ROLE_PATTERN)
    reviewer_user_id: str = Field(..., min_length=1)
    comment: Optional[str] = Field(default=None, max_length=2000)
    decided_at: datetime = Field(default_factory=now_utc)
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    revoke_reason: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("id", mode="before")
    @classmethod
    def _stringify_id(cls, value: Any) -> Optional[str]:
        return None if value is None else str(value)


class SkillApprovalRequest(BaseModel):
    kind: SkillApprovalKind
    reviewer_role: str = Field(..., pattern=APPROVER_ROLE_PATTERN)
    eval_report: SkillEvalReport
    comment: Optional[str] = Field(default=None, max_length=2000)


class SkillApprovalRevokeRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=2000)


class SkillApprovalStatus(BaseModel):
    """Whether one required review is in place for a version and evaluation."""

    kind: SkillApprovalKind
    reviewer_role: str
    satisfied: bool = False
    # missing | stale_content | stale_policy | stale_evaluation | revoked | ok
    state: str = "missing"
    approval: Optional[SkillApproval] = None


class SkillApprovalListResponse(BaseModel):
    skill_key: str
    version: str
    content_hash: str
    # The version's manifest hash; approvals must carry the same one.
    policy_hash: Optional[str] = None
    # The reviews required, server floor included (not only what the manifest lists).
    required: List[SkillApprovalRequirement] = Field(default_factory=list)
    approvals: List[SkillApproval] = Field(default_factory=list)


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
    required_approvals: List[SkillApprovalRequirement] = Field(default_factory=list)


class SkillReviewQueueReview(BaseModel):
    kind: SkillApprovalKind
    reviewer_role: str
    #: The caller holds this reviewer role at platform scope.
    mine: bool = False
    #: A live approval is on record for this review (for any evaluation result).
    given: bool = False
    given_by_me: bool = False


class SkillReviewQueueItem(BaseModel):
    """One draft a platform reviewer is asked to review."""

    skill_key: str
    version: str
    name: str
    kind: SkillKind
    content_hash: str
    created_at: Optional[datetime] = None
    reviews: List[SkillReviewQueueReview] = Field(default_factory=list)
    #: At least one review that is the caller's to give is still outstanding.
    waiting_on_me: bool = False


class SkillReviewQueueResponse(BaseModel):
    #: The reviewer roles the caller holds at platform scope.
    my_roles: List[str] = Field(default_factory=list)
    items: List[SkillReviewQueueItem] = Field(default_factory=list)


class SkillAdminListResponse(BaseModel):
    skills: List[Skill] = Field(default_factory=list)
    versions: List[SkillVersionSummary] = Field(default_factory=list)
    bindings: List[SkillBinding] = Field(default_factory=list)


__all__ = [
    "SYSTEM_SCOPE",
    "EffectiveSkill",
    "Skill",
    "SkillAdminListResponse",
    "SkillReviewQueueItem",
    "SkillReviewQueueResponse",
    "SkillReviewQueueReview",
    "SkillApproval",
    "SkillApprovalKind",
    "SkillApprovalListResponse",
    "SkillApprovalRequest",
    "SkillApprovalRequirement",
    "SkillApprovalRevokeRequest",
    "SkillApprovalStatus",
    "SkillBinding",
    "SkillBindingRequest",
    "SkillChainRule",
    "SkillEvalReport",
    "SkillImportOptions",
    "SkillKind",
    "SkillManifest",
    "SkillPublishRequest",
    "SkillReference",
    "SkillRunStep",
    "SkillSubjectType",
    "SkillRole",
    "SkillVersion",
    "SkillVersionStatus",
    "SkillVersionSummary",
]
