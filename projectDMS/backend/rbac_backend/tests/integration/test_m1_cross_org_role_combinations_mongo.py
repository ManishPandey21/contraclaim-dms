"""M1 (owner decision 2026-11-20): built-in roles are global today. Cross-organisation tests.

A built-in role (``orgadmin``, ``orguser``, ``projectadmin``, ``contraclaim_billing_admin``,
...) is stored as a bare key on the user and applies wherever the user's membership reaches
(``services/role_grants.py``: built-in grants have no scope). So a user who is Org Admin of
A and merely a member of B acts as Org Admin of B, and a user who is Project Admin of a
project in A and Org User of B passes the organisation-wide check in A.

Two kinds of test here, both against real MongoDB through the production ``app``:

- **Negative tests** for boundaries that hold today (single-organisation users, custom
  roles of another organisation, the user-read rule). They must stay green through the M1
  remediation.
- **Pinned exposures** (``M1-E1`` to ``M1-E5``): combinations that today reach another
  organisation's data or another tier's view. They assert today's result so a change is
  never silent; the M1 remediation flips each to a refusal and rewrites it as a negative
  test (the pattern used for DEF-5). They are not an acceptance of the behaviour.

No production role, user or membership is changed by this work (owner decision: no broad
RBAC migration before P-05, security review and separate approval).
Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_security_remediation_mongo import _role, _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_A2, PROJ_B1

pytestmark = pytest.mark.integration

MONETIZATION = "/api/rbac-monetization"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _headers(persona: str, **extra: str) -> Dict[str, str]:
    import jwt  # type: ignore[import-untyped]

    from rbac_backend.core.config import settings

    now = int(time.time())
    token = jwt.encode({"sub": f"{persona}@example.com", "type": "access", "iat": now, "exp": now + 3600},
                       settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return {"Authorization": f"Bearer {token}", **extra}


async def _person(env: Any, persona: str, roles: List[str], *, primary: str, listed: Optional[List[str]] = None,
                  membership_rows: Optional[List[str]] = None, projects: Optional[List[str]] = None) -> str:
    user_id = await _user(env.db, persona, roles, organization_id=primary, projects=projects or [])
    if listed is not None:
        await env.db.users.update_one({"username": persona}, {"$set": {"organizations": listed}})
    for org in membership_rows or []:
        await env.db.organization_memberships.insert_one({"user_id": user_id, "organization_id": org, "status": "active"})
    return user_id


async def _status(env: Any, persona: str, path: str, **headers: str) -> int:
    response = await env.client.get(path, headers=_headers(persona, **headers))
    env.client.cookies.clear()
    return response.status_code


async def _effective(env: Any, persona: str) -> Dict[str, Any]:
    response = await env.client.get(f"{MONETIZATION}/plan-settings/effective-services", headers=_headers(persona))
    env.client.cookies.clear()
    assert response.status_code == 200, response.text[:200]
    return response.json()["effective"]


async def _targets(env: Any) -> Dict[str, str]:
    return {
        "b_user": await _user(env.db, "m1_target_b", ["orguser"], organization_id=ORG_B, projects=[PROJ_B1]),
        "a2_user": await _user(env.db, "m1_target_a2", ["projectuser"], organization_id=ORG_A, projects=[PROJ_A2]),
    }


# --------------------------------------------------------------------------- #
# Negative tests: boundaries that hold today
# --------------------------------------------------------------------------- #


def test_single_organisation_users_never_reach_another_organisation(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            targets = await _targets(env)
            await _person(env, "m1_orgadmin_a", ["orgadmin"], primary=ORG_A)
            await _person(env, "m1_billing_a", ["contraclaim_billing_admin"], primary=ORG_A)
            await _person(env, "m1_projadmin_a1", ["projectadmin"], primary=ORG_A, projects=[PROJ_A1])
            for persona in ("m1_orgadmin_a", "m1_billing_a", "m1_projadmin_a1"):
                assert await _status(env, persona, f"/api/users/{targets['b_user']}") == 403, persona
                assert await _status(env, persona, f"{MONETIZATION}/billing/records/{ORG_B}") == 403, persona
                assert await _status(env, persona, "/api/claims", **{"X-Org-Id": ORG_B, "X-Proj-Id": PROJ_B1}) == 403, persona
                assert set((await _effective(env, persona))["organizations"]) <= {ORG_A}, persona
            # A Project Admin of A1 alone: no sibling project, no organisation-wide view.
            assert await _status(env, "m1_projadmin_a1", f"/api/users/{targets['a2_user']}") == 403
            assert await _status(env, "m1_projadmin_a1", "/api/claims", **{"X-Proj-Id": PROJ_A2}) == 403
            assert "billing_status" not in (await _effective(env, "m1_projadmin_a1"))["projects"].get(PROJ_A2, {})

    _run(scenario())


def test_a_custom_role_of_another_organisation_confers_nothing_where_the_user_is_not_a_member(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.roles.insert_one(_role("m1-b-manager", ["dms.document.view", "projects:read"], organization_id=ORG_B))
            await _person(env, "m1_custom_b_not_member", ["m1-b-manager"], primary=ORG_A)
            assert await _status(env, "m1_custom_b_not_member", "/api/claims", **{"X-Org-Id": ORG_B, "X-Proj-Id": PROJ_B1}) == 403
            assert await _status(env, "m1_custom_b_not_member", f"{MONETIZATION}/billing/records/{ORG_B}") == 403

    _run(scenario())


def test_the_user_read_rule_is_judged_in_the_primary_organisation(monkeypatch) -> None:
    """GET /api/users/{id} already refuses a secondary organisation's users to an Org Admin
    of A who is merely listed in, or a member of, B (check_user_access compares the primary
    organisation)."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            targets = await _targets(env)
            await _person(env, "m1_listed", ["orgadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            await _person(env, "m1_row", ["orgadmin"], primary=ORG_A, membership_rows=[ORG_B])
            await _person(env, "m1_billing_listed", ["contraclaim_billing_admin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            for persona in ("m1_listed", "m1_row", "m1_billing_listed"):
                assert await _status(env, persona, f"/api/users/{targets['b_user']}") == 403, persona

    _run(scenario())


# --------------------------------------------------------------------------- #
# Pinned exposures: today's results, to be flipped by the M1 remediation
# --------------------------------------------------------------------------- #


def test_m1_e1_e2_an_org_admin_of_a_who_is_a_member_of_b_acts_as_org_admin_of_b(monkeypatch) -> None:
    """M1-E1 (listed in ``organizations``) and M1-E2 (an active membership row): B's billing
    records, B's claims and B's full service state. Remediation: 403 and B absent."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _person(env, "m1_e1", ["orgadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            await _person(env, "m1_e2", ["orgadmin"], primary=ORG_A, membership_rows=[ORG_B])
            for persona in ("m1_e1", "m1_e2"):
                assert await _status(env, persona, f"{MONETIZATION}/billing/records/{ORG_B}") == 200, f"{persona}: M1 still open"
                assert await _status(env, persona, "/api/claims", **{"X-Org-Id": ORG_B, "X-Proj-Id": PROJ_B1}) == 200, persona
                effective = await _effective(env, persona)
                assert ORG_B in effective["organizations"], persona
                assert "billing_status" in effective["organizations"][ORG_B], persona

    _run(scenario())


def test_m1_e3_project_admin_in_a_plus_org_user_in_b_passes_the_organisation_wide_check_in_a(monkeypatch) -> None:
    """M1-E3: the global ``orguser`` key counts in A, so the user reads A's sibling project's
    claims and its full service state (decision 4's project-tier cut does not apply).
    Remediation: A2 refused, A2's state operational only."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _person(env, "m1_e3", ["projectadmin", "orguser"], primary=ORG_B, listed=[ORG_A, ORG_B], projects=[PROJ_A1])
            assert await _status(env, "m1_e3", "/api/claims", **{"X-Proj-Id": PROJ_A2}) == 200, "M1 still open"
            assert "billing_status" in (await _effective(env, "m1_e3"))["projects"][PROJ_A2]
            assert await _status(env, "m1_e3", f"{MONETIZATION}/billing/records/{ORG_A}") == 200

    _run(scenario())


def test_m1_e4_a_billing_admin_of_a_who_is_a_member_of_b_reads_bs_billing(monkeypatch) -> None:
    """M1-E4: Remediation: 403 for B."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _person(env, "m1_e4", ["contraclaim_billing_admin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            assert await _status(env, "m1_e4", f"{MONETIZATION}/billing/records/{ORG_B}") == 200, "M1 still open"
            assert ORG_B in (await _effective(env, "m1_e4"))["organizations"]

    _run(scenario())


def test_m1_e5_a_built_in_role_held_for_a_also_counts_where_a_custom_role_brought_the_user(monkeypatch) -> None:
    """M1-E5 (mixed built-in and custom): Org User of A who is a member of B for a narrow
    custom role of B. The custom role is correctly bounded to B, but the global ``orguser``
    key's legacy organizations:read reaches B's billing records. Remediation: 403 for B."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.roles.insert_one(_role("m1-b-viewer", ["dms.document.view"], organization_id=ORG_B))
            await _person(env, "m1_e5", ["orguser", "m1-b-viewer"], primary=ORG_A, listed=[ORG_A, ORG_B])
            assert await _status(env, "m1_e5", f"{MONETIZATION}/billing/records/{ORG_B}") == 200, "M1 still open"
            # The custom role itself grants nothing beyond B's documents.
            assert await _status(env, "m1_e5", "/api/claims", **{"X-Org-Id": ORG_B, "X-Proj-Id": PROJ_B1}) == 403

    _run(scenario())


def test_m1_permission_cache_never_carries_a_grant_across_organisations(monkeypatch) -> None:
    """A cached decision for A is never served for B: the cache keys each role's names with
    the scope they apply in (SEC-1). A negative test that holds today."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.permission_service import PermissionService

            await env.db.roles.insert_one(_role("m1-a-claims", ["dms.claim.view"], organization_id=ORG_A))
            user_id = await _person(env, "m1_cache", ["m1-a-claims"], primary=ORG_A, listed=[ORG_A, ORG_B])
            service = PermissionService()
            assert await service.user_has_permission(user_id, "dms.claim.view", organization_id=ORG_A)
            assert not await service.user_has_permission(user_id, "dms.claim.view", organization_id=ORG_B)
            assert await service.user_has_permission(user_id, "dms.claim.view", organization_id=ORG_A)
            assert not await service.user_has_permission(user_id, "dms.claim.view", organization_id=ORG_B)

    _run(scenario())


def test_the_p05_m1_reading_counts_affected_users_and_proposes_scoped_assignments(monkeypatch) -> None:
    """The read-only P-05 extension on a seeded database: counts and organisation ids only."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.roles.insert_one(_role("m1-b-viewer", ["dms.document.view"], organization_id=ORG_B))
            await _person(env, "m1_r_listed", ["orgadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            await _person(env, "m1_r_row", ["orgadmin"], primary=ORG_A, membership_rows=[ORG_B])
            await _person(env, "m1_r_mixed", ["projectadmin", "orguser"], primary=ORG_B, listed=[ORG_A, ORG_B], projects=[PROJ_A1])
            await _person(env, "m1_r_billing", ["contraclaim_billing_admin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            await _person(env, "m1_r_custom", ["orguser", "m1-b-viewer"], primary=ORG_A, listed=[ORG_A, ORG_B])
            await _person(env, "m1_r_single", ["orgadmin"], primary=ORG_A)
            from pymongo import MongoClient

            from rbac_backend.tests.integration.test_claim_arbitration_readings_mongo import _reader, _uri

            reader = _reader()
            client = MongoClient(_uri(), serverSelectionTimeoutMS=5_000)
            try:
                sync_db = client[env.db.name]
                names = ("users", "roles", "organization_memberships", "project_memberships", "projects")
                before = {name: sync_db[name].count_documents({}) for name in names}
                reading = reader._read_built_in_role_scope_impact(reader.ReadOnlyDatabase(sync_db))
                after = {name: sync_db[name].count_documents({}) for name in names}
            finally:
                client.close()
            assert after == before, "read-only"
            assert reading["org_wide_role_reaching_a_secondary_organisation"] >= 4  # listed, row, mixed, custom
            assert reading["mixed_org_wide_and_project_tier_roles"] >= 1
            assert reading["mixed_built_in_and_custom_roles"] >= 1
            assert reading["billing_admin_reaching_more_than_one_organisation"] >= 1
            assert reading["projects_outside_the_primary_organisation"] >= 1  # the mixed user's A1 under primary B
            assert reading["affected_users"] >= 5
            assert reading["affected_users_by_organization"].get(ORG_B, 0) >= 5
            assert reading["proposed_scoped_assignments"].get("orgadmin@organisation", 0) >= 2
            assert reading["assignments_needing_a_decision"] >= 5
            text = repr(reading)
            for private in ("example.com", "m1_r_", "user-"):
                assert private not in text, private

    _run(scenario())
