"""Independent review 7 (2026-11-21): defects introduced by the R6-5/R6-6/R6-8 commits.

- R8-1/R6-1: a trial closed by its paid conversion became reactivatable once an ordinary update
  rewrote ``cancellation_reason``; a plainly cancelled trial came back as active service.
- R5-1: a trial conversion and an ordinary checkout for the same scope could both await payment,
  both activate and both bill.
- R5-2 / R6-2: a crash between a "starting" claim and its outcome blocked the conversion or the
  reactivation for ever (and a checkout already created for the trial was orphaned).
- R5-3: a period the plan does not price was sold at the monthly price.
- R6-3: a replacement activated while the gateway resumed a subscription was not re-checked.

Real HTTP; the gateway is a recording fake; real MongoDB. Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
"""

from __future__ import annotations

import asyncio
import itertools
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_gateway_identity_protection_mongo import _headers
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeGateway:
    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.on_resume: Any = None
        self._ids = itertools.count(1)

    async def create_customer(self, *, organization_id: str, name: str, email: Optional[str] = None) -> Any:
        return SimpleNamespace(gateway_customer_id=f"cust_{organization_id}")

    async def create_subscription(self, *, gateway_customer_id: str, plan_code: str, billing_period: str,
                                  metadata: Optional[Dict[str, Any]] = None) -> Any:
        gateway_id = f"sub_rv7_{next(self._ids)}"
        self.calls.append(("create", gateway_id))
        return SimpleNamespace(gateway_subscription_id=gateway_id, metadata={"short_url": f"https://pay.example/{gateway_id}"})

    async def reactivate_subscription(self, *, gateway_subscription_id: str) -> Any:
        self.calls.append(("resume", gateway_subscription_id))
        if self.on_resume:
            await self.on_resume()
        return SimpleNamespace(status="active")


@pytest.fixture()
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    from rbac_backend.core.config import settings
    from rbac_backend.routers import rbac_monetization
    from rbac_backend.services import monetization_service

    fake = FakeGateway()
    monkeypatch.setattr(monetization_service, "get_payment_gateway", lambda *a, **k: fake)
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "razorpay", raising=False)
    monkeypatch.setattr(rbac_monetization.monetization_mutation_limiter, "max_requests", 10_000)
    return fake


async def _post(env: Any, path: str, body: Dict[str, Any], action: str) -> Any:
    response = await env.client.post(f"{BASE}{path}", json=body, headers=_headers("superadmin", None, action=action))
    env.client.cookies.clear()
    return response


async def _put(env: Any, sub_id: str, body: Dict[str, Any]) -> Any:
    response = await env.client.put(f"{BASE}/subscriptions/{sub_id}", json=body,
                                    headers=_headers("superadmin", None, action="subscription.entitlement.manage"))
    env.client.cookies.clear()
    return response


def _trial(sub_id: str, project: str = PROJ_A1, **extra: Any) -> Dict[str, Any]:
    now = datetime.utcnow()
    return {"_id": sub_id, "organization_id": ORG_A, "project_id": project, "package_id": None, "plan_code": "dms_starter",
            "billing_period": "monthly", "status": "trial", "billing_status": "trial", "trial": True,
            "starts_at": now - timedelta(days=2), "ends_at": now + timedelta(days=12), "trial_ends_at": now + timedelta(days=12),
            "governing_since": now - timedelta(days=2), "active_add_ons": [], **extra}


def _cancelled_paid(sub_id: str, project: str = PROJ_A1, **extra: Any) -> Dict[str, Any]:
    now = datetime.utcnow()
    return {"_id": sub_id, "organization_id": ORG_A, "project_id": project, "package_id": None, "plan_code": "dms_starter",
            "billing_period": "monthly", "status": "cancelled", "billing_status": "inactive", "auto_renew": False,
            "governing_since": now - timedelta(days=20), "current_period_end": now + timedelta(days=10),
            "payment_provider": "razorpay", "payment_gateway_subscription_id": f"gw_{sub_id}",
            "payment_gateway_customer_id": "cust", "payment_gateway_bound_by": "checkout", **extra}


