"""SEC-1: where a role's permissions count (services/role_grants.py)."""

from __future__ import annotations

import asyncio

from rbac_backend.initial_data.default_roles import DEFAULT_ROLES
from rbac_backend.services.role_grants import (
    BUILT_IN_ROLE_KEYS,
    applicable_names,
    grant_applies,
    role_grant_scope,
    role_is_built_in,
)


class _Projects:
    def __init__(self, rows):
        self.rows = rows

    async def find_one(self, query, *_args, **_kwargs):
        wanted = {str(value) for value in query["_id"]["$in"]}
        return next((row for row in self.rows if str(row["_id"]) in wanted), None)


class _DB:
    projects = _Projects([{"_id": "p1", "organization_id": "org-a"}, {"_id": "p9", "organization_id": "org-b"}])


def _scope(role, organisations=(), projects=()):
    return asyncio.run(role_grant_scope(_DB(), role, (set(organisations), set(projects)), {}))


def test_every_seeded_role_is_built_in_even_without_its_system_flag() -> None:
    seeded = {str(role["_id"]) for role in DEFAULT_ROLES}
    assert seeded <= BUILT_IN_ROLE_KEYS, sorted(seeded - BUILT_IN_ROLE_KEYS)
    for key in seeded:
        assert role_is_built_in({"_id": key})


def test_a_name_never_makes_a_role_built_in() -> None:
    """Review R-1: built-in status comes from the stored key or the system flag, never the name."""
    for name in ("Organization Admin", "legal_reviewer", "contract_expert", "Super Admin"):
        assert not role_is_built_in({"_id": "65f000000000000000000001", "name": name}), name
    assert role_is_built_in({"_id": "orgadmin", "name": "Anything"})
    assert role_is_built_in({"_id": "65f000000000000000000003", "is_system": True})
    assert role_is_built_in({"_id": "65f000000000000000000004", "scope": "system"})


def test_organisation_project_and_platform_roles_count_only_where_granted() -> None:
    org_role = {"_id": "r1", "organization_id": "org-a"}
    assert _scope(org_role, ["org-a"]) == (True, {"organization_id": "org-a", "project_id": None})
    assert _scope(org_role, ["org-b"]) == (False, None)

    project_role = {"_id": "r2", "organization_id": "org-a", "project_id": "p1"}
    assert _scope(project_role, ["org-a"], ["p1"])[0] is True
    assert _scope(project_role, ["org-a"], []) == (False, None)
    # A project role whose project belongs to another organisation never applies.
    assert _scope({"_id": "r3", "organization_id": "org-a", "project_id": "p9"}, ["org-a"], ["p9"]) == (False, None)

    platform = {"_id": "r4"}
    assert _scope(platform, ["org-a"]) == (False, None)
    assert _scope(platform, [])[0] is True


def test_a_grant_counts_only_in_its_own_organisation_and_project() -> None:
    org_scope = {"organization_id": "org-a", "project_id": None}
    project_scope = {"organization_id": "org-a", "project_id": "p1"}
    assert grant_applies(None, organization_id="org-b")  # built-in: no role-level bound
    assert grant_applies(org_scope, organization_id="org-a", project_id="p2")
    # Review R-3: a project grant never counts for its whole organisation.
    assert not grant_applies(project_scope, organization_id="org-a")
    # Review R-4: with nothing named, only the primary organisation counts.
    assert grant_applies(org_scope, primary_organization_id="org-a")
    assert not grant_applies(org_scope, primary_organization_id="org-b")
    assert not grant_applies(org_scope)
    assert not grant_applies(project_scope, primary_organization_id="org-a")  # re-review: a project role needs its project named
    assert not grant_applies(org_scope, organization_id="org-b")
    assert grant_applies(project_scope, organization_id="org-a", project_id="p1")
    assert not grant_applies(project_scope, organization_id="org-a", project_id="p2")
    # A project named without its resolved organisation fails closed for every custom grant.
    assert not grant_applies(project_scope, project_id="p1")
    assert not grant_applies(org_scope, project_id="p-of-org-b")
    assert grant_applies(None, project_id="p-of-org-b")  # built-in: bounded by ScopeService
    assert grant_applies({"organization_id": None, "project_id": None, "platform": True}, project_id="p1")
    grants = [{"names": ["a"], "scope": org_scope}, {"names": ["b"], "scope": project_scope}, {"names": ["c"], "scope": None}]
    assert applicable_names(grants, organization_id="org-a", project_id="p2") == {"a", "c"}
    assert applicable_names(grants, organization_id="org-a") == {"a", "c"}
    assert applicable_names(grants, project_id="p1") == {"c"}
    assert applicable_names(grants, organization_id="org-b") == {"c"}
