"""Where a role's permissions count (SEC-1).

Before this, every permission on every role a user held was pooled into one flat
set: a custom role created for organisation A and held by a member of
organisation B granted its permissions in B, and a project role granted in every
project. A permission held through a role now counts only where that role was
granted:

- **Built-in roles** (Super Admin, Super User, organisation and project
  administrator and user, the ContraClaim expert and reviewer keys, and any role
  marked ``is_system`` or ``scope: system``) keep their existing meaning: their
  permissions apply wherever the user's own membership reaches, and the scope
  checks (``ScopeService``, ``authorize_scope``, ``build_scope_query``) bound them
  as before.
- **A custom role with an organisation** counts only for a user who is a member
  of that organisation (``organization_id``, ``organizations`` or an active
  ``organization_memberships`` row), and, when the request names an organisation,
  only in that organisation.
- **A custom role with a project** counts only for a user assigned to that project
  (``projects`` or an active ``project_memberships`` row), only if the project
  belongs to the role's organisation when it names one, and, when the request
  names a project, only in that project.
- **A custom role with neither** is a platform role: it counts only for a user
  with no organisation (a platform user); held by an organisation's user it
  grants nothing.

A grant is evaluated per role, never from a pooled set, so the cached form (see
``PermissionService.user_has_permission``) keeps each role's names with the
scope they apply in.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from ..core.permissions import REVIEWER_ROLE_KEYS
from ..core.role_reference import normalize_role_key

#: Role keys whose meaning is defined by the code, not by their document.
BUILT_IN_ROLE_KEYS: frozenset = frozenset(
    {
        "superadmin",
        "superuser",
        "orgadmin",
        "orguser",
        "projectadmin",
        "projectuser",
        "user",
        "contraclaim_expert_drafter",
        "contraclaim_expert_reviewer",
        "contraclaim_drafting_manager",
        "contract_expert",
        # Every role seeded by initial_data/default_roles.py (tests/test_role_grants.py
        # keeps this list and the seed in step).
        "contractmgr_org",
        "doccontroller",
        "reporter",
        "settings_manager",
        "limited_user",
        "contraclaim_billing_admin",
    }
) | REVIEWER_ROLE_KEYS



def _key(value: Any) -> str:
    text = str(value or "").strip().lower()
    return normalize_role_key(text) if text else ""


def role_is_built_in(document: Mapping[str, Any]) -> bool:
    """A role whose meaning the code defines: marked system, or stored under a built-in key.

    Never by name (independent review R-1, 2026-11-19): a custom role named
    ``legal_reviewer`` or ``Organization Admin`` is a custom role, bounded like any other.
    Role creation and renaming refuse built-in names; P-05 counts the stored ones.
    """
    if document.get("is_system") is True:
        return True
    scope = document.get("scope")
    if isinstance(scope, str) and scope.strip().lower() == "system":
        return True
    # Not core.role_reference.role_is_system: with no stored scope it falls back to the name.
    return _key(document.get("_id")) in BUILT_IN_ROLE_KEYS


def _text(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


async def user_memberships(db: Any, user_doc: Mapping[str, Any]) -> Tuple[Set[str], Set[str]]:
    """The organisations and projects a user belongs to, as the scope checks read them."""
    user_id = str(user_doc.get("_id") or "")
    organisations: Set[str] = set()
    if _text(user_doc.get("organization_id")):
        organisations.add(str(user_doc["organization_id"]))
    organisations.update(str(org) for org in (user_doc.get("organizations") or []) if _text(org))
    projects: Set[str] = {str(project) for project in (user_doc.get("projects") or []) if _text(project)}
    if user_id:
        active = {"user_id": user_id, "status": {"$in": ["active", None]}}
        # A database handle without these collections (a narrow test double) has no
        # membership rows; a real one always has them, and a read error propagates.
        org_rows = getattr(db, "organization_memberships", None)
        project_rows = getattr(db, "project_memberships", None)
        if org_rows is not None:
            for row in await org_rows.find(active, {"organization_id": 1}).to_list(length=None):
                if _text(row.get("organization_id")):
                    organisations.add(str(row["organization_id"]))
        if project_rows is not None:
            for row in await project_rows.find(active, {"project_id": 1}).to_list(length=None):
                if _text(row.get("project_id")):
                    projects.add(str(row["project_id"]))
    return organisations, projects


_INACTIVE = "\x00inactive-project"


async def _project_organisation(db: Any, project_id: str, cache: Dict[str, Optional[str]]) -> Optional[str]:
    if project_id in cache:
        return cache[project_id]
    candidates: List[Any] = [project_id]
    try:
        from bson import ObjectId

        candidates.append(ObjectId(project_id))
    except Exception:
        pass
    project = await db.projects.find_one({"_id": {"$in": candidates}}, {"organization_id": 1, "organizationId": 1, "is_active": 1})
    organisation = _text((project or {}).get("organization_id") or (project or {}).get("organizationId"))
    if project is not None and project.get("is_active") is False:
        organisation = _INACTIVE  # review: a project role stops counting once its project is inactive
    cache[project_id] = organisation
    return organisation


async def role_grant_scope(
    db: Any,
    role: Mapping[str, Any],
    memberships: Tuple[Set[str], Set[str]],
    project_cache: Dict[str, Optional[str]],
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """``(applies, scope)``. Built-in: ``(True, None)`` (no role-level bound). Custom:
    the organisation/project it counts in, or ``(False, None)`` when the user is not
    a member where the role was granted."""
    if role_is_built_in(role):
        return True, None
    organisations, projects = memberships
    role_org = _text(role.get("organization_id"))
    role_project = _text(role.get("project_id"))
    if role_project:
        if role_project not in projects:
            return False, None
        project_org = await _project_organisation(db, role_project, project_cache)
        if project_org is None or project_org == _INACTIVE:
            return False, None
        if role_org and project_org != role_org:
            return False, None
        return True, {"organization_id": project_org, "project_id": role_project}
    if role_org:
        if role_org not in organisations:
            return False, None
        return True, {"organization_id": role_org, "project_id": None}
    # A platform role: only for a user bound to no organisation.
    if organisations:
        return False, None
    return True, {"organization_id": None, "project_id": None, "platform": True}


def grant_applies(
    scope: Optional[Mapping[str, Any]],
    *,
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
    primary_organization_id: Optional[str] = None,
) -> bool:
    """Whether a grant with this scope counts for a request in (organization_id, project_id).

    - Built-in (``scope`` None) and platform grants: as before.
    - A request naming a project: ``organization_id`` must be that project's own
      organisation, resolved by the caller (``PermissionService.resolve_request_scope``);
      without it a custom grant is refused (SEC-1 boundary).
    - A project grant counts only for a request naming that project; never for its
      whole organisation (review R-3).
    - A request naming nothing (a permission-only gate): a custom grant counts only in
      the principal's primary organisation (review R-4), never in another organisation
      the user is also listed in.
    """
    if scope is None:
        return True
    if scope.get("platform"):
        return True
    if not organization_id and not project_id:
        # Organisation roles of the primary organisation only: a project role needs its project named.
        return (
            bool(primary_organization_id)
            and not scope.get("project_id")
            and str(scope.get("organization_id")) == str(primary_organization_id)
        )
    # A project named without its resolved organisation compares "None" here and fails closed.
    if str(organization_id) != str(scope.get("organization_id")):
        return False
    if scope.get("project_id"):
        return bool(project_id) and str(project_id) == str(scope.get("project_id"))
    return True


def applicable_names(
    grants: Iterable[Mapping[str, Any]],
    *,
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
    primary_organization_id: Optional[str] = None,
) -> Set[str]:
    names: Set[str] = set()
    for grant in grants:
        if grant_applies(
            grant.get("scope"),
            organization_id=organization_id,
            project_id=project_id,
            primary_organization_id=primary_organization_id,
        ):
            names.update(grant.get("names") or [])
    return names


__all__ = [
    "BUILT_IN_ROLE_KEYS",
    "applicable_names",
    "grant_applies",
    "role_grant_scope",
    "role_is_built_in",
    "user_memberships",
]
