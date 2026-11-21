"""Workstream 4 (owner decision 2026-11-21): provider reliability.

Before: a provider failure was classified only as "refused connection" or "anything else";
a ``4xx`` refusal of a cancel was an unknown outcome and a timeout in a plan-settings
cancellation was recorded "failed" (nothing changed) though the provider may have applied it;
a checkout whose ``create`` timed out left no record, so the next request - under any key -
created a second provider subscription (two payment links for one scope); an abandoned
checkout was cancelled when the provider could not be read; and a Razorpay cancellation at
cycle end (status stays ``active`` until the cycle ends) could not be told from a failed one.

Now: failures are classified by what they prove (not sent / rejected / unknown); an unknown
outcome is ``outcome_unknown``, listed and reconciled against the provider, never re-sent as a
new create; a checkout is a durable operation whose id the provider subscription carries, so
an unanswered create is found again; a cycle-end cancel is recorded with its effective date
and a read-only reconciliation reads "scheduled" before it and "still billable" after it.

Real HTTP; the provider is a fake implementing the adapter interface. Opt-in:
``CLAIM_BENCH_MONGODB_URI``. No real provider is called.
"""

from __future__ import annotations

import asyncio
import itertools
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.services.payment_gateway import GatewayCustomer, GatewaySubscription, PaymentGatewayInterface
from rbac_backend.services.payment_gateway_reliability import (
    GatewayOutcomeUnknown,
    GatewayRejected,
    GatewayUnavailable,
    ProviderSubscriptionState,
)
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_gateway_identity_protection_mongo import SECRET, _headers, _webhook
from rbac_backend.tests.integration.test_security_remediation_mongo import _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"
STEP = "subscription.entitlement.manage"
CHECKOUT = {"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_starter"}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class Provider(PaymentGatewayInterface):
    """A provider whose answers each test programs. ``subscriptions`` is its own state."""

    supports_in_place_plan_change = False
    supports_reference_search = True

    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.subscriptions: Dict[str, Dict[str, Any]] = {}
        self.create_failure: Any = None      # raised after the provider created it (unknown) or before
        self.create_applies = True           # whether a failing create was applied at the provider
        self.cancel_failures: List[Any] = []  # raised in order by successive cancels
        self.cancel_applies = True
        self.fetch_failure: Any = None
        self.cycle_end = datetime.utcnow() + timedelta(days=20)
        self._ids = itertools.count(1)

    async def create_customer(self, *, organization_id: str, name: str, email: Optional[str] = None) -> GatewayCustomer:
        return GatewayCustomer(gateway_customer_id=f"cust_{organization_id}")

    async def create_subscription(self, *, gateway_customer_id: str, plan_code: str, billing_period: str,
                                  metadata: Optional[Dict[str, Any]] = None) -> GatewaySubscription:
        self.calls.append(("create", plan_code, dict(metadata or {})))
        gateway_id = f"sub_pr_{next(self._ids)}"
        if self.create_failure and not self.create_applies:
            raise self.create_failure
        self.subscriptions[gateway_id] = {"status": "created", "notes": dict(metadata or {}),
                                          "customer_id": gateway_customer_id, "plan_id": plan_code}
        if self.create_failure:
            raise self.create_failure
        return GatewaySubscription(gateway_subscription_id=gateway_id, gateway_customer_id=gateway_customer_id,
                                   plan_id=plan_code, status="created", metadata={"short_url": f"https://pay/{gateway_id}"})

    async def find_subscription_by_reference(self, *, reference: str, created_after: Any = None) -> Any:
        self.calls.append(("search", reference))
        if self.fetch_failure:
            raise self.fetch_failure
        for gateway_id, row in self.subscriptions.items():
            if row["notes"].get("checkout_reference") == reference:
                return GatewaySubscription(gateway_subscription_id=gateway_id, gateway_customer_id=row["customer_id"],
                                           plan_id=row["plan_id"], status=row["status"])
        return None

    async def fetch_subscription_state(self, *, gateway_subscription_id: str) -> Optional[ProviderSubscriptionState]:
        self.calls.append(("fetch", gateway_subscription_id))
        if self.fetch_failure:
            raise self.fetch_failure
        row = self.subscriptions.get(gateway_subscription_id)
        return ProviderSubscriptionState(status=row["status"], current_end=self.cycle_end) if row else None

    async def fetch_subscription_status(self, *, gateway_subscription_id: str) -> Optional[str]:
        state = await self.fetch_subscription_state(gateway_subscription_id=gateway_subscription_id)
        return state.status if state else None

    async def cancel_with_evidence(self, *, gateway_subscription_id: str, immediate: bool = False) -> Any:
        self.calls.append(("cancel", gateway_subscription_id, immediate))
        row = self.subscriptions.setdefault(gateway_subscription_id, {"status": "active", "notes": {}})
        failure = self.cancel_failures.pop(0) if self.cancel_failures else None
        if failure is not None and not self.cancel_applies:
            raise failure
        if row["status"] in {"cancelled", "completed"} or row.get("cancel_scheduled") and not immediate:
            # Razorpay documents 400 for a cancelled subscription; a repeat of a scheduled
            # cancel is undocumented - this provider refuses it too (the hard case).
            raise GatewayRejected("Subscription is not cancellable", cause_type="BadRequestError")
        if immediate:
            row["status"] = "cancelled"
        else:
            row["cancel_scheduled"] = True  # status stays active until the cycle ends
        if failure is not None:
            raise failure
        return ProviderSubscriptionState(status=row["status"], current_end=self.cycle_end)

    async def cancel_subscription(self, *, gateway_subscription_id: str, immediate: bool = False) -> bool:
        await self.cancel_with_evidence(gateway_subscription_id=gateway_subscription_id, immediate=immediate)
        return True

    async def update_subscription(self, **kwargs: Any) -> Any:
        raise AssertionError("not used")

    async def reactivate_subscription(self, **kwargs: Any) -> Any:
        raise AssertionError("not used")

    async def create_invoice(self, **kwargs: Any) -> Any:
        raise AssertionError("not used")

    async def charge_invoice(self, **kwargs: Any) -> Any:
        raise AssertionError("never charged")


