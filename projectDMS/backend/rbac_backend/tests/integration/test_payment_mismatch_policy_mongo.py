"""Workstream 2 (approved policy design, 2026-11-21): payment amount mismatch, behind
``PAYMENT_MISMATCH_POLICY`` (default ``off``; activation and the production grace length need
separate owner approval - the 7 days here are a test value).

Before (and with the flag off): a renewal payment that did not match marked billing "review"
and changed nothing else - no time limit, no record of how long; an activation whose plan
price could not be resolved activated without any amount check.

With ``enforce``: initial activation needs a verified amount and currency; a renewal
discrepancy enters financial review with an auditable reason and a grace anchored at the
first open discrepancy (repeats never extend it); nothing is granted from the unverified
payment; when the grace lapses unsettled the subscription is blocking, and only a Super Admin
settlement (or write-off) ends the review. Real HTTP; signed webhooks; no real provider.
"""

from __future__ import annotations

import asyncio
import itertools
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_gateway_identity_protection_mongo import _webhook
from rbac_backend.tests.integration.test_provider_reliability_mongo import Provider, _bound, _call
from rbac_backend.tests.integration.test_security_remediation_mongo import _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

STEP = "subscription.entitlement.manage"
GRACE_DAYS = 7
_nonce = itertools.count(20_000_000_000)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture()
def provider(monkeypatch: pytest.MonkeyPatch) -> Provider:
    from rbac_backend.core.config import settings
    from rbac_backend.routers import rbac_monetization
    from rbac_backend.services import monetization_service
    from rbac_backend.tests.integration.test_gateway_identity_protection_mongo import SECRET

    fake = Provider()
    monkeypatch.setattr(monetization_service, "get_payment_gateway", lambda *a, **k: fake)
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "razorpay", raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", SECRET, raising=False)
    monkeypatch.setattr(rbac_monetization.monetization_mutation_limiter, "max_requests", 10_000)
    return fake


def _enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "PAYMENT_MISMATCH_POLICY", "enforce", raising=False)
    monkeypatch.setattr(settings, "PAYMENT_MISMATCH_GRACE_DAYS", GRACE_DAYS, raising=False)


async def _plans(env: Any) -> None:
    """The plan catalogue the webhook prices against (seeded at startup in production)."""
    from rbac_backend.services.monetization_service import MonetizationService

    await MonetizationService(env.db).seed_default_plans()


async def _price(env: Any) -> int:
    row = await env.db.plans.find_one({"code": "dms_starter"})
    return int((row.get("pricing_tiers") or {}).get("monthly") or row.get("base_price_minor"))


async def _charge(env: Any, sub_id: str, payment_id: Optional[str], amount: Optional[int] = None, currency: str = "INR",
                  event: str = "subscription.charged") -> Any:
    payment = None if payment_id is None else {
        "id": payment_id, "amount": amount if amount is not None else await _price(env), "currency": currency,
        "subscription_id": f"gw_{sub_id}"}
    return await _webhook(env, event, f"gw_{sub_id}", event_id=f"evt-{payment_id or next(_nonce)}-{next(_nonce)}",
                          nonce=next(_nonce), entity={"customer_id": "cust"}, payment=payment)


async def _row(env: Any, sub_id: str) -> Dict[str, Any]:
    return await env.db.subscriptions.find_one({"_id": sub_id})


async def _class_at(env: Any, moment: datetime) -> Optional[str]:
    from rbac_backend.services.entitlement_service import EntitlementService

    resolution = await EntitlementService(env.db).resolve_subscription(organization_id=ORG_A, project_id=PROJ_A1, now=moment)
    return resolution.status_class


# --- flag off: unchanged ---------------------------------------------------------------------


