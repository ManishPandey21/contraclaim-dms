"""Independent review 9 (2026-11-21) of 5255dd5: the remaining Medium/Low gaps.

R9-1 checkout over a paid plan did not warn that the old plan keeps billing; R9-2 a tenant could
check out a zero-priced plan and any payment activated it; R9-3 a failed trial creation locked
the scope out of trials for ever; R9-4 an "abandoned" checkout the provider shows as paid was
cancelled; R9-5 a checkout whose lease lapsed during the gateway call was recorded beside
another; R9-6 a binding of another provider was changed under the configured one; R9-7 an
administrative cancellation was recognised by an editable field; R9-8/R9-9 stale conversion
checkouts blocked conversion; R9-10 a gateway failure at checkout was a 500.

Real HTTP; real MongoDB; the gateway is a configurable fake. Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
"""

from __future__ import annotations

import asyncio
import itertools
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_gateway_identity_protection_mongo import SECRET, _headers, _webhook
from rbac_backend.tests.integration.test_security_remediation_mongo import _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeGateway:
    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.provider_status: Optional[str] = None
        self.create_fails: Any = None
        self.during_create: Any = None
        self._ids = itertools.count(1)
        self.supports_in_place_plan_change = True

    async def create_customer(self, *, organization_id: str, name: str, email: Optional[str] = None) -> Any:
        return SimpleNamespace(gateway_customer_id=f"cust_{organization_id}")

    async def create_subscription(self, *, gateway_customer_id: str, plan_code: str, billing_period: str,
                                  metadata: Optional[Dict[str, Any]] = None) -> Any:
        if self.create_fails:
            raise self.create_fails("no answer")
        if self.during_create:
            await self.during_create()
        gateway_id = f"sub_rv9_{next(self._ids)}"
        self.calls.append(("create", gateway_id))
        return SimpleNamespace(gateway_subscription_id=gateway_id, metadata={"short_url": f"https://pay.example/{gateway_id}"})

    async def update_subscription(self, **kwargs: Any) -> Any:
        self.calls.append(("update", kwargs.get("gateway_subscription_id")))

    async def cancel_subscription(self, *, gateway_subscription_id: str, immediate: bool = False) -> bool:
        self.calls.append(("cancel", gateway_subscription_id, immediate))
        return True

    async def reactivate_subscription(self, *, gateway_subscription_id: str) -> Any:
        self.calls.append(("resume", gateway_subscription_id))
        return SimpleNamespace(status="active")

    async def fetch_subscription_status(self, *, gateway_subscription_id: str) -> Optional[str]:
        return self.provider_status


@pytest.fixture()
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    from rbac_backend.core.config import settings
    from rbac_backend.routers import rbac_monetization
    from rbac_backend.services import monetization_service

    fake = FakeGateway()
    monkeypatch.setattr(monetization_service, "get_payment_gateway", lambda *a, **k: fake)
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "razorpay", raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", SECRET, raising=False)
    monkeypatch.setattr(rbac_monetization.monetization_mutation_limiter, "max_requests", 10_000)
    return fake


async def _billing(env: Any) -> str:
    return await _user(env.db, "rv9_billing", ["contraclaim_billing_admin"], organization_id=ORG_A, projects=[PROJ_A1])


async def _call(env: Any, path: str, body: Dict[str, Any], action: str, persona: str = "superadmin",
                user_id: Optional[str] = None, method: str = "POST") -> Any:
    response = await env.client.request(method, f"{BASE}{path}", json=body, headers=_headers(persona, user_id, action=action))
    env.client.cookies.clear()
    return response


def _row(sub_id: str, *, status: str = "active", provider: str = "razorpay", **extra: Any) -> Dict[str, Any]:
    now = datetime.utcnow()
    return {"_id": sub_id, "organization_id": ORG_A, "project_id": PROJ_A1, "package_id": None, "plan_code": "dms_starter",
            "billing_period": "monthly", "status": status, "billing_status": "active", "auto_renew": True,
            "governing_since": now - timedelta(days=3), "current_period_end": now + timedelta(days=25),
            "active_add_ons": [], "payment_provider": provider, "payment_gateway_subscription_id": f"gw_{sub_id}",
            "payment_gateway_customer_id": "cust", "payment_gateway_bound_by": "checkout", **extra}


