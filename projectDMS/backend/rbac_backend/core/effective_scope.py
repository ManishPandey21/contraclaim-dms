"""The one place organisation/project access is decided.

    Effective Data Scope = User Entitlement ∩ Validated Navbar Selection

Entitlement is what the authenticated user's role and assignments permit. The
selection is the Organisation/Project the navbar currently has active, already
validated against that entitlement by ``TenantContextResolver`` before it ever
reaches ``CurrentUser``. This module intersects the two and hands back a filter.

Two rules give the object its shape:

* A selection may only ever **narrow**. Intersecting with entitlement makes that
  structural rather than something each call site has to remember.
* ``superadmin`` means *entitlement may be platform-wide*, *not* "skip scoping".
  A superadmin who has selected Organisation A is working in Organisation A, and
  a query that ignores the selection is a defect. Historically several call
  sites carried ``if superadmin: <no filter>``; that class of bypass is why the
  Document Library returned every organisation's documents while a single
  organisation was selected.

Consume this instead of rebuilding tenant rules per router, service, exporter or
retrieval backend. ``build_scope_query`` in ``core.security`` is a thin wrapper
over ``mongo_filter`` and stays the convenient entry point for Mongo list
endpoints; reach for ``EffectiveScope`` directly when the target is a vector
store, a graph query, an export or a background job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, Optional, Sequence

# Roles whose entitlement is not bounded by their own organisation record.
GLOBAL_ROLES = frozenset({"superadmin", "superuser"})
ORG_ROLES = frozenset({"orgadmin", "orguser"})
PROJECT_ROLES = frozenset({"projectadmin", "projectuser"})
# Experts are allocated to projects and work across organisation boundaries by
# design, so they are bounded by their project assignments alone and an
# organisation selection does not apply to them.
EXPERT_ROLES = frozenset({
    "contraclaim_expert_drafter",
    "contraclaim_expert_reviewer",
    "contraclaim_drafting_manager",
    "contract_expert",
})

# ``None`` for an authorised-id set means "not bounded" (platform-wide), which is
# different from an empty set, meaning "bounded to nothing" -> deny.
UNBOUNDED: Optional[FrozenSet[str]] = None


def _clean(values: Any) -> FrozenSet[str]:
    if not values:
        return frozenset()
    if isinstance(values, (str, bytes)):
        values = [values]
    return frozenset(str(v).strip() for v in values if v is not None and str(v).strip())


def role_names(user: Any) -> FrozenSet[str]:
    return frozenset(str(r).strip().lower() for r in (getattr(user, "roles", None) or []) if r)


def role_tier(roles: FrozenSet[str]) -> str:
    if roles & GLOBAL_ROLES:
        return "global"
    if roles & ORG_ROLES:
        return "org"
    if roles & PROJECT_ROLES:
        return "project"
    if roles & EXPERT_ROLES:
        return "expert"
    return "restricted"


@dataclass(frozen=True)
class EffectiveScope:
    """A resolved working scope. Holding one means the ids in it are safe to use."""

    user_id: Optional[str]
    roles: FrozenSet[str]
    tier: str
    # None => unbounded at this level; empty frozenset => no reach at all.
    authorized_org_ids: Optional[FrozenSet[str]]
    authorized_project_ids: Optional[FrozenSet[str]]
    # The navbar working context.
    selected_org_id: Optional[str]
    selected_project_id: Optional[str]
    # An explicit route/query/body filter. A *record-selection criterion within*
    # the working context, never a way to switch to another one.
    requested_org_id: Optional[str] = None
    requested_project_id: Optional[str] = None

    # -- construction ---------------------------------------------------

    @classmethod
    def resolve(
        cls,
        current_user: Any,
        *,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> "EffectiveScope":
        """Resolve the working scope, then narrow it by any explicit filter.

            Final Data Scope = EffectiveScope ∩ Explicit Resource Filter
            EffectiveScope   = RBAC Entitlement ∩ Validated Navbar Selection

        ``organization_id``/``project_id`` are an explicit filter from the call
        site -- a query string, route parameter, request body or export filter.
        They may *narrow* the working context and nothing more. A filter naming
        an organisation the caller is entitled to but is not currently working
        in resolves to nothing rather than switching context to it, because
        otherwise a single request could disagree with every other module on
        screen.
        """
        roles = role_names(current_user)
        tier = role_tier(roles)

        actor_org = getattr(current_user, "organization_id", None)
        actor_project = getattr(current_user, "project_id", None)

        # The navbar selection alone; the explicit filter is kept separate so
        # it can be intersected rather than substituted.
        selected_project = str(actor_project) if actor_project else None

        assigned_orgs = _clean(getattr(current_user, "organizations", None))
        assigned_projects = _clean(getattr(current_user, "projects", None))

        if tier == "global":
            # For a global role the actor's organization_id is the *selection*
            # (get_current_user clears the home organisation for these roles),
            # so it is read as one rather than as an entitlement bound.
            selected_org = str(actor_org) if actor_org else None
            if "superadmin" in roles:
                authorized_orgs: Optional[FrozenSet[str]] = UNBOUNDED
                authorized_projects: Optional[FrozenSet[str]] = UNBOUNDED
            else:
                # A Super User is bounded by their assignments; the selection
                # narrows within them.
                authorized_orgs = assigned_orgs | (_clean([actor_org]) if actor_org else frozenset())
                authorized_projects = assigned_projects or UNBOUNDED
        elif tier == "expert":
            # Allocated across organisations: bounded by project assignments
            # only. An organisation selection is not applicable and is ignored
            # rather than denied, so an expert keeps working when the navbar
            # happens to hold one.
            authorized_orgs = UNBOUNDED
            authorized_projects = assigned_projects
            selected_org = None
        else:
            # Tenant-bound roles: organization_id is the account's own
            # organisation and is an entitlement bound, not a selection. The
            # server pins them to it.
            own = _clean([actor_org]) if actor_org else frozenset()
            # A tenant-bound account's organisation is an entitlement bound, not
            # a selection; the server pins them to it.
            selected_org = None
            if tier == "project":
                # A project-tier account is bounded by its assignments. When the
                # account carries no organisation, the project assignments alone
                # bound it -- denying instead would lock out rows that predate
                # organisation stamping.
                authorized_orgs = own or assigned_orgs or UNBOUNDED
                authorized_projects = assigned_projects
            else:
                # An org-tier account with no organisation has no reach at all.
                authorized_orgs = own or assigned_orgs
                authorized_projects = UNBOUNDED

        return cls(
            user_id=str(getattr(current_user, "id", "") or "") or None,
            roles=roles,
            tier=tier,
            authorized_org_ids=authorized_orgs,
            authorized_project_ids=authorized_projects,
            selected_org_id=selected_org,
            selected_project_id=selected_project,
            requested_org_id=str(organization_id) if organization_id else None,
            requested_project_id=str(project_id) if project_id else None,
        )

    # -- shape ----------------------------------------------------------

    @property
    def organization_mode(self) -> str:
        return "selected" if self.selected_org_id else "all"

    @property
    def project_mode(self) -> str:
        return "selected" if self.selected_project_id else "all"

    @property
    def is_platform_wide(self) -> bool:
        """True only when nothing bounds the scope -- no entitlement limit and
        no active selection."""
        return (
            self.authorized_org_ids is UNBOUNDED
            and not self.selected_org_id
            and not self.selected_project_id
        )

    @property
    def has_no_reach(self) -> bool:
        """The caller can see nothing at all, regardless of what they asked for.

        Distinct from asking for something outside an otherwise-real scope: that
        is a 403, this is simply an empty result.
        """
        if self.authorized_org_ids is not UNBOUNDED and not self.authorized_org_ids:
            return True
        return self.tier in {"project", "expert"} and not self.authorized_project_ids

    @property
    def is_denied(self) -> bool:
        """A caller with no reach, or one whose selection fell outside it."""
        if self.authorized_org_ids is not UNBOUNDED and not self.authorized_org_ids:
            return True
        if self.tier in {"project", "expert"} and not self.authorized_project_ids:
            return True
        if self.tier == "project" and self.selected_org_id and self.authorized_org_ids is UNBOUNDED:
            # Bounded by project assignments only: the account has no
            # organisation of its own, so it cannot claim one either.
            return True
        if self.selected_org_id and self.authorized_org_ids is not UNBOUNDED:
            if self.selected_org_id not in self.authorized_org_ids:
                return True
        if self.selected_project_id and self.authorized_project_ids is not UNBOUNDED:
            if self.selected_project_id not in (self.authorized_project_ids or frozenset()):
                return True
        # An explicit filter that intersects the working context to nothing.
        # Covers both "not yours" and "yours, but not what you are working in".
        orgs = self.effective_org_ids()
        if orgs is not UNBOUNDED and not orgs:
            return True
        projects = self.effective_project_ids()
        if self.requested_project_id and projects is not UNBOUNDED and not projects:
            return True
        return False

    # -- entitlement questions -------------------------------------------

    def permits_organization(self, organization_id: str) -> bool:
        """Is this organisation within the caller's entitlement (ignoring the
        current selection)? Use to tell "not yours" from "not selected"."""
        if self.authorized_org_ids is UNBOUNDED:
            return True
        return str(organization_id) in self.authorized_org_ids

    def permits_project(self, project_id: str) -> bool:
        if self.authorized_project_ids is UNBOUNDED:
            return True
        return str(project_id) in (self.authorized_project_ids or frozenset())

    def permits_within_selection(
        self,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> bool:
        """Does an explicitly claimed scope fall inside the effective one?

        For surfaces where the caller names the scope in a request body or query
        string -- vector and graph retrieval, exports, background-job payloads --
        rather than having it applied to a database query for them. Entitlement
        is the outer bound; this is the narrowing half, so a caller working in
        Organisation A cannot ask a retrieval backend for Organisation B.
        """
        if self.is_denied:
            return False
        orgs = self.effective_org_ids()
        if organization_id and orgs is not UNBOUNDED:
            if str(organization_id) not in orgs:
                return False
        projects = self.effective_project_ids()
        if project_id and projects is not UNBOUNDED and projects:
            if str(project_id) not in projects:
                return False
        return True

    # -- the intersection -----------------------------------------------

    def working_org_ids(self) -> Optional[FrozenSet[str]]:
        """Entitlement ∩ navbar selection, before any explicit filter."""
        if self.selected_org_id:
            return frozenset({self.selected_org_id})
        return self.authorized_org_ids

    def working_project_ids(self) -> Optional[FrozenSet[str]]:
        if self.selected_project_id:
            return frozenset({self.selected_project_id})
        return self.authorized_project_ids

    def effective_org_ids(self) -> Optional[FrozenSet[str]]:
        """The working scope narrowed by any explicit filter. None = unbounded.

        The filter intersects; it cannot substitute. Naming an organisation
        outside the working context yields the empty set, which ``is_denied``
        reports and ``mongo_filter`` renders as a deny-all.
        """
        working = self.working_org_ids()
        if not self.requested_org_id:
            return working
        requested = frozenset({self.requested_org_id})
        return requested if working is UNBOUNDED else (working & requested)

    def effective_project_ids(self) -> Optional[FrozenSet[str]]:
        working = self.working_project_ids()
        if not self.requested_project_id:
            return working
        requested = frozenset({self.requested_project_id})
        return requested if working is UNBOUNDED else (working & requested)

    # -- consumption ----------------------------------------------------

    def mongo_filter(
        self,
        org_field: str = "organization_id",
        project_field: Optional[str] = "project_id",
        id_field: str = "_id",
        expand: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """The tenant predicate for a Mongo query.

        ``expand`` optionally widens each id into its ObjectId/string variants,
        which stored documents are inconsistent about.
        """
        if self.is_denied:
            return {id_field: {"$in": []}}

        def _values(ids: FrozenSet[str]) -> Any:
            listed = sorted(ids)
            return expand(listed) if expand else listed

        query: Dict[str, Any] = {}

        orgs = self.effective_org_ids()
        if orgs is not UNBOUNDED:
            query[org_field] = {"$in": _values(orgs)}

        projects = self.effective_project_ids()
        if projects is not UNBOUNDED and projects:
            target = project_field or id_field
            query[target] = {"$in": _values(projects)}

        return query

    def ownership_filter(
        self,
        org_field: str = "organization_id",
        project_field: str = "project_id",
    ) -> Dict[str, Any]:
        """Filter for collections whose records are owned at differing levels.

        ``mongo_filter`` assumes every record is project-owned, which is wrong
        for things like notifications: a subscription-expiry notice belongs to
        an organisation and to no project, and narrowing to a project would hide
        exactly the messages that matter most.

        Three ownership levels, each judged on its own terms:

        * **platform** -- no organisation, no project. Belongs to no tenant, so
          the tenant scope does not exclude it.
        * **organisation** -- an organisation, no project. Visible whenever that
          organisation is in scope, *including* while a specific project is
          selected, because it applies to the whole organisation.
        * **project** -- both. Visible only when the project is in scope.

        So Organisation A / Project A1 shows organisation-A-wide notices and
        project-A1 notices, and never project A2 or anything under B.
        """
        if self.is_denied:
            return {"_id": {"$in": []}}

        orgs = self.effective_org_ids()
        projects = self.effective_project_ids()
        if orgs is UNBOUNDED and projects is UNBOUNDED:
            return {}  # platform-wide: every record is permitted

        def _absent(field: str) -> Dict[str, Any]:
            # Stored either as null or omitted entirely, depending on writer.
            return {"$or": [{field: None}, {field: {"$exists": False}}]}

        org_predicate: Dict[str, Any] = (
            {"$exists": True} if orgs is UNBOUNDED else {"$in": sorted(orgs)}
        )
        project_predicate: Dict[str, Any] = (
            {"$exists": True} if projects is UNBOUNDED else {"$in": sorted(projects or [])}
        )

        return {
            "$or": [
                # platform-level
                {"$and": [_absent(org_field), _absent(project_field)]},
                # organisation-level: survives a project selection
                {"$and": [{org_field: org_predicate}, _absent(project_field)]},
                # project-level
                {"$and": [{org_field: org_predicate}, {project_field: project_predicate}]},
            ]
        }

    def describe(self) -> Dict[str, Any]:
        """Audit/debug view. Safe to log -- ids only, no record content."""
        return {
            "user_id": self.user_id,
            "roles": sorted(self.roles),
            "tier": self.tier,
            "organisation_mode": self.organization_mode,
            "project_mode": self.project_mode,
            "selected_organisation": self.selected_org_id,
            "selected_project": self.selected_project_id,
            "effective_organisations": (
                None if self.effective_org_ids() is UNBOUNDED else sorted(self.effective_org_ids())
            ),
            "effective_projects": (
                None
                if self.effective_project_ids() is UNBOUNDED
                else sorted(self.effective_project_ids() or [])
            ),
            "is_platform_wide": self.is_platform_wide,
            "is_denied": self.is_denied,
        }
