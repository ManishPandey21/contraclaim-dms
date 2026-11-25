"""The party a project's workspace acts for (plan 4B-15, D-11, C-14).

Claim tests, determinations, simulations and submissions are worked from one
party's position: Contractor, Engineer or Employer. That position is a fact
about the project, so it has:

- **one write path** (the dedicated route; a project update cannot carry it);
- **an audit trail** (before, after, reason, who);
- **a lock**: once a determination, a simulation or a submission exists for the
  project, only an administrator of the organisation may change it, with a
  reason. A Super Admin may not: the platform does not decide which party a
  tenant's project acts for.

An organisation may record a default, which answers for a project that has not
stated its own. Changing the default is locked the same way, across the
projects that rely on it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from bson import ObjectId
from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument

from .audit_event_service import AuditEventService

# Imported first on purpose: the skills package and letter drafting import each
# other, and loading the prompts module first is the order that resolves it
# (the same order services/arbitration_drafting/service.py uses).
from .letter_drafting import prompts as _letter_prompts  # noqa: F401  isort: skip
from .claim_determinations import DETERMINATIONS
from .claim_privileged import any_simulation_for_projects
from .claim_submission import SUBMISSIONS
from .scope_service import ScopeService

Capacity = Literal["contractor", "engineer", "employer"]
CAPACITIES = frozenset({"contractor", "engineer", "employer"})
ORG_DEFAULT_FIELD = "default_contractual_capacity"


class PerspectiveChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contractual_capacity: Capacity
    #: Required whenever an existing position is being changed.
    reason: Optional[str] = Field(default=None, max_length=500)


class PerspectiveState(BaseModel):
    """What a screen needs to show the position and whether this person may set it."""

    value: Optional[Capacity] = None
    #: "project", "organisation_default" or "none".
    source: str = "none"
    #: A determination, simulation or submission already relies on it.
    in_use: bool = False
    can_set: bool = False
    cannot_set_reason: Optional[str] = None


def _refuse(code: int, error_code: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": error_code, "user_message": message, "detail": message})


def _lookup(value: str) -> Dict[str, Any]:
    values: List[Any] = [str(value)]
    if ObjectId.is_valid(str(value)):
        values.append(ObjectId(str(value)))
    return {"_id": {"$in": values}}


def _valid(value: Any) -> Optional[str]:
    return value if value in CAPACITIES else None


async def resolve_perspective(db: Any, project: Optional[Dict[str, Any]]) -> tuple[Optional[str], str]:
    """The position in force for a project, and where it comes from."""
    if not project:
        return None, "none"
    own = _valid(project.get("contractual_capacity"))
    if own:
        return own, "project"
    organization_id = project.get("organization_id")
    if organization_id:
        organization = await db.organizations.find_one(_lookup(str(organization_id)), {ORG_DEFAULT_FIELD: 1})
        default = _valid((organization or {}).get(ORG_DEFAULT_FIELD))
        if default:
            return default, "organisation_default"
    return None, "none"


async def perspective_in_use(db: Any, project_ids: List[str]) -> bool:
    """Whether any finding has been made from the position of one of these projects."""
    ids = [str(item) for item in project_ids if item]
    if not ids:
        return False
    query = {"project_id": {"$in": ids}}
    if await db[DETERMINATIONS].find_one(query, {"_id": 1}):
        return True
    if await db[SUBMISSIONS].find_one(query, {"_id": 1}):
        return True
    return await any_simulation_for_projects(db, ids)


class ProjectPerspectiveService:
    def __init__(self, db: Any) -> None:
        self.db = db
        self.scope = ScopeService(db)
        self.audit = AuditEventService(db)

    async def _is_organisation_admin(self, user: Any, organization_id: Optional[str]) -> bool:
        """An Organization Admin of that organisation. Deliberately not a Super Admin."""
        if not organization_id or self.scope.is_superadmin(user):
            return False
        if "orgadmin" not in await self.scope.role_names_in(user, str(organization_id)):  # M1: assigned there
            return False
        return await self.scope.has_organization_wide_scope(user, organization_id=str(organization_id))

    async def _cannot_set_reason(
        self, user: Any, organization_id: Optional[str], in_use: bool, may_update: bool
    ) -> Optional[str]:
        if not may_update:
            return "You do not have permission to change this project's settings."
        if in_use and not await self._is_organisation_admin(user, organization_id):
            return (
                "A determination, simulation or submission has already been made from this position. "
                "Only an administrator of the organisation can change it, with a reason."
            )
        return None

    # ---------------------------------------------------------------- project
    async def state(self, project: Dict[str, Any], user: Any, *, may_update: bool) -> PerspectiveState:
        value, source = await resolve_perspective(self.db, project)
        in_use = await perspective_in_use(self.db, [str(project["_id"])])
        reason = await self._cannot_set_reason(user, project.get("organization_id"), in_use, may_update)
        return PerspectiveState(
            value=value,  # type: ignore[arg-type]
            source=source,
            in_use=in_use,
            can_set=reason is None,
            cannot_set_reason=reason,
        )

    async def set_for_project(self, project: Dict[str, Any], change: PerspectiveChange, user: Any) -> Dict[str, Any]:
        """Set the project's own position. The caller has already authorised a project update."""
        before = _valid(project.get("contractual_capacity"))
        if before == change.contractual_capacity:
            return project
        project_id = str(project["_id"])
        organization_id = str(project.get("organization_id") or "") or None
        in_use = await perspective_in_use(self.db, [project_id])
        if in_use and not await self._is_organisation_admin(user, organization_id):
            raise _refuse(
                status.HTTP_409_CONFLICT,
                "perspective_locked",
                "A determination, simulation or submission has already been made from this position. "
                "Only an administrator of the organisation can change it, with a reason.",
            )
        effective_before, _source = await resolve_perspective(self.db, project)
        reason = (change.reason or "").strip()
        if (in_use or effective_before is not None) and len(reason) < 10:
            raise _refuse(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "perspective_reason_required",
                "Say why the party this project acts for is being changed (at least 10 characters).",
            )
        updated = await self.db.projects.find_one_and_update(
            {"_id": project["_id"], "contractual_capacity": project.get("contractual_capacity")},
            {"$set": {"contractual_capacity": change.contractual_capacity}},
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            raise _refuse(
                status.HTTP_409_CONFLICT,
                "perspective_changed_concurrently",
                "Someone changed this at the same time. Reload and try again.",
            )
        await self.audit.emit(
            action="project.contractual_capacity.changed",
            actor_id=str(getattr(user, "id", "") or "") or None,
            resource_type="project",
            resource_id=project_id,
            organization_id=organization_id,
            project_id=project_id,
            reason=reason or None,
            before={"contractual_capacity": before, "effective": effective_before},
            after={"contractual_capacity": change.contractual_capacity},
            metadata={"in_use": in_use},
        )
        return updated

    # ----------------------------------------------------------- organisation
    async def _projects_relying_on_default(self, organization_id: str) -> List[str]:
        rows = await self.db.projects.find(
            {
                "organization_id": _lookup(str(organization_id))["_id"],
                "contractual_capacity": {"$nin": sorted(CAPACITIES)},
            },
            {"_id": 1},
        ).to_list(length=5000)
        return [str(row["_id"]) for row in rows]

    async def set_organisation_default(
        self, organization: Dict[str, Any], change: PerspectiveChange, user: Any
    ) -> Dict[str, Any]:
        organization_id = str(organization["_id"])
        if not await self._is_organisation_admin(user, organization_id):
            raise _refuse(
                status.HTTP_403_FORBIDDEN,
                "perspective_default_needs_organisation_admin",
                "Only an administrator of the organisation can set its default.",
            )
        before = _valid(organization.get(ORG_DEFAULT_FIELD))
        if before == change.contractual_capacity:
            return organization
        relying = await self._projects_relying_on_default(organization_id)
        in_use = await perspective_in_use(self.db, relying)
        reason = (change.reason or "").strip()
        if (before is not None or in_use) and len(reason) < 10:
            raise _refuse(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "perspective_reason_required",
                "Say why the party this project acts for is being changed (at least 10 characters).",
            )
        updated = await self.db.organizations.find_one_and_update(
            {"_id": organization["_id"]},
            {"$set": {ORG_DEFAULT_FIELD: change.contractual_capacity}},
            return_document=ReturnDocument.AFTER,
        )
        await self.audit.emit(
            action="organization.default_contractual_capacity.changed",
            actor_id=str(getattr(user, "id", "") or "") or None,
            resource_type="organization",
            resource_id=organization_id,
            organization_id=organization_id,
            reason=reason or None,
            before={ORG_DEFAULT_FIELD: before},
            after={ORG_DEFAULT_FIELD: change.contractual_capacity},
            metadata={"projects_relying_on_default": len(relying), "in_use": in_use},
        )
        return updated or organization


__all__ = [
    "CAPACITIES",
    "ORG_DEFAULT_FIELD",
    "PerspectiveChange",
    "PerspectiveState",
    "ProjectPerspectiveService",
    "perspective_in_use",
    "resolve_perspective",
]
