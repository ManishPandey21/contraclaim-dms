"""Authoritative Active Organisation / Active Project context.

Single server-side source of truth for "which organisation and project is this
request operating on". Nothing here trusts an identifier that arrived from the
browser: every id is re-validated against the authenticated user's real
memberships, against the organisation the project actually belongs to, and
against the active status of both records.

Design rules (deny by default):

* An organisation id the user cannot reach is a 403, never a silent empty list.
* A project id that does not belong to the supplied organisation is a 403 even
  when the user can reach both independently -- a mismatched pair is always a
  manipulation attempt or a stale client.
* A disabled organisation or a deactivated project is a 403.
* Absence of a required id is a 400 carrying ``selection_required`` so the UI
  can raise its "select an Organisation and Project" prompt rather than
  rendering an empty page that looks like "no data".
* Every rejection is written to the audit log with the attempted ids.

Role tiers, derived from the invariants already enforced by ``User``:

``global``   ``superadmin`` (every organisation) and ``superuser`` (its
             assigned organisations). Must choose an organisation explicitly.
``org``      ``orgadmin`` / ``orguser``. Organisation is fixed to their own.
``project``  ``projectadmin`` / ``projectuser``. Organisation and project are
             both fixed to their assignment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Set

from fastapi import Depends, HTTPException, Request, status

from .database import get_db
from .security import CurrentUser, get_current_user

logger = logging.getLogger(__name__)

GLOBAL_ROLES = frozenset({"superadmin", "superuser"})
ORG_ROLES = frozenset({"orgadmin", "orguser"})
PROJECT_ROLES = frozenset({"projectadmin", "projectuser"})

SELECTION_REQUIRED = "selection_required"
CONTEXT_FORBIDDEN = "context_forbidden"

# Records predating the is_active migration have no flag; absent means active so
# the guard never retroactively locks anyone out of existing data.
ACTIVE_FILTER = {"$ne": False}


class TenantContextError(HTTPException):
    """403/400 carrying a machine-readable reason for the client."""

    def __init__(self, *, status_code: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message, **extra})
        self.code = code


@dataclass(frozen=True)
class TenantContext:
    """A validated context. Existence of an instance means the ids are safe."""

    organization_id: Optional[str]
    project_id: Optional[str]
    role_tier: str
    can_switch_organization: bool
    can_switch_project: bool

    @property
    def is_complete(self) -> bool:
        return bool(self.organization_id and self.project_id)

    def audit_fields(self) -> dict:
        return {"organization_id": self.organization_id, "project_id": self.project_id}


def role_names(user: Any) -> Set[str]:
    return {str(role).lower() for role in (getattr(user, "roles", []) or []) if role}


def role_tier(user: Any) -> str:
    roles = role_names(user)
    if roles & GLOBAL_ROLES:
        return "global"
    if roles & ORG_ROLES:
        return "org"
    if roles & PROJECT_ROLES:
        return "project"
    return "restricted"


def _as_id(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _object_id_candidates(value: str) -> list:
    from bson import ObjectId

    candidates: list = [str(value)]
    try:
        candidates.append(ObjectId(str(value)))
    except Exception:
        pass
    return candidates


class TenantContextResolver:
    """Resolves and validates an Active Organisation / Active Project pair."""

    def __init__(self, db: Any) -> None:
        self.db = db

    # -- membership -----------------------------------------------------

    async def allowed_organization_ids(self, user: Any) -> Optional[Set[str]]:
        """Organisations the user may reach. ``None`` means unrestricted."""
        roles = role_names(user)
        if "superadmin" in roles:
            return None

        allowed = set()
        own = getattr(user, "organization_id", None)
        if own:
            allowed.add(_as_id(own))
        allowed.update(_as_id(org) for org in (getattr(user, "organizations", []) or []) if org)

        cursor = self.db.organization_memberships.find(
            {"user_id": _as_id(getattr(user, "id", "")), "status": {"$in": ["active", None]}}
        )
        for membership in await cursor.to_list(length=None):
            if membership.get("organization_id"):
                allowed.add(_as_id(membership["organization_id"]))
        return allowed

    async def allowed_project_ids(self, user: Any) -> Optional[Set[str]]:
        """Projects the user is explicitly assigned. ``None`` means org-wide."""
        tier = role_tier(user)
        if tier in {"global", "org"}:
            # These tiers are bounded by organisation, not by project assignment.
            return None

        allowed = {_as_id(p) for p in (getattr(user, "projects", []) or []) if p}
        cursor = self.db.project_memberships.find(
            {"user_id": _as_id(getattr(user, "id", "")), "status": {"$in": ["active", None]}}
        )
        for membership in await cursor.to_list(length=None):
            if membership.get("project_id"):
                allowed.add(_as_id(membership["project_id"]))
        return allowed

    # -- record checks --------------------------------------------------

    async def organization_is_active(self, organization_id: str) -> bool:
        organizations = getattr(self.db, "organizations", None)
        if organizations is None or not hasattr(organizations, "find_one"):
            return False
        doc = await organizations.find_one(
            {"_id": {"$in": _object_id_candidates(organization_id)}, "is_active": ACTIVE_FILTER}
        )
        return bool(doc)

    async def load_active_project(self, project_id: str) -> Optional[dict]:
        projects = getattr(self.db, "projects", None)
        if projects is None or not hasattr(projects, "find_one"):
            return None
        return await projects.find_one(
            {"_id": {"$in": _object_id_candidates(project_id)}, "is_active": ACTIVE_FILTER}
        )

    # -- resolution -----------------------------------------------------

    async def resolve(
        self,
        user: Any,
        *,
        organization_id: Optional[str],
        project_id: Optional[str],
        require_project: bool,
        require_organization: bool = True,
    ) -> TenantContext:
        tier = role_tier(user)
        requested_org = _as_id(organization_id)
        requested_project = _as_id(project_id)

        allowed_orgs = await self.allowed_organization_ids(user)
        allowed_projects = await self.allowed_project_ids(user)

        # A restricted role has no tenant reach at all.
        if tier == "restricted" and allowed_orgs is not None and not allowed_orgs:
            raise self._forbid(user, "No organisation is assigned to this account.", requested_org, requested_project)

        # -- organisation ------------------------------------------------
        effective_org = requested_org
        if tier in {"org", "project"}:
            # Server pins these tiers to their own organisation and ignores any
            # organisation the client asked for -- but a *mismatched* request is
            # an explicit manipulation attempt, so reject rather than silently
            # rewrite it.
            own_org = _as_id(getattr(user, "organization_id", ""))
            if not own_org:
                raise self._forbid(user, "No organisation is assigned to this account.", requested_org, requested_project)
            if requested_org and requested_org != own_org:
                raise self._forbid(
                    user, "This organisation is not accessible to your account.", requested_org, requested_project
                )
            effective_org = own_org
        elif tier == "global":
            if not effective_org:
                if require_organization:
                    raise self._require_selection("Please select an Organisation and Project to continue.")
                return TenantContext(None, None, tier, True, True)
            if allowed_orgs is not None and effective_org not in allowed_orgs:
                raise self._forbid(
                    user, "This organisation is not accessible to your account.", requested_org, requested_project
                )

        if effective_org and not await self.organization_is_active(effective_org):
            raise self._forbid(user, "This organisation is disabled.", effective_org, requested_project)

        # -- project -----------------------------------------------------
        effective_project = requested_project
        if tier == "project":
            assigned = sorted(allowed_projects or set())
            if not assigned:
                raise self._forbid(user, "No project is assigned to this account.", effective_org, requested_project)
            if requested_project and requested_project not in set(assigned):
                raise self._forbid(
                    user, "This project is not accessible to your account.", effective_org, requested_project
                )
            # Pin to the single assignment when the client sent nothing.
            effective_project = requested_project or (assigned[0] if len(assigned) == 1 else "")

        if not effective_project:
            if require_project:
                raise self._require_selection("Please select an Organisation and Project to continue.")
            return TenantContext(
                effective_org or None,
                None,
                tier,
                tier == "global",
                tier != "project",
            )

        project = await self.load_active_project(effective_project)
        if not project:
            raise self._forbid(
                user, "This project is unavailable or has been deactivated.", effective_org, effective_project
            )

        # The pair must be internally consistent even when both halves are
        # individually reachable: a project from another organisation is never
        # valid under this organisation.
        project_org = _as_id(project.get("organization_id") or project.get("organizationId"))
        if not project_org or (effective_org and project_org != effective_org):
            raise self._forbid(
                user,
                "The selected project does not belong to the selected organisation.",
                effective_org,
                effective_project,
            )

        if allowed_orgs is not None and project_org not in allowed_orgs:
            raise self._forbid(
                user, "This project is not accessible to your account.", project_org, effective_project
            )
        if allowed_projects is not None and effective_project not in allowed_projects:
            raise self._forbid(
                user, "This project is not accessible to your account.", project_org, effective_project
            )

        return TenantContext(
            organization_id=effective_org or project_org,
            project_id=effective_project,
            role_tier=tier,
            can_switch_organization=tier == "global",
            can_switch_project=tier != "project",
        )

    # -- failures --------------------------------------------------------

    def _require_selection(self, message: str) -> TenantContextError:
        return TenantContextError(
            status_code=status.HTTP_400_BAD_REQUEST, code=SELECTION_REQUIRED, message=message
        )

    def _forbid(self, user: Any, message: str, organization_id: str, project_id: str) -> TenantContextError:
        logger.warning(
            "tenant context rejected: actor=%s org=%s project=%s reason=%s",
            _as_id(getattr(user, "id", "")),
            organization_id or "-",
            project_id or "-",
            message,
        )
        self._audit_rejection(user, message, organization_id, project_id)
        return TenantContextError(
            status_code=status.HTTP_403_FORBIDDEN,
            code=CONTEXT_FORBIDDEN,
            message=message,
            organization_id=organization_id or None,
            project_id=project_id or None,
        )

    def _audit_rejection(self, user: Any, reason: str, organization_id: str, project_id: str) -> None:
        """Record the attempt. Never let auditing failure mask the rejection."""
        try:
            import asyncio

            from ..services.audit_event_service import AuditEventService

            coro = AuditEventService(self.db).emit(
                action="tenant.context.rejected",
                actor_id=_as_id(getattr(user, "id", "")),
                resource_type="tenant_context",
                organization_id=organization_id or None,
                project_id=project_id or None,
                result="denied",
                reason=reason,
            )
            asyncio.ensure_future(coro)
        except Exception:  # pragma: no cover - auditing must never raise here
            logger.debug("failed to audit tenant context rejection", exc_info=True)


def _first_present(request: Request, names: Sequence[str]) -> str:
    """Read an id from query or header. Body is deliberately not consulted."""
    for name in names:
        value = request.query_params.get(name)
        if value:
            return str(value)
    for name in names:
        value = request.headers.get(name)
        if value:
            return str(value)
    return ""


ORG_PARAM_NAMES = ("organization_id", "organizationId", "org_id", "X-Organization-Id")
PROJECT_PARAM_NAMES = ("project_id", "projectId", "proj_id", "X-Project-Id")


def tenant_context(*, require_project: bool = True, require_organization: bool = True):
    """FastAPI dependency returning a validated :class:`TenantContext`.

    Usage::

        @router.get("/letters")
        async def list_letters(ctx: TenantContext = Depends(tenant_context())):
            return await service.list(ctx.organization_id, ctx.project_id)
    """

    async def dependency(
        request: Request,
        db=Depends(get_db),
        current_user: CurrentUser = Depends(get_current_user),
    ) -> TenantContext:
        return await TenantContextResolver(db).resolve(
            current_user,
            organization_id=_first_present(request, ORG_PARAM_NAMES),
            project_id=_first_present(request, PROJECT_PARAM_NAMES),
            require_project=require_project,
            require_organization=require_organization,
        )

    return dependency


async def assert_context(
    db: Any,
    user: Any,
    *,
    organization_id: Optional[str],
    project_id: Optional[str],
    require_project: bool = True,
) -> TenantContext:
    """Imperative form for services, workers and background jobs."""
    return await TenantContextResolver(db).resolve(
        user,
        organization_id=organization_id,
        project_id=project_id,
        require_project=require_project,
    )
