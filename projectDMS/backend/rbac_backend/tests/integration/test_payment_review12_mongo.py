"""Independent re-review 12 of the review-11 fixes (43ef3e0): each finding reproduced at
43ef3e0 (outputs kept with the session's evidence), then fixed and pinned here (E1-E7 are the
reviewer's scenarios). Real HTTP and real MongoDB; the provider is a fake implementing the
adapter interface. Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict

import pytest
from bson import ObjectId

from rbac_backend.tests.integration.test_checkout_replacement_mongo import (
    REPLACE, SOURCE, ScheduledProvider, _cancels, _creates, _oid, _seed, _source,
)
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_gateway_identity_protection_mongo import _webhook
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
    monkeypatch.setattr(settings, "CHECKOUT_REPLACEMENT_ENABLED", True, raising=False)
    monkeypatch.setattr(rbac_monetization.monetization_mutation_limiter, "max_requests", 10_000)
    return fake


async def _lapse(env: Any) -> None:
    past = datetime.utcnow() - timedelta(seconds=1)
    await env.db.subscription_operations.update_many({"status": "in_progress"}, {"$set": {"lease_until": past}})
    await env.db.subscription_operation_locks.update_many({}, {"$set": {"lease_until": past}})


# E1: keep_leases failing (lease lost before any provider call) leaves an in_progress op that
# later reads as an unknown create and blocks the scope.
def test_e1_keep_leases_lost_before_any_call_blocks_scope(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            original = provider.fetch_subscription_state
            fired = {"n": 0}

            async def slow_read(**kwargs: Any) -> Any:
                # The source read was slower than the lease: another request took the scope.
                if fired["n"] == 0:
                    fired["n"] = 1
                    await env.db.subscription_operation_locks.update_many({}, {"$set": {"holder": "someone-else"}})
                return await original(**kwargs)

            provider.fetch_subscription_state = slow_read  # type: ignore[method-assign]
            a = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k1")
            assert a.status_code == 409
            provider.fetch_subscription_state = original  # type: ignore[method-assign]
            (op,) = await _ops(env, "replacement")
            assert op["status"] == "failed" and len(_creates(provider)) == 0, "R12-1: stopped before the provider: failed"
            await _lapse(env)
            await env.db.subscription_operation_locks.delete_many({})
            same = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k1")
            assert same.status_code == 200, "nothing reached the provider: the same key may try again"
            new = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k1-new")
            assert new.status_code == 409 and "awaiting payment" in new.text
            assert len(_creates(provider)) == 1

    _run(scenario())


# E2: a replacement refused by _reconcile_unknown_checkouts (raise, not refuse) is left in_progress
def test_e2_blocked_replacement_left_in_progress(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            past = datetime.utcnow() - timedelta(minutes=5)
            await env.db.subscription_operations.insert_one({
                "_id": "op_unk", "kind": "checkout", "organization_id": ORG_A, "project_id": PROJ_A1,
                "status": "needs_review", "lease_until": None, "created_at": past, "updated_at": past,
                "items": [{"reference": "op_unk", "gateway": "outcome_unknown"}], "request": {}})
            r = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k2")
            assert r.status_code == 409
            (op,) = await _ops(env, "replacement")
            assert op["status"] == "failed", "R12-9: never left in progress to become another blocker"
            res = await _call(env, "/plan-settings/operations/op_unk/resolve",
                              {"resolution": "not_created_at_provider", "reason": "checked the provider dashboard"}, STEP)
            assert res.status_code == 200
            await _lapse(env)
            again = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k2-b")
            assert again.status_code == 200, "resolving the blocker unblocks the scope"

    _run(scenario())


# E3: a confirmed-created, unrecorded purchase (lease lost, legacy row without customer id) is
# closed as "not_created_at_provider" with no check; the provider subscription stays payable.
def test_e3_resolve_confirmed_unrecorded_as_not_created(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            past = datetime.utcnow() - timedelta(minutes=5)
            provider.subscriptions["sub_live"] = {"status": "created", "customer_id": "cust_x", "plan_id": "dms_professional",
                                                  "notes": {"checkout_reference": "op_ll"}}
            await env.db.subscription_operations.insert_one({
                "_id": "op_ll", "kind": "checkout", "organization_id": ORG_A, "project_id": PROJ_A1,
                "status": "needs_review", "lease_until": None, "created_at": past, "updated_at": past,
                "items": [{"reference": "op_ll", "gateway": "confirmed", "gateway_subscription_id": "sub_live",
                           "local": "not_recorded_lease_lost"}],
                "request": {"plan_code": "dms_professional", "billing_period": "monthly"}})
            res = await _call(env, "/plan-settings/operations/op_ll/resolve",
                              {"resolution": "not_created_at_provider", "reason": "admin unblocking the scope"}, STEP)
            assert res.status_code == 409 and "holds a subscription" in res.text, res.text[:200]
            assert (await env.db.subscription_operations.find_one({"_id": "op_ll"}))["status"] == "needs_review"
            # binding conflict variant
            await env.db.subscriptions.insert_one({**_bound("other-row", project_id="p-other"),
                                                   "payment_gateway_subscription_id": "sub_bc"})
            await env.db.subscription_operations.insert_one({
                "_id": "op_bc", "kind": "replacement", "organization_id": ORG_A, "project_id": PROJ_A1,
                "status": "needs_review", "lease_until": None, "created_at": past, "updated_at": past,
                "items": [{"role": "target", "gateway": "confirmed", "local": "binding_conflict",
                           "gateway_subscription_id": "sub_bc", "customer_id": "c"}],
                "request": {"replaces_subscription_id": SOURCE}})
            res = await _call(env, "/plan-settings/operations/op_bc/resolve",
                              {"resolution": "not_created_at_provider", "reason": "admin unblocking the scope"}, STEP)
            assert res.status_code == 409
            ok = await _call(env, "/plan-settings/operations/op_bc/resolve",
                             {"resolution": "provider_subscription_cancelled", "reason": "cancelled in the dashboard",
                              "provider_checked_manually": True}, STEP)
            assert ok.status_code == 200 and _cancels(provider) == []

    _run(scenario())


# E4: a legacy interrupted replacement (no source item) that the provider DOES show is resolved
# as "not_created_at_provider" (reconcile returned False for another reason).
def test_e4_found_but_unrecordable_resolved_not_created(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            past = datetime.utcnow() - timedelta(minutes=5)
            await env.db.subscription_operations.insert_one({
                "_id": "op_int", "kind": "replacement", "organization_id": ORG_A, "project_id": PROJ_A1,
                "status": "in_progress", "lease_until": past, "created_at": past, "updated_at": past, "items": [],
                "request": {"replaces_subscription_id": SOURCE, "plan_code": "dms_professional", "billing_period": "monthly"},
                "token": "t"})
            provider.subscriptions["sub_int"] = {"status": "created", "customer_id": "cust_int", "plan_id": "dms_professional",
                                                 "notes": {"checkout_reference": "op_int"}}
            r = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k4")
            assert r.status_code == 409
            res = await _call(env, "/plan-settings/operations/op_int/resolve",
                              {"resolution": "not_created_at_provider", "reason": "admin unblocking the scope"}, STEP)
            assert res.status_code == 409, "R12-3: found at the provider - never 'not created'"
            r2 = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k4b")
            assert r2.status_code == 409 and len(_creates(provider)) == 0

    _run(scenario())


# E5: pagination by _id with mixed string and ObjectId ids
def test_e5_pagination_mixed_ids(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.monetization_service import MonetizationService

            for i in range(3):
                await env.db.subscriptions.insert_one({**_bound(f"str-{i}", project_id=f"ps{i}"), "status": "cancelled"})
            for i in range(3):
                await env.db.subscriptions.insert_one({**_bound("x", project_id=f"po{i}"), "_id": ObjectId(),
                                                       "payment_gateway_subscription_id": f"gw_oid_{i}", "status": "cancelled"})
            svc = MonetizationService()
            seen = []
            after = None
            for _ in range(10):
                page = await svc.reconcile_gateway_cancellations(ORG_A, limit=2, after=after)
                seen += [r["subscription_id"] for r in page["requires_review"]]
                if page["complete"]:
                    break
                after = page["next_after"]
            assert len(seen) == 6 and len(set(seen)) == 6, seen

    _run(scenario())


# E6: settlement label - payment failed then a verified payment during the review
def test_e6_label_failed_then_paid(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.core.config import settings
            from rbac_backend.services import payment_discrepancy_service as pd

            monkeypatch.setattr(settings, "PAYMENT_MISMATCH_POLICY", "enforce", raising=False)
            monkeypatch.setattr(settings, "PAYMENT_MISMATCH_GRACE_DAYS", 7, raising=False)
            await env.db.subscriptions.insert_one(_bound("r12-l"))
            row = await env.db.subscriptions.find_one({"_id": "r12-l"})
            from rbac_backend.tests.integration.test_payment_mismatch_policy_mongo import _plans, _price, _charge
            await _plans(env)
            price = await _price(env)
            bad = await _charge(env, "r12-l", "pay_l1", amount=price - 1)
            assert bad.json().get("validation") == "financial_review"
            await _charge(env, "r12-l", "pay_l2", event="payment.failed")

            await _charge(env, "r12-l", "pay_l3")

            row = await env.db.subscriptions.find_one({"_id": "r12-l"})

            (d,) = await env.db[pd.COLLECTION].find({"subscription_id": "r12-l"}).to_list(5)
            await pd.resolve(env.db, d["_id"], outcome="settled", reason="ok", actor="sa")
            row = await env.db.subscriptions.find_one({"_id": "r12-l"})
            assert (row["status"], row["billing_status"]) == ("active", "active"), "R12-7: the latest payment decides"

    _run(scenario())


async def _auth(env: Any, target: Dict[str, Any], eid: str) -> Any:
    return await _webhook(env, "subscription.authenticated", target["payment_gateway_subscription_id"], event_id=eid,
                          entity={"customer_id": target["payment_gateway_customer_id"], "status": "authenticated"})


# E7: a same-key repeat during a slow (live) completion supersedes it after the cancel was sent
def test_e7_repeat_supersedes_live_completion(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            body = (await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k7")).json()
            target = await env.db.subscriptions.find_one({"_id": _oid(body["subscription_id"])})
            gate, entered = asyncio.Event(), asyncio.Event()
            original = provider.cancel_with_evidence

            async def slow_cancel(**kwargs: Any) -> Any:
                result = await original(**kwargs)   # applied at the provider
                entered.set()
                await gate.wait()
                return result

            provider.cancel_with_evidence = slow_cancel  # type: ignore[method-assign]
            hook = asyncio.create_task(_auth(env, target, "evt-e7"))
            await entered.wait()
            past = datetime.utcnow() - timedelta(seconds=1)
            await env.db.subscription_operations.update_many({}, {"$set": {"lease_until": past}})
            await env.db.subscription_operation_locks.update_many({}, {"$set": {"lease_until": past}})
            await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k7")

            gate.set()
            await hook
            provider.cancel_with_evidence = original  # type: ignore[method-assign]
            (op,) = await _ops(env, "replacement")
            src = await _source(env)
            assert src.get("replaced_by_subscription_id") is None
            await _call(env, f"/plan-settings/operations/{op['_id']}/retry", None, STEP)
            (op,) = await _ops(env, "replacement")
            # R12-5: a cancel that may have been applied is never recorded as "nothing applied".
            assert op["status"] == "needs_review" and op["items"][1].get("gateway") == "outcome_unknown", op["items"][1]

    _run(scenario())


# R12-8: an authorised target unpaid a week after its start date no longer blocks for ever
def test_r12_8_an_authorised_target_long_past_its_start_can_expire(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.monetization_service import MonetizationService

            now = datetime.utcnow()
            pending = {**_bound("r12-stale", status="pending"), "billing_status": "pending",
                       "created_at": now - timedelta(days=40), "replacement_operation_id": "op_x",
                       "replacement_authorised_at": now - timedelta(days=39), "starts_at": now - timedelta(days=8)}
            provider.subscriptions["gw_r12-stale"] = {"status": "pending", "notes": {}}
            await env.db.subscriptions.insert_one(pending)
            await MonetizationService(env.db)._expire_abandoned_checkout(env.db, pending, None)
            assert (await env.db.subscriptions.find_one({"_id": "r12-stale"}))["status"] == "cancelled"

    _run(scenario())


# R12-6: a repeat of a refusal under review that fails another check keeps the review listed
def test_r12_6_a_repeat_of_a_reviewed_refusal_keeps_the_review(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            provider.subscriptions[f"gw_{SOURCE}"]["status"] = "halted"
            first = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k6")
            assert first.status_code == 409
            provider.subscriptions[f"gw_{SOURCE}"]["status"] = "active"
            provider.fetch_failure = RuntimeError("provider unreadable")
            again = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k6")
            assert again.status_code in {409, 502}
            (op,) = await _ops(env, "replacement")
            assert op["status"] == "needs_review", "the earlier review is not hidden"

    _run(scenario())


# R12-1: a checkout interrupted before the provider's create does not block the scope
def test_r12_1_a_checkout_interrupted_before_the_create_does_not_block(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            past = datetime.utcnow() - timedelta(minutes=5)
            await env.db.subscription_operations.insert_one({
                "_id": "op_early", "kind": "checkout", "organization_id": ORG_A, "project_id": PROJ_A1,
                "status": "in_progress", "lease_until": past, "created_at": past, "updated_at": past, "token": "t",
                "items": [{"reference": "op_early", "gateway": "pending", "attempts": 1}],
                "request": {"plan_code": "dms_starter", "billing_period": "monthly"}})
            bought = await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")
            assert bought.status_code == 200, bought.text[:200]
            assert (await env.db.subscription_operations.find_one({"_id": "op_early"}))["status"] == "failed"
            assert len(_creates(provider)) == 1

    _run(scenario())