def test_with_the_policy_off_the_webhook_is_unchanged(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _plans(env)
            await env.db.subscriptions.insert_one(_bound("pm-off"))
            response = await _charge(env, "pm-off", "pay-off-1", amount=1)
            assert response.status_code == 200 and response.json().get("validation") == "amount_mismatch"
            row = await _row(env, "pm-off")
            assert (row["status"], row["billing_status"]) == ("active", "review") and "financial_review" not in row
            assert await env.db.payment_discrepancies.count_documents({}) == 0
            # Pre-existing (recorded, closed only with the policy on): an unresolvable price activates.
            await env.db.subscriptions.insert_one({**_bound("pm-off-new", project_id="pm-off-p", plan_code="no_such_plan"),
                                                   "status": "pending", "billing_status": "pending"})
            await _charge(env, "pm-off-new", "pay-off-2", amount=5)
            assert (await _row(env, "pm-off-new"))["status"] == "active"

    _run(scenario())


# --- initial activation ------------------------------------------------------------------------


def test_initial_activation_requires_a_verified_amount_and_currency(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _plans(env)
            _enforce(monkeypatch)
            price = await _price(env)
            cases = [("pm-i-plan", {"plan_code": "no_such_plan"}, price, "INR"),
                     ("pm-i-amount", {}, price + 1, "INR"),
                     ("pm-i-currency", {}, price, "USD")]
            for sub_id, extra, amount, currency in cases:
                await env.db.subscriptions.insert_one({**_bound(sub_id, project_id=f"p-{sub_id}", **extra),
                                                       "status": "pending", "billing_status": "pending"})
                response = await _charge(env, sub_id, f"pay-{sub_id}", amount=amount, currency=currency)
                assert response.status_code == 200 and response.json().get("changed") is False, response.text[:200]
                assert (await _row(env, sub_id))["status"] == "pending", sub_id
                assert await env.db.payment_discrepancies.count_documents(
                    {"subscription_id": sub_id, "kind": "initial", "status": "open"}) == 1, sub_id
            # Without a payment in the event, nothing activates either.
            await env.db.subscriptions.insert_one({**_bound("pm-i-none", project_id="p-none"), "status": "pending",
                                                   "billing_status": "pending"})
            await _charge(env, "pm-i-none", None, event="subscription.activated")
            assert (await _row(env, "pm-i-none"))["status"] == "pending"
            await env.db.subscriptions.insert_one({**_bound("pm-i-ok", project_id="p-ok"), "status": "pending",
                                                   "billing_status": "pending"})
            await _charge(env, "pm-i-ok", "pay-pm-i-ok")
            assert ((await _row(env, "pm-i-ok"))["status"], (await _row(env, "pm-i-ok"))["billing_status"]) == ("active", "active")

    _run(scenario())


# --- renewal discrepancies -----------------------------------------------------------------------


def test_a_renewal_discrepancy_keeps_paid_service_for_a_bounded_grace_that_repeats_never_extend(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _plans(env)
            _enforce(monkeypatch)
            await env.db.subscriptions.insert_one(_bound("pm-r"))
            before = await _row(env, "pm-r")
            first = await _charge(env, "pm-r", "pay-r-1", amount=1)
            assert first.json().get("validation") == "financial_review", first.text[:200]
            row = await _row(env, "pm-r")
            assert (row["status"], row["billing_status"]) == ("active", "financial_review")
            review = row["financial_review"]
            grace_until = review["grace_until"]
            assert abs((grace_until - review["opened_at"]) - timedelta(days=GRACE_DAYS)) < timedelta(seconds=1)
            # Nothing granted from the unverified payment.
            for field in ("plan_code", "billing_period", "active_add_ons", "current_period_end", "ends_at", "governing_since"):
                assert row.get(field) == before.get(field), field
            # Repeated mismatches (and the same one delivered again) never move the grace.
            for n in range(2, 6):
                await _charge(env, "pm-r", f"pay-r-{n}", amount=n)
            await _charge(env, "pm-r", "pay-r-2", amount=2)
            row = await _row(env, "pm-r")
            assert row["financial_review"]["grace_until"] == grace_until
            assert await env.db.payment_discrepancies.count_documents({"subscription_id": "pm-r", "kind": "renewal"}) == 5
            assert row["financial_review"]["discrepancies"] == 5
            assert await env.db.audit_events.count_documents(
                {"action": "billing.payment_discrepancy_renewal", "metadata.requires_admin_review": True}) == 5
            # Service continues within the grace and stops after it.
            assert await _class_at(env, datetime.utcnow()) == "eligible"
            assert await _class_at(env, grace_until + timedelta(minutes=1)) == "blocking"
            # A correct payment later does not lift the review: only a settlement does.
            await _charge(env, "pm-r", "pay-r-good")
            row = await _row(env, "pm-r")
            assert row["billing_status"] == "financial_review" and row["financial_review"]["state"] == "open"
            assert await _class_at(env, grace_until + timedelta(minutes=1)) == "blocking"

    _run(scenario())


def test_a_past_due_subscription_is_not_reactivated_by_an_unverified_payment(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _plans(env)
            _enforce(monkeypatch)
            await env.db.subscriptions.insert_one(_bound("pm-pd", status="past_due", billing_status="past_due"))
            await _charge(env, "pm-pd", "pay-pd-1", amount=1)
            row = await _row(env, "pm-pd")
            assert row["status"] == "past_due" and row["financial_review"]["state"] == "open"

    _run(scenario())


def test_a_lifecycle_event_without_a_payment_changes_nothing_for_a_paid_subscription(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _plans(env)
            _enforce(monkeypatch)
            await env.db.subscriptions.insert_one(_bound("pm-np"))
            response = await _charge(env, "pm-np", None, event="subscription.activated")
            assert response.json().get("validation") == "no_payment_in_event", response.text[:200]
            assert await env.db.payment_discrepancies.count_documents({}) == 0
            assert (await _row(env, "pm-np"))["billing_status"] == "active"

    _run(scenario())


# --- settlement ------------------------------------------------------------------------------------


def test_settlement_escalation_and_write_off_are_super_admin_decisions(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _plans(env)
            _enforce(monkeypatch)
            await env.db.subscriptions.insert_one(_bound("pm-s"))
            await _charge(env, "pm-s", "pay-s-1", amount=1)
            await _charge(env, "pm-s", "pay-s-2", amount=2)
            grace_until = (await _row(env, "pm-s"))["financial_review"]["grace_until"]
            listed = await _call(env, "/billing/discrepancies", None, "x", method="GET")
            ids = sorted(row["id"] for row in listed.json() if row["subscription_id"] == "pm-s")
            assert len(ids) == 2
            billing = await _user(env.db, "pm_billing", ["contraclaim_billing_admin"], organization_id=ORG_A, projects=[PROJ_A1])
            body = {"outcome": "settled", "reason": "Customer paid the difference by bank transfer"}
            tenant = await _call(env, f"/billing/discrepancies/{ids[0]}/resolve", body, STEP, "pm_billing", billing)
            assert tenant.status_code == 403
            assert (await _call(env, "/billing/discrepancies", None, "x", "pm_billing", billing, method="GET")).status_code == 403
            escalated = await _call(env, f"/billing/discrepancies/{ids[0]}/resolve",
                                    {"outcome": "escalated", "reason": "Needs finance team confirmation"}, STEP)
            assert escalated.status_code == 200 and escalated.json()["review_closed"] is False
            assert (await _row(env, "pm-s"))["financial_review"]["grace_until"] == grace_until, "escalation never extends"
            first = await _call(env, f"/billing/discrepancies/{ids[0]}/resolve", body, STEP)
            assert first.status_code == 200 and first.json()["review_closed"] is False, "one still open"
            again = await _call(env, f"/billing/discrepancies/{ids[0]}/resolve", body, STEP)
            assert again.status_code == 409
            last = await _call(env, f"/billing/discrepancies/{ids[1]}/resolve",
                               {"outcome": "written_off", "reason": "Rounding difference accepted by finance"}, STEP)
            assert last.status_code == 200 and last.json()["review_closed"] is True, last.text[:200]
            row = await _row(env, "pm-s")
            assert (row["billing_status"], row["financial_review"]["state"]) == ("active", "settled")
            assert await _class_at(env, grace_until + timedelta(days=1)) == "eligible"
            assert await env.db.audit_events.count_documents({"action": {"$regex": "^billing.payment_discrepancy_"},
                                                             "resource_type": "payment_discrepancy"}) == 3

    _run(scenario())


def test_one_discrepancy_per_payment_whatever_event_carried_it(monkeypatch, provider) -> None:
    """The webhook already drops a payment it has seen; the discrepancy record is keyed on the
    payment too, so two deliveries racing past that check still record one discrepancy."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import payment_discrepancy_service as pd

            _enforce(monkeypatch)
            await env.db.subscriptions.insert_one(_bound("pm-race"))
            sub = await _row(env, "pm-race")
            now = datetime.utcnow()
            results = await asyncio.gather(*[
                pd.record(env.db, subscription=sub, provider="razorpay", kind="renewal", reason="amount 1 != expected",
                          event_id=f"evt-race-{n}", gateway_payment_id="pay-race", amount_minor=1, currency="INR",
                          expected={"amount_minor": 2500000, "currency": "INR"}, now=now)
                for n in range(3)])
            assert sum(1 for r in results if r["new"]) == 1
            assert await env.db.payment_discrepancies.count_documents({"subscription_id": "pm-race"}) == 1

    _run(scenario())