def test_r8_1_a_trial_is_never_reactivated_whatever_its_cancellation_reason_says(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_many([
                {**_trial("rv7-converted"), "status": "cancelled", "cancellation_reason": "converted_to_paid",
                 "conversion": {"state": "paid", "subscription_id": "x"}},
                {**_trial("rv7-user-cancelled", project="rv7-p2"), "status": "cancelled", "cancellation_reason": "too dear"},
            ])
            assert (await _put(env, "rv7-converted", {"cancellation_reason": "customer request"})).status_code == 200
            for sub_id in ("rv7-converted", "rv7-user-cancelled"):
                response = await _post(env, f"/subscriptions/{sub_id}/reactivate", {}, "subscription.upgrade")
                assert response.status_code == 409 and "trial" in response.text, (sub_id, response.text[:200])
                assert (await env.db.subscriptions.find_one({"_id": sub_id}))["status"] == "cancelled"
            assert gateway.calls == []

    _run(scenario())


def test_r5_1_a_scope_awaiting_one_payment_gets_no_second_purchase(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_trial("rv7-trial"))
            converted = await _post(env, "/subscriptions/rv7-trial/convert-trial", {"billing_period": "monthly"},
                                    "subscription.trial.manage")
            assert converted.status_code == 200 and converted.json()["status"] == "pending_payment", converted.text[:200]
            checkout = await _post(env, "/subscriptions/checkout",
                                   {"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_starter"},
                                   "subscription.checkout")
            assert checkout.status_code == 409, checkout.text[:200]
            # The other order: a checkout awaiting payment refuses a conversion.
            await env.db.subscriptions.insert_one(_trial("rv7-trial-2", project="rv7-p3"))
            await env.db.projects.update_one({"_id": "rv7-p3"}, {"$set": {"organization_id": ORG_A, "name": "p3",
                                                                          "is_active": True}}, upsert=True)
            first = await _post(env, "/subscriptions/checkout",
                                {"organization_id": ORG_A, "project_id": "rv7-p3", "plan_code": "dms_starter"},
                                "subscription.checkout")
            assert first.status_code == 200, first.text[:200]
            refused = await _post(env, "/subscriptions/rv7-trial-2/convert-trial", {"billing_period": "monthly"},
                                  "subscription.trial.manage")
            assert refused.status_code == 409, refused.text[:200]
            assert len([c for c in gateway.calls if c[0] == "create"]) == 2

    _run(scenario())


def test_r5_2_a_stale_conversion_claim_is_taken_over_and_an_orphan_checkout_adopted(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            long_ago = datetime.utcnow() - timedelta(hours=1)
            await env.db.subscriptions.insert_many([
                _trial("rv7-fresh", conversion={"state": "starting", "requested_at": datetime.utcnow()}),
                _trial("rv7-stale", project="rv7-p4", conversion={"state": "starting", "requested_at": long_ago}),
                _trial("rv7-orphan", project="rv7-p5", conversion={"state": "starting", "requested_at": long_ago}),
                {"_id": "rv7-orphan-checkout", "organization_id": ORG_A, "project_id": "rv7-p5", "package_id": None,
                 "plan_code": "dms_starter", "status": "pending", "billing_status": "pending",
                 "converts_trial_id": "rv7-orphan", "payment_gateway_subscription_id": "sub_orphan",
                 "payment_gateway_bound_by": "checkout", "payment_provider": "razorpay",
                 "created_at": datetime.utcnow() - timedelta(minutes=20)},  # as checkout records it
            ])
            fresh = await _post(env, "/subscriptions/rv7-fresh/convert-trial", {"billing_period": "monthly"},
                                "subscription.trial.manage")
            assert fresh.status_code == 409, "a fresh claim is still in progress"
            stale = await _post(env, "/subscriptions/rv7-stale/convert-trial", {"billing_period": "monthly"},
                                "subscription.trial.manage")
            assert stale.status_code == 200 and stale.json()["status"] == "pending_payment", stale.text[:200]
            orphan = await _post(env, "/subscriptions/rv7-orphan/convert-trial", {"billing_period": "monthly"},
                                 "subscription.trial.manage")
            assert orphan.status_code == 200 and orphan.json()["subscription_id"] == "rv7-orphan-checkout", orphan.text[:200]
            assert len([c for c in gateway.calls if c[0] == "create"]) == 1, "the orphan checkout is adopted, not duplicated"
            trial = await env.db.subscriptions.find_one({"_id": "rv7-orphan"})
            assert trial["conversion"]["subscription_id"] == "rv7-orphan-checkout"

    _run(scenario())


def test_r5_3_only_priced_periods_are_sold(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_trial("rv7-trial"))
            conversion = await _post(env, "/subscriptions/rv7-trial/convert-trial", {"billing_period": "semi_annual"},
                                     "subscription.trial.manage")
            assert conversion.status_code == 400, conversion.text[:200]
            checkout = await _post(env, "/subscriptions/checkout",
                                   {"organization_id": ORG_A, "project_id": "rv7-p6", "plan_code": "dms_starter",
                                    "billing_period": "semi_annual"}, "subscription.checkout")
            assert checkout.status_code in {400, 403, 404}, checkout.text[:200]
            assert gateway.calls == []

    _run(scenario())


def test_r6_2_a_stale_reactivation_claim_is_taken_over(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_many([
                _cancelled_paid("rv7-fresh-claim", reactivation={"state": "starting", "requested_at": datetime.utcnow()}),
                _cancelled_paid("rv7-stale-claim", project="rv7-p7",
                                reactivation={"state": "starting", "requested_at": datetime.utcnow() - timedelta(hours=1)}),
            ])
            fresh = await _post(env, "/subscriptions/rv7-fresh-claim/reactivate", {}, "subscription.upgrade")
            assert fresh.status_code == 409 and "in progress" in fresh.text
            stale = await _post(env, "/subscriptions/rv7-stale-claim/reactivate", {}, "subscription.upgrade")
            assert stale.status_code == 200, stale.text[:200]
            assert (await env.db.subscriptions.find_one({"_id": "rv7-stale-claim"}))["status"] == "active"

    _run(scenario())


def test_r6_3_a_replacement_activated_during_the_resume_stops_the_reactivation(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_cancelled_paid("rv7-old"))

            async def replacement_activated() -> None:
                await env.db.subscriptions.insert_one({
                    "_id": "rv7-new", "organization_id": ORG_A, "project_id": PROJ_A1, "package_id": None,
                    "plan_code": "dms_professional", "status": "active", "billing_status": "active",
                    "governing_since": datetime.utcnow()})

            gateway.on_resume = replacement_activated
            response = await _post(env, "/subscriptions/rv7-old/reactivate", {}, "subscription.upgrade")
            assert response.status_code == 409 and "listed for review" in response.text, response.text[:200]
            assert (await env.db.subscriptions.find_one({"_id": "rv7-old"}))["status"] == "cancelled"
            assert await env.db.audit_events.count_documents(
                {"action": "subscription.reactivation_needs_reconciliation", "resource_id": "rv7-old"}) == 1

    _run(scenario())
