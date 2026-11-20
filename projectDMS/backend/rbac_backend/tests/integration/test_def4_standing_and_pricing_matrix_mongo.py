"""Owner verification 2026-11-20 (steps 2 and 3): N1 standing and platform-pricing consistency.

**N1 (`d9f1011`).** A custom organisation role gives organisation-level billing standing only
if it itself grants the financial view. Combinations: Project User / Project Admin plus an
unrelated organisation role, a role granting the real billing name, a role granting a legacy
alias, mixed built-in and custom roles, users of two organisations, and revoked, inactive
and foreign-organisation roles; both ``BILLING_EXPLICIT_PERMISSIONS`` states; through the
HTTP routes and through ``PolicyService`` directly.

**Platform pricing (`8a45f3b`, `a98aa94`).** ``platform.pricing.manage`` is decided the same
way by ``PermissionService.user_has_permission``, ``PolicyService.has_permission`` and
``authorize``, ``require_permission``, the advertised permission list and the catalogue
routes, for every persona.

Real MongoDB through the production ``app``; nothing overridden.
Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_security_remediation_mongo import _role, _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_B1

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"
PRICING = "platform.pricing.manage"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _headers(persona: str, *, user_id: Optional[str] = None, step_up: Optional[str] = None) -> Dict[str, str]:
    import jwt  # type: ignore[import-untyped]

    from rbac_backend.core.config import settings

    now = int(time.time())
    token = jwt.encode({"sub": f"{persona}@example.com", "type": "access", "iat": now, "exp": now + 3600},
                       settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    headers = {"Authorization": f"Bearer {token}"}
    if step_up:
        from rbac_backend.services.step_up_service import StepUpService

        headers["X-Step-Up-Token"] = StepUpService().create_token(user_id=user_id or f"user-{persona}", action=step_up)
    return headers


def _flag(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "BILLING_EXPLICIT_PERMISSIONS", on)


async def _principal(env: Any, user_id: str) -> SimpleNamespace:
    from bson import ObjectId

    doc = await env.db.users.find_one({"_id": ObjectId(user_id)})
    return SimpleNamespace(id=user_id, roles=doc.get("roles") or [], organization_id=doc.get("organization_id"),
                           organizations=doc.get("organizations") or [], projects=doc.get("projects") or [],
                           account_type=doc.get("account_type"))


async def _status(env: Any, persona: str, path: str) -> int:
    response = await env.client.get(f"{BASE}{path}", headers=_headers(persona))
    env.client.cookies.clear()
    return response.status_code


async def _policy_allows(env: Any, user_id: str, permission: str, organization_id: str) -> bool:
    from rbac_backend.services.policy_service import PolicyService

    try:
        await PolicyService().authorize(await _principal(env, user_id), permission, organization_id=organization_id,
                                        resource_type="probe", audit=False)
        return True
    except HTTPException as exc:
        assert exc.status_code == 403, exc.status_code
        return False


# --------------------------------------------------------------------------- #
# Step 2: N1 standing combinations
# --------------------------------------------------------------------------- #

ROLES = [
    _role("n1-a-tags", ["tags:read"], organization_id=ORG_A),
    _role("n1-a-plan-view", ["billing.plan.view", "subscription.history.view", "billing.record.view"], organization_id=ORG_A),
    _role("n1-a-orgread", ["organizations:read"], organization_id=ORG_A),
    _role("n1-a-inactive-orgread", ["organizations:read"], organization_id=ORG_A, active=False),
    _role("n1-b-orgread", ["organizations:read"], organization_id=ORG_B),
    _role("n1-a1-project-orgread", ["organizations:read"], organization_id=ORG_A, project_id=PROJ_A1),
]

#: persona -> (roles, extra user fields, standing expected in A with the flag off)
CASES: Dict[str, tuple] = {
    "n1_pu_tags": (["projectuser", "n1-a-tags"], {}, False),
    "n1_pa_tags": (["projectadmin", "n1-a-tags"], {}, False),
    "n1_pu_real_billing": (["projectuser", "n1-a-plan-view"], {}, True),
    "n1_pa_legacy_alias": (["projectadmin", "n1-a-orgread"], {}, True),
    "n1_pu_inactive_role": (["projectuser", "n1-a-inactive-orgread"], {}, False),
    "n1_pu_project_role": (["projectuser", "n1-a1-project-orgread"], {}, False),
    "n1_pu_foreign_role_not_member": (["projectuser", "n1-b-orgread"], {}, False),
    "n1_pu_b_role_member_of_b": (["projectuser", "n1-b-orgread"], {"organizations": [ORG_A, ORG_B]}, False),
    "n1_mixed_builtin_orguser": (["projectuser", "orguser", "n1-a-tags"], {}, True),
    "n1_custom_only_real_billing": (["n1-a-plan-view"], {}, True),
}

FINANCIAL = ("/billing/summary/{org}", "/billing/history/{org}", "/billing/records/{org}", "/billing/review-queue/{org}")


async def _seed_n1(env: Any) -> Dict[str, str]:
    await env.db.roles.insert_many([dict(role) for role in ROLES])
    ids = {}
    for persona, (roles, extra, _) in CASES.items():
        ids[persona] = await _user(env.db, persona, roles, organization_id=ORG_A, projects=[PROJ_A1], **extra)
    # A revoked organisation membership is no membership: a B role does not apply for it.
    revoked = await _user(env.db, "n1_pu_revoked_b", ["projectuser", "n1-b-orgread"], organization_id=ORG_A, projects=[PROJ_A1])
    await env.db.organization_memberships.insert_one({"user_id": revoked, "organization_id": ORG_B, "status": "revoked"})
    ids["n1_pu_revoked_b"] = revoked
    return ids


def test_n1_standing_combinations_flag_off(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, False)
            ids = await _seed_n1(env)
            from rbac_backend.services.policy_service import PolicyService

            policy = PolicyService()
            expected = {persona: case[2] for persona, case in CASES.items()} | {"n1_pu_revoked_b": False}
            for persona, standing in expected.items():
                principal = await _principal(env, ids[persona])
                assert await policy.has_organisation_level_standing(principal, ORG_A, "billing.plan.view") is standing, persona
                for path in FINANCIAL:
                    code = await _status(env, persona, path.format(org=ORG_A))
                    assert code == (200 if standing else 403), (persona, path, code)
                assert await _policy_allows(env, ids[persona], "billing.plan.view", ORG_A) is standing, persona
                assert await _policy_allows(env, ids[persona], "subscription.history.view", ORG_A) is standing, persona
                # Never organisation B's billing, whatever the B role says, unless the user
                # is a member of B and the B role grants the view there.
                b_expected = persona == "n1_pu_b_role_member_of_b"
                assert (await _status(env, persona, f"/billing/summary/{ORG_B}") == 200) is b_expected, persona

    _run(scenario())


def test_n1_standing_combinations_flag_on(monkeypatch) -> None:
    """With the flag on the explicit name decides: aliases never count, and an unrelated
    organisation role never confers anything. A custom organisation role holding the explicit
    name answers for the organisation even alongside a project role (review F4: the same N1
    rule as with the flag off)."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, True)
            ids = await _seed_n1(env)
            for persona in ids:
                allowed = persona in {"n1_custom_only_real_billing", "n1_pu_real_billing"}
                for path in FINANCIAL:
                    code = await _status(env, persona, path.format(org=ORG_A))
                    assert (code == 200) is allowed, (persona, path, code)
                assert await _policy_allows(env, ids[persona], "billing.record.view", ORG_A) is allowed, persona
                assert await _status(env, persona, f"/billing/summary/{ORG_B}") == 403, persona

    _run(scenario())


