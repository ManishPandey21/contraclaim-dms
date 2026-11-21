"""M1 shadow evaluator: built-in roles scoped to their assignment (workstream 5, 2026-11-21).

**Shadow only.** With ``BUILT_IN_ROLE_ASSIGNMENTS=shadow`` every decision that
``PolicyService.authorize`` *allowed* is evaluated a second time under the proposed model of
``docs/M1_BUILT_IN_ROLE_SCOPING.md`` §4, and the comparison is counted. Nothing it computes
reaches a response: it runs after the decision, any error in it is swallowed and logged, and
``off`` (the default) never calls it. No role, user or membership is written or migrated.

**Proposed model (not enforced).** A built-in role counts only where it is *assigned*:

- an organisation-tier built-in role (``orgadmin``, ``orguser``, the billing administrator and
  every other tenant built-in key) is assigned in the user's primary organisation;
- a project-tier role (``projectadmin``, ``projectuser``) is assigned on each project the user
  is on *within* the primary organisation;
- plus any explicit row in ``role_assignments`` (``user_id``, ``role_key``,
  ``organization_id``, ``project_id``, ``status``) - none exist until an owner approves them;
- ``superadmin`` / ``superuser`` keep their platform meaning; custom roles keep their SEC-1
  scope unchanged.

The proposed decision is "the existing decision allowed it **and** some role holding the
permission (by name or one-hop equivalent) is assigned where the request is". It is narrower
by construction, so the only divergence is ``would_deny``.

**What is recorded (privacy-safe).** Daily counters in ``rbac_shadow_observations``, keyed by
day, permission, outcome (``agree`` / ``would_deny``), the sorted built-in role keys the user
holds, and how the request relates to the user (primary organisation, secondary organisation,
project inside / outside the primary organisation, unscoped). No user id, organisation id,
project id, name or email is stored.

**Coverage, stated.** Only ``PolicyService.authorize`` allow decisions are compared. The role-name
sites listed in the M1 audit (``build_scope_query`` row visibility, ``AuthorizationService``
tiers, the subscription role sets and others) are not shadowed by this module.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .role_grants import BUILT_IN_ROLE_KEYS, role_is_built_in

logger = logging.getLogger(__name__)

OBSERVATIONS = "rbac_shadow_observations"
ASSIGNMENTS = "role_assignments"

PLATFORM_KEYS = frozenset({"superadmin", "superuser"})
PROJECT_TIER_KEYS = frozenset({"projectadmin", "projectuser"})


def _text(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def enabled() -> bool:
    from ..core.config import settings

    return str(getattr(settings, "BUILT_IN_ROLE_ASSIGNMENTS", "off") or "off").strip().lower() == "shadow"


async def _project_organisation(db: Any, project_id: str, cache: Dict[str, Optional[str]]) -> Optional[str]:
    if project_id not in cache:
        candidates: List[Any] = [project_id]
        try:
            from bson import ObjectId

            candidates.append(ObjectId(project_id))
        except Exception:
            pass
        row = await db.projects.find_one({"_id": {"$in": candidates}}, {"organization_id": 1, "organizationId": 1})
        cache[project_id] = _text((row or {}).get("organization_id") or (row or {}).get("organizationId"))
    return cache[project_id]


async def proposed_assignments(db: Any, user_doc: Mapping[str, Any], role_key: str,
                               projects: Iterable[str], cache: Dict[str, Optional[str]]) -> List[Dict[str, Any]]:
    """Where a built-in role would be assigned under the proposed model."""
    primary = _text(user_doc.get("organization_id"))
    scopes: List[Dict[str, Any]] = []
    if role_key in PROJECT_TIER_KEYS:
        for project in sorted(set(projects)):
            if primary and await _project_organisation(db, project, cache) == primary:
                scopes.append({"organization_id": primary, "project_id": project})
    elif primary:
        scopes.append({"organization_id": primary, "project_id": None})
    user_id = _text(user_doc.get("_id"))
    rows = getattr(db, ASSIGNMENTS, None)
    if user_id and rows is not None:
        for row in await rows.find({"user_id": user_id, "role_key": role_key, "status": "active"},
                                   {"organization_id": 1, "project_id": 1}).to_list(length=200):
            scopes.append({"organization_id": _text(row.get("organization_id")), "project_id": _text(row.get("project_id"))})
    return scopes


async def _covers(db: Any, scope: Mapping[str, Any], organization_id: Optional[str], project_id: Optional[str],
                  primary: Optional[str], cache: Dict[str, Optional[str]]) -> bool:
    if scope.get("platform"):
        return True
    scope_org, scope_project = _text(scope.get("organization_id")), _text(scope.get("project_id"))
    if project_id:
        if scope_project:
            return scope_project == project_id
        target_org = organization_id or await _project_organisation(db, project_id, cache)
        return bool(scope_org) and scope_org == target_org
    if organization_id:
        return not scope_project and bool(scope_org) and scope_org == organization_id
    # Unscoped (permission-only) request: the primary organisation's organisation-level grants.
    return not scope_project and bool(scope_org) and scope_org == primary


async def proposed_decision(db: Any, user_doc: Mapping[str, Any], permission: str, *, organization_id: Optional[str],
                            project_id: Optional[str]) -> Tuple[bool, List[str]]:
    """``(allowed_under_proposed_model, built_in_role_keys_held)`` for an allowed request."""
    from .permission_service import PermissionService
    from .role_grants import user_memberships

    service = PermissionService()
    keys = sorted({str(key).strip().lower() for key in (user_doc.get("roles") or []) if _text(key)})
    held_built_in = sorted(key for key in keys if key in BUILT_IN_ROLE_KEYS)
    if set(keys) & PLATFORM_KEYS:
        return True, held_built_in
    wanted = service.checks_satisfied_by(permission)
    cache: Dict[str, Optional[str]] = {}
    _orgs, projects = await user_memberships(db, user_doc)
    primary = _text(user_doc.get("organization_id"))
    for rid in user_doc.get("roles") or []:
        resolution = await service._load_role(db, rid)
        role = resolution.document
        key = (resolution.key or "").strip().lower()
        if not role:
            continue
        entries = {str(p).strip() for p in (role.get("permissions") or []) if p is not None and str(p).strip()}
        names = entries | await service._catalogue_names(db, entries)
        held = {held_name for name in names for held_name in service.checks_satisfied_by(name)}
        if not (wanted & (names | held)) and "*" not in entries:
            continue
        if role_is_built_in(role):
            scopes = await proposed_assignments(db, user_doc, key, projects, cache)
        else:
            from .role_grants import role_grant_scope

            applies, scope = await role_grant_scope(db, role, (_orgs, projects), cache)
            scopes = [scope] if applies and scope is not None else []
        for scope in scopes:
            if await _covers(db, scope, organization_id, project_id, primary, cache):
                return True, held_built_in
    return False, held_built_in


async def _relation(db: Any, user_doc: Mapping[str, Any], organization_id: Optional[str], project_id: Optional[str]) -> str:
    primary = _text(user_doc.get("organization_id"))
    if project_id:
        owner = organization_id or await _project_organisation(db, project_id, {})
        return "project_in_primary_organisation" if owner and owner == primary else "project_outside_primary_organisation"
    if organization_id:
        return "primary_organisation" if organization_id == primary else "secondary_organisation"
    return "unscoped"


async def observe(db: Any, current_user: Any, permission: str, *, organization_id: Optional[str],
                  project_id: Optional[str], now: Optional[datetime] = None) -> Optional[str]:
    """Compare an allowed decision with the proposed model and count it. Never raises; returns
    the outcome (for tests) or ``None`` when nothing was recorded."""
    try:
        user_id = _text(getattr(current_user, "id", None))
        if not user_id:
            return None
        from bson import ObjectId

        lookup: List[Any] = [user_id] + ([ObjectId(user_id)] if ObjectId.is_valid(user_id) else [])
        user_doc = await db.users.find_one({"_id": {"$in": lookup}},
                                           {"roles": 1, "organization_id": 1, "organizations": 1, "projects": 1})
        if not user_doc:
            return None
        org, project = _text(organization_id), _text(project_id)
        allowed, held = await proposed_decision(db, user_doc, permission, organization_id=org, project_id=project)
        outcome = "agree" if allowed else "would_deny"
        relation = await _relation(db, user_doc, org, project)
        day = (now or datetime.utcnow()).strftime("%Y-%m-%d")
        combination = "+".join(held) or "(custom roles only)"
        key = hashlib.sha256(f"{day}|{permission}|{outcome}|{combination}|{relation}".encode()).hexdigest()[:32]
        await db[OBSERVATIONS].update_one(
            {"_id": key},
            {"$setOnInsert": {"day": day, "permission": permission, "outcome": outcome,
                              "role_combination": combination, "relation": relation},
             "$inc": {"count": 1}},
            upsert=True,
        )
        return outcome
    except Exception:  # shadow evaluation never affects a response
        logger.warning("M1 shadow evaluation failed", exc_info=True)
        return None


async def summary(db: Any) -> Dict[str, Any]:
    """Divergence counts for review: totals, and ``would_deny`` by role combination and relation."""
    totals: Dict[str, int] = {}
    divergent: Dict[str, int] = {}
    async for row in db[OBSERVATIONS].find({}):
        totals[row["outcome"]] = totals.get(row["outcome"], 0) + int(row.get("count") or 0)
        if row["outcome"] == "would_deny":
            label = f"{row['role_combination']} @ {row['relation']}"
            divergent[label] = divergent.get(label, 0) + int(row.get("count") or 0)
    return {"totals": totals, "would_deny_by_role_combination_and_relation": dict(sorted(divergent.items()))}


__all__ = ["OBSERVATIONS", "enabled", "observe", "proposed_assignments", "proposed_decision", "summary"]
