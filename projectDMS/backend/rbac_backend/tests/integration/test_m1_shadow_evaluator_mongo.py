"""M1 shadow evaluator (workstream 5, 2026-11-21): built-in roles compared with their proposed
per-assignment scope, without changing any decision.

With ``BUILT_IN_ROLE_ASSIGNMENTS=shadow`` every allowed ``PolicyService.authorize`` decision is
evaluated again under the proposed model (``docs/M1_BUILT_IN_ROLE_SCOPING.md`` §4) and counted
in ``rbac_shadow_observations`` - day, permission, outcome, the role keys held and how the
request relates to the user; never a user, organisation or project id. Responses are the same
with the flag ``off`` and ``shadow``. No role, user or membership is written.

Real HTTP through the production app on real MongoDB. Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Tuple

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_m1_cross_org_role_combinations_mongo import MONETIZATION, _headers, _person
from rbac_backend.tests.integration.test_security_remediation_mongo import _role
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_A2, PROJ_B1

pytestmark = pytest.mark.integration


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _shadow(monkeypatch: pytest.MonkeyPatch, mode: str = "shadow") -> None:
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "BUILT_IN_ROLE_ASSIGNMENTS", mode, raising=False)


async def _get(env: Any, persona: str, path: str, **headers: str) -> int:
    response = await env.client.get(path, headers=_headers(persona, **headers))
    env.client.cookies.clear()
    return response.status_code


async def _observations(env: Any) -> List[Dict[str, Any]]:
    return await env.db.rbac_shadow_observations.find({}).to_list(length=None)


async def _outcomes(env: Any) -> set:
    return {(row["outcome"], row["role_combination"], row["relation"]) for row in await _observations(env)}


REQUESTS: List[Tuple[str, str, Dict[str, str]]] = [
    ("billing_b", f"{MONETIZATION}/billing/records/{ORG_B}", {}),
    ("billing_a", f"{MONETIZATION}/billing/records/{ORG_A}", {}),
    ("claims_b1", "/api/claims", {"X-Org-Id": ORG_B, "X-Proj-Id": PROJ_B1}),
    ("claims_a2", "/api/claims", {"X-Proj-Id": PROJ_A2}),
]


async def _seed_people(env: Any) -> None:
    await env.db.roles.insert_one(_role("m1s-b-viewer", ["dms.document.view"], organization_id=ORG_B))
    await _person(env, "m1s_dual_listed", ["orgadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
    await _person(env, "m1s_dual_row", ["orgadmin"], primary=ORG_A, membership_rows=[ORG_B])
    await _person(env, "m1s_mixed", ["projectadmin", "orguser"], primary=ORG_B, listed=[ORG_A, ORG_B], projects=[PROJ_A1])
    await _person(env, "m1s_billing", ["contraclaim_billing_admin"], primary=ORG_A, listed=[ORG_A, ORG_B])
    await _person(env, "m1s_custom", ["orguser", "m1s-b-viewer"], primary=ORG_A, listed=[ORG_A, ORG_B])
    await _person(env, "m1s_single", ["orgadmin"], primary=ORG_A)


PERSONAS = ("m1s_dual_listed", "m1s_dual_row", "m1s_mixed", "m1s_billing", "m1s_custom", "m1s_single")


def test_shadow_mode_changes_no_response_and_writes_no_role(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed_people(env)
            users_before = await env.db.users.find({}).sort("_id", 1).to_list(length=None)
            roles_before = await env.db.roles.find({}).sort("_id", 1).to_list(length=None)
            _shadow(monkeypatch, "off")
            off = {(p, name): await _get(env, p, path, **h) for p in PERSONAS for name, path, h in REQUESTS}
            assert await _observations(env) == [], "off records nothing"
            _shadow(monkeypatch)
            shadow = {(p, name): await _get(env, p, path, **h) for p in PERSONAS for name, path, h in REQUESTS}
            assert shadow == off, "shadow never changes a response"
            assert await env.db.users.find({}).sort("_id", 1).to_list(length=None) == users_before
            assert await env.db.roles.find({}).sort("_id", 1).to_list(length=None) == roles_before
            assert await env.db.role_assignments.count_documents({}) == 0

    _run(scenario())


def test_dual_organisation_and_mixed_role_users_are_counted_as_would_deny(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed_people(env)
            _shadow(monkeypatch)
            for persona in PERSONAS:
                for _name, path, headers in REQUESTS:
                    await _get(env, persona, path, **headers)
            outcomes = await _outcomes(env)
            # M1-E1/E2: Org Admin of A reading B's billing - allowed today, would be refused.
            assert ("would_deny", "orgadmin", "secondary_organisation") in outcomes
            # M1-E4: billing admin of A in B.
            assert ("would_deny", "contraclaim_billing_admin", "secondary_organisation") in outcomes
            # M1-E3: Project Admin of A1 + Org User of B reading A's sibling project A2.
            assert ("would_deny", "orguser+projectadmin", "project_outside_primary_organisation") in outcomes
            # M1-E5: Org User of A, member of B through a custom role, reading B's billing.
            assert ("would_deny", "orguser", "secondary_organisation") in outcomes
            # Their own primary organisation agrees.
            assert ("agree", "orgadmin", "primary_organisation") in outcomes
            assert not [o for o in outcomes if o[0] == "would_deny" and o[2] == "primary_organisation"], outcomes

    _run(scenario())


def test_an_explicit_assignment_in_the_secondary_organisation_agrees(monkeypatch) -> None:
    """A legacy reach the owner decides to keep becomes an explicit assignment; the shadow then
    agrees. (Nothing writes these rows in production; the test seeds one.)"""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            user_id = await _person(env, "m1s_kept", ["orgadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            _shadow(monkeypatch)
            assert await _get(env, "m1s_kept", f"{MONETIZATION}/billing/records/{ORG_B}") == 200
            assert ("would_deny", "orgadmin", "secondary_organisation") in await _outcomes(env)
            await env.db.rbac_shadow_observations.delete_many({})
            await env.db.role_assignments.insert_one({"user_id": user_id, "role_key": "orgadmin",
                                                      "organization_id": ORG_B, "project_id": None, "status": "active"})
            assert await _get(env, "m1s_kept", f"{MONETIZATION}/billing/records/{ORG_B}") == 200
            outcomes = await _outcomes(env)
            assert ("agree", "orgadmin", "secondary_organisation") in outcomes
            assert not [o for o in outcomes if o[0] == "would_deny"], outcomes

    _run(scenario())


def test_project_membership_rows_inside_and_outside_the_primary_organisation(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            user_id = await _person(env, "m1s_proj", ["projectadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            await env.db.project_memberships.insert_many([
                {"user_id": user_id, "project_id": PROJ_A1, "status": "active"},
                {"user_id": user_id, "project_id": PROJ_B1, "status": "active"},
            ])
            _shadow(monkeypatch)
            assert await _get(env, "m1s_proj", "/api/claims", **{"X-Proj-Id": PROJ_A1}) == 200
            assert await _get(env, "m1s_proj", "/api/claims", **{"X-Org-Id": ORG_B, "X-Proj-Id": PROJ_B1}) == 200, \
                "a project membership row outside the primary organisation reaches it today"
            outcomes = await _outcomes(env)
            assert ("agree", "projectadmin", "project_in_primary_organisation") in outcomes
            assert ("would_deny", "projectadmin", "project_outside_primary_organisation") in outcomes
            assert ("would_deny", "projectadmin", "project_in_primary_organisation") not in outcomes

    _run(scenario())


def test_a_disabled_account_is_refused_before_any_shadow_evaluation(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _person(env, "m1s_disabled", ["orgadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            await env.db.users.update_one({"username": "m1s_disabled"}, {"$set": {"disabled": True, "is_active": False}})
            _shadow(monkeypatch)
            assert await _get(env, "m1s_disabled", f"{MONETIZATION}/billing/records/{ORG_B}") in {401, 403}
            assert await _observations(env) == []

    _run(scenario())


def test_the_shadow_reads_roles_fresh_after_a_grant_change(monkeypatch) -> None:
    """Cache invalidation: after a role change the shadow compares against the new roles
    (it reads the user, not the permission cache); no stale divergence is counted."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.permission_service import complete_grant_change

            user_id = await _person(env, "m1s_change", ["orgadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            _shadow(monkeypatch)
            assert await _get(env, "m1s_change", f"{MONETIZATION}/billing/records/{ORG_B}") == 200
            assert ("would_deny", "orgadmin", "secondary_organisation") in await _outcomes(env)
            await env.db.rbac_shadow_observations.delete_many({})
            await env.db.users.update_one({"username": "m1s_change"}, {"$set": {"organizations": [ORG_A]}})
            await complete_grant_change([user_id])
            assert await _get(env, "m1s_change", f"{MONETIZATION}/billing/records/{ORG_B}") == 403
            assert not [o for o in await _outcomes(env) if o[0] == "would_deny"]

    _run(scenario())


def test_observations_hold_no_identifiers_and_a_shadow_failure_changes_nothing(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed_people(env)
            _shadow(monkeypatch)
            for persona in PERSONAS:
                await _get(env, persona, f"{MONETIZATION}/billing/records/{ORG_B}")
            rows = await _observations(env)
            assert rows
            identifiers = [ORG_A, ORG_B, PROJ_A1, PROJ_A2, PROJ_B1, *PERSONAS, "@example.com"]
            text = repr(rows)
            assert not [value for value in identifiers if value in text], text[:400]
            for row in rows:
                assert set(row) == {"_id", "day", "permission", "outcome", "role_combination", "relation", "count"}

            from rbac_backend.services import role_scope_shadow

            async def broken(*args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("shadow failure")

            monkeypatch.setattr(role_scope_shadow, "proposed_decision", broken)
            await env.db.rbac_shadow_observations.delete_many({})
            assert await _get(env, "m1s_single", f"{MONETIZATION}/billing/records/{ORG_A}") == 200
            assert await _observations(env) == []

    _run(scenario())


def test_the_p05_reading_summarises_shadow_divergence_read_only(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed_people(env)
            _shadow(monkeypatch)
            for persona in PERSONAS:
                await _get(env, persona, f"{MONETIZATION}/billing/records/{ORG_B}")
            from pymongo import MongoClient

            from rbac_backend.tests.integration.test_claim_arbitration_readings_mongo import _reader, _uri

            reader = _reader()
            client = MongoClient(_uri(), serverSelectionTimeoutMS=5_000)
            try:
                sync_db = client[env.db.name]
                before = sync_db.rbac_shadow_observations.count_documents({})
                reading = reader._read_built_in_role_shadow_divergence(reader.ReadOnlyDatabase(sync_db))
                assert sync_db.rbac_shadow_observations.count_documents({}) == before
            finally:
                client.close()
            assert reading["observations"]["would_deny"] >= 4
            assert reading["would_deny_by_role_combination_and_relation"]["orgadmin @ secondary_organisation"] >= 2
            assert ORG_B not in repr(reading)

    _run(scenario())
