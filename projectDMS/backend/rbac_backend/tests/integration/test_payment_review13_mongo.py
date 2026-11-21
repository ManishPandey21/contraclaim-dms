"""Independent re-review 13 of the review-12 fixes (fdef504): each finding reproduced at fdef504
(outputs kept with the session's evidence), then fixed and pinned here. X1 runs with every flag
off. Real HTTP and real MongoDB; the provider is a fake. Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import pytest

from rbac_backend.tests.integration.test_checkout_replacement_mongo import (
    REPLACE, SOURCE, ScheduledProvider, _creates, _seed,
)
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_provider_reliability_mongo import CHECKOUT, _bound, _call, _ops
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration
STEP = "subscription.entitlement.manage"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture()
def provider(monkeypatch: pytest.MonkeyPatch) -> ScheduledProvider:
    from rbac_backend.core.config import settings
    from rbac_backend.routers import rbac_monetization
    from rbac_backend.services import monetization_service
    from rbac_backend.tests.integration.test_gateway_identity_protection_mongo import SECRET

    fake = ScheduledProvider()
    monkeypatch.setattr(monetization_service, "get_payment_gateway", lambda *a, **k: fake)
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "razorpay", raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", SECRET, raising=False)
    monkeypatch.setattr(rbac_monetization.monetization_mutation_limiter, "max_requests", 10_000)
    return fake


async def _lapse(env: Any) -> None:
    past = datetime.utcnow() - timedelta(seconds=1)
    await env.db.subscription_operations.update_many({"status": "in_progress"}, {"$set": {"lease_until": past}})
    await env.db.subscription_operation_locks.update_many({}, {"$set": {"lease_until": past}})


# R13-1 (flags off): a plain checkout slower than its lease, classified by another request,
# never reaches the provider's create afterwards.
def test_r13_1_a_stalled_checkout_classified_elsewhere_never_creates(monkeypatch, provider) -> None:
    async def scenario() -> None:
        from rbac_backend.core.config import settings

        assert not settings.CHECKOUT_REPLACEMENT_ENABLED
        async with _env(monkeypatch) as env:
            gate, entered = asyncio.Event(), asyncio.Event()
            orig_cust = provider.create_customer
            n = {"c": 0}

            async def slow_customer(**kw: Any) -> Any:
                n["c"] += 1
                if n["c"] == 1:
                    entered.set()
                    await gate.wait()
                return await orig_cust(**kw)

            provider.create_customer = slow_customer  # type: ignore[method-assign]
            a = asyncio.create_task(_call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout"))
            await entered.wait()
            (op_a,) = await _ops(env, "checkout")
            await _lapse(env)  # A's create_customer ran past the lease
            b = await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")
            assert b.status_code == 200, b.text[:200]
            gate.set()
            ra = await a
            assert ra.status_code == 409 and "nothing was created" in ra.text, ra.text[:200]
            assert len(_creates(provider)) == 1, "one provider subscription for the scope"
            stored = await env.db.subscription_operations.find_one({"_id": op_a["_id"]})
            assert stored["status"] == "failed" and not stored["items"][0].get("create_started")

    _run(scenario())


# R13-2: an operation written by the previous version just before its create (customer id saved,
# no marker) is treated as possibly created - never created again.
def test_r13_2_an_old_record_interrupted_at_the_create_is_not_created_again(monkeypatch, provider) -> None:
    async def scenario() -> None:
        from rbac_backend.core.config import settings

        monkeypatch.setattr(settings, "CHECKOUT_REPLACEMENT_ENABLED", True, raising=False)
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            provider.fetch_failure = RuntimeError("unreadable")
            r = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="kold")
            assert r.status_code in {409, 502}, r.text
            provider.fetch_failure = None
            (op,) = await _ops(env, "replacement")
            past = datetime.utcnow() - timedelta(minutes=5)
            items = [{"role": "target", "reference": op["_id"], "gateway": "pending", "attempts": 1, "last_error": None,
                      "customer_id": f"cust_{ORG_A}"},
                     {"role": "source", "subscription_id": SOURCE, "status_before": "active",
                      "gateway_subscription_id": f"gw_{SOURCE}", "gateway": "awaiting_target_payment",
                      "local": "unchanged", "effective_at": provider.cycle_end, "evidence": {}}]
            await env.db.subscription_operations.update_one({"_id": op["_id"]}, {"$set": {
                "status": "in_progress", "lease_until": past, "items": items}})
            provider.subscriptions["sub_old"] = {"status": "created", "customer_id": f"cust_{ORG_A}",
                                                 "plan_id": "dms_professional",
                                                 "notes": {"checkout_reference": op["_id"]}}
            await env.db.subscription_operation_locks.delete_many({})
            await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="kold")
            assert len(_creates(provider)) == 0, "found by its reference, never created again"
            recorded = await env.db.subscriptions.find_one({"payment_gateway_subscription_id": "sub_old"})
            assert recorded is not None and recorded.get("starts_at") is not None

    _run(scenario())


# R13-3: a halt after a verified payment during the review is the label settlement keeps
def test_r13_3_a_halt_after_a_payment_survives_settlement(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.core.config import settings
            from rbac_backend.services import payment_discrepancy_service as pd
            from rbac_backend.tests.integration.test_payment_mismatch_policy_mongo import _charge, _plans, _price

            monkeypatch.setattr(settings, "PAYMENT_MISMATCH_POLICY", "enforce", raising=False)
            monkeypatch.setattr(settings, "PAYMENT_MISMATCH_GRACE_DAYS", 7, raising=False)
            await env.db.subscriptions.insert_one(_bound("r13-h"))
            await _plans(env)
            price = await _price(env)
            bad = await _charge(env, "r13-h", "pay_h1", amount=price - 1)
            assert bad.json().get("validation") == "financial_review"
            await _charge(env, "r13-h", "pay_h2")
            await _charge(env, "r13-h", None, event="subscription.halted")
            (d,) = await env.db[pd.COLLECTION].find({"subscription_id": "r13-h"}).to_list(5)
            await pd.resolve(env.db, d["_id"], outcome="settled", reason="ok", actor="sa")
            row = await env.db.subscriptions.find_one({"_id": "r13-h"})
            assert row["billing_status"] == "past_due"

    _run(scenario())


# R13-4 / R12-3 caveat: resolution needs evidence the provider can give
def test_r13_4_an_incomplete_search_or_a_live_subscription_is_never_resolved_on_say_so(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.payment_gateway_reliability import GatewayUnavailable

            past = datetime.utcnow() - timedelta(minutes=5)
            await env.db.subscription_operations.insert_one({
                "_id": "op_s", "kind": "checkout", "organization_id": ORG_A, "project_id": PROJ_A1,
                "status": "needs_review", "lease_until": None, "created_at": past, "updated_at": past,
                "items": [{"reference": "op_s", "gateway": "outcome_unknown"}], "request": {}})
            provider.fetch_failure = GatewayUnavailable("search incomplete", cause_type="SearchIncomplete")
            body = {"resolution": "not_created_at_provider", "reason": "admin unblocking the scope"}
            unchecked = await _call(env, "/plan-settings/operations/op_s/resolve", body, STEP)
            assert unchecked.status_code == 409 and "provider_checked_manually" in unchecked.text
            checked = await _call(env, "/plan-settings/operations/op_s/resolve",
                                  {**body, "provider_checked_manually": True}, STEP)
            assert checked.status_code == 200
            provider.fetch_failure = None
            # A purchase the provider created and still shows billable is not closed as cancelled.
            await env.db.subscription_operations.insert_one({
                "_id": "op_live", "kind": "checkout", "organization_id": ORG_A, "project_id": "p-live",
                "status": "needs_review", "lease_until": None, "created_at": past, "updated_at": past,
                "items": [{"reference": "op_live", "gateway": "confirmed", "local": "not_recorded_lease_lost",
                           "gateway_subscription_id": "gw_live"}], "request": {}})
            provider.subscriptions["gw_live"] = {"status": "active", "notes": {}}
            live = await _call(env, "/plan-settings/operations/op_live/resolve",
                               {"resolution": "provider_subscription_cancelled", "reason": "said it was cancelled",
                                "provider_checked_manually": True}, STEP)
            assert live.status_code == 409 and "still shows" in live.text, live.text[:200]

    _run(scenario())


# R13-5: a binding conflict with this purchase's own record is already recorded
def test_r13_5_a_conflict_with_its_own_record_is_resolved(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            past = datetime.utcnow() - timedelta(minutes=5)
            await env.db.subscriptions.insert_one({**_bound("r13-own", status="pending"), "checkout_reference": "op_own",
                                                   "payment_gateway_subscription_id": "gw_own"})
            await env.db.subscription_operations.insert_one({
                "_id": "op_own", "kind": "checkout", "organization_id": ORG_A, "project_id": PROJ_A1,
                "status": "needs_review", "lease_until": None, "created_at": past, "updated_at": past,
                "items": [{"reference": "op_own", "gateway": "confirmed", "local": "binding_conflict",
                           "gateway_subscription_id": "gw_own", "customer_id": "c"}], "request": {}})
            retried = await _call(env, "/plan-settings/operations/op_own/retry", None, STEP)
            assert retried.status_code == 200 and retried.json().get("reconciled") is True, retried.text[:200]

    _run(scenario())


# R14-2: a checkout recorded locally before its attempt stopped is resolved as recorded, even
# where the provider cannot be searched - never a false "not created" statement.
def test_r14_2_a_purchase_recorded_before_the_crash_is_resolved_as_recorded(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            past = datetime.utcnow() - timedelta(minutes=5)
            await env.db.subscriptions.insert_one({**_bound("r14-rec", status="pending"), "checkout_reference": "op_rec"})
            await env.db.subscription_operations.insert_one({
                "_id": "op_rec", "kind": "checkout", "organization_id": ORG_A, "project_id": PROJ_A1,
                "status": "needs_review", "lease_until": None, "created_at": past, "updated_at": past,
                "items": [{"reference": "op_rec", "gateway": "outcome_unknown", "create_started": True}],
                "request": {"plan_code": "dms_starter", "billing_period": "monthly"}})
            provider.supports_reference_search = False
            retried = await _call(env, "/plan-settings/operations/op_rec/retry", None, STEP)
            assert retried.status_code == 200 and retried.json().get("reconciled") is True, retried.text[:200]
            op = await env.db.subscription_operations.find_one({"_id": "op_rec"})
            assert op["status"] == "completed" and op["items"][0]["subscription_id"] == "r14-rec"
            assert await env.db.subscriptions.count_documents({"checkout_reference": "op_rec"}) == 1

    _run(scenario())
