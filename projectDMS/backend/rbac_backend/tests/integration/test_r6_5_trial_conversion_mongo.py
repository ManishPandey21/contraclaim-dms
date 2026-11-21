"""R6-5 (owner decision 2026-11-21): a trial never becomes paid service by a local update.

Before: ``POST /subscriptions/{id}/convert-trial`` set ``status=active``,
``billing_status=active`` and ``ends_at=None`` on any row whose status read ``trial`` -
expired or outranked trials included - with unvalidated add-ons, no gateway and no payment.

Now: only a running trial that enforcement selects converts; plan, period and add-ons are
validated; a priced plan converts through a payment-confirmed checkout (the trial governs
until the signed webhook confirms payment, then the paid subscription governs and the trial
is closed); a free plan converts in place as ``not_billed``; a no-charge conversion of a
priced plan needs Super Admin authority and a reason and is audited. One checkout per trial,
whatever the number of requests; a gateway failure changes nothing and can be retried.

Real HTTP through the production ``app``; webhooks signed with a test Razorpay secret; the
gateway's outbound calls are a recording fake (no network). Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
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
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_B1

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"
TRIAL = "r65-trial-a1"
TRIAL_B = "r65-trial-b1"
STEP = "subscription.trial.manage"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeGateway:
    """Records outbound gateway calls; ``fail`` makes subscription creation raise."""

    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.fail = False
        self._ids = itertools.count(1)

    async def create_customer(self, *, organization_id: str, name: str, email: Optional[str] = None) -> Any:
        self.calls.append(("customer", organization_id))
        return SimpleNamespace(gateway_customer_id=f"cust_{organization_id}")

    async def create_subscription(self, *, gateway_customer_id: str, plan_code: str, billing_period: str,
                                  metadata: Optional[Dict[str, Any]] = None) -> Any:
        if self.fail:
            # Refused: provably never reached the provider (workstream 4). An ambiguous
            # failure is an unknown outcome instead - test_provider_reliability_mongo.py.
            raise ConnectionRefusedError("gateway unavailable")
        await asyncio.sleep(0.05)  # widen the window for concurrent requests
        gateway_id = f"sub_r65_{next(self._ids)}"
        self.calls.append(("subscription", gateway_id, plan_code, (metadata or {}).get("converts_trial_id")))
        return SimpleNamespace(gateway_subscription_id=gateway_id, metadata={"short_url": f"https://pay.example/{gateway_id}"})


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


def _trial(sub_id: str, org: str, project: str, *, days_left: int = 10, plan: str = "dms_starter", **extra: Any) -> Dict[str, Any]:
    now = datetime.utcnow()
    return {"_id": sub_id, "organization_id": org, "project_id": project, "package_id": None, "plan_code": plan,
            "billing_period": "monthly", "status": "trial", "billing_status": "trial", "trial": True,
            "starts_at": now - timedelta(days=4), "ends_at": now + timedelta(days=days_left),
            "trial_ends_at": now + timedelta(days=days_left), "governing_since": now - timedelta(days=4),
            "auto_renew": False, "active_add_ons": [], "updated_at": now, **extra}


async def _convert(env: Any, sub_id: str, body: Dict[str, Any], persona: str = "superadmin",
                   user_id: Optional[str] = None) -> Any:
    response = await env.client.post(f"{BASE}/subscriptions/{sub_id}/convert-trial", json=body,
                                     headers=_headers(persona, user_id, action=STEP))
    env.client.cookies.clear()
    return response


async def _row(env: Any, sub_id: str = TRIAL) -> Dict[str, Any]:
    from bson import ObjectId

    key: Any = ObjectId(sub_id) if ObjectId.is_valid(sub_id) else sub_id
    return await env.db.subscriptions.find_one({"_id": key})


async def _governing(env: Any, project: str = PROJ_A1, org: str = ORG_A) -> Optional[str]:
    from rbac_backend.services.entitlement_service import EntitlementService

    resolution = await EntitlementService(env.db).resolve_subscription(organization_id=org, project_id=project)
    return str((resolution.subscription or {}).get("_id") or "") or None


def test_expired_ended_and_outranked_trials_are_refused_and_nothing_changes(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            now = datetime.utcnow()
            await env.db.subscriptions.insert_many([
                _trial("r65-expired", ORG_A, PROJ_A1, trial_ends_at=now - timedelta(days=1)),
                _trial("r65-ended", ORG_A, "proj-r65-2", ends_at=now - timedelta(days=1)),
                _trial("r65-outranked", ORG_A, "proj-r65-3"),
                {**_trial("r65-newer", ORG_A, "proj-r65-3"), "status": "active", "billing_status": "active",
                 "trial": False, "trial_ends_at": None, "ends_at": None, "governing_since": now - timedelta(hours=1)},
                {**_trial("r65-cancelled", ORG_A, "proj-r65-4"), "status": "cancelled"},
            ])
            for sub_id in ("r65-expired", "r65-ended", "r65-outranked", "r65-cancelled"):
                before = await _row(env, sub_id)
                response = await _convert(env, sub_id, {"billing_period": "monthly"})
                assert response.status_code == 409, (sub_id, response.status_code, response.text[:200])
                assert await _row(env, sub_id) == before, sub_id
            assert gateway.calls == []
            assert await env.db.subscriptions.count_documents({"billing_status": "active", "converts_trial_id": {"$exists": True}}) == 0

    _run(scenario())


def test_a_priced_plan_converts_only_through_payment_confirmed_checkout(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_trial(TRIAL, ORG_A, PROJ_A1))
            response = await _convert(env, TRIAL, {"billing_period": "monthly"})
            assert response.status_code == 200, response.text[:300]
            body = response.json()
            assert body["status"] == "pending_payment" and body["checkout_url"].startswith("https://pay.example/")
            paid_id = body["subscription_id"]
            paid = await _row(env, paid_id)
            assert (paid["status"], paid["billing_status"], paid["converts_trial_id"]) == ("pending", "pending", TRIAL)
            trial = await _row(env)
            assert trial["status"] == "trial" and trial["billing_status"] == "trial"
            assert trial["conversion"]["state"] == "pending_payment"
            assert await _governing(env) == TRIAL, "the trial governs until payment is confirmed"
            # Payment confirmed by the signed webhook: the paid subscription governs, the trial closes.
            activated = await _webhook(env, "subscription.activated", paid["payment_gateway_subscription_id"],
                                       event_id="evt-r65-1", entity={"customer_id": paid["payment_gateway_customer_id"]},
                                       payment={"id": "pay_r65_1", "amount": 2500000, "currency": "INR"})
            assert activated.status_code == 200 and activated.json().get("changed") is True, activated.text
            assert await _governing(env) == paid_id
            assert (await _row(env, paid_id))["billing_status"] == "active"
            trial = await _row(env)
            assert (trial["status"], trial["cancellation_reason"], trial["conversion"]["state"]) == (
                "cancelled", "converted_to_paid", "paid")
            # Replay of the same signed event changes nothing.
            replay = await _webhook(env, "subscription.activated", paid["payment_gateway_subscription_id"],
                                    event_id="evt-r65-1", entity={"customer_id": paid["payment_gateway_customer_id"]},
                                       payment={"id": "pay_r65_1", "amount": 2500000, "currency": "INR"})
            assert replay.status_code == 200 and replay.json().get("changed") is not True, replay.text
            assert await env.db.subscription_history.count_documents({"subscription_id": TRIAL, "change_type": "trial_convert"}) == 1
            assert await env.db.audit_events.count_documents({"action": "subscription.trial_conversion_paid", "resource_id": TRIAL}) == 1

    _run(scenario())


def test_duplicate_and_concurrent_requests_start_one_checkout(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_trial(TRIAL, ORG_A, PROJ_A1))
            responses = await asyncio.gather(*[
                env.client.post(f"{BASE}/subscriptions/{TRIAL}/convert-trial", json={"billing_period": "monthly"},
                                headers=_headers("superadmin", None, action=STEP))
                for _ in range(6)
            ])
            codes = [r.status_code for r in responses]
            assert set(codes) <= {200, 409} and codes.count(200) >= 1, codes
            ids = {r.json().get("subscription_id") for r in responses if r.status_code == 200}
            assert len(ids) == 1, ids
            assert len([c for c in gateway.calls if c[0] == "subscription"]) == 1
            assert await env.db.subscriptions.count_documents({"converts_trial_id": TRIAL}) == 1
            again = await _convert(env, TRIAL, {"billing_period": "monthly"})
            assert again.status_code == 200 and again.json()["subscription_id"] in ids and again.json()["repeated"] is True
            assert len([c for c in gateway.calls if c[0] == "subscription"]) == 1

    _run(scenario())


def test_a_gateway_failure_changes_nothing_and_can_be_retried(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_trial(TRIAL, ORG_A, PROJ_A1))
            gateway.fail = True
            failed = await _convert(env, TRIAL, {"billing_period": "monthly"})
            assert failed.status_code == 502, failed.text[:200]
            trial = await _row(env)
            assert trial["status"] == "trial" and trial["conversion"]["state"] == "failed"
            assert await env.db.subscriptions.count_documents({"converts_trial_id": TRIAL}) == 0
            assert await env.db.audit_events.count_documents({"action": "subscription.trial_conversion_failed"}) == 1
            gateway.fail = False
            retried = await _convert(env, TRIAL, {"billing_period": "monthly"})
            assert retried.status_code == 200 and retried.json()["status"] == "pending_payment", retried.text[:200]
            assert await env.db.subscriptions.count_documents({"converts_trial_id": TRIAL}) == 1

    _run(scenario())


def test_add_ons_and_periods_are_validated(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_trial(TRIAL, ORG_A, PROJ_A1))
            cases = [
                ({"billing_period": "monthly", "add_on_codes": ["no_such_addon"]}, 400),
                ({"billing_period": "monthly", "add_on_codes": ["addon_api_webhooks"]}, 400),  # not for dms_starter
                ({"billing_period": "monthly", "add_on_codes": ["addon_x", "addon_x"]}, 400),
                ({"billing_period": "fortnightly"}, 400),
                ({"billing_period": "monthly", "status": "active"}, 422),  # unknown key
            ]
            for body, expected in cases:
                response = await _convert(env, TRIAL, body)
                assert response.status_code == expected, (body, response.status_code, response.text[:200])
            # A priced add-on on a priced conversion is refused (checkout would not charge it).
            await env.db.subscriptions.update_one({"_id": TRIAL}, {"$set": {"plan_code": "dms_professional"}})
            response = await _convert(env, TRIAL, {"billing_period": "monthly", "add_on_codes": ["addon_api_webhooks"]})
            assert response.status_code == 409, response.text[:200]
            assert (await _row(env))["status"] == "trial" and gateway.calls == []

    _run(scenario())


def test_free_plans_convert_in_place_without_inventing_a_payment(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.plans.insert_one({"code": "r65_free", "name": "Free", "family": "dms_saas", "tier": 0,
                                           "base_price_minor": 0, "pricing_tiers": {}, "is_active": True,
                                           "trial_config": {"enabled": True}})
            await env.db.subscriptions.insert_one(_trial(TRIAL, ORG_A, PROJ_A1, plan="r65_free"))
            response = await _convert(env, TRIAL, {"billing_period": "monthly"})
            assert response.status_code == 200, response.text[:200]
            row = await _row(env)
            assert (row["status"], row["billing_status"], row["trial"]) == ("active", "not_billed", False)
            assert gateway.calls == [] and await _governing(env) == TRIAL
            assert await env.db.audit_events.count_documents({"action": "subscription.trial_converted_free", "resource_id": TRIAL}) == 1

    _run(scenario())


def test_a_no_charge_conversion_needs_super_admin_and_a_reason(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_trial(TRIAL, ORG_A, PROJ_A1))
            billing = await _user(env.db, "r65_billing", ["contraclaim_billing_admin"], organization_id=ORG_A, projects=[PROJ_A1])
            reason = {"billing_period": "monthly", "no_charge_reason": "Pilot agreed in the framework contract"}
            refused = await _convert(env, TRIAL, reason, "r65_billing", billing)
            assert refused.status_code in {403, 409}, refused.text[:200]
            short = await _convert(env, TRIAL, {"billing_period": "monthly", "no_charge_reason": "ok"})
            assert short.status_code == 400, short.text[:200]
            assert (await _row(env))["status"] == "trial"
            granted = await _convert(env, TRIAL, reason)
            assert granted.status_code == 200, granted.text[:200]
            row = await _row(env)
            assert (row["status"], row["billing_status"]) == ("active", "no_charge_override")
            audit = await env.db.audit_events.find_one({"action": "subscription.trial_converted_no_charge", "resource_id": TRIAL})
            assert audit and audit["metadata"]["reason"] == reason["no_charge_reason"]
            assert audit["metadata"]["list_price_minor"] > 0 and gateway.calls == []
            assert await env.db.subscriptions.count_documents({"billing_status": "active", "_id": TRIAL}) == 0

    _run(scenario())


def test_cross_tenant_and_revoked_permissions_are_refused(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_many([_trial(TRIAL, ORG_A, PROJ_A1), _trial(TRIAL_B, ORG_B, PROJ_B1)])
            billing = await _user(env.db, "r65_billing_a", ["contraclaim_billing_admin"], organization_id=ORG_A, projects=[PROJ_A1])
            ordinary = await _user(env.db, "r65_ordinary", ["contraclaim_user"], organization_id=ORG_A, projects=[PROJ_A1])
            assert (await _convert(env, TRIAL_B, {"billing_period": "monthly"}, "r65_billing_a", billing)).status_code == 403
            assert (await _convert(env, TRIAL, {"billing_period": "monthly"}, "r65_ordinary", ordinary)).status_code == 403
            # Revoked: the billing administrator's role is removed between requests.
            await env.db.users.update_one({"email": "r65_billing_a@example.com"}, {"$set": {"roles": []}})
            assert (await _convert(env, TRIAL, {"billing_period": "monthly"}, "r65_billing_a", billing)).status_code == 403
            for sub_id in (TRIAL, TRIAL_B):
                assert (await _row(env, sub_id))["status"] == "trial"
            assert gateway.calls == []

    _run(scenario())


def test_only_a_running_trial_converts(monkeypatch, gateway) -> None:
    """A paid, active row is not a trial (whatever its flags); a governing trial whose period
    cannot be read is not a running trial either."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_many([
                {**_trial("r65-active", ORG_A, PROJ_A1), "status": "active", "billing_status": "active"},
                _trial("r65-unreadable", ORG_A, "proj-r65-5", trial_ends_at="next tuesday"),
            ])
            assert await _governing(env, "proj-r65-5") == "r65-unreadable", "an invalid period still governs (and denies)"
            for sub_id in ("r65-active", "r65-unreadable"):
                before = await _row(env, sub_id)
                response = await _convert(env, sub_id, {"billing_period": "monthly"})
                assert response.status_code == 409, (sub_id, response.text[:200])
                assert await _row(env, sub_id) == before
            assert gateway.calls == []

    _run(scenario())


