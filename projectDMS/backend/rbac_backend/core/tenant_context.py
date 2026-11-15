"""Active Organisation / Active Project context: the navbar selection as a request boundary.

Minimal port of the tenant-context design on `codex/csv-import-scope`
(`core/tenant_context.py`). Same header names, same `selection_required` /
`context_forbidden` semantics, same refusal audit. It is shared infrastructure; it
binds the routes that depend on it:

* the Hindrance & Constraint Register and its `/api/delay-events` compatibility
  routes (PR #22, 2026-09-22);
* the Variation Register (CL-3A, closing the CL-2 debt);
* Programme Milestones and Chronology events (CL-3B);
* the core DMS modules (CL-4A): Documents, Contract Documents / Contract Master,
  Claims, IPC / Bills, Insurance, Bank Guarantees, Key Dates / achievements / EOT,
  the dashboard totals and document search;
* on the shared relationship routes, the target types whose adapter sets
  `active_scope_enforced` (`services/entity_adapter_registry.py`).

Registers not listed (correspondence/letters, tasks, reports, arbitration) keep their
existing semantics until they are moved deliberately (CL-4B). The browser sends the
headers on every request, but a route that does not depend on this module never
reads them.

What it answers, and what it deliberately does not:

* The browser sends its current selection as ``X-Org-Id`` / ``X-Proj-Id`` on every API
  request (`client/src/services/http.ts` via `active-scope.ts`). Nothing here trusts those ids: a selected
  project must exist, be active, belong to the selected organisation, and be inside the
  principal's own scope (``ScopeService.is_client_scope_allowed``, the same membership
  test ``PolicyService`` uses). Otherwise the request is refused 403 ``context_forbidden``.
* ``EffectiveScope = Entitlement ∩ Navbar Selection``: once a project is selected, a
  record, a create body or a link target in any OTHER project is refused 403
  ``context_forbidden`` - even for a member of both projects, and even for superadmin
  (the selection is a request boundary, not a membership test).
* A record-level or mutating operation with no project selected is 400
  ``selection_required``. The project is never inferred from the record or the body.
* Membership and permission are still answered by ``PolicyService``; this layer only
  narrows. Row visibility for listings is still ``build_scope_query``.

Unlike the codex design, ids are read from headers only. On these routes ``project_id``
is an existing query FILTER; reading it as the selection would let a filter widen scope.

Threat model, stated so nobody over-reads it: the headers come from the browser. A
member of A and B can always declare A. The boundary stops cross-project reads and
writes from stale or mismatched UI state and makes every record-level request state its
scope; it cannot deny a principal a project it is entitled to.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from fastapi import Depends, HTTPException, Request, status

from .database import get_db
from .security import CurrentUser, get_current_user

logger = logging.getLogger(__name__)

SELECTION_REQUIRED = "selection_required"
CONTEXT_FORBIDDEN = "context_forbidden"

ORG_HEADERS = ("X-Org-Id", "X-Organization-Id")
PROJECT_HEADERS = ("X-Proj-Id", "X-Project-Id")

#: Records predating the is_active migration have no flag; absent means active.
_ACTIVE = {"$ne": False}


class TenantContextError(HTTPException):
    """400/403 carrying a machine-readable ``code`` for the client."""

    def __init__(self, *, status_code: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message, **extra})
        self.code = code


def _as_id(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _object_id_candidates(value: str) -> list:
    from bson import ObjectId

    candidates: list = [str(value)]
    try:
        candidates.append(ObjectId(str(value)))
    except Exception:
        pass
    return candidates


def _first_header(request: Request, names: tuple[str, ...]) -> str:
    for name in names:
        value = request.headers.get(name)
        if value and value.strip():
            return value.strip()
    return ""


@dataclass
class ActiveScope:
    """A validated selection. ``project_id is None`` means "no project selected"."""

    db: Any
    user: Any
    organization_id: Optional[str]
    project_id: Optional[str]

    @property
    def has_project(self) -> bool:
        return bool(self.project_id)

    def require_selection(self) -> str:
        """Record-level and mutating operations need an explicit project."""
        if not self.project_id:
            raise TenantContextError(
                status_code=status.HTTP_400_BAD_REQUEST,
                code=SELECTION_REQUIRED,
                message="Select a project in the navbar to continue.",
            )
        return self.project_id

    async def require_project(self, project_id: Any, organization_id: Any = None) -> None:
        """Refuse a target outside the selected project (400 when nothing is selected)."""
        selected = self.require_selection()
        target = _as_id(project_id)
        target_org = _as_id(organization_id)
        if target != selected or (target_org and self.organization_id and target_org != self.organization_id):
            # The audit keeps the target's ids; the response discloses none, so a
            # non-member who guesses a record id learns nothing about its project.
            raise await _forbid(
                self.db,
                self.user,
                "This record is not available in the project selected in the navbar.",
                target_org or self.organization_id or "",
                target,
                disclose=False,
            )

    async def require_record(self, record: Mapping[str, Any], *, allow_unscoped: bool = False) -> None:
        """Refuse a record outside the selected project (400 when none is selected).

        ``allow_unscoped`` is for registers that must keep legacy rows with NO
        project readable and removable (Variation, CL-2): such a row is in no
        other project, so only its organisation is held to the selection - and
        the membership/permission gate still decides. A row that names a project
        is always held to it.
        """
        project_id = record.get("project_id") or record.get("projectId")
        organization_id = record.get("organization_id") or record.get("organizationId")
        if allow_unscoped and not _as_id(project_id):
            self.require_selection()
            target_org = _as_id(organization_id)
            if target_org and self.organization_id and target_org != self.organization_id:
                raise await _forbid(
                    self.db,
                    self.user,
                    "This record is not available in the organisation selected in the navbar.",
                    target_org,
                    "",
                    disclose=False,
                )
            return
        await self.require_project(project_id, organization_id)

    async def require_organization(self, organization_id: Any) -> None:
        """Refuse a listing filter that leaves the selected organisation."""
        target = _as_id(organization_id)
        if target and self.organization_id and target != self.organization_id:
            raise await _forbid(
                self.db,
                self.user,
                "This organisation is not the one selected in the navbar.",
                target,
                "",
                disclose=False,
            )

    def narrows(self, project_id: Any) -> bool:
        """For listings: whether a row in ``project_id`` is visible under the selection."""
        return not self.project_id or _as_id(project_id) == self.project_id

    async def list_filters(
        self, organization_id: Any = None, project_id: Any = None
    ) -> tuple[Optional[str], Optional[str]]:
        """The ``(organization_id, project_id)`` filters a listing must use.

        A filter may narrow the selection, never leave it: a project filter other
        than the selected project, or an organisation filter other than the
        selected organisation, is 403 ``context_forbidden``. With a project
        selected the listing is pinned to it; with only an organisation selected
        it is pinned to that organisation; with nothing selected the caller's
        filters pass through unchanged and ``build_scope_query`` bounds the rows.
        """
        org = _as_id(organization_id) or None
        project = _as_id(project_id) or None
        if not self.has_project:
            if self.organization_id:
                await self.require_organization(org)
                return self.organization_id, project
            return org, project
        if project or org:
            await self.require_project(project or self.project_id, org)
        return self.organization_id, self.project_id


async def _audit_rejection(db: Any, user: Any, reason: str, organization_id: str, project_id: str) -> None:
    """Record the attempt. An audit failure is logged and never masks the refusal."""
    try:
        from ..services.audit_event_service import AuditEventService

        await AuditEventService(db).emit(
            action="tenant.context.rejected",
            actor_id=_as_id(getattr(user, "id", "")),
            resource_type="tenant_context",
            organization_id=organization_id or None,
            project_id=project_id or None,
            result="denied",
            reason=reason,
        )
    except Exception:  # noqa: BLE001 - auditing must never turn a 403 into a 500
        logger.warning("failed to audit tenant context rejection", exc_info=True)


async def _forbid(
    db: Any,
    user: Any,
    message: str,
    organization_id: str,
    project_id: str,
    *,
    disclose: bool = True,
    audit_reason: Optional[str] = None,
) -> TenantContextError:
    """403 ``context_forbidden``. ``disclose`` echoes the ids - only ever the ones the client sent.

    ``audit_reason`` keeps the precise cause in the log and the audit event while the
    response says one thing, so a principal that may not use a selection learns neither
    whether the project exists nor which organisation owns it.
    """
    logger.warning(
        "tenant context rejected: actor=%s org=%s project=%s reason=%s",
        _as_id(getattr(user, "id", "")),
        organization_id or "-",
        project_id or "-",
        audit_reason or message,
    )
    await _audit_rejection(db, user, audit_reason or message, organization_id, project_id)
    echoed = {"organization_id": organization_id or None, "project_id": project_id or None} if disclose else {}
    return TenantContextError(status_code=status.HTTP_403_FORBIDDEN, code=CONTEXT_FORBIDDEN, message=message, **echoed)


async def resolve_active_scope(db: Any, user: Any, *, organization_id: Any, project_id: Any) -> ActiveScope:
    """Validate a requested selection against the principal's real scope."""
    from ..services.scope_service import ScopeService

    requested_org = _as_id(organization_id)
    requested_project = _as_id(project_id)
    scope = ScopeService(db)

    if not requested_project:
        if requested_org and not scope.is_superadmin(user):
            if not await scope.is_client_scope_allowed(user, organization_id=requested_org):
                raise await _forbid(db, user, "This organisation is not accessible to your account.", requested_org, "")
        return ActiveScope(db, user, requested_org or None, None)

    # One message for every unusable selection - absent, deactivated, owned by another
    # organisation, or outside the principal's scope. Varying it would let any
    # authenticated caller probe project ids and their owning organisation.
    unusable = "This project is not available to your account."
    project = await db.projects.find_one(
        {"_id": {"$in": _object_id_candidates(requested_project)}, "is_active": _ACTIVE}
    )
    if not project:
        raise await _forbid(
            db, user, unusable, requested_org, requested_project,
            audit_reason="The project is unavailable or has been deactivated.",
        )
    project_org = _as_id(project.get("organization_id") or project.get("organizationId"))
    if not project_org or (requested_org and requested_org != project_org):
        raise await _forbid(
            db, user, unusable, requested_org, requested_project,
            audit_reason="The selected project does not belong to the selected organisation.",
        )
    if not scope.is_superadmin(user) and not await scope.is_client_scope_allowed(
        user, organization_id=project_org, project_id=requested_project
    ):
        raise await _forbid(
            db, user, unusable, requested_org, requested_project,
            audit_reason="The project is not accessible to this account.",
        )
    return ActiveScope(db, user, project_org, requested_project)


async def active_scope(
    request: Request,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> ActiveScope:
    """FastAPI dependency: the validated navbar selection of this request."""
    return await resolve_active_scope(
        db,
        current_user,
        organization_id=_first_header(request, ORG_HEADERS),
        project_id=_first_header(request, PROJECT_HEADERS),
    )


@dataclass
class RequestedScope:
    """The raw selection, validated only on demand.

    Shared routes (``/api/entities/...``, ``/api/documents/{id}/entity-links``) serve
    every relationship target; only selection-bound targets (``active_scope_enforced``)
    resolve it, so an unrelated target is never refused over a header.
    """

    db: Any
    user: Any
    organization_id: str
    project_id: str

    async def resolve(self) -> ActiveScope:
        return await resolve_active_scope(
            self.db, self.user, organization_id=self.organization_id, project_id=self.project_id
        )


async def requested_scope(
    request: Request,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> RequestedScope:
    return RequestedScope(
        db, current_user, _first_header(request, ORG_HEADERS), _first_header(request, PROJECT_HEADERS)
    )