@pytest.fixture()
def provider(monkeypatch: pytest.MonkeyPatch) -> Provider:
    from rbac_backend.core.config import settings
    from rbac_backend.routers import rbac_monetization
    from rbac_backend.services import monetization_service

    fake = Provider()
    monkeypatch.setattr(monetization_service, "get_payment_gateway", lambda *a, **k: fake)
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "razorpay", raising=False)
    monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", SECRET, raising=False)
    monkeypatch.setattr(rbac_monetization.monetization_mutation_limiter, "max_requests", 10_000)
    return fake


async def _call(env: Any, path: str, body: Any, action: str, persona: str = "superadmin", user_id: Optional[str] = None,
                method: str = "POST", key: Optional[str] = None) -> Any:
    headers = _headers(persona, user_id, action=action)
    if key:
        headers["Idempotency-Key"] = key
    response = await env.client.request(method, f"{BASE}{path}", json=body, headers=headers)
    env.client.cookies.clear()
    return response


def _bound(sub_id: str, **extra: Any) -> Dict[str, Any]:
    now = datetime.utcnow()
    return {"_id": sub_id, "organization_id": ORG_A, "project_id": PROJ_A1, "package_id": None, "plan_code": "dms_starter",
            "billing_period": "monthly", "status": "active", "billing_status": "active", "auto_renew": True,
            "governing_since": now - timedelta(days=3), "current_period_end": now + timedelta(days=20),
            "active_add_ons": [], "payment_provider": "razorpay", "payment_gateway_subscription_id": f"gw_{sub_id}",
            "payment_gateway_customer_id": "cust", "payment_gateway_bound_by": "checkout", **extra}


