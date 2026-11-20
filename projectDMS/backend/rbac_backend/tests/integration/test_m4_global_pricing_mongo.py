"""M4 (owner decision 2026-11-20): global pricing administration.

The plan, price-tier and add-on catalogue (``POST/PUT /plans``, ``POST/PUT /add-ons``) is
shared by every tenant. Before this fix ``PolicyService`` allowed an unscoped
``billing.plan.manage`` to anyone holding the name, so a tenant billing administrator could
reprice every organisation's plans. Now a write needs Super Admin, or
``platform.pricing.manage`` held by name on a platform role (a custom role with no
organisation or project, which applies only to a user bound to no organisation). Step-up and
the audit trail are kept.

Real HTTP through the production ``app``; nothing overridden.
Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_security_remediation_mongo import _role, _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"
STEP_UP = "billing.plan.manage"
PLAN = "m4_plan"
ADDON = "m4_addon"
PRICING = "platform.pricing.manage"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _headers(persona: str, user_id: Optional[str] = None, *, step_up: Optional[str] = STEP_UP) -> Dict[str, str]:
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


async def _seed_catalogue(env: Any) -> Tuple[str, str]:
    plan = await env.db.plans.insert_one({"code": PLAN, "name": "M4 plan", "family": "dms_saas", "base_price_minor": 1000,
                                          "pricing_tiers": {"monthly": 1000}, "is_active": True})
    addon = await env.db.addons.insert_one({"code": ADDON, "name": "M4 add-on", "price_minor": 500, "is_active": True})
    return str(plan.inserted_id), str(addon.inserted_id)


async def _writes(env: Any, persona: str, user_id: Optional[str], plan_id: str, addon_id: str,
                  *, step_up: Optional[str] = STEP_UP) -> List[int]:
    """Status of each of the four global pricing writes, in order: create plan, update plan,
    create add-on, update add-on."""
    calls = [
        ("POST", "/plans", {"code": f"{PLAN}_new_{persona}", "name": "New", "family": "dms_saas", "base_price_minor": 1}),
        ("PUT", f"/plans/{plan_id}", {"base_price_minor": 1}),
        ("POST", "/add-ons", {"code": f"{ADDON}_new_{persona}", "name": "New", "price_minor": 1}),
        ("PUT", f"/add-ons/{addon_id}", {"price_minor": 1}),
    ]
    statuses = []
    for method, path, body in calls:
        response = await env.client.request(method, f"{BASE}{path}", json=body,
                                            headers=_headers(persona, user_id, step_up=step_up))
        env.client.cookies.clear()
        statuses.append(response.status_code)
    return statuses


async def _catalogue_unchanged(env: Any) -> None:
    plan = await env.db.plans.find_one({"code": PLAN})
    addon = await env.db.addons.find_one({"code": ADDON})
    assert plan["base_price_minor"] == 1000 and addon["price_minor"] == 500
    assert await env.db.plans.count_documents({"code": {"$regex": f"^{PLAN}_new_"}}) == 0
    assert await env.db.addons.count_documents({"code": {"$regex": f"^{ADDON}_new_"}}) == 0


async def _platform_admin(env: Any, persona: str, **extra: Any) -> str:
    """A platform user (no organisation) holding the pricing permission on a platform role."""
    if not await env.db.roles.find_one({"_id": "m4-platform-pricing"}):
        await env.db.roles.insert_one(_role("m4-platform-pricing", [PRICING], organization_id=None))
    return await _user(env.db, persona, ["m4-platform-pricing"], organization_id=None, account_type="contraclaim_staff", **extra)


def test_a_tenant_billing_administrator_cannot_write_the_global_catalogue(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            plan_id, addon_id = await _seed_catalogue(env)
            billing_admin = await _user(env.db, "m4_billing_admin", ["contraclaim_billing_admin"], organization_id=ORG_A)
            role = await env.db.roles.find_one({"_id": "contraclaim_billing_admin"})
            assert "billing.plan.manage" in (role or {}).get("permissions", []), "the seeded role still holds the name"
            # Direct API calls with a valid step-up token: the policy refuses each one.
            assert await _writes(env, "m4_billing_admin", billing_admin, plan_id, addon_id) == [403] * 4
            # Organisation B's billing administrator neither.
            other = await _user(env.db, "m4_billing_admin_b", ["contraclaim_billing_admin"], organization_id=ORG_B)
            assert await _writes(env, "m4_billing_admin_b", other, plan_id, addon_id) == [403] * 4
            # Organisation administrators and members neither.
            assert await _writes(env, "org_admin", None, plan_id, addon_id) == [403] * 4
            assert await _writes(env, "org_user", None, plan_id, addon_id) == [403] * 4
            await _catalogue_unchanged(env)
            denials = await env.db.audit_events.count_documents(
                {"action": "policy.authorize", "actor_id": billing_admin, "result": "deny",
                 "reason": "platform_admin_required", "metadata.permission": PRICING}
            )
            assert denials == 4, "every refusal is audited"

    _run(scenario())


def test_the_tenant_billing_administrator_keeps_their_organisations_billing_operations(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            billing_admin = await _user(env.db, "m4_ops_admin", ["contraclaim_billing_admin"], organization_id=ORG_A)
            response = await env.client.post(
                f"{BASE}/billing-records",
                headers=_headers("m4_ops_admin", billing_admin),
                json={"organization_id": ORG_A, "line_items": [], "currency": "INR"},
            )
            assert response.status_code == 200, response.text[:300]
            # ...and never another organisation's.
            response = await env.client.post(
                f"{BASE}/billing-records",
                headers=_headers("m4_ops_admin", billing_admin),
                json={"organization_id": ORG_B, "line_items": [], "currency": "INR"},
            )
            assert response.status_code == 403

    _run(scenario())


def test_super_admin_writes_the_catalogue_with_step_up_and_an_audit_trail(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            plan_id, addon_id = await _seed_catalogue(env)
            # Step-up is still required.
            no_step_up = await _writes(env, "superadmin", None, plan_id, addon_id, step_up=None)
            assert all(code in (401, 403, 428) for code in no_step_up), no_step_up
            await _catalogue_unchanged(env)
            assert await _writes(env, "superadmin", None, plan_id, addon_id) == [200] * 4
            assert (await env.db.plans.find_one({"code": PLAN}))["base_price_minor"] == 1
            for action in ("billing.plan.upserted", "billing.plan.updated", "billing.addon.upserted", "billing.addon.updated"):
                event = await env.db.audit_events.find_one({"action": action, "actor_id": "user-superadmin"})
                assert event, action
            updated = await env.db.audit_events.find_one({"action": "billing.plan.updated"})
            assert updated["before"]["base_price_minor"] == 1000 and updated["after"]["base_price_minor"] == 1
            # An unknown id is a 404, not a 500.
            response = await env.client.put(f"{BASE}/plans/000000000000000000000000", json={"base_price_minor": 2},
                                            headers=_headers("superadmin"))
            assert response.status_code == 404

    _run(scenario())


def test_a_platform_pricing_administrator_is_allowed_only_as_a_platform_user(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            plan_id, addon_id = await _seed_catalogue(env)
            platform = await _platform_admin(env, "m4_platform")
            assert await _writes(env, "m4_platform", platform, plan_id, addon_id) == [200] * 4
            assert await env.db.audit_events.count_documents(
                {"action": "policy.authorize", "actor_id": platform, "result": "allow", "reason": "platform_grant"}
            ) == 4

            # The same platform role held by a tenant user grants nothing.
            await env.db.plans.update_one({"code": PLAN}, {"$set": {"base_price_minor": 1000}})
            await env.db.addons.update_one({"code": ADDON}, {"$set": {"price_minor": 500}})
            await env.db.plans.delete_many({"code": {"$regex": f"^{PLAN}_new_"}})
            await env.db.addons.delete_many({"code": {"$regex": f"^{ADDON}_new_"}})
            tenant = await _user(env.db, "m4_tenant_with_platform_role", ["m4-platform-pricing"], organization_id=ORG_A)
            assert await _writes(env, "m4_tenant_with_platform_role", tenant, plan_id, addon_id) == [403] * 4
            # A platform user who joins an organisation by membership row loses it too.
            joined = await _platform_admin(env, "m4_platform_joined")
            await env.db.organization_memberships.insert_one({"user_id": joined, "organization_id": ORG_A, "status": "active"})
            assert await _writes(env, "m4_platform_joined", joined, plan_id, addon_id) == [403] * 4
            await _catalogue_unchanged(env)

    _run(scenario())


def test_no_tenant_role_shape_confers_global_pricing(monkeypatch) -> None:
    """Custom roles, wildcards, built-in roles edited in the database and cross-tenant
    roles: none counts, whatever names it lists."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            plan_id, addon_id = await _seed_catalogue(env)
            await env.db.roles.insert_many([
                _role("m4-org-pricing", [PRICING], organization_id=ORG_A),
                _role("m4-org-wildcard", ["*"], organization_id=ORG_A),
                _role("m4-org-b-pricing", [PRICING], organization_id=ORG_B),
            ])
            cases = {
                "m4_org_pricing": ["m4-org-pricing"],
                "m4_org_wildcard": ["m4-org-wildcard"],
                "m4_cross_tenant": ["m4-org-b-pricing"],
                "m4_dual": ["contraclaim_billing_admin", "m4-org-pricing", "orgadmin"],
            }
            # A built-in role edited directly in the database (bypassing the role fences).
            await env.db.roles.update_one({"_id": "orgadmin"}, {"$addToSet": {"permissions": PRICING}}, upsert=False)
            for persona, roles in cases.items():
                user_id = await _user(env.db, persona, roles, organization_id=ORG_A)
                assert await _writes(env, persona, user_id, plan_id, addon_id) == [403] * 4, persona
            await _catalogue_unchanged(env)

    _run(scenario())