def test_a_valid_add_on_named_twice_is_refused(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.addons.insert_one({"code": "r65_free_addon", "name": "Free add-on", "price_minor": 0,
                                             "compatible_plans": ["dms_starter"], "is_active": True})
            await env.db.subscriptions.insert_one(_trial(TRIAL, ORG_A, PROJ_A1))
            response = await _convert(env, TRIAL, {"billing_period": "monthly", "add_on_codes": ["r65_free_addon"] * 2})
            assert response.status_code == 400, response.text[:200]
            assert (await _row(env))["status"] == "trial" and gateway.calls == []

    _run(scenario())


def test_an_in_place_conversion_never_overwrites_a_checkout_claimed_meanwhile(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.monetization_service import MonetizationService, TrialConversionRefused

            await env.db.subscriptions.insert_one(_trial(TRIAL, ORG_A, PROJ_A1))
            decided_on = await _row(env)  # read before the claim lands
            await env.db.subscriptions.update_one({"_id": TRIAL}, {"$set": {"conversion": {"state": "pending_payment"}}})
            with pytest.raises(TrialConversionRefused):
                await MonetizationService(env.db)._convert_trial_in_place(
                    decided_on, "dms_starter", "monthly", [], "no_charge_override", SimpleNamespace(id="r65-actor"),
                    audit_action="subscription.trial_converted_no_charge", audit_metadata={"reason": "raced"})
            assert (await _row(env))["status"] == "trial"

    _run(scenario())