# --------------------------------------------------------------------------- #
# Step 3: platform.pricing.manage, every evaluator agrees
# --------------------------------------------------------------------------- #


async def _evaluators(env: Any, user_id: str, persona: str) -> Dict[str, bool]:
    from rbac_backend.core.security import require_permission
    from rbac_backend.services.permission_service import PermissionService
    from rbac_backend.services.policy_service import PolicyService

    principal = await _principal(env, user_id)
    results: Dict[str, bool] = {}
    results["user_has_permission"] = await PermissionService().user_has_permission(user_id, PRICING, log=False)
    results["policy_has_permission"] = await PolicyService().has_permission(principal, PRICING)
    try:
        await PolicyService().authorize(principal, PRICING, resource_type="plan", audit=False)
        results["policy_authorize"] = True
    except HTTPException:
        results["policy_authorize"] = False
    try:
        await require_permission(PRICING)(current_user=principal)
        results["require_permission"] = True
    except HTTPException:
        results["require_permission"] = False
    advertised = await PermissionService().get_effective_permission_names(user_id)
    results["advertised"] = "*" in advertised or PRICING in advertised
    response = await env.client.post(
        f"{BASE}/plans", headers=_headers(persona, user_id=user_id, step_up="billing.plan.manage"),
        json={"code": f"mx_{persona}", "name": "matrix", "family": "dms_saas", "base_price_minor": 1},
    )
    env.client.cookies.clear()
    results["route_create_plan"] = response.status_code == 200
    return results


