"""Workstream 1 (owner approval for development, 2026-11-21): checkout replacement of a
gateway-billed subscription, behind ``CHECKOUT_REPLACEMENT_ENABLED`` (default off).

Before (review 9 R9-1): a checkout over a gateway-billed plan created a second subscription
for the scope; once paid, both billed until the old one was cancelled by hand.

Now, with the flag on: a checkout over a gateway-billed governing subscription must name it
(``replaces_subscription_id``); the source is verified exactly; the provider must guarantee a
scheduled start, and the target starts when the source's paid period ends (provider and local
dates must agree); the target governs only from then; only the target's signed payment
confirmation cancels the source - at the gateway at its cycle end, then locally with its
service ending on the same date. A durable operation, scope lease and idempotency key make it
retry-safe; any failure or disagreement is ``needs_review``; nothing is refunded and no other
subscription is touched. With the flag off nothing changes.

Real HTTP; the provider is a fake implementing the adapter interface. No real provider is
called. Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pytest

from rbac_backend.services.payment_gateway import GatewaySubscription
from rbac_backend.services.payment_gateway_reliability import GatewayOutcomeUnknown
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_gateway_identity_protection_mongo import _webhook
from rbac_backend.tests.integration.test_provider_reliability_mongo import (
    CHECKOUT,
    Provider,
    _bound,
    _call,
    _ops,
)
from rbac_backend.tests.integration.test_security_remediation_mongo import _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1

pytestmark = pytest.mark.integration

STEP = "subscription.entitlement.manage"
SOURCE = "rp-source"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class ScheduledProvider(Provider):
    supports_scheduled_start = True

    async def create_subscription(self, *, gateway_customer_id: str, plan_code: str, billing_period: str,
                                  metadata: Optional[Dict[str, Any]] = None, start_at: Any = None) -> GatewaySubscription:
        result = await super().create_subscription(gateway_customer_id=gateway_customer_id, plan_code=plan_code,
                                                   billing_period=billing_period, metadata=metadata)
        self.calls[-1] = (*self.calls[-1], start_at)
        return result


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


REPLACE = {**CHECKOUT, "plan_code": "dms_professional", "replaces_subscription_id": SOURCE}


async def _seed(env: Any, provider: ScheduledProvider, **extra: Any) -> None:
    row = _bound(SOURCE, **{"current_period_end": provider.cycle_end, **extra})
    await env.db.subscriptions.insert_one(row)
    provider.subscriptions[f"gw_{SOURCE}"] = {"status": "active", "notes": {}}


async def _price(env: Any, plan: str = "dms_professional") -> int:
    row = await env.db.plans.find_one({"code": plan})
    return int((row.get("pricing_tiers") or {}).get("monthly") or row.get("base_price_minor"))


async def _pay(env: Any, target: Dict[str, Any], event_id: str, amount: Optional[int] = None) -> Any:
    return await _webhook(env, "subscription.activated", target["payment_gateway_subscription_id"], event_id=event_id,
                          entity={"customer_id": target["payment_gateway_customer_id"]},
                          payment={"id": f"pay_{event_id}", "amount": amount if amount is not None else await _price(env),
                                   "currency": "INR"})


async def _source(env: Any) -> Dict[str, Any]:
    return await env.db.subscriptions.find_one({"_id": SOURCE})


def _cancels(provider: ScheduledProvider) -> list:
    return [call for call in provider.calls if call[0] == "cancel"]


def _creates(provider: ScheduledProvider) -> list:
    return [call for call in provider.calls if call[0] == "create"]


# --- flag off: nothing changes ----------------------------------------------------------


def test_with_the_flag_off_replacement_is_refused_and_checkout_is_unchanged(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.core.config import settings

            monkeypatch.setattr(settings, "CHECKOUT_REPLACEMENT_ENABLED", False, raising=False)
            await _seed(env, provider)
            refused = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-off")
            assert refused.status_code == 409 and "not enabled" in refused.text, refused.text[:200]
            plain = await _call(env, "/subscriptions/checkout", {**CHECKOUT, "plan_code": "dms_professional"},
                                "subscription.checkout")
            assert plain.status_code == 200 and "keeps billing" in plain.json().get("warning", ""), plain.text[:200]
            assert len(_creates(provider)) == 1 and _creates(provider)[0][-1] is None, "no scheduled start"

    _run(scenario())


# --- flag on: refusals ----------------------------------------------------------------------


def test_a_second_paid_plan_must_name_the_one_it_replaces(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            plain = await _call(env, "/subscriptions/checkout", {**CHECKOUT, "plan_code": "dms_professional"},
                                "subscription.checkout")
            assert plain.status_code == 409 and "replaces_subscription_id" in plain.text, plain.text[:200]
            no_key = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout")
            assert no_key.status_code == 400 and "Idempotency-Key" in no_key.text, no_key.text[:200]
            assert _creates(provider) == []

    _run(scenario())


@pytest.mark.parametrize("case", [
    "other_organisation", "other_project", "package", "trial", "past_due", "cancelled", "unbound", "other_provider",
    "not_from_checkout", "outranked", "missing",
])
def test_only_the_exact_governing_gateway_billed_subscription_is_replaced(monkeypatch, provider, case) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            changes: Dict[str, Any] = {
                "other_organisation": {"organization_id": ORG_B}, "other_project": {"project_id": "rp-other"},
                "package": {"package_id": "pkg-1"}, "trial": {"status": "trial", "trial": True},
                "past_due": {"status": "past_due"}, "cancelled": {"status": "cancelled"},
                "unbound": {"payment_gateway_subscription_id": None}, "other_provider": {"payment_provider": "stripe"},
                "not_from_checkout": {"payment_gateway_bound_by": "admin_update"}, "outranked": {}, "missing": {},
            }[case]
            if case != "missing":
                await _seed(env, provider, **changes)
            if case == "outranked":
                await env.db.subscriptions.insert_one(_bound("rp-newer", governing_since=datetime.utcnow(),
                                                             payment_gateway_subscription_id="gw_rp-newer"))
            before = await env.db.subscriptions.find({}).sort("_id", 1).to_list(length=None)
            response = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key=f"k-{case}")
            assert response.status_code in {400, 409}, (case, response.text[:200])
            expected = {"other_organisation": "another organisation", "other_project": "another organisation",
                        "package": "another organisation", "trial": "status 'trial'", "past_due": "status 'past_due'",
                        "cancelled": "status 'cancelled'", "unbound": "configured payment gateway",
                        "other_provider": "configured payment gateway", "not_from_checkout": "configured payment gateway",
                        "outranked": "does not govern", "missing": "not found"}[case]
            assert expected in response.json()["detail"], (case, response.text[:200])
            assert _creates(provider) == [] and _cancels(provider) == [], case
            assert await env.db.subscriptions.find({}).sort("_id", 1).to_list(length=None) == before, case

    _run(scenario())


def test_a_provider_that_cannot_schedule_the_start_is_refused(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            provider.supports_scheduled_start = False
            await _seed(env, provider)
            response = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-sched")
            assert response.status_code == 409 and "never bill the same days" in response.text, response.text[:200]
            assert _creates(provider) == []

    _run(scenario())


def test_provider_and_database_disagreeing_is_listed_for_review_with_evidence(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider, current_period_end=provider.cycle_end + timedelta(days=9))
            dates = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-dates")
            assert dates.status_code == 409 and "disagree" in dates.text, dates.text[:200]
            (op,) = await _ops(env, "replacement")
            assert op["status"] == "needs_review" and op["items"][0]["evidence"]["provider"]["status"] == "active"
            assert await env.db.audit_events.count_documents(
                {"action": "subscription.replacement_needs_review", "reason": "paid_through_dates_disagree"}) == 1
            await env.db.subscriptions.update_one({"_id": SOURCE}, {"$set": {"current_period_end": provider.cycle_end}})
            provider.subscriptions[f"gw_{SOURCE}"]["status"] = "cancelled"
            ended = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-ended")
            assert ended.status_code == 409 and "cancelled" in ended.text, ended.text[:200]
            assert _creates(provider) == [] and (await _source(env))["status"] == "active"

    _run(scenario())


# --- the replacement itself ----------------------------------------------------------------


def test_the_target_is_scheduled_and_the_source_is_cancelled_only_on_payment(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.entitlement_service import EntitlementService

            await _seed(env, provider)
            await env.db.subscriptions.insert_one(_bound("rp-elsewhere", project_id="rp-elsewhere-project"))
            elsewhere = await env.db.subscriptions.find_one({"_id": "rp-elsewhere"})
            response = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-1")
            assert response.status_code == 200, response.text[:300]
            body = response.json()
            target_id = body["subscription_id"]
            (create,) = _creates(provider)
            assert abs((create[-1] - provider.cycle_end).total_seconds()) < 1, "starts when the paid period ends"
            target = await env.db.subscriptions.find_one({"_id": _oid(target_id)})
            assert (target["status"], target["replaces_subscription_id"]) == ("pending", SOURCE)
            assert (await _source(env))["status"] == "active" and _cancels(provider) == [], "nothing until payment"
            # The same request again: the same answer, never a second provider subscription.
            again = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-1")
            assert again.status_code == 200 and again.json()["subscription_id"] == target_id
            other = await _call(env, "/subscriptions/checkout", {**REPLACE, "plan_code": "dms_enterprise"},
                                "subscription.checkout", key="k-1")
            assert other.status_code == 409
            assert len(_creates(provider)) == 1

            paid = await _pay(env, target, "rp-pay-1")
            assert paid.status_code == 200 and paid.json().get("changed") is True, paid.text[:300]
            assert _cancels(provider) == [("cancel", f"gw_{SOURCE}", False)], "cancelled at its cycle end, once"
            source = await _source(env)
            assert source["status"] == "active" and source["replaced_by_subscription_id"] == target_id
            assert abs((source["ends_at"] - provider.cycle_end).total_seconds()) < 1
            assert source["gateway_cancellation"]["state"] == "scheduled"
            (op,) = await _ops(env, "replacement")
            assert op["status"] == "completed"
            # No gap and no overlap: the source governs until its paid period ends, then the target.
            service = EntitlementService(env.db)
            before_end = await service.resolve_subscription(organization_id=ORG_A, project_id=PROJ_A1,
                                                            now=provider.cycle_end - timedelta(hours=1))
            after_end = await service.resolve_subscription(organization_id=ORG_A, project_id=PROJ_A1,
                                                           now=provider.cycle_end + timedelta(hours=1))
            assert str(before_end.subscription["_id"]) == SOURCE
            assert str(after_end.subscription["_id"]) == target_id
            # A duplicate confirmation (another event id) cancels nothing more.
            await _pay(env, target, "rp-pay-2")
            assert len(_cancels(provider)) == 1
            assert await env.db.subscriptions.find_one({"_id": "rp-elsewhere"}) == elsewhere, "nothing else touched"
            assert not [c for c in provider.calls if c[0] in {"refund", "charge"}]

    _run(scenario())


def _oid(value: str) -> Any:
    from bson import ObjectId

    return ObjectId(value) if ObjectId.is_valid(value) else value


def test_an_amount_mismatch_on_the_target_cancels_nothing(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            body = (await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-mm")).json()
            target = await env.db.subscriptions.find_one({"_id": _oid(body["subscription_id"])})
            await _pay(env, target, "rp-mm", amount=1)
            assert _cancels(provider) == [] and (await _source(env)).get("replaced_by_subscription_id") is None
            assert (await env.db.subscriptions.find_one({"_id": target["_id"]}))["status"] == "pending"

    _run(scenario())


def test_simultaneous_replacements_create_one_target(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            answers = await asyncio.gather(*[
                _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key=f"k-sim-{i}") for i in range(5)])
            assert sorted(a.status_code for a in answers).count(200) == 1, [a.text[:100] for a in answers]
            assert len(_creates(provider)) == 1
            same = await asyncio.gather(*[
                _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-sim-same") for _ in range(3)])
            assert len(_creates(provider)) == 1, [a.text[:100] for a in same]
            assert await env.db.subscriptions.count_documents({"replaces_subscription_id": SOURCE}) == 1

    _run(scenario())


def test_a_failed_source_cancellation_is_reviewed_and_completed_by_retry(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            body = (await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-f")).json()
            target = await env.db.subscriptions.find_one({"_id": _oid(body["subscription_id"])})
            provider.cancel_applies = False
            provider.cancel_failures = [GatewayOutcomeUnknown("timeout", cause_type="ReadTimeout")]
            paid = await _pay(env, target, "rp-f-1")
            assert paid.status_code == 200 and paid.json().get("changed") is True, "the payment still counts"
            (op,) = await _ops(env, "replacement")
            assert op["status"] == "needs_review" and op["items"][1]["gateway"] == "outcome_unknown"
            assert (await _source(env)).get("replaced_by_subscription_id") is None, "source untouched until confirmed"
            billing = await _user(env.db, "rp_billing", ["contraclaim_billing_admin"], organization_id=ORG_A, projects=[PROJ_A1])
            tenant = await _call(env, f"/plan-settings/operations/{op['_id']}/retry", None, STEP, "rp_billing", billing)
            assert tenant.status_code == 403
            retried = await _call(env, f"/plan-settings/operations/{op['_id']}/retry", None, STEP)
            assert retried.status_code == 200 and retried.json()["status"] == "completed", retried.text[:200]
            assert (await _source(env))["replaced_by_subscription_id"] == str(target["_id"])
            assert len(_cancels(provider)) == 2, "the unapplied cancel was sent again; nothing else"

    _run(scenario())


def test_a_source_changed_before_payment_is_left_alone_and_reviewed(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            body = (await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-c")).json()
            target = await env.db.subscriptions.find_one({"_id": _oid(body["subscription_id"])})
            await env.db.subscriptions.update_one({"_id": SOURCE}, {"$set": {"status": "cancelled"}})
            await _pay(env, target, "rp-c-1")
            (op,) = await _ops(env, "replacement")
            assert op["status"] == "needs_review" and op["items"][1]["local"] == "changed_meanwhile"
            assert _cancels(provider) == []
            assert await env.db.audit_events.count_documents(
                {"action": "subscription.replacement_needs_review", "reason": "source_changed_meanwhile"}) == 1

    _run(scenario())


def test_an_unanswered_target_create_is_reconciled_then_completed(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            provider.create_failure = GatewayOutcomeUnknown("timeout", cause_type="ReadTimeout")  # applied there
            first = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-u")
            assert first.status_code == 502 and "unknown" in first.text, first.text[:200]
            provider.create_failure = None
            (op,) = await _ops(env, "replacement")
            retried = await _call(env, f"/plan-settings/operations/{op['_id']}/retry", None, STEP)
            assert retried.status_code == 200 and retried.json()["reconciled"] is True, retried.text[:200]
            target = await env.db.subscriptions.find_one({"replaces_subscription_id": SOURCE})
            assert target["replacement_operation_id"] == op["_id"] and target["starts_at"] is not None
            assert len(_creates(provider)) == 1
            await _pay(env, target, "rp-u-1")
            assert (await env.db.subscription_operations.find_one({"_id": op["_id"]}))["status"] == "completed"
            assert _cancels(provider) == [("cancel", f"gw_{SOURCE}", False)]

    _run(scenario())


def test_a_tenant_cannot_replace_another_organisations_subscription(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            outsider = await _user(env.db, "rp_outsider", ["contraclaim_billing_admin"], organization_id=ORG_B)
            response = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", "rp_outsider",
                                   outsider, key="k-x")
            assert response.status_code == 403, response.text[:200]
            assert _creates(provider) == [] and await env.db.subscription_operations.count_documents({}) == 0

    _run(scenario())


def test_completion_before_the_target_is_paid_cancels_nothing(monkeypatch, provider) -> None:
    """The source is cancelled only on the target's confirmed payment: an early or repeated
    completion attempt (a retry, a stray event for another subscription) changes nothing."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.monetization_service import MonetizationService

            await _seed(env, provider)
            body = (await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-early")).json()
            (op,) = await _ops(env, "replacement")
            for target_state in ({}, {"status": "active", "billing_status": "pending"}):
                if target_state:
                    await env.db.subscriptions.update_one({"_id": _oid(body["subscription_id"])}, {"$set": target_state})
                answer = await MonetizationService(env.db).complete_replacement(op["_id"], actor="test")
                assert answer["status"] == "awaiting_payment", answer
            assert _cancels(provider) == [] and (await _source(env)).get("replaced_by_subscription_id") is None

    _run(scenario())
