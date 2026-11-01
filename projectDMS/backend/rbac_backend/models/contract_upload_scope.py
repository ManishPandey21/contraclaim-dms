"""The upload scope discriminated union.

An uploader states organisation or project scope **affirmatively**. Nothing
stands in for that decision: not an omitted field, not an empty string, not the
navbar selection, not a cleared navbar, not the actor's role tier, not a
client-persisted preference.

The reason the discriminator must be a positive value rather than a nullability
is a serialisation fact, not a style preference. A falsy value is dropped by
ordinary client serialisation before any server code runs, so "no project" and
"I did not answer" arrive identically — which is exactly how the current upload
path came to treat an absent project as an organisation-scope decision nobody
made. A non-empty ``scope_level`` cannot be dropped that way.

``project_id`` supplied *with* organisation scope is rejected rather than
ignored: silently dropping it would let a client believe it had scoped an upload
to a project when the server recorded the opposite.

There is deliberately no ``UNKNOWN`` member. If scope cannot be determined, the
right action is to upload a generic Document — not to record an uncertainty as
though it were a contract-scope decision.
"""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "ContractUploadScope",
    "OrganizationScopeUpload",
    "ProjectScopeUpload",
    "ScopeLevel",
    "parse_upload_scope",
]

ScopeLevel = Literal["organization", "project"]


class OrganizationScopeUpload(BaseModel):
    """The document belongs to the organisation, not to any one project."""

    scope_level: Literal["organization"]
    #: Present only so an accidental value can be REJECTED rather than ignored.
    project_id: Optional[str] = None

    @field_validator("project_id")
    @classmethod
    def _no_project_on_organisation_scope(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and str(value).strip():
            raise ValueError(
                "project_id must not be supplied with organisation scope; it is "
                "rejected rather than ignored, because silently dropping it would "
                "let a client believe it scoped the upload to a project"
            )
        return None


class ProjectScopeUpload(BaseModel):
    """The document belongs to one project, which must be named."""

    scope_level: Literal["project"]
    project_id: str = Field(min_length=1)

    @field_validator("project_id")
    @classmethod
    def _anchor_must_be_real(cls, value: str) -> str:
        anchor = str(value).strip()
        if not anchor:
            # A blank anchor is the same defect as an omitted one wearing a
            # value's clothes.
            raise ValueError(
                "project scope requires a non-blank project_id; a blank anchor is "
                "an unanswered question, not a project"
            )
        return anchor


ContractUploadScope = Union[OrganizationScopeUpload, ProjectScopeUpload]


def parse_upload_scope(payload: object) -> ContractUploadScope:
    """Build a scope decision from a request body, or raise.

    An omitted or empty discriminator raises rather than defaulting. There is no
    branch here that turns absence into a scope, which is the whole point.
    """
    if not isinstance(payload, dict):
        raise ValueError("upload scope must be an object carrying scope_level")

    raw = payload.get("scope_level")
    level = str(raw).strip() if raw is not None else ""
    if not level:
        raise ValueError(
            "scope_level is required and must be sent affirmatively; an omitted "
            "or empty discriminator is rejected, never reinterpreted as "
            "organisation scope"
        )
    if level == "organization":
        return OrganizationScopeUpload(**payload)
    if level == "project":
        return ProjectScopeUpload(**payload)
    raise ValueError(
        f"unsupported scope_level {level!r}; the only scopes are 'organization' "
        "and 'project' - there is no UNKNOWN, because an undetermined scope means "
        "the document should be uploaded as a generic Document instead"
    )