def test_platform_pricing_every_evaluator_agrees(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.roles.insert_many([
                _role("mx-platform-pricing", [PRICING], organization_id=None),
                _role("mx-a-pricing", [PRICING], organization_id=ORG_A),
                _role("mx-a-wildcard", ["*"], organization_id=ORG_A),
            ])
            await env.db.roles.update_one({"_id": "orgadmin"}, {"$addToSet": {"permissions": PRICING}})
            personas: Dict[str, tuple] = {
                "mx_platform": (["mx-platform-pricing"], None, "contraclaim_staff", True),
                "mx_tenant_platform_role": (["mx-platform-pricing"], ORG_A, "client_user", False),
                "mx_billing_admin": (["contraclaim_billing_admin"], ORG_A, "client_user", False),
                "mx_tenant_pricing_role": (["mx-a-pricing"], ORG_A, "client_user", False),
                "mx_tenant_wildcard": (["mx-a-wildcard"], ORG_A, "client_user", False),
                "mx_org_admin_edited_builtin": (["orgadmin"], ORG_A, "client_user", False),
                "mx_platform_no_role": ([], None, "contraclaim_staff", False),
            }
            failures: List[str] = []
            for persona, (roles, org, account_type, expected) in personas.items():
                user_id = await _user(env.db, persona, roles, organization_id=org, account_type=account_type)
                results = await _evaluators(env, user_id, persona)
                for evaluator, value in results.items():
                    if value is not expected:
                        failures.append(f"{persona} {evaluator}={value} expected {expected}")
            assert failures == [], "\n".join(failures)
            # Super Admin: every evaluator that applies grants it.
            from rbac_backend.services.policy_service import PolicyService

            superadmin = SimpleNamespace(id="user-superadmin", roles=["superadmin"], organization_id=None, organizations=[], projects=[])
            assert await PolicyService().has_permission(superadmin, PRICING)
            response = await env.client.post(
                f"{BASE}/plans", headers=_headers("superadmin", step_up="billing.plan.manage"),
                json={"code": "mx_superadmin", "name": "matrix", "family": "dms_saas", "base_price_minor": 1},
            )
            assert response.status_code == 200
            # Only the two allowed callers created plans.
            created = {doc["code"] async for doc in env.db.plans.find({"code": {"$regex": "^mx_"}}, {"code": 1})}
            assert created == {"mx_mx_platform", "mx_superadmin"}, created

    _run(scenario())


def test_revoking_the_platform_role_takes_effect_on_the_next_request(monkeypatch) -> None:
    """No cache serves a revoked platform grant (the platform check never reads the cache)."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.permission_service import PermissionService

            await env.db.roles.insert_one(_role("mx-revocable", [PRICING], organization_id=None))
            user_id = await _user(env.db, "mx_revocable", ["mx-revocable"], organization_id=None, account_type="contraclaim_staff")
            service = PermissionService()
            assert await service.user_has_permission(user_id, PRICING, log=False)
            await env.db.roles.update_one({"_id": "mx-revocable"}, {"$set": {"permissions": []}})
            assert not await service.user_has_permission(user_id, PRICING, log=False)
            assert PRICING not in await service.get_effective_permission_names(user_id)
            # Joining an organisation removes the platform reach as well.
            await env.db.roles.update_one({"_id": "mx-revocable"}, {"$set": {"permissions": [PRICING]}})
            await env.db.organization_memberships.insert_one({"user_id": user_id, "organization_id": ORG_A, "status": "active"})
            assert not await service.user_has_permission(user_id, PRICING, log=False)

    _run(scenario())


def test_the_p05_flag_off_disclosure_reading_counts_who_is_affected(monkeypatch) -> None:
    """Step 4: the read-only P-05 reading of what the sixth pass changes with the flag off."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed_n1(env)
            await env.db.roles.insert_many([
                _role("mx-platform-pricing", [PRICING], organization_id=None),
                _role("mx-a-pricing", [PRICING], organization_id=ORG_A),
            ])
            await _user(env.db, "rd_platform", ["mx-platform-pricing"], organization_id=None, account_type="contraclaim_staff")
            await _user(env.db, "rd_billing", ["contraclaim_billing_admin"], organization_id=ORG_A)
            from pymongo import MongoClient

            from rbac_backend.tests.integration.test_claim_arbitration_readings_mongo import _reader, _uri

            reader = _reader()
            client = MongoClient(_uri(), serverSelectionTimeoutMS=5_000)
            try:
                sync_db = client[env.db.name]
                before = {n: sync_db[n].count_documents({}) for n in ("users", "roles", "organization_memberships")}
                reading = reader._read_flag_off_disclosure_impact(reader.ReadOnlyDatabase(sync_db))
                after = {n: sync_db[n].count_documents({}) for n in ("users", "roles", "organization_memberships")}
            finally:
                client.close()
            assert after == before
            # The _env personas project_user / project_admin and the N1 Project Users without
            # standing reach a financial view through organizations:read today.
            assert reading["project_level_users_losing_financial_reads"] >= 6, reading
            assert reading["project_level_users_losing_financial_reads_by_organization"].get(ORG_A, 0) >= 6
            assert reading["project_level_users_given_the_operational_view"] >= reading["project_level_users_losing_financial_reads"]
            assert reading["tenant_holders_losing_global_catalogue_writes"] >= 1  # the billing administrator
            assert reading["entitlement_managers_losing_cross_org_service_state"] >= 1
            assert reading["platform_pricing_holders"] == 1
            assert reading["platform_pricing_names_on_tenant_roles"] == 1
            text = repr(reading)
            for private in ("example.com", "n1_", "rd_", "user-"):
                assert private not in text, private

    _run(scenario())


# --------------------------------------------------------------------------- #
# Independent review of the complete DEF-4 work (2026-11-20): F1, F2, F4, F5
# --------------------------------------------------------------------------- #

F1_SECRETS = ("gw-f1-secret", "NEGOTIATED-F1", ORG_B)


@pytest.mark.parametrize("flag", [False, True])
def test_f1_plan_settings_fails_closed_without_a_resolvable_organisation(monkeypatch, flag: bool) -> None:
    """A user whose projects resolve to no organisation (a deleted project, or a project of
    a missing organisation) received every organisation and unminimised subscriptions."""

    async def scenario() -> None:
        import json
        from datetime import datetime

        async with _env(monkeypatch) as env:
            _flag(monkeypatch, flag)
            await env.db.projects.insert_one({"_id": "proj-orphan", "name": "Orphan", "organization_id": "org-missing"})
            for sub_id, org, project in (("f1-b", ORG_B, PROJ_B1), ("f1-orphan", "org-missing", "proj-orphan")):
                await env.db.subscriptions.insert_one(
                    {"_id": sub_id, "organization_id": org, "project_id": project, "plan_code": "dms_enterprise",
                     "status": "active", "billing_status": "active", "payment_gateway_subscription_id": "gw-f1-secret",
                     "entitlement_overrides": {"negotiated": "NEGOTIATED-F1"}, "updated_at": datetime.utcnow()}
                )
            await _user(env.db, "f1_stale", ["projectuser"], organization_id=None, projects=["deleted-project"])
            await _user(env.db, "f1_orphan", ["projectuser"], organization_id="org-missing", projects=["proj-orphan"])
            for persona in ("f1_stale", "f1_orphan"):
                for path in ("/plan-settings", "/plan-settings/effective-services", "/subscriptions"):
                    response = await env.client.get(f"{BASE}{path}", headers=_headers(persona))
                    env.client.cookies.clear()
                    if response.status_code == 200:
                        text = json.dumps(response.json(), default=str)
                        for secret in F1_SECRETS:
                            assert secret not in text, (persona, path, secret)
                        if persona == "f1_stale":
                            assert ORG_A not in text, (persona, path)
                    else:
                        assert response.status_code == 403, (persona, path, response.status_code)

    _run(scenario())


def test_f2_an_entitlement_role_elsewhere_does_not_widen_to_sibling_projects(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, False)
            await env.db.roles.insert_one(_role("f2-b-entitlement", ["subscription.entitlement.manage"], organization_id=ORG_B))
            await _user(env.db, "f2_mixed", ["projectuser", "f2-b-entitlement"], organization_id=ORG_B,
                        organizations=[ORG_A, ORG_B], projects=[PROJ_A1])
            response = await env.client.get(f"{BASE}/plan-settings/effective-services", headers=_headers("f2_mixed"))
            env.client.cookies.clear()
            projects = set(response.json()["effective"]["projects"])
            assert "proj-a2" not in projects, projects
            assert PROJ_A1 in projects and PROJ_B1 in projects, projects

    _run(scenario())


@pytest.mark.parametrize("flag", [False, True])
def test_f4_a_custom_finance_role_counts_alongside_a_project_role(monkeypatch, flag: bool) -> None:
    """The N1 rule in both states: a custom organisation role granting the views stands for
    the organisation, whether or not the user also holds projectuser."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            _flag(monkeypatch, flag)
            await env.db.roles.insert_one(_role("f4-a-finance", ["subscription.history.view", "billing.record.view",
                                                                 "billing.plan.view"], organization_id=ORG_A))
            await _user(env.db, "f4_with_project_role", ["projectuser", "f4-a-finance"], organization_id=ORG_A, projects=[PROJ_A1])
            await _user(env.db, "f4_alone", ["f4-a-finance"], organization_id=ORG_A)
            for persona in ("f4_with_project_role", "f4_alone"):
                for path in (f"/billing/records/{ORG_A}", f"/billing/history/{ORG_A}"):
                    assert await _status(env, persona, path) == 200, (persona, path, flag)
                assert await _status(env, persona, f"/billing/records/{ORG_B}") == 403, persona
                if flag:
                    # Flag on, the listing follows the same rule: the organisation's full rows.
                    # (Flag off, a projectuser's listing stays filtered to their projects by the
                    # pre-existing role-name scope; that withholds rows, it discloses nothing.)
                    rows = (await env.client.get(f"{BASE}/subscriptions", headers=_headers(persona))).json()
                    env.client.cookies.clear()
                    org_rows = [row for row in rows if row.get("organization_id") == ORG_A and not row.get("project_id")]
                    assert org_rows and "billing_status" in org_rows[0], (persona, rows)

    _run(scenario())


def test_f5_platform_rules_scoped_refusal_and_wildcard(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.policy_service import PolicyService

            superadmin = SimpleNamespace(id="user-superadmin", roles=["superadmin"], organization_id=None, organizations=[], projects=[])
            with pytest.raises(HTTPException) as refused:
                await PolicyService().authorize(superadmin, PRICING, organization_id=ORG_A, resource_type="plan", audit=False)
            assert refused.value.status_code == 403
            # A platform role holding only "*" is not the platform pricing role.
            await env.db.roles.insert_one(_role("f5-platform-wildcard", ["*"], organization_id=None))
            user_id = await _user(env.db, "f5_platform_wildcard", ["f5-platform-wildcard"], organization_id=None,
                                  account_type="contraclaim_staff")
            results = await _evaluators(env, user_id, "f5_platform_wildcard")
            assert not any(results.values()), results

    _run(scenario())
