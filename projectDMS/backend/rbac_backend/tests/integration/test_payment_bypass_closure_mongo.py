"""Independent review 7 (2026-11-21), F1-F4: no route grants paid service without payment.

A tenant billing administrator holds ``subscription.entitlement.manage``, which the policy
accepts for every ``subscription.*`` action. Before:

- F1 ``POST /subscriptions`` created an active, "paid" (``billing_status=active``) enterprise
  subscription ending in 2099 with add-ons and entitlement overrides - no payment, no gateway;
- F2 ``upgrade`` on a running trial made it ``active``/``billing_status=active`` on the top plan;
- F3 ``add-ons/add`` added a priced add-on that nothing ever charges;
- F4 ``start-trial`` accepted a 100-year trial, repeatedly, over an existing subscription.

Now: direct creation is Super Admin only and never writes ``billing_status=active``; plan,
period and add-on changes refuse trials (they convert through convert-trial, R6-5) and an
upgrade leaves status and billing status alone; priced add-ons need Super Admin (manually
invoiced, audited); trials are bounded by the plan, one per scope, never over a governing
subscription (Super Admin may extend, audited).

Real HTTP; the gateway is a recording fake. Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_gateway_identity_protection_mongo import _headers
from rbac_backend.tests.integration.test_security_remediation_mongo import _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"
TRIAL = "pb-trial"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeGateway:
    def __init__(self) -> None:
        self.calls: List[tuple] = []

    def __getattr__(self, name: str) -> Any:
        async def record(**kwargs: Any) -> Any:
            self.calls.append((name, kwargs))
            return None
        return record


@pytest.fixture()
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    from rbac_backend.routers import rbac_monetization
    from rbac_backend.services import monetization_service

    fake = FakeGateway()
    monkeypatch.setattr(monetization_service, "get_payment_gateway", lambda *a, **k: fake)
    monkeypatch.setattr(rbac_monetization.monetization_mutation_limiter, "max_requests", 10_000)
    return fake


async def _billing_admin(env: Any) -> str:
    return await _user(env.db, "pb_billing", ["contraclaim_billing_admin"], organization_id=ORG_A, projects=[PROJ_A1])


async def _call(env: Any, method: str, path: str, body: Dict[str, Any], action: str, persona: str = "pb_billing",
                user_id: Optional[str] = None) -> Any:
    response = await env.client.request(method, f"{BASE}{path}", json=body, headers=_headers(persona, user_id, action=action))
    env.client.cookies.clear()
    return response


def _trial(project: str = PROJ_A1) -> Dict[str, Any]:
    now = datetime.utcnow()
    return {"_id": TRIAL, "organization_id": ORG_A, "project_id": project, "package_id": None, "plan_code": "dms_starter",
            "billing_period": "monthly", "status": "trial", "billing_status": "trial", "trial": True,
            "starts_at": now - timedelta(days=2), "ends_at": now + timedelta(days=12),
            "trial_ends_at": now + timedelta(days=12), "governing_since": now - timedelta(days=2), "active_add_ons": []}


def test_f1_direct_creation_is_super_admin_only_and_never_paid(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            billing = await _billing_admin(env)
            free_lunch = {"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_enterprise", "status": "active",
                          "billing_status": "active", "ends_at": "2099-01-01T00:00:00",
                          "active_add_ons": ["addon_priority_support"], "entitlement_overrides": {"max_users": 100000}}
            refused = await _call(env, "POST", "/subscriptions", free_lunch, "subscription.entitlement.manage", "pb_billing", billing)
            assert refused.status_code == 403, refused.text[:200]
            assert await env.db.subscriptions.count_documents({"plan_code": "dms_enterprise", "project_id": PROJ_A1}) == 0
            paid = await _call(env, "POST", "/subscriptions", free_lunch, "subscription.entitlement.manage", "superadmin")
            assert paid.status_code == 400 and "payment confirmation" in paid.text, paid.text[:200]
            unknown_plan = await _call(env, "POST", "/subscriptions", {"organization_id": ORG_A, "plan_code": "nope"},
                                       "subscription.entitlement.manage", "superadmin")
            assert unknown_plan.status_code == 400
            manual = await _call(env, "POST", "/subscriptions",
                                 {k: v for k, v in free_lunch.items() if k != "billing_status"},
                                 "subscription.entitlement.manage", "superadmin")
            assert manual.status_code == 200 and manual.json()["billing_status"] == "manual", manual.text[:200]
            assert gateway.calls == []

    _run(scenario())


def test_f2_f3_a_trial_is_not_upgraded_or_given_paid_add_ons(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            billing = await _billing_admin(env)
            await env.db.subscriptions.insert_one(_trial())
            before = await env.db.subscriptions.find_one({"_id": TRIAL})
            for path, body, action in (
                (f"/subscriptions/{TRIAL}/upgrade", {"new_plan_code": "dms_enterprise", "effective_immediately": True},
                 "subscription.upgrade"),
                (f"/subscriptions/{TRIAL}/add-ons/add", {"add_on_code": "addon_priority_support"},
                 "subscription.addon.manage"),
                (f"/subscriptions/{TRIAL}/change-period", {"new_billing_period": "annual"}, "subscription.entitlement.manage"),
            ):
                response = await _call(env, "POST", path, body, action, "pb_billing", billing)
                assert response.status_code == 409 and "convert" in response.text, (path, response.text[:200])
            assert await env.db.subscriptions.find_one({"_id": TRIAL}) == before and gateway.calls == []

    _run(scenario())


def test_f2_an_upgrade_never_writes_a_payment_state(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            now = datetime.utcnow()
            await env.db.subscriptions.insert_one({
                "_id": "pb-manual", "organization_id": ORG_A, "project_id": PROJ_A1, "package_id": None,
                "plan_code": "dms_starter", "billing_period": "monthly", "status": "active", "billing_status": "manual",
                "governing_since": now - timedelta(days=3), "active_add_ons": []})
            response = await _call(env, "POST", "/subscriptions/pb-manual/upgrade",
                                   {"new_plan_code": "dms_professional", "effective_immediately": True},
                                   "subscription.upgrade", "superadmin")
            assert response.status_code == 200, response.text[:200]
            row = await env.db.subscriptions.find_one({"_id": "pb-manual"})
            assert (row["plan_code"], row["status"], row["billing_status"]) == ("dms_professional", "active", "manual")

    _run(scenario())


def test_f3_priced_add_ons_need_super_admin_and_are_audited(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            billing = await _billing_admin(env)
            now = datetime.utcnow()
            await env.db.subscriptions.insert_one({
                "_id": "pb-pro", "organization_id": ORG_A, "project_id": PROJ_A1, "package_id": None,
                "plan_code": "dms_professional", "billing_period": "monthly", "status": "active", "billing_status": "active",
                "governing_since": now - timedelta(days=3), "active_add_ons": []})
            body = {"add_on_code": "addon_priority_support"}
            refused = await _call(env, "POST", "/subscriptions/pb-pro/add-ons/add", body, "subscription.addon.manage",
                                  "pb_billing", billing)
            assert refused.status_code == 409 and "priced" in refused.text, refused.text[:200]
            assert (await env.db.subscriptions.find_one({"_id": "pb-pro"}))["active_add_ons"] == []
            granted = await _call(env, "POST", "/subscriptions/pb-pro/add-ons/add", body, "subscription.addon.manage",
                                  "superadmin")
            assert granted.status_code == 200, granted.text[:200]
            assert await env.db.audit_events.count_documents(
                {"action": "subscription.priced_addon_added_manual_billing", "metadata.requires_admin_review": True}) == 1

    _run(scenario())


def test_f4_trials_are_bounded_single_and_never_over_a_subscription(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            billing = await _billing_admin(env)

            async def start(body: Dict[str, Any], persona: str = "pb_billing", user_id: Optional[str] = billing) -> Any:
                return await _call(env, "POST", "/subscriptions/start-trial", body, "subscription.trial.manage", persona, user_id)

            century = await start({"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_enterprise",
                                   "trial_days": 36500})
            assert century.status_code == 409, century.text[:200]
            first = await start({"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_enterprise"})
            assert first.status_code == 200, first.text[:200]
            second = await start({"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_starter"})
            assert second.status_code == 409, "one trial per scope"
            assert await env.db.subscriptions.count_documents({"project_id": PROJ_A1, "status": "trial"}) == 1
            # Even once that trial has ended (it no longer governs), the scope gets no second trial.
            await env.db.subscriptions.update_many({"project_id": PROJ_A1, "status": "trial"}, {"$set": {"status": "cancelled"}})
            again = await start({"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_starter"})
            assert again.status_code == 409 and "already had a trial" in again.text, again.text[:200]
            # Over a governing paid subscription: refused, Super Admin included.
            now = datetime.utcnow()
            await env.db.subscriptions.insert_one({
                "_id": "pb-paid-p2", "organization_id": ORG_A, "project_id": "pb-proj-2", "package_id": None,
                "plan_code": "dms_starter", "status": "active", "billing_status": "active",
                "governing_since": now - timedelta(days=1)})
            await env.db.projects.update_one({"_id": "pb-proj-2"}, {"$set": {"organization_id": ORG_A, "name": "p2",
                                                                              "is_active": True}}, upsert=True)
            over = await start({"organization_id": ORG_A, "project_id": "pb-proj-2", "plan_code": "dms_starter"}, "superadmin", None)
            assert over.status_code == 409, over.text[:200]
            # Super Admin may extend a trial beyond the plan's length (audited).
            await env.db.projects.update_one({"_id": "pb-proj-3"}, {"$set": {"organization_id": ORG_A, "name": "p3",
                                                                              "is_active": True}}, upsert=True)
            extended = await start({"organization_id": ORG_A, "project_id": "pb-proj-3", "plan_code": "dms_starter",
                                    "trial_days": 45}, "superadmin", None)
            assert extended.status_code == 200, extended.text[:200]
            assert await env.db.audit_events.count_documents({"action": "subscription.trial_extended"}) == 1

    _run(scenario())