async def _ops(env: Any, kind: str) -> List[Dict[str, Any]]:
    return await env.db.subscription_operations.find({"kind": kind}).to_list(length=None)


# --- lifecycle cancellation --------------------------------------------------------------


def test_a_timed_out_cycle_end_cancel_is_unknown_then_reconciled_as_scheduled(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_bound("pr-c1"))
            provider.subscriptions["gw_pr-c1"] = {"status": "active", "notes": {}}
            # The provider applied the cycle-end cancel, but the answer never came back.
            provider.cancel_failures = [GatewayOutcomeUnknown("read timeout", cause_type="ReadTimeout")]
            first = await _call(env, "/subscriptions/pr-c1/cancel", {"immediate": False}, "subscription.cancel", key="c-1")
            assert first.status_code == 502 and "unknown" in first.text, first.text[:200]
            row = await env.db.subscriptions.find_one({"_id": "pr-c1"})
            assert row["status"] == "active", "never cancelled locally on an unknown outcome"
            (op,) = await _ops(env, "cancellation")
            assert (op["status"], op["items"][0]["gateway"], op["items"][0]["last_error"]) == (
                "needs_review", "outcome_unknown", "ReadTimeout")
            # Retried with the same key: the provider still shows active (Razorpay keeps the
            # status until cycle end) and refuses a second cycle-end cancel. That proves nothing
            # about the first request: still unknown, never "failed", never done.
            second = await _call(env, "/subscriptions/pr-c1/cancel", {"immediate": False}, "subscription.cancel", key="c-1")
            assert second.status_code == 502, second.text[:200]
            (op,) = await _ops(env, "cancellation")
            assert (op["status"], op["items"][0]["gateway"]) == ("needs_review", "outcome_unknown")
            assert (await env.db.subscriptions.find_one({"_id": "pr-c1"}))["status"] == "active"
            assert not [c for c in provider.calls if c[0] == "create"]

    _run(scenario())


def test_a_confirmed_cycle_end_cancel_is_recorded_with_its_effective_date(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_bound("pr-c2"))
            provider.subscriptions["gw_pr-c2"] = {"status": "active", "notes": {}}
            response = await _call(env, "/subscriptions/pr-c2/cancel", {"immediate": False}, "subscription.cancel", key="c-2")
            assert response.status_code == 200, response.text[:200]
            row = await env.db.subscriptions.find_one({"_id": "pr-c2"})
            record = row["gateway_cancellation"]
            assert (record["mode"], record["state"], record["request_confirmed"]) == ("cycle_end", "scheduled", True)
            assert abs((record["effective_at"] - provider.cycle_end).total_seconds()) < 1
            assert record["provider_status_at_request"] == "active"

    _run(scenario())


def test_a_rejected_cancel_changes_nothing_and_is_not_a_review_item(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_bound("pr-c3"))
            provider.subscriptions["gw_pr-c3"] = {"status": "active", "notes": {}}
            provider.cancel_applies = False
            provider.cancel_failures = [GatewayRejected("in final cycle", cause_type="BadRequestError")]
            response = await _call(env, "/subscriptions/pr-c3/cancel", {"immediate": False}, "subscription.cancel", key="c-3")
            assert response.status_code == 502 and "unknown" not in response.text, response.text[:200]
            (op,) = await _ops(env, "cancellation")
            assert (op["status"], op["items"][0]["gateway"]) == ("failed", "failed")
            listed = await _call(env, "/plan-settings/operations", None, "x", method="GET")
            assert op["_id"] not in {row["id"] for row in listed.json()}
            assert (await env.db.subscriptions.find_one({"_id": "pr-c3"}))["status"] == "active"

    _run(scenario())


# --- checkout: an unanswered create is never repeated ------------------------------------


