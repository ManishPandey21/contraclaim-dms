"""M1 enforcement: built-in roles count only where they are assigned (``BUILT_IN_ROLE_ASSIGNMENTS=on``).

Security owner decision S-4 (2026-11-25): M1 is a production release blocker and its
remediation is approved for implementation and verification. Owner direction the same day:
start the M1 fix. This module is that fix, behind the existing flag:

- ``off`` (the default) and ``shadow``: nothing here changes a decision. Bare built-in role
  keys keep their meaning wherever the user's membership reaches, exactly as before.
- ``on``: a tenant built-in role (``orgadmin``, ``orguser``, ``projectadmin``,
  ``projectuser``, the billing administrator, every other tenant built-in key) is
  **assigned in the user's primary organisation** (``users.organization_id``) and nowhere
  else, plus any explicit row in ``role_assignments`` that names it
  (``{user_id, role_key, organization_id, project_id, status: "active"}``). A secondary
  organisation the user is merely listed in, or holds a membership row for, gets no
  built-in authority from the bare key. ``superadmin`` / ``superuser`` keep their platform
  meaning. Custom roles are unchanged (SEC-1). A tenant built-in role held by a user with
  no primary organisation is assigned nowhere.

Within the primary organisation the meaning is unchanged: a project-tier role still counts
at the organisation's level for permissions, and its projects are still bounded by the
membership checks (``ScopeService``, ``build_scope_query``), as today. M1 is about reach
into *other* organisations; that is what ``on`` removes.

Nothing here writes a role, a user, a membership or an assignment. Activation needs the
§5 plan of ``docs/M1_BUILT_IN_ROLE_SCOPING.md``: P-05, an owner decision for every
assignment that needs one, assignments written (grant before revoke), shadow on staging,
then ``on`` on staging with two deployed passes, a security review and a release decision.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

#: Role keys with platform meaning: never bound to an organisation by this module.
PLATFORM_ROLE_KEYS = frozenset({"superadmin", "superuser"})
#: The only keys a project-level assignment row can confer (an organisation-tier key needs an
#: organisation-level row: a project row never makes anyone organisation-wide elsewhere).
PROJECT_TIER_ROLE_KEYS = frozenset({"projectadmin", "projectuser"})
#: Where explicit assignments are read from (written only by an approved migration).
ASSIGNMENTS = "role_assignments"


def mode() -> str:
    from ..core.config import settings

    return str(getattr(settings, "BUILT_IN_ROLE_ASSIGNMENTS", "off") or "off").strip().lower()


def enforced() -> bool:
    return mode() == "on"


def _text(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def primary_organisation_of(principal: Any) -> Optional[str]:
    """The primary organisation of a user document or a ``CurrentUser``."""
    value = principal.get("organization_id") if isinstance(principal, dict) else getattr(principal, "organization_id", None)
    return _text(value)


def _user_id_of(principal: Any) -> Optional[str]:
    if isinstance(principal, dict):
        return _text(principal.get("_id") or principal.get("id"))
    return _text(getattr(principal, "id", None))


async def explicit_assignments(db: Any, principal: Any, role_keys: Iterable[str]) -> List[Dict[str, Optional[str]]]:
    """Active ``role_assignments`` rows for these keys: ``{role_key, organization_id, project_id}``."""
    user_id = _user_id_of(principal)
    keys = sorted({str(key).strip().lower() for key in role_keys if _text(key)})
    rows = getattr(db, ASSIGNMENTS, None) if db is not None else None
    if not user_id or not keys or rows is None:
        return []
    found: List[Dict[str, Optional[str]]] = []
    for row in await rows.find(
        {"user_id": user_id, "role_key": {"$in": keys}, "status": "active"},
        {"role_key": 1, "organization_id": 1, "project_id": 1},
    ).to_list(length=500):
        found.append({
            "role_key": str(row.get("role_key") or "").strip().lower(),
            "organization_id": _text(row.get("organization_id")),
            "project_id": _text(row.get("project_id")),
        })
    return found


async def _project_organisation(db: Any, project_id: str, cache: Dict[str, Optional[str]]) -> Optional[str]:
    if project_id not in cache:
        candidates: List[Any] = [project_id]
        try:
            from bson import ObjectId

            if ObjectId.is_valid(project_id):
                candidates.append(ObjectId(project_id))
        except Exception:
            pass
        row = await db.projects.find_one({"_id": {"$in": candidates}}, {"organization_id": 1, "organizationId": 1})
        cache[project_id] = _text((row or {}).get("organization_id") or (row or {}).get("organizationId"))
    return cache[project_id]


async def project_organisation(db: Any, project_id: str) -> Optional[str]:
    """The organisation a project belongs to, or ``None`` when it cannot be read."""
    return await _project_organisation(db, str(project_id), {})


async def grant_scopes(
    db: Any, principal: Any, role_key: str, project_cache: Optional[Dict[str, Optional[str]]] = None
) -> List[Dict[str, Any]]:
    """The scopes a tenant built-in role's permissions count in under ``on``.

    The primary organisation at organisation level, plus each explicit assignment: an
    organisation row at organisation level, a project row (project-tier keys only) on that
    project only (its own organisation, read from ``projects``; an unreadable project counts
    nowhere)."""
    key = str(role_key or "").strip().lower()
    scopes: List[Dict[str, Any]] = []
    primary = primary_organisation_of(principal)
    if primary:
        scopes.append({"organization_id": primary, "project_id": None, "built_in": key})
    cache = project_cache if project_cache is not None else {}
    for row in await explicit_assignments(db, principal, [key]):
        if row["project_id"]:
            if key not in PROJECT_TIER_ROLE_KEYS:
                continue
            owner = await _project_organisation(db, row["project_id"], cache)
            if owner and (not row["organization_id"] or row["organization_id"] == owner):
                scopes.append({"organization_id": owner, "project_id": row["project_id"], "built_in": key})
        elif row["organization_id"] and row["organization_id"] != primary:
            scopes.append({"organization_id": row["organization_id"], "project_id": None, "built_in": key})
    return scopes


async def role_names_in(db: Any, principal: Any, names: Set[str], organization_id: Optional[str]) -> Set[str]:
    """The role names that count in ``organization_id`` (``None``: the principal's own context).

    Flag not ``on``: ``names`` unchanged. ``on``: platform keys and non-built-in entries are
    kept; a tenant built-in key counts in the primary organisation, or in another
    organisation only through an explicit assignment there (organisation row, or a project
    row of a project of that organisation). With no primary organisation a tenant built-in
    key counts nowhere."""
    if not enforced():
        return set(names)
    from .role_grants import BUILT_IN_ROLE_KEYS

    tenant_built_in = {name for name in names if name in BUILT_IN_ROLE_KEYS and name not in PLATFORM_ROLE_KEYS}
    kept = set(names) - tenant_built_in
    primary = primary_organisation_of(principal)
    target = _text(organization_id) or primary
    if not tenant_built_in or not target:
        return kept
    if target == primary:
        return kept | tenant_built_in
    cache: Dict[str, Optional[str]] = {}
    for row in await explicit_assignments(db, principal, tenant_built_in):
        if row["role_key"] not in tenant_built_in:
            continue
        if row["project_id"]:
            if row["role_key"] not in PROJECT_TIER_ROLE_KEYS:
                continue
            if await _project_organisation(db, row["project_id"], cache) == target and (
                not row["organization_id"] or row["organization_id"] == target
            ):
                kept.add(row["role_key"])
        elif row["organization_id"] == target:
            kept.add(row["role_key"])
    return kept


__all__ = [
    "ASSIGNMENTS",
    "PLATFORM_ROLE_KEYS",
    "PROJECT_TIER_ROLE_KEYS",
    "enforced",
    "explicit_assignments",
    "grant_scopes",
    "mode",
    "primary_organisation_of",
    "project_organisation",
    "role_names_in",
]
