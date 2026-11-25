"""Independent re-review 11 of the review-10 fixes (71402c3): each finding reproduced at
71402c3 (outputs kept with the session's evidence), then fixed and pinned here.

Real HTTP and real MongoDB; the provider is a fake implementing the adapter interface (it
declares a scheduled start, reference search and webhooks - the replacement paths are latent
in production, where Razorpay declares no scheduled start). Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict

import pytest

from rbac_backend.services.payment_gateway_reliability import GatewayCallNotSent as GatewayNotSent
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


async def _auth(env: Any, target: Dict[str, Any], eid: str) -> Any:
    return await _webhook(env, "subscription.authenticated", target["payment_gateway_subscription_id"], event_id=eid,
                          entity={"customer_id": target["payment_gateway_customer_id"], "status": "authenticated"})


# R11-1: a same-key retry by the customer turns a needs_review replacement back into awaiting_payment
def test_r11_1_same_key_retry_hides_a_replacement_review(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            body = (await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k1")).json()
            target = await env.db.subscriptions.find_one({"_id": _oid(body["subscription_id"])})
            provider.cancel_applies = False
            provider.cancel_failures = [GatewayNotSent("down", cause_type="ConnectionError")]
            r = await _auth(env, target, "evt-r11-1")
            assert r.json().get("replacement") == "needs_review"
            again = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k1")
            assert again.status_code == 200, again.text[:200]
            (op,) = await _ops(env, "replacement")
            assert op["status"] == "needs_review", "a repeat of the request never hides the open source step"
            listed = await _call(env, "/plan-settings/operations", None, "x", method="GET")
            assert op["_id"] in {row["id"] for row in listed.json()}
            assert len(_creates(provider)) == 1

    _run(scenario())


# R11-2: a review refusal retried under the same key becomes an "unknown create" that blocks the scope
def test_r11_2_review_refusal_retry_blocks_scope(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            provider.subscriptions[f"gw_{SOURCE}"]["status"] = "halted"
            first = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k2")
            assert first.status_code == 409
            again = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k2")
            assert again.status_code == 409 and "halted" in again.text, "re-validated, not treated as a create"
            (op,) = await _ops(env, "replacement")
            assert op["items"][0].get("gateway") != "outcome_unknown"
            provider.subscriptions[f"gw_{SOURCE}"]["status"] = "active"
            fresh = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k2-new")
            assert fresh.status_code == 200, "the scope is not blocked by a refusal"
            assert len(_creates(provider)) == 1

    _run(scenario())


# R11-3: authorisation flag survives the target's cancellation; a later retry cancels the source
def test_r11_3_cancelled_target_still_cancels_source(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            body = (await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k3")).json()
            target = await env.db.subscriptions.find_one({"_id": _oid(body["subscription_id"])})
            provider.fetch_failure = RuntimeError("provider read down")
            r = await _auth(env, target, "evt-r11-3")
            assert r.json().get("replacement") == "needs_review"
            provider.fetch_failure = None
            c = await _webhook(env, "subscription.cancelled", target["payment_gateway_subscription_id"], event_id="evt-r11-3c",
                               entity={"customer_id": target["payment_gateway_customer_id"], "status": "cancelled"})
            assert c.status_code == 200
            (op,) = await _ops(env, "replacement")
            retried = await _call(env, f"/plan-settings/operations/{op['_id']}/retry", None, STEP)
            assert retried.json().get("reason") == "target_no_longer_valid", retried.text[:200]
            src = await _source(env)
            assert src.get("replaced_by_subscription_id") is None and _cancels(provider) == []

    _run(scenario())


# R11-4: an interrupted replacement (items never saved) is reconciled without its source item
def test_r11_4_interrupted_replacement_reconciled_without_source(monkeypatch, provider) -> None:
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
            assert r.status_code == 409, r.text[:200]
            # Without its source record there is no start date: never recorded to govern at once.
            assert await env.db.subscriptions.find_one({"payment_gateway_subscription_id": "sub_int"}) is None
            op = await env.db.subscription_operations.find_one({"_id": "op_int"})
            assert op["status"] == "needs_review"
            listed = await _call(env, "/plan-settings/operations", None, "x", method="GET")
            assert "op_int" in {row["id"] for row in listed.json()}
            assert _creates(provider) == [] and _cancels(provider) == []

    _run(scenario())


# R11-5: a binding_conflict operation blocks every purchase of the scope with no admin route
def test_r11_5_binding_conflict_blocks_scope(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            past = datetime.utcnow() - timedelta(minutes=5)
            await env.db.subscription_operations.insert_one({
                "_id": "op_bc", "kind": "replacement", "organization_id": ORG_A, "project_id": PROJ_A1,
                "status": "needs_review", "lease_until": None, "created_at": past, "updated_at": past,
                "items": [{"role": "target", "gateway": "confirmed", "local": "binding_conflict", "gateway_subscription_id": "gw_x"}],
                "request": {"replaces_subscription_id": SOURCE, "plan_code": "dms_professional", "billing_period": "monthly"}})
            r = await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")
            assert r.status_code == 409
            retried = await _call(env, "/plan-settings/operations/op_bc/retry", None, STEP)
            assert retried.status_code == 409
            # Review 12 R12-2: the provider created it - never closed as "not created".
            wrong = await _call(env, "/plan-settings/operations/op_bc/resolve",
                                {"resolution": "not_created_at_provider", "reason": "checked the provider dashboard",
                                 "provider_checked_manually": True}, STEP)
            assert wrong.status_code == 409 and "holds a subscription" in wrong.text, wrong.text[:200]
            resolved = await _call(env, "/plan-settings/operations/op_bc/resolve",
                                   {"resolution": "provider_subscription_cancelled",
                                    "reason": "cancelled it in the provider dashboard", "provider_checked_manually": True}, STEP)
            assert resolved.status_code == 200 and resolved.json()["status"] == "resolved", resolved.text[:200]

    _run(scenario())


# R10-9: a subscription past due (billing label) is relabelled active by settlement
def test_r10_9_billing_past_due_relabelled_active(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import payment_discrepancy_service as pd

            await env.db.subscriptions.insert_one({**_bound("r11-d"), "billing_status": "past_due"})
            now = datetime.utcnow()
            snap = await env.db.subscriptions.find_one({"_id": "r11-d"})
            first = await pd.record(env.db, subscription=snap, provider="razorpay", kind="renewal", reason="x",
                                    event_id="e1", gateway_payment_id="p1", amount_minor=1, currency="INR",
                                    expected={"amount_minor": 2, "currency": "INR"}, now=now)
            await pd.resolve(env.db, first["discrepancy_id"], outcome="written_off", reason="r", actor="sa")
            row = await env.db.subscriptions.find_one({"_id": "r11-d"})
            assert row["billing_status"] == "past_due"
            # case B: payment failed during the review
            await env.db.subscriptions.insert_one({**_bound("r11-e", project_id="r11-e-p")})
            snap = await env.db.subscriptions.find_one({"_id": "r11-e"})
            first = await pd.record(env.db, subscription=snap, provider="razorpay", kind="renewal", reason="x",
                                    event_id="e2", gateway_payment_id="p2", amount_minor=1, currency="INR",
                                    expected={"amount_minor": 2, "currency": "INR"}, now=now)
            await env.db.subscriptions.update_one({"_id": "r11-e"}, {"$set": {"billing_status": "past_due"}})
            await pd.resolve(env.db, first["discrepancy_id"], outcome="settled", reason="r", actor="sa")
            row = await env.db.subscriptions.find_one({"_id": "r11-e"})
            assert row["billing_status"] == "past_due"
            # case C: cancelled during review
            await env.db.subscriptions.insert_one({**_bound("r11-f", project_id="r11-f-p")})
            snap = await env.db.subscriptions.find_one({"_id": "r11-f"})
            first = await pd.record(env.db, subscription=snap, provider="razorpay", kind="renewal", reason="x",
                                    event_id="e3", gateway_payment_id="p3", amount_minor=1, currency="INR",
                                    expected={"amount_minor": 2, "currency": "INR"}, now=now)
            await env.db.subscriptions.update_one({"_id": "r11-f"}, {"$set": {"status": "cancelled", "billing_status": "inactive"}})
            await pd.resolve(env.db, first["discrepancy_id"], outcome="settled", reason="r", actor="sa")
            row = await env.db.subscriptions.find_one({"_id": "r11-f"})
            assert row["billing_status"] == "inactive"

    _run(scenario())


# R10-10: a discrepancy recorded between settlement's "no other open" check and its close escapes
def test_r10_10_interleaving(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import payment_discrepancy_service as pd

            await env.db.subscriptions.insert_one(_bound("r11-g"))
            now = datetime.utcnow()
            snap = await env.db.subscriptions.find_one({"_id": "r11-g"})
            first = await pd.record(env.db, subscription=snap, provider="razorpay", kind="renewal", reason="x",
                                    event_id="g1", gateway_payment_id="pg1", amount_minor=1, currency="INR",
                                    expected={"amount_minor": 2, "currency": "INR"}, now=now)
            real = env.db
            fired = {"done": False}

            class Coll:
                def __init__(self, c: Any) -> None:
                    self._c = c

                def __getattr__(self, n: str) -> Any:
                    return getattr(self._c, n)

                async def find_one(self, *a: Any, **k: Any) -> Any:
                    res = await self._c.find_one(*a, **k)
                    q = a[0] if a else {}
                    if not fired["done"] and isinstance(q, dict) and q.get("kind") == "renewal":
                        fired["done"] = True
                        s = await real.subscriptions.find_one({"_id": "r11-g"})
                        await pd.record(real, subscription=s, provider="razorpay", kind="renewal", reason="x",
                                        event_id="g2", gateway_payment_id="pg2", amount_minor=1, currency="INR",
                                        expected={"amount_minor": 2, "currency": "INR"}, now=now)
                    return res

            class DB:
                def __getattr__(self, n: str) -> Any:
                    return getattr(real, n)

                def __getitem__(self, n: str) -> Any:
                    return Coll(real[n]) if n == pd.COLLECTION else real[n]

            out = await pd.resolve(DB(), first["discrepancy_id"], outcome="settled", reason="r", actor="sa")
            row = await env.db.subscriptions.find_one({"_id": "r11-g"})
            open_left = await env.db[pd.COLLECTION].count_documents({"subscription_id": "r11-g", "status": "open"})
            assert open_left == 1 and out["review_closed"] is False
            assert (row["financial_review"]["state"], row["billing_status"]) == ("open", "financial_review")

    _run(scenario())


# R11-7: a live slow replacement whose leases lapse, retried under the same key
def test_r11_7_live_slow_replacement_same_key(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            gate = asyncio.Event()
            entered = asyncio.Event()
            original = provider.create_subscription

            async def slow(**kwargs: Any) -> Any:
                entered.set()
                await gate.wait()
                return await original(**kwargs)

            provider.create_subscription = slow  # type: ignore[method-assign]
            a = asyncio.create_task(_call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k7"))
            await entered.wait()
            past = datetime.utcnow() - timedelta(seconds=1)
            await env.db.subscription_operations.update_many({}, {"$set": {"lease_until": past}})
            await env.db.subscription_operation_locks.update_many({}, {"$set": {"lease_until": past}})
            provider.create_subscription = original  # type: ignore[method-assign]
            b = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k7")
            assert b.status_code == 409
            gate.set()
            ra = await a
            assert ra.status_code == 409
            c = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k7")
            assert c.status_code == 200, c.text[:200]
            (op,) = await _ops(env, "replacement")
            target = await env.db.subscriptions.find_one({"replacement_operation_id": op["_id"]})
            assert len(op["items"]) == 2 and target is not None and target.get("starts_at") is not None
            assert len(_creates(provider)) == 1

    _run(scenario())


# R11-6: a lease-lost purchase recorded from the item loses its customer id (webhook ownership check skipped)
def test_r11_6_lease_lost_record_has_no_customer(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            original = provider.create_subscription

            async def create_then_lose_lease(**kwargs: Any) -> Any:
                result = await original(**kwargs)
                await env.db.subscription_operation_locks.update_many({}, {"$set": {"holder": "someone-else"}})
                return result

            provider.create_subscription = create_then_lose_lease  # type: ignore[method-assign]
            lost = await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")
            assert lost.status_code == 409
            provider.create_subscription = original  # type: ignore[method-assign]
            await env.db.subscription_operation_locks.delete_many({})
            provider.supports_reference_search = False
            (op,) = await _ops(env, "checkout")
            retried = await _call(env, f"/plan-settings/operations/{op['_id']}/retry", None, STEP)
            assert retried.status_code == 200
            row = await env.db.subscriptions.find_one({"checkout_reference": op["_id"]})
            assert row.get("payment_gateway_customer_id"), "recorded with the customer it belongs to"
            other = await _webhook(env, "subscription.activated", row["payment_gateway_subscription_id"], event_id="evt-r11-6",
                                   entity={"customer_id": "cust_SOMEONE_ELSE"})
            assert other.json().get("refused") == "gateway_customer_mismatch"

    _run(scenario())


# R11-8: the reconciliation read pages instead of hiding rows past the cap
def test_r11_8_the_cancellation_reconciliation_pages(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            for n in range(3):
                await env.db.subscriptions.insert_one({**_bound(f"r11-pg-{n}", project_id=f"r11-pg-p{n}"),
                                                       "organization_id": "org-r11-pg", "status": "cancelled"})
            seen = []
            after = None
            for _ in range(3):
                path = "/plan-settings/reconciliation/cancellations?organization_id=org-r11-pg&limit=2" + (
                    f"&after={after}" if after else "")
                page = (await _call(env, path, None, "x", method="GET")).json()
                seen.append(page["checked"])
                after = page["next_after"]
                if page["complete"]:
                    break
            assert seen == [2, 1]

    _run(scenario())


# R11-9: an authorised replacement target waiting for its start date is never expired as abandoned
def test_r11_9_an_authorised_target_is_never_expired_as_abandoned(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.monetization_service import MonetizationService, PurchaseInProgress

            pending = {**_bound("r11-auth", status="pending"), "billing_status": "pending",
                       "created_at": datetime.utcnow() - timedelta(days=5), "replacement_operation_id": "op_x",
                       "replacement_authorised_at": datetime.utcnow() - timedelta(days=4)}
            await env.db.subscriptions.insert_one(pending)
            with pytest.raises(PurchaseInProgress):
                await MonetizationService(env.db)._expire_abandoned_checkout(env.db, pending, None)
            assert (await env.db.subscriptions.find_one({"_id": "r11-auth"}))["status"] == "pending"
            assert _cancels(provider) == []

    _run(scenario())


# R10-6 (caller): a resume whose outcome is unknown is reported as unknown, not "nothing changed"
def test_r10_6_an_unknown_resume_is_reported_as_unknown(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.routers import rbac_monetization
            from rbac_backend.services import monetization_service
            from rbac_backend.services.payment_gateway_reliability import GatewayOutcomeUnknown
            from rbac_backend.tests.integration.test_r6_6_reactivation_mongo import FakeGateway, _reactivate, _sub

            class Unknown(FakeGateway):
                async def reactivate_subscription(self, *, gateway_subscription_id: str) -> Any:
                    raise GatewayOutcomeUnknown("resume sent; state unread", cause_type="ReadTimeout")

            fake = Unknown()
            monkeypatch.setattr(monetization_service, "get_payment_gateway", lambda *a, **k: fake)
            monkeypatch.setattr(rbac_monetization.monetization_mutation_limiter, "max_requests", 10_000)
            await env.db.subscriptions.insert_one(_sub("r11-resume", project="r11-resume-p"))
            response = await _reactivate(env, "r11-resume")
            assert response.status_code == 502 and "unknown" in response.text, response.text[:200]
            assert await env.db.audit_events.count_documents(
                {"action": "subscription.reactivation_gateway_outcome_unknown"}) == 1

    _run(scenario())


# R11-6: an operation recorded before the fix (no customer on its item) is never recorded blind
def test_r11_6_an_unrecorded_purchase_without_its_customer_is_left_for_a_person(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            past = datetime.utcnow() - timedelta(minutes=5)
            await env.db.subscription_operations.insert_one({
                "_id": "op_nocust", "kind": "checkout", "organization_id": ORG_A, "project_id": PROJ_A1,
                "status": "needs_review", "lease_until": None, "created_at": past, "updated_at": past,
                "items": [{"reference": "op_nocust", "gateway": "confirmed", "local": "not_recorded_lease_lost",
                           "gateway_subscription_id": "gw_nocust"}],
                "request": {"plan_code": "dms_starter", "billing_period": "monthly"}})
            provider.supports_reference_search = False
            retried = await _call(env, "/plan-settings/operations/op_nocust/retry", None, STEP)
            assert retried.status_code == 409, retried.text[:200]
            assert await env.db.subscriptions.find_one({"payment_gateway_subscription_id": "gw_nocust"}) is None

    _run(scenario())


# R10-9: a subscription cancelled during the review (label untouched) is labelled inactive
def test_r10_9_cancelled_during_review_is_inactive(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import payment_discrepancy_service as pd

            await env.db.subscriptions.insert_one(_bound("r11-cx"))
            snap = await env.db.subscriptions.find_one({"_id": "r11-cx"})
            opened = await pd.record(env.db, subscription=snap, provider="razorpay", kind="renewal", reason="x",
                                     event_id="ecx", gateway_payment_id="pcx", amount_minor=1, currency="INR",
                                     expected={"amount_minor": 2, "currency": "INR"}, now=datetime.utcnow())
            await env.db.subscriptions.update_one({"_id": "r11-cx"}, {"$set": {"status": "cancelled"}})
            await pd.resolve(env.db, opened["discrepancy_id"], outcome="settled", reason="r", actor="sa")
            assert (await env.db.subscriptions.find_one({"_id": "r11-cx"}))["billing_status"] == "inactive"

    _run(scenario())