def test_an_unanswered_checkout_blocks_the_scope_and_is_found_again_by_reference(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            provider.create_failure = GatewayOutcomeUnknown("read timeout", cause_type="ReadTimeout")
            first = await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")
            assert first.status_code == 502 and "unknown" in first.text, first.text[:200]
            assert len([c for c in provider.calls if c[0] == "create"]) == 1
            (op,) = await _ops(env, "checkout")
            assert (op["status"], op["items"][0]["gateway"]) == ("needs_review", "outcome_unknown")
            assert provider.calls[0][2]["checkout_reference"] == op["_id"], "the provider subscription carries the reference"
            assert await env.db.subscriptions.count_documents({"project_id": PROJ_A1}) == 0
            # A new request - a fresh request, no key, another plan - never creates a second one:
            # the earlier checkout is reconciled first and found at the provider.
            provider.create_failure = None
            again = await _call(env, "/subscriptions/checkout", {**CHECKOUT, "plan_code": "dms_professional"},
                                "subscription.checkout")
            assert again.status_code == 409 and "awaiting payment" in again.text, again.text[:200]
            assert len([c for c in provider.calls if c[0] == "create"]) == 1
            recorded = await env.db.subscriptions.find_one({"project_id": PROJ_A1})
            assert (recorded["status"], recorded["payment_gateway_bound_by"], recorded["checkout_reference"]) == (
                "pending", "checkout", op["_id"])
            assert recorded["plan_code"] == "dms_starter", "recorded as what the provider holds, not the new request"
            assert (await env.db.subscription_operations.find_one({"_id": op["_id"]}))["status"] == "completed"
            assert await env.db.audit_events.count_documents({"action": "subscription.checkout_reconciled"}) == 1

    _run(scenario())


def test_simultaneous_checkouts_after_an_unknown_outcome_create_nothing(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            provider.create_failure = GatewayOutcomeUnknown("timeout", cause_type="ReadTimeout")
            provider.create_applies = False  # the provider never received it
            assert (await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")).status_code == 502
            provider.create_failure = None
            answers = await asyncio.gather(*[
                _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout") for _ in range(5)])
            assert all(answer.status_code == 409 for answer in answers), [a.text[:120] for a in answers]
            assert len([c for c in provider.calls if c[0] == "create"]) == 1
            assert await env.db.subscriptions.count_documents({"project_id": PROJ_A1}) == 0

    _run(scenario())


def test_an_unknown_checkout_not_at_the_provider_is_resolved_only_explicitly(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            billing = await _user(env.db, "pr_billing", ["contraclaim_billing_admin"], organization_id=ORG_A, projects=[PROJ_A1])
            provider.create_failure = GatewayOutcomeUnknown("timeout", cause_type="ReadTimeout")
            provider.create_applies = False
            assert (await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")).status_code == 502
            (op,) = await _ops(env, "checkout")
            retry = await _call(env, f"/plan-settings/operations/{op['_id']}/retry", None, STEP)
            assert retry.status_code == 409 and "no subscription" in retry.text, retry.text[:200]
            body = {"resolution": "not_created_at_provider", "reason": "Provider dashboard shows no subscription"}
            tenant = await _call(env, f"/plan-settings/operations/{op['_id']}/resolve", body, STEP, "pr_billing", billing)
            assert tenant.status_code == 403
            # Where the provider cannot be searched, the administrator must state they checked it.
            provider.supports_reference_search = False
            unchecked = await _call(env, f"/plan-settings/operations/{op['_id']}/resolve", body, STEP)
            assert unchecked.status_code == 409 and "provider_checked_manually" in unchecked.text, unchecked.text[:200]
            assert (await env.db.subscription_operations.find_one({"_id": op["_id"]}))["status"] == "needs_review"
            provider.supports_reference_search = True
            resolved = await _call(env, f"/plan-settings/operations/{op['_id']}/resolve", body, STEP)
            assert resolved.status_code == 200 and resolved.json()["status"] == "resolved", resolved.text[:200]
            assert await env.db.audit_events.count_documents({"action": "subscription.checkout_resolved_not_created"}) == 1
            provider.create_failure = None
            bought = await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")
            assert bought.status_code == 200, bought.text[:200]
            assert len([c for c in provider.calls if c[0] == "create"]) == 2

    _run(scenario())


def test_resolving_as_not_created_searches_the_provider_first(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            provider.create_failure = GatewayOutcomeUnknown("timeout", cause_type="ReadTimeout")  # applied there
            assert (await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")).status_code == 502
            (op,) = await _ops(env, "checkout")
            body = {"resolution": "not_created_at_provider", "reason": "Believed never created at the provider"}
            answer = await _call(env, f"/plan-settings/operations/{op['_id']}/resolve", body, STEP)
            assert answer.status_code == 200 and answer.json().get("reconciled") is True, answer.text[:200]
            assert (await env.db.subscription_operations.find_one({"_id": op["_id"]}))["status"] == "completed"
            assert await env.db.subscriptions.count_documents({"checkout_reference": op["_id"]}) == 1
            assert await env.db.audit_events.count_documents({"action": "subscription.checkout_resolved_not_created"}) == 0

    _run(scenario())


def test_a_refused_or_unsent_checkout_create_is_failed_and_does_not_block(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            provider.create_applies = False
            for failure in (ConnectionRefusedError("down"), GatewayRejected("bad plan", cause_type="BadRequestError")):
                provider.create_failure = failure
                response = await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")
                assert response.status_code == 502 and "unknown" not in response.text, response.text[:200]
            assert {op["status"] for op in await _ops(env, "checkout")} == {"failed"}
            provider.create_failure = None
            assert (await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")).status_code == 200

    _run(scenario())


def test_an_abandoned_checkout_is_not_cancelled_when_the_provider_cannot_be_read(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one({**_bound("pr-old", status="pending"), "billing_status": "pending",
                                                   "created_at": datetime.utcnow() - timedelta(days=3)})
            provider.fetch_failure = GatewayUnavailable("no answer", cause_type="ReadTimeout")
            response = await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")
            assert response.status_code == 409 and "could not be checked" in response.text, response.text[:200]
            assert (await env.db.subscriptions.find_one({"_id": "pr-old"}))["status"] == "pending"
            assert not [c for c in provider.calls if c[0] in {"cancel", "create"}]

    _run(scenario())


# --- reconciliation (read-only) ----------------------------------------------------------


def test_reconciliation_reads_scheduled_overdue_and_unconfirmed_cancellations_and_writes_nothing(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            now = datetime.utcnow()

            def record(effective: datetime, mode: str = "cycle_end") -> Dict[str, Any]:
                return {"mode": mode, "request_confirmed": True, "effective_at": effective, "state": "scheduled"}

            rows = {
                "rc-scheduled": ("active", record(now + timedelta(days=5))),
                "rc-overdue": ("active", record(now - timedelta(days=3))),
                "rc-ended": ("cancelled", record(now - timedelta(days=3))),
                "rc-legacy": ("active", None),          # pre-workstream-4 plan-settings cancellation
                "rc-immediate": ("active", record(now, mode="immediate")),
            }
            for sub_id, (provider_status, cancellation) in rows.items():
                await env.db.subscriptions.insert_one({
                    **_bound(sub_id, status="cancelled", project_id=f"p-{sub_id}"),
                    "cancellation_reason": "replaced_by_plan_settings",
                    **({"gateway_cancellation": cancellation} if cancellation else {})})
                provider.subscriptions[f"gw_{sub_id}"] = {"status": provider_status, "notes": {}}
            await env.db.subscriptions.insert_one(_bound("rc-unreadable", status="cancelled", project_id="p-u"))
            before = await env.db.subscriptions.find({}).sort("_id", 1).to_list(length=None)
            billing = await _user(env.db, "rc_billing", ["contraclaim_billing_admin"], organization_id=ORG_A, projects=[PROJ_A1])
            refused = await _call(env, "/plan-settings/reconciliation/cancellations", None, "x", "rc_billing", billing, method="GET")
            assert refused.status_code == 403
            response = await _call(env, "/plan-settings/reconciliation/cancellations", None, "x", method="GET")
            assert response.status_code == 200, response.text[:200]
            result = response.json()
            assert result["counts"] == {"scheduled": 1, "still_billable_after_effective_date": 1, "cancelled": 1,
                                        "unconfirmed": 1, "not_applied": 1, "unreadable": 1}, result["counts"]
            assert {row["subscription_id"] for row in result["requires_review"]} == {
                "rc-overdue", "rc-legacy", "rc-immediate", "rc-unreadable"}
            assert result["writes"] == 0
            assert await env.db.subscriptions.find({}).sort("_id", 1).to_list(length=None) == before
            assert not [c for c in provider.calls if c[0] in {"cancel", "create"}]

    _run(scenario())


# --- webhook delay -----------------------------------------------------------------------


def test_a_late_renewal_charge_for_a_scheduled_cancellation_never_revives_it(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_bound("pr-late"))
            provider.subscriptions["gw_pr-late"] = {"status": "active", "notes": {}}
            assert (await _call(env, "/subscriptions/pr-late/cancel", {"immediate": False}, "subscription.cancel",
                                key="late")).status_code == 200
            charge = await _webhook(env, "subscription.charged", "gw_pr-late", event_id="evt-late-1", nonce=10_000_000_000,
                                    entity={"customer_id": "cust"},
                                    payment={"id": "pay_late", "amount": 10, "currency": "INR", "subscription_id": "gw_pr-late"})
            assert charge.status_code == 200, charge.text[:200]
            row = await env.db.subscriptions.find_one({"_id": "pr-late"})
            assert row["status"] == "cancelled", "a delayed charge never reactivates a cancellation"
            record = await env.db.billing_records.find_one({"gateway_payment_id": "pay_late"})
            assert record is not None and record["requires_admin_review"] is True, "the money is kept for review"
            ended = await _webhook(env, "subscription.cancelled", "gw_pr-late", event_id="evt-late-2", nonce=10_000_000_001,
                                   entity={"customer_id": "cust", "status": "cancelled"})
            assert ended.status_code == 200
            assert (await env.db.subscriptions.find_one({"_id": "pr-late"}))["status"] == "cancelled"

    _run(scenario())


# --- plan-settings cancellation ----------------------------------------------------------


def test_a_plan_settings_cancel_timeout_is_unknown_and_completes_once_reconciled(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_bound("pr-ps"))
            provider.subscriptions["gw_pr-ps"] = {"status": "active", "notes": {}}
            provider.cancel_applies = False  # timed out before the provider applied it
            provider.cancel_failures = [GatewayOutcomeUnknown("timeout", cause_type="ReadTimeout")]
            body = {"organization_id": ORG_A, "project_id": PROJ_A1, "mode": "plan", "plan_code": "dms_enterprise",
                    "billing_period": "monthly", "status": "active"}
            first = await _call(env, "/plan-settings/scope", body, STEP, method="PUT", key="ps-1")
            assert first.status_code == 502, first.text[:200]
            (op,) = await _ops(env, "plan_settings_replacement")
            assert op["items"][0]["gateway"] == "outcome_unknown"
            assert (await env.db.subscriptions.find_one({"_id": "pr-ps"}))["status"] == "active"
            second = await _call(env, "/plan-settings/scope", body, STEP, method="PUT", key="ps-1")
            assert second.status_code == 200, second.text[:200]
            row = await env.db.subscriptions.find_one({"_id": "pr-ps"})
            assert row["status"] == "cancelled" and row["gateway_cancellation"]["state"] == "scheduled"
            assert [c for c in provider.calls if c[0] == "cancel"] == [("cancel", "gw_pr-ps", False)] * 2

    _run(scenario())
