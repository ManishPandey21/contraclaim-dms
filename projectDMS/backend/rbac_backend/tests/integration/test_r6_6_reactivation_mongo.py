"""R6-6 (owner decision 2026-11-21): reactivation never revives an ended or replaced subscription.

Before: ``POST /subscriptions/{id}/reactivate`` turned any ``cancelled`` row ``active`` with
``billing_status=active`` - whatever its period (an immediately cancelled one stayed ``ended``
while the gateway was asked to bill again), even when a replacement already governed and
billed the scope, re-stamping it so the old plan outranked the new one; concurrent requests
each resumed the gateway subscription.

Now a cancelled subscription resumes only inside the period it paid for, when nothing else
governs its scope and no replacement checkout is pending, once at a time, and - when bound to
the gateway - only when the gateway confirms the resume. An expired subscription is a new
purchase. An older subscription that was never cancelled still governs when the newer one is
cancelled (it never stopped billing); a cancelled one does not resume by itself.

Real HTTP; the gateway's outbound calls are a recording fake. Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_gateway_identity_protection_mongo import _headers
from rbac_backend.tests.integration.test_security_remediation_mongo import _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_B1

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"
SUB = "r66-sub"
STEP = "subscription.upgrade"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeGateway:
    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.fail = False
        self.resume_status = "active"

    async def reactivate_subscription(self, *, gateway_subscription_id: str) -> Any:
        if self.fail:
            raise ConnectionError("gateway unavailable")
        await asyncio.sleep(0.05)
        self.calls.append(("reactivate", gateway_subscription_id))
        return SimpleNamespace(status=self.resume_status)

    async def cancel_subscription(self, *, gateway_subscription_id: str, immediate: bool = False) -> bool:
        self.calls.append(("cancel", gateway_subscription_id, immediate))
        return True


@pytest.fixture()
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    from rbac_backend.routers import rbac_monetization
    from rbac_backend.services import monetization_service

    fake = FakeGateway()
    monkeypatch.setattr(monetization_service, "get_payment_gateway", lambda *a, **k: fake)
    monkeypatch.setattr(rbac_monetization.monetization_mutation_limiter, "max_requests", 10_000)
    return fake


def _sub(sub_id: str, *, status: str = "cancelled", project: str = PROJ_A1, org: str = ORG_A, days_left: Optional[int] = 10,
         bound: bool = True, **extra: Any) -> Dict[str, Any]:
    now = datetime.utcnow()
    row = {"_id": sub_id, "organization_id": org, "project_id": project, "package_id": None, "plan_code": "dms_starter",
           "billing_period": "monthly", "status": status, "billing_status": "inactive" if status == "cancelled" else "active",
           "auto_renew": False, "active_add_ons": [], "governing_since": now - timedelta(days=20),
           "current_period_start": now - timedelta(days=20), "updated_at": now,
           "cancelled_at": now - timedelta(days=1) if status == "cancelled" else None}
    if days_left is not None:
        row["current_period_end"] = now + timedelta(days=days_left)
    if bound:
        row.update({"payment_provider": "noop", "payment_gateway_subscription_id": f"gw_{sub_id}",
                    "payment_gateway_customer_id": "cust_a", "payment_gateway_bound_by": "checkout"})
    row.update(extra)
    return row


async def _reactivate(env: Any, sub_id: str = SUB, persona: str = "superadmin", user_id: Optional[str] = None) -> Any:
    response = await env.client.post(f"{BASE}/subscriptions/{sub_id}/reactivate",
                                     headers=_headers(persona, user_id, action=STEP))
    env.client.cookies.clear()
    return response


async def _row(env: Any, sub_id: str = SUB) -> Dict[str, Any]:
    return await env.db.subscriptions.find_one({"_id": sub_id})


async def _governing(env: Any, project: str = PROJ_A1) -> Optional[str]:
    from rbac_backend.services.entitlement_service import EntitlementService

    resolution = await EntitlementService(env.db).resolve_subscription(organization_id=ORG_A, project_id=project)
    return str((resolution.subscription or {}).get("_id") or "") or None


def _commerce(row: Dict[str, Any]) -> tuple:
    return row["status"], row["billing_status"], row.get("governing_since")


def test_ended_or_unknown_periods_are_a_new_purchase_not_a_revival(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            now = datetime.utcnow()
            await env.db.subscriptions.insert_many([
                _sub("r66-immediate", project="p66-1", ends_at=now - timedelta(minutes=5)),  # cancelled immediately
                _sub("r66-lapsed", project="p66-2", days_left=-3),
                _sub("r66-no-period", project="p66-3", days_left=None),
                _sub("r66-unreadable", project="p66-4", current_period_end="soon"),
            ])
            for sub_id in ("r66-immediate", "r66-lapsed", "r66-no-period", "r66-unreadable"):
                before = await _row(env, sub_id)
                response = await _reactivate(env, sub_id)
                assert response.status_code == 409 and "new purchase" in response.json()["detail"], (sub_id, response.text)
                assert _commerce(await _row(env, sub_id)) == _commerce(before), sub_id
            assert gateway.calls == []

    _run(scenario())


def test_a_replacement_that_governs_or_awaits_payment_blocks_the_old_one(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            now = datetime.utcnow()
            await env.db.subscriptions.insert_many([
                _sub(SUB),
                _sub("r66-replacement", status="active", governing_since=now - timedelta(hours=2), cancelled_at=None),
                _sub("r66-old-2", project="p66-5"),
                _sub("r66-pending", status="pending", project="p66-5", days_left=None, cancelled_at=None),
            ])
            for sub_id, why in ((SUB, "would bill both"), ("r66-old-2", "awaiting payment")):
                before = await _row(env, sub_id)
                response = await _reactivate(env, sub_id)
                assert response.status_code == 409 and why in response.json()["detail"], (sub_id, response.text)
                assert _commerce(await _row(env, sub_id)) == _commerce(before)
            assert gateway.calls == [] and await _governing(env) == "r66-replacement"

    _run(scenario())


def test_within_its_paid_period_it_resumes_once_the_gateway_confirms(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_sub(SUB))
            response = await _reactivate(env)
            assert response.status_code == 200, response.text[:200]
            row = await _row(env)
            assert (row["status"], row["billing_status"], row["auto_renew"]) == ("active", "active", True)
            assert row["reactivation"]["state"] == "completed" and gateway.calls == [("reactivate", f"gw_{SUB}")]
            assert await _governing(env) == SUB
            history = await env.db.subscription_history.find_one({"subscription_id": SUB, "change_type": "reactivation"})
            assert history and history["metadata"]["gateway_confirmed"] is True
            # Unbound (billed outside the gateway): resumes as manual billing, no gateway call.
            await env.db.subscriptions.insert_one(_sub("r66-manual", project="p66-6", bound=False))
            assert (await _reactivate(env, "r66-manual")).status_code == 200
            assert (await _row(env, "r66-manual"))["billing_status"] == "manual" and len(gateway.calls) == 1

    _run(scenario())


def test_gateway_failure_or_an_unconfirmed_resume_changes_nothing(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_sub(SUB))
            before = _commerce(await _row(env))
            gateway.fail = True
            failed = await _reactivate(env)
            assert failed.status_code == 502 and _commerce(await _row(env)) == before
            gateway.fail, gateway.resume_status = False, "cancelled"
            unconfirmed = await _reactivate(env)
            assert unconfirmed.status_code == 409 and "cancelled" in unconfirmed.json()["detail"]
            assert _commerce(await _row(env)) == before
            reviews = await env.db.audit_events.count_documents({"resource_id": SUB, "metadata.requires_admin_review": True})
            assert reviews == 2
            gateway.resume_status = "active"
            assert (await _reactivate(env)).status_code == 200, "a later retry may succeed"

    _run(scenario())


def test_concurrent_reactivations_resume_the_gateway_once(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_sub(SUB))
            responses = await asyncio.gather(*[
                env.client.post(f"{BASE}/subscriptions/{SUB}/reactivate", headers=_headers("superadmin", None, action=STEP))
                for _ in range(6)
            ])
            codes = sorted(r.status_code for r in responses)
            assert codes.count(200) == 1 and set(codes) <= {200, 409}, codes
            assert len(gateway.calls) == 1
            assert await env.db.subscription_history.count_documents({"subscription_id": SUB, "change_type": "reactivation"}) == 1

    _run(scenario())


def test_only_cancelled_subscriptions_and_never_a_converted_trial(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            rows = [_sub(f"r66-{status}", status=status, project=f"p66-{status}", cancelled_at=None)
                    for status in ("archive", "offboarding", "active", "pending", "paused")]
            rows.append(_sub("r66-converted", project="p66-conv", cancellation_reason="converted_to_paid"))
            await env.db.subscriptions.insert_many(rows)
            for row in rows:
                response = await _reactivate(env, row["_id"])
                assert response.status_code == 409, (row["_id"], response.text[:200])
                assert (await _row(env, row["_id"]))["status"] == row["status"]
            assert gateway.calls == []

    _run(scenario())


def test_an_older_subscription_resumes_only_by_the_explicit_rule(monkeypatch, gateway) -> None:
    """Never cancelled: it governs again when the newer one is cancelled (it never stopped
    billing). Cancelled: it stays cancelled until reactivated within its period."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import CancelSubscriptionRequest
            from rbac_backend.services.monetization_service import MonetizationService

            now = datetime.utcnow()
            await env.db.subscriptions.insert_many([
                _sub("r66-older-active", status="active", cancelled_at=None, governing_since=now - timedelta(days=30)),
                _sub("r66-older-cancelled", governing_since=now - timedelta(days=20)),
                _sub("r66-newest", status="active", cancelled_at=None, governing_since=now - timedelta(days=1)),
            ])
            assert await _governing(env) == "r66-newest"
            await MonetizationService(env.db).cancel_subscription("r66-newest", CancelSubscriptionRequest(immediate=True),
                                                                  SimpleNamespace(id="r66-actor"))
            assert await _governing(env) == "r66-older-active"
            assert (await _row(env, "r66-older-cancelled"))["status"] == "cancelled"
            refused = await _reactivate(env, "r66-older-cancelled")
            assert refused.status_code == 409, "the never-cancelled older plan governs the scope"

    _run(scenario())


def test_cross_tenant_and_revoked_permissions_are_refused(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_many([_sub(SUB), _sub("r66-b", org=ORG_B, project=PROJ_B1)])
            billing = await _user(env.db, "r66_billing_a", ["contraclaim_billing_admin"], organization_id=ORG_A, projects=[PROJ_A1])
            ordinary = await _user(env.db, "r66_ordinary", ["contraclaim_user"], organization_id=ORG_A, projects=[PROJ_A1])
            assert (await _reactivate(env, "r66-b", "r66_billing_a", billing)).status_code == 403
            assert (await _reactivate(env, SUB, "r66_ordinary", ordinary)).status_code == 403
            await env.db.users.update_one({"email": "r66_billing_a@example.com"}, {"$set": {"roles": []}})
            assert (await _reactivate(env, SUB, "r66_billing_a", billing)).status_code == 403
            assert (await _row(env))["status"] == "cancelled" and (await _row(env, "r66-b"))["status"] == "cancelled"
            assert gateway.calls == []

    _run(scenario())
