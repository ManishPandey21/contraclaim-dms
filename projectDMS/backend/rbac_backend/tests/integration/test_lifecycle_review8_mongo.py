"""Independent review 8 (2026-11-21) of the review-7 fixes: what was still open.

A1 an in-place upgrade of a Razorpay-bound plan was recorded although Razorpay cannot change a
plan in place (enterprise service at the starter price); A2 a tenant upgraded manual,
administrative or no-charge plans, including to a zero-priced plan; A3 concurrent first trials;
A4 a scope could trial again after an in-place conversion; B1 a tenant revived a withdrawn
administrative grant; B2 concurrent checkouts all created pending purchases; B3 an abandoned
checkout blocked the scope for ever; B4 a trial whose conversion never recorded its checkout
was never closed; C1 a refused retry hid a review item; C2 two attempts of one operation both
"held" the scope; C3 a retry after the period changed was refused as key reuse; C4 a refused
plan-settings attempt stayed "in progress"; C5 an unanswered cancel that did reach the
provider could never complete; C6 reactivation ran outside the scope lease.

Real HTTP where a route is involved; real MongoDB; the gateway is a configurable fake.
Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
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
        self.supports_in_place_plan_change = True
        self.provider_status: Optional[str] = None
        self.cancel_fails: Any = None
        self._ids = itertools.count(1)

    async def create_customer(self, *, organization_id: str, name: str, email: Optional[str] = None) -> Any:
        return SimpleNamespace(gateway_customer_id=f"cust_{organization_id}")

    async def create_subscription(self, *, gateway_customer_id: str, plan_code: str, billing_period: str,
                                  metadata: Optional[Dict[str, Any]] = None) -> Any:
        await asyncio.sleep(0.05)
        gateway_id = f"sub_rv8_{next(self._ids)}"
        self.calls.append(("create", gateway_id))
        return SimpleNamespace(gateway_subscription_id=gateway_id, metadata={"short_url": f"https://pay.example/{gateway_id}"})

    async def update_subscription(self, **kwargs: Any) -> Any:
        self.calls.append(("update", kwargs.get("gateway_subscription_id")))

    async def cancel_subscription(self, *, gateway_subscription_id: str, immediate: bool = False) -> bool:
        if self.cancel_fails:
            raise self.cancel_fails("no answer")
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
    for project in ("rv8-p2", "rv8-p3"):
        await env.db.projects.update_one({"_id": project}, {"$set": {"organization_id": ORG_A, "name": project,
                                                                     "is_active": True}}, upsert=True)
    return await _user(env.db, "rv8_billing", ["contraclaim_billing_admin"], organization_id=ORG_A,
                       projects=[PROJ_A1, "rv8-p2", "rv8-p3"])


async def _call(env: Any, path: str, body: Dict[str, Any], action: str, persona: str = "rv8_billing",
                user_id: Optional[str] = None, method: str = "POST", key: Optional[str] = None) -> Any:
    headers = _headers(persona, user_id, action=action)
    if key:
        headers["Idempotency-Key"] = key
    response = await env.client.request(method, f"{BASE}{path}", json=body, headers=headers)
    env.client.cookies.clear()
    return response


def _active(sub_id: str, *, bound: bool, project: str = PROJ_A1, **extra: Any) -> Dict[str, Any]:
    now = datetime.utcnow()
    row = {"_id": sub_id, "organization_id": ORG_A, "project_id": project, "package_id": None, "plan_code": "dms_starter",
           "billing_period": "monthly", "status": "active", "billing_status": "active" if bound else "no_charge_override",
           "governing_since": now - timedelta(days=3), "current_period_end": now + timedelta(days=25), "active_add_ons": []}
    if bound:
        row.update({"payment_provider": "razorpay", "payment_gateway_subscription_id": f"gw_{sub_id}",
                    "payment_gateway_customer_id": "cust", "payment_gateway_bound_by": "checkout"})
    row.update(extra)
    return row


UPGRADE = {"new_plan_code": "dms_enterprise", "billing_period": "annual", "effective_immediately": True}


def test_a1_an_in_place_change_needs_a_gateway_that_can_make_it(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            billing = await _billing(env)
            await env.db.subscriptions.insert_one(_active("rv8-bound", bound=True))
            gateway.supports_in_place_plan_change = False  # as RazorpayGateway declares
            for path, body, action in (("upgrade", UPGRADE, "subscription.upgrade"),
                                       ("change-period", {"new_billing_period": "annual"}, "subscription.entitlement.manage")):
                response = await _call(env, f"/subscriptions/rv8-bound/{path}", body, action, "rv8_billing", billing)
                assert response.status_code == 409 and "checkout" in response.text, (path, response.text[:200])
            row = await env.db.subscriptions.find_one({"_id": "rv8-bound"})
            assert (row["plan_code"], row["billing_period"]) == ("dms_starter", "monthly") and gateway.calls == []

    _run(scenario())


def test_a1_razorpay_declares_it_cannot_change_a_plan_in_place() -> None:
    from rbac_backend.services.payment_gateway import NoOpPaymentGateway, RazorpayGateway

    assert RazorpayGateway(key_id="k", key_secret="s").supports_in_place_plan_change is False
    assert NoOpPaymentGateway().supports_in_place_plan_change is True


def test_a2_tenants_do_not_change_unbound_plans_or_buy_unpriced_ones(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            billing = await _billing(env)
            await env.db.subscriptions.insert_many([_active("rv8-grant", bound=False),
                                                    _active("rv8-paid", bound=True, project="rv8-p2")])
            refused = await _call(env, "/subscriptions/rv8-grant/upgrade", UPGRADE, "subscription.upgrade", "rv8_billing", billing)
            assert refused.status_code == 409 and "Super Admin" in refused.text, refused.text[:200]
            free_target = {"new_plan_code": "dedicated_expert_desk", "effective_immediately": True}
            await env.db.plans.update_one({"code": "dedicated_expert_desk"}, {"$set": {"base_price_minor": 0, "pricing_tiers": {}}})
            unpriced = await _call(env, "/subscriptions/rv8-paid/upgrade", free_target, "subscription.upgrade",
                                   "rv8_billing", billing)
            assert unpriced.status_code in {400, 409}, unpriced.text[:200]
            assert (await env.db.subscriptions.find_one({"_id": "rv8-paid"}))["plan_code"] == "dms_starter"
            admin = await _call(env, "/subscriptions/rv8-grant/upgrade", UPGRADE, "subscription.upgrade", "superadmin")
            assert admin.status_code == 200, admin.text[:200]

    _run(scenario())


def test_a3_a4_one_trial_per_scope_even_concurrently_and_after_conversion(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            billing = await _billing(env)
            body = {"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_starter"}
            responses = await asyncio.gather(*[
                env.client.post(f"{BASE}/subscriptions/start-trial", json=body,
                                headers=_headers("rv8_billing", billing, action="subscription.trial.manage"))
                for _ in range(4)
            ])
            assert sorted(r.status_code for r in responses) == [200, 409, 409, 409], [r.status_code for r in responses]
            assert await env.db.subscriptions.count_documents({"project_id": PROJ_A1, "status": "trial"}) == 1
            # Even with every trial row gone (converted in place and cancelled), the marker stays.
            await env.db.subscriptions.delete_many({"project_id": PROJ_A1})
            again = await _call(env, "/subscriptions/start-trial", body, "subscription.trial.manage", "rv8_billing", billing)
            assert again.status_code == 409 and "already had a trial" in again.text

    _run(scenario())


def test_b1_a_withdrawn_or_unbilled_plan_is_restored_only_by_super_admin(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            billing = await _billing(env)
            await env.db.subscriptions.insert_many([
                {**_active("rv8-withdrawn", bound=False), "status": "cancelled",
                 "cancellation_reason": "replaced_by_plan_settings", "cancellation_operation_id": "op_x"},
                {**_active("rv8-admin-cancel", bound=True, project="rv8-p3"), "status": "cancelled",
                 "cancellation_reason": "replaced_by_plan_settings", "cancellation_operation_id": "op_y"},
                # Not billed through the gateway, cancelled by the customer (not administratively).
                {**_active("rv8-manual", bound=False, project="rv8-p2"), "status": "cancelled",
                 "cancellation_reason": "customer request"},
            ])
            for sub_id in ("rv8-withdrawn", "rv8-admin-cancel", "rv8-manual"):
                response = await _call(env, f"/subscriptions/{sub_id}/reactivate", {}, "subscription.upgrade",
                                       "rv8_billing", billing)
                assert response.status_code == 409 and "Super Admin" in response.text, (sub_id, response.text[:200])
                assert (await env.db.subscriptions.find_one({"_id": sub_id}))["status"] == "cancelled"
            restored = await _call(env, "/subscriptions/rv8-withdrawn/reactivate", {}, "subscription.upgrade", "superadmin")
            assert restored.status_code == 200, restored.text[:200]

    _run(scenario())


def test_b2_concurrent_checkouts_for_one_scope_create_one_purchase(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            body = {"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_starter"}
            responses = await asyncio.gather(*[
                env.client.post(f"{BASE}/subscriptions/checkout", json=body,
                                headers=_headers("superadmin", None, action="subscription.checkout"))
                for _ in range(3)
            ])
            assert sorted(r.status_code for r in responses) == [200, 409, 409], [r.status_code for r in responses]
            assert await env.db.subscriptions.count_documents({"project_id": PROJ_A1, "status": "pending"}) == 1
            assert len([c for c in gateway.calls if c[0] == "create"]) == 1

    _run(scenario())


def test_b3_an_abandoned_checkout_is_cancelled_at_the_gateway_before_a_new_one(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            stale = {**_active("rv8-abandoned", bound=True), "status": "pending", "billing_status": "pending",
                     "created_at": datetime.utcnow() - timedelta(days=3)}
            await env.db.subscriptions.insert_one(stale)
            body = {"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_starter"}
            gateway.cancel_fails = TimeoutError
            blocked = await _call(env, "/subscriptions/checkout", body, "subscription.checkout", "superadmin")
            assert blocked.status_code == 409, "never a second purchase while the old one may still be paid"
            gateway.cancel_fails = None
            fresh = await _call(env, "/subscriptions/checkout", body, "subscription.checkout", "superadmin")
            assert fresh.status_code == 200, fresh.text[:200]
            assert ("cancel", "gw_rv8-abandoned", True) in gateway.calls
            old = await env.db.subscriptions.find_one({"_id": "rv8-abandoned"})
            assert (old["status"], old["cancellation_reason"]) == ("cancelled", "checkout_abandoned")

    _run(scenario())


def test_b4_a_paid_conversion_closes_its_trial_even_if_the_claim_never_recorded_it(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            now = datetime.utcnow()
            await env.db.subscriptions.insert_many([
                {"_id": "rv8-trial", "organization_id": ORG_A, "project_id": PROJ_A1, "package_id": None,
                 "plan_code": "dms_starter", "status": "trial", "trial": True, "billing_status": "trial",
                 "trial_ends_at": now + timedelta(days=5), "governing_since": now - timedelta(days=2),
                 "conversion": {"state": "starting", "requested_at": now - timedelta(hours=1)}},
                {"_id": "rv8-orphan", "organization_id": ORG_A, "project_id": PROJ_A1, "package_id": None,
                 "plan_code": "dms_starter", "billing_period": "monthly", "status": "pending", "billing_status": "pending",
                 "converts_trial_id": "rv8-trial", "payment_provider": "razorpay",
                 "payment_gateway_subscription_id": "sub_orphan_paid", "payment_gateway_customer_id": "cust",
                 "payment_gateway_bound_by": "checkout"},
            ])
            paid = await _webhook(env, "subscription.activated", "sub_orphan_paid", event_id="evt-rv8-b4",
                                  entity={"customer_id": "cust"},
                                  payment={"id": "pay_rv8", "amount": 2500000, "currency": "INR"})
            assert paid.status_code == 200 and paid.json().get("changed") is True, paid.text[:200]
            trial = await env.db.subscriptions.find_one({"_id": "rv8-trial"})
            assert (trial["status"], trial["conversion"]["state"]) == ("cancelled", "paid")

    _run(scenario())


def test_c1_c4_a_refused_attempt_leaves_the_operation_as_it_was(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import subscription_operations as ops

            await env.db.subscriptions.insert_one(_active("rv8-up", bound=True))

            async def disagree(**kwargs: Any) -> None:
                gateway.calls.append(("update", kwargs.get("gateway_subscription_id")))
                await env.db.subscriptions.update_one({"_id": "rv8-up"}, {"$set": {"billing_period": "quarterly"}})

            gateway.update_subscription = disagree  # type: ignore[method-assign]
            first = await _call(env, "/subscriptions/rv8-up/upgrade", {"new_plan_code": "dms_professional",
                                "billing_period": "monthly"}, "subscription.upgrade", "superadmin", key="k-c1")
            assert first.status_code == 409
            assert await env.db.subscription_operations.count_documents({"kind": "upgrade", "status": "needs_review"}) == 1
            await ops.lock_scope(env.db, ORG_A, PROJ_A1, "op_elsewhere")
            retried = await _call(env, "/subscriptions/rv8-up/upgrade", {"new_plan_code": "dms_professional",
                                  "billing_period": "monthly"}, "subscription.upgrade", "superadmin", key="k-c1")
            assert retried.status_code == 409
            assert await env.db.subscription_operations.count_documents({"kind": "upgrade", "status": "needs_review"}) == 1, \
                "C1: the review item is still listed"
            # C4: a plan-settings attempt refused by the held scope is not left "in progress".
            response = await _call(env, "/plan-settings/scope", {"organization_id": ORG_A, "project_id": PROJ_A1,
                                   "mode": "inherit"}, "subscription.entitlement.manage", "superadmin", method="PUT", key="k-c4")
            assert response.status_code == 409
            op = await env.db.subscription_operations.find_one({"kind": "plan_settings_inherit"})
            assert op["status"] == "failed" and op["lease_until"] is None

    _run(scenario())


def test_c2_a_superseded_attempt_cannot_record_anything(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import subscription_operations as ops

            first = await ops.begin(env.db, kind="plan_settings_inherit", organization_id=ORG_A, project_id=PROJ_A1,
                                    idempotency_key="k-c2", request={"mode": "inherit"}, requested_by="a")
            await env.db.subscription_operations.update_one({"_id": first["_id"]},
                                                            {"$set": {"lease_until": datetime.utcnow() - timedelta(minutes=1)}})
            second = await ops.begin(env.db, kind="plan_settings_inherit", organization_id=ORG_A, project_id=PROJ_A1,
                                     idempotency_key="k-c2", request={"mode": "inherit"}, requested_by="b")
            assert second["token"] != first["token"]
            with pytest.raises(ops.LeaseLost):
                await ops.finish(env.db, first["_id"], "completed", token=first["token"])
            with pytest.raises(ops.LeaseLost):
                await ops.save_items(env.db, first["_id"], [{"x": 1}], token=first["token"])
            # Even while the superseded attempt still holds the scope lock, its operation token is stale.
            await ops.lock_scope(env.db, ORG_A, PROJ_A1, first["token"])
            with pytest.raises(ops.LeaseLost):
                await ops.renew(env.db, ORG_A, PROJ_A1, first["_id"], first["token"])
            await ops.unlock_scope(env.db, ORG_A, PROJ_A1, first["token"])
            await ops.lock_scope(env.db, ORG_A, PROJ_A1, second["token"])
            with pytest.raises(ops.LeaseLost):
                await ops.renew(env.db, ORG_A, PROJ_A1, first["_id"], first["token"])
            stored = await env.db.subscription_operations.find_one({"_id": first["_id"]})
            assert stored["status"] == "in_progress" and stored["items"] == []

    _run(scenario())


def test_c3_a_retry_after_the_period_changed_is_not_mistaken_for_key_reuse(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_active("rv8-c3", bound=True))

            async def period_changed(**kwargs: Any) -> None:
                gateway.calls.append(("update", kwargs.get("gateway_subscription_id")))
                await env.db.subscriptions.update_one({"_id": "rv8-c3"}, {"$set": {"billing_period": "quarterly"}})

            gateway.update_subscription = period_changed  # type: ignore[method-assign]
            body = {"new_plan_code": "dms_professional", "effective_immediately": True}  # period omitted
            first = await _call(env, "/subscriptions/rv8-c3/upgrade", body, "subscription.upgrade", "superadmin", key="k-c3")
            assert first.status_code == 409
            retried = await _call(env, "/subscriptions/rv8-c3/upgrade", body, "subscription.upgrade", "superadmin", key="k-c3")
            assert retried.status_code == 409 and "review queue" in retried.text, retried.text[:200]
            assert "different request" not in retried.text
            assert len([c for c in gateway.calls if c[0] == "update"]) == 1

    _run(scenario())


def test_c5_an_unanswered_cancel_the_provider_did_apply_completes_without_repeating(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_active("rv8-c5", bound=True))
            gateway.cancel_fails = TimeoutError
            first = await _call(env, "/subscriptions/rv8-c5/cancel", {"immediate": False}, "subscription.cancel",
                                "superadmin", key="k-c5")
            assert first.status_code == 502 and "unknown" in first.text
            gateway.cancel_fails = RuntimeError  # a repeated cancel would now fail at the provider
            gateway.provider_status = "cancelled"
            retried = await _call(env, "/subscriptions/rv8-c5/cancel", {"immediate": False}, "subscription.cancel",
                                  "superadmin", key="k-c5")
            assert retried.status_code == 200, retried.text[:200]
            assert (await env.db.subscriptions.find_one({"_id": "rv8-c5"}))["status"] == "cancelled"

    _run(scenario())


def test_c6_reactivation_waits_for_the_scope(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import subscription_operations as ops

            await env.db.subscriptions.insert_one({**_active("rv8-c6", bound=True), "status": "cancelled"})
            await ops.lock_scope(env.db, ORG_A, PROJ_A1, "op_plan_settings_running")
            response = await _call(env, "/subscriptions/rv8-c6/reactivate", {}, "subscription.upgrade", "superadmin")
            assert response.status_code == 409 and gateway.calls == []
            assert (await env.db.subscriptions.find_one({"_id": "rv8-c6"}))["status"] == "cancelled"

    _run(scenario())