CHECKOUT = {"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_starter"}


def test_r9_1_checkout_over_a_paid_plan_warns_that_it_keeps_billing(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_row("rv9-paid"))
            response = await _call(env, "/subscriptions/checkout", {**CHECKOUT, "plan_code": "dms_professional"},
                                   "subscription.checkout")
            assert response.status_code == 200 and "keeps billing" in response.json().get("warning", ""), response.text[:200]

    _run(scenario())


def test_r9_2_zero_priced_plans_are_not_sold_or_paid_into_service(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            billing = await _billing(env)
            for plan in ("no_service_override", "dedicated_expert_desk"):  # the seeded zero-priced plans
                response = await _call(env, "/subscriptions/checkout", {**CHECKOUT, "plan_code": plan},
                                       "subscription.checkout", "rv9_billing", billing)
                assert response.status_code in {400, 409}, (plan, response.text[:200])
            assert gateway.calls == []
            await env.db.subscriptions.insert_one({**_row("rv9-free-pending", status="pending"),
                                                   "plan_code": "dedicated_expert_desk", "billing_status": "pending"})
            paid = await _webhook(env, "subscription.activated", "gw_rv9-free-pending", event_id="evt-rv9-2",
                                  entity={"customer_id": "cust"}, payment={"id": "pay_rv9_2", "amount": 100, "currency": "INR"})
            assert paid.status_code == 200 and paid.json().get("changed") is not True, paid.text[:200]
            assert (await env.db.subscriptions.find_one({"_id": "rv9-free-pending"}))["status"] == "pending"

    _run(scenario())


def test_r9_3_a_failed_trial_creation_leaves_the_scope_able_to_trial(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.monetization_service import MonetizationService

            billing = await _billing(env)
            original = MonetizationService.create_subscription
            failures = {"left": 1}

            async def flaky(self: Any, payload: Any, current_user: Any) -> Any:
                if failures["left"]:
                    failures["left"] -= 1
                    raise ConnectionError("database blip")
                return await original(self, payload, current_user)

            monkeypatch.setattr(MonetizationService, "create_subscription", flaky)
            body = {"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_starter"}
            try:
                failed = await _call(env, "/subscriptions/start-trial", body, "subscription.trial.manage", "rv9_billing", billing)
                assert failed.status_code >= 500
            except ConnectionError:
                pass  # depending on the client, the server error is raised instead of answered
            assert failures["left"] == 0
            retried = await _call(env, "/subscriptions/start-trial", body, "subscription.trial.manage", "rv9_billing", billing)
            assert retried.status_code == 200, retried.text[:200]
            # And a Super Admin can grant a further trial, audited.
            await env.db.subscriptions.update_many({"project_id": PROJ_A1}, {"$set": {"status": "cancelled"}})
            regranted = await _call(env, "/subscriptions/start-trial", body, "subscription.trial.manage")
            assert regranted.status_code == 200, regranted.text[:200]
            assert await env.db.audit_events.count_documents({"action": "subscription.trial_regranted"}) == 1

    _run(scenario())


def test_r9_4_an_old_checkout_the_provider_shows_paid_is_never_cancelled(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one({**_row("rv9-late", status="pending"), "billing_status": "pending",
                                                   "created_at": datetime.utcnow() - timedelta(days=3)})
            gateway.provider_status = "active"
            response = await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")
            assert response.status_code == 409 and "paid at the provider" in response.text, response.text[:200]
            assert (await env.db.subscriptions.find_one({"_id": "rv9-late"}))["status"] == "pending"
            assert not [c for c in gateway.calls if c[0] in {"cancel", "create"}]
            assert await env.db.audit_events.count_documents(
                {"action": "subscription.pending_checkout_paid_at_provider", "metadata.requires_admin_review": True}) == 1

    _run(scenario())


def test_r9_5_a_checkout_that_lost_its_lease_is_not_recorded(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            async def lease_taken() -> None:
                await env.db.subscription_operation_locks.update_one({"_id": f"{ORG_A}|{PROJ_A1}"},
                                                                     {"$set": {"holder": "checkout:someone-else"}})

            gateway.during_create = lease_taken
            response = await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")
            assert response.status_code == 409 and "review" in response.text, response.text[:200]
            assert await env.db.subscriptions.count_documents({"project_id": PROJ_A1, "status": "pending"}) == 0
            assert await env.db.audit_events.count_documents({"action": "subscription.checkout_lease_lost"}) == 1

    _run(scenario())


def test_r9_6_a_binding_of_another_provider_is_not_changed_here(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_row("rv9-stripe", provider="stripe"))
            response = await _call(env, "/subscriptions/rv9-stripe/upgrade",
                                   {"new_plan_code": "dms_enterprise", "effective_immediately": True}, "subscription.upgrade")
            assert response.status_code == 409 and "another payment provider" in response.text, response.text[:200]
            assert (await env.db.subscriptions.find_one({"_id": "rv9-stripe"}))["plan_code"] == "dms_starter"

    _run(scenario())


def test_r9_7_an_administrative_cancellation_survives_an_edited_reason(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            billing = await _billing(env)
            await env.db.subscriptions.insert_one({**_row("rv9-admin", status="cancelled"),
                                                   "cancellation_reason": "checkout_abandoned",
                                                   "cancelled_administratively": True})
            edited = await _call(env, "/subscriptions/rv9-admin", {"cancellation_reason": "customer"},
                                 "subscription.entitlement.manage", "rv9_billing", billing, method="PUT")
            assert edited.status_code == 200
            refused = await _call(env, "/subscriptions/rv9-admin/reactivate", {}, "subscription.upgrade", "rv9_billing", billing)
            assert refused.status_code == 409 and "administratively" in refused.text, refused.text[:200]
            sneaky = await _call(env, "/subscriptions/rv9-admin", {"cancelled_administratively": False},
                                 "subscription.entitlement.manage", "rv9_billing", billing, method="PUT")
            assert sneaky.status_code == 400

    _run(scenario())


def test_r9_8_r9_9_stale_checkouts_do_not_block_a_conversion(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            now = datetime.utcnow()
            trial = {"_id": "rv9-trial", "organization_id": ORG_A, "project_id": PROJ_A1, "package_id": None,
                     "plan_code": "dms_starter", "billing_period": "monthly", "status": "trial", "trial": True,
                     "billing_status": "trial", "trial_ends_at": now + timedelta(days=5), "ends_at": now + timedelta(days=5),
                     "governing_since": now - timedelta(days=4), "active_add_ons": [],
                     "conversion": {"state": "pending_payment", "subscription_id": "rv9-gone"}}
            await env.db.subscriptions.insert_many([
                trial,
                {**_row("rv9-gone", status="cancelled"), "converts_trial_id": "rv9-trial"},
                {**_row("rv9-stale", status="pending"), "billing_status": "pending", "created_at": now - timedelta(days=3)},
            ])
            response = await _call(env, "/subscriptions/rv9-trial/convert-trial", {"billing_period": "monthly"},
                                   "subscription.trial.manage")
            assert response.status_code == 200 and response.json()["status"] == "pending_payment", response.text[:200]
            assert response.json()["subscription_id"] not in {"rv9-gone", "rv9-stale"}
            assert ("cancel", "gw_rv9-stale", True) in gateway.calls
            assert (await env.db.subscriptions.find_one({"_id": "rv9-stale"}))["status"] == "cancelled"

    _run(scenario())


def test_r9_10_a_gateway_failure_at_checkout_is_502_and_releases_the_scope(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            gateway.create_fails = TimeoutError
            response = await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")
            assert response.status_code == 502, response.text[:200]
            assert await env.db.subscription_operation_locks.count_documents({}) == 0

    _run(scenario())