def test_tenants_cannot_create_a_role_carrying_the_platform_permission(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            for organization_id in (ORG_A, None):
                response = await env.client.post(
                    "/api/roles",
                    headers=_headers("org_admin", step_up="platform.role.manage"),
                    json={"name": f"m4 smuggle {organization_id}", "description": "m4", "permissions": [PRICING],
                          "organization_id": organization_id},
                )
                assert response.status_code in (400, 403, 422), (organization_id, response.status_code, response.text[:200])
            assert await env.db.roles.count_documents({"permissions": PRICING}) == 0

    _run(scenario())


def test_the_permission_is_advertised_only_where_it_is_enforced(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.permission_service import PermissionService

            await env.db.roles.insert_many([
                _role("m4-adv-wildcard", ["*"], organization_id=ORG_A),
                _role("m4-adv-org", [PRICING], organization_id=ORG_A),
            ])
            platform = await _platform_admin(env, "m4_adv_platform")
            wildcard = await _user(env.db, "m4_adv_wildcard", ["m4-adv-wildcard"], organization_id=ORG_A)
            org_role = await _user(env.db, "m4_adv_org", ["m4-adv-org"], organization_id=ORG_A)
            billing_admin = await _user(env.db, "m4_adv_billing", ["contraclaim_billing_admin"], organization_id=ORG_A)
            service = PermissionService()
            assert PRICING in await service.get_effective_permission_names(platform)
            for user_id in (wildcard, org_role, billing_admin):
                assert PRICING not in await service.get_effective_permission_names(user_id), user_id
            assert PRICING not in await service.get_effective_permission_names(wildcard, organization_id=ORG_A)

    _run(scenario())
