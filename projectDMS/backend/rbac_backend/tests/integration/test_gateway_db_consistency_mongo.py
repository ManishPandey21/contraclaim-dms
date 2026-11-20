"""Gateway/database consistency for lifecycle changes (owner decision 2026-11-21, item 6).

Upgrade, downgrade, billing-period change and cancel change the gateway first and the
database second; no transaction spans the two. Before, a database write refused after the
gateway had changed (a concurrent change, R6-3 residual) left the two systems disagreeing with
no record; a gateway failure was a 500; a retried request applied the change twice.

Now each change is a durable operation recorded before the gateway call: a gateway failure is
``failed`` (nothing changed anywhere; 502; retryable); a refused database write after a
gateway change is ``needs_review`` - listed for Super Admins with the exact subscription,
state before and target, and audited - never reported as done; with an ``Idempotency-Key`` a
repeat of a completed change does not reach the gateway again, and a retry after review does
not repeat the confirmed gateway call. No cross-system atomicity is claimed; nothing is
refunded or cancelled elsewhere.

Real HTTP; the gateway's outbound calls are a recording fake. Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_gateway_identity_protection_mongo import _headers
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"
SUB = "gdc-sub"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeGateway:
    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.fail: Any = None  # an exception class to raise
        self.during: Any = None

    async def update_subscription(self, *, gateway_subscription_id: str, new_plan_code: Optional[str] = None,
                                  new_billing_period: Optional[str] = None) -> Any:
        if self.fail:
            raise self.fail("gateway unavailable")
        self.calls.append(("update", gateway_subscription_id, new_plan_code, new_billing_period))
        if self.during:
            await self.during()

    async def cancel_subscription(self, *, gateway_subscription_id: str, immediate: bool = False) -> bool:
        if self.fail:
            raise self.fail("gateway unavailable")
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


async def _seed(env: Any) -> None:
    now = datetime.utcnow()
    await env.db.subscriptions.insert_one({
        "_id": SUB, "organization_id": ORG_A, "project_id": PROJ_A1, "package_id": None, "plan_code": "dms_starter",
        "billing_period": "monthly", "status": "active", "billing_status": "active", "auto_renew": True,
        "active_add_ons": [], "governing_since": now - timedelta(days=3), "current_period_end": now + timedelta(days=25),
        "payment_provider": "noop", "payment_gateway_subscription_id": "gw_gdc", "payment_gateway_customer_id": "cust",
        "payment_gateway_bound_by": "checkout", "updated_at": now})


async def _post(env: Any, path: str, body: Dict[str, Any], action: str, key: Optional[str] = None) -> Any:
    headers = _headers("superadmin", None, action=action)
    if key:
        headers["Idempotency-Key"] = key
    response = await env.client.post(f"{BASE}/subscriptions/{SUB}/{path}", json=body, headers=headers)
    env.client.cookies.clear()
    return response


async def _open(env: Any) -> List[Dict[str, Any]]:
    response = await env.client.get(f"{BASE}/plan-settings/operations", headers=_headers("superadmin", None, action="x"))
    env.client.cookies.clear()
    return response.json()


async def _row(env: Any) -> Dict[str, Any]:
    return await env.db.subscriptions.find_one({"_id": SUB})


UPGRADE = {"new_plan_code": "dms_professional", "effective_immediately": True}


def test_a_refused_local_write_after_a_gateway_change_is_listed_for_review(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)

            async def concurrent_period_change() -> None:
                await env.db.subscriptions.update_one({"_id": SUB}, {"$set": {"billing_period": "annual"}})

            gateway.during = concurrent_period_change
            response = await _post(env, "upgrade", UPGRADE, "subscription.upgrade", key="up-1")
            assert response.status_code == 409, response.text[:200]
            assert (await _row(env))["plan_code"] == "dms_starter", "the database was not changed"
            open_ops = [op for op in await _open(env) if op["kind"] == "upgrade"]
            assert len(open_ops) == 1 and open_ops[0]["status"] == "needs_review"
            item = open_ops[0]["items"][0]
            assert (item["subscription_id"], item["gateway"], item["local"]) == (SUB, "confirmed", "refused")
            stored = await env.db.subscription_operations.find_one({"_id": open_ops[0]["id"]})
            assert stored["items"][0]["before"]["plan_code"] == "dms_starter"
            assert stored["items"][0]["target"] == {"plan_code": "dms_professional", "billing_period": "monthly"}
            assert await env.db.audit_events.count_documents(
                {"action": "subscription.upgrade_needs_reconciliation", "resource_id": SUB,
                 "metadata.requires_admin_review": True}) == 1
            # The same request retried once the race is over: the confirmed gateway call is not repeated.
            gateway.during = None
            await env.db.subscriptions.update_one({"_id": SUB}, {"$set": {"billing_period": "monthly"}})
            retried = await _post(env, "upgrade", UPGRADE, "subscription.upgrade", key="up-1")
            assert retried.status_code == 200, retried.text[:200]
            assert (await _row(env))["plan_code"] == "dms_professional"
            assert [c for c in gateway.calls if c[0] == "update"] == [("update", "gw_gdc", "dms_professional", "monthly")]
            assert not [op for op in await _open(env) if op["kind"] == "upgrade"]

    _run(scenario())


def test_a_refused_gateway_connection_changes_nothing_and_is_not_a_review_item(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            gateway.fail = ConnectionRefusedError  # provably never reached the gateway
            for path, body, action in (("upgrade", UPGRADE, "subscription.upgrade"),
                                       ("cancel", {"immediate": False}, "subscription.cancel"),
                                       ("change-period", {"new_billing_period": "annual"}, "subscription.entitlement.manage")):
                response = await _post(env, path, body, action)
                assert response.status_code == 502, (path, response.status_code, response.text[:200])
            row = await _row(env)
            assert (row["plan_code"], row["status"], row["billing_period"]) == ("dms_starter", "active", "monthly")
            assert await _open(env) == []
            assert await env.db.subscription_operations.count_documents({"status": "failed"}) == 3

    _run(scenario())


def test_a_repeated_request_with_the_same_key_is_applied_once(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            first = await _post(env, "upgrade", UPGRADE, "subscription.upgrade", key="up-2")
            assert first.status_code == 200, first.text[:200]
            again = await _post(env, "upgrade", UPGRADE, "subscription.upgrade", key="up-2")
            assert again.status_code in {200, 409}, again.text[:200]
            assert len([c for c in gateway.calls if c[0] == "update"]) == 1
            assert await env.db.subscription_history.count_documents({"subscription_id": SUB, "change_type": "upgrade"}) == 1
            other = await _post(env, "change-period", {"new_billing_period": "annual"}, "subscription.entitlement.manage",
                                key="up-2")
            assert other.status_code == 200, "keys are per operation kind and subscription"
            reused = await _post(env, "change-period", {"new_billing_period": "quarterly"}, "subscription.entitlement.manage",
                                 key="up-2")
            assert reused.status_code in {409, 400}, reused.text[:200]

    _run(scenario())


def test_cancel_reaches_the_gateway_once_and_records_a_completed_operation(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            responses = await asyncio.gather(*[
                env.client.post(f"{BASE}/subscriptions/{SUB}/cancel", json={"immediate": False},
                                headers={**_headers("superadmin", None, action="subscription.cancel"), "Idempotency-Key": "c-1"})
                for _ in range(4)
            ])
            assert set(r.status_code for r in responses) <= {200, 409}, [r.status_code for r in responses]
            assert [c for c in gateway.calls if c[0] == "cancel"] == [("cancel", "gw_gdc", False)]
            assert (await _row(env))["status"] == "cancelled"
            op = await env.db.subscription_operations.find_one({"kind": "cancellation"})
            assert op["status"] == "completed" and op["items"][0]["local"] == "applied"

    _run(scenario())


def test_an_operation_completed_while_a_repeat_was_starting_is_not_applied_again(monkeypatch, gateway) -> None:
    """A repeat that passed the replay check just before the first request completed reaches
    the operation itself: it must answer with the completed operation, not run again."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from types import SimpleNamespace

            from rbac_backend.services.monetization_service import MonetizationService

            await _seed(env)
            assert (await _post(env, "upgrade", UPGRADE, "subscription.upgrade", key="up-race")).status_code == 200
            service = MonetizationService(env.db)
            sub = await service.get_subscription_by_id(SUB)
            ran: List[str] = []

            async def gateway_call() -> None:
                ran.append("gateway")

            async def local_call() -> Dict[str, Any]:
                ran.append("local")
                return {}

            result = await service._gateway_then_local(
                kind="upgrade", sub=sub, target={"plan_code": "dms_professional", "billing_period": "monthly"},
                gateway_call=gateway_call, local_call=local_call, current_user=SimpleNamespace(id="gdc"),
                idempotency_key="up-race", asked={"plan_code": "dms_professional", "billing_period": None})
            assert ran == [] and result["operation"]["status"] == "completed"

    _run(scenario())


def test_an_unknown_gateway_outcome_is_listed_for_review_and_retried(monkeypatch, gateway) -> None:
    """Review 7 G-3: a timeout may have been applied at the gateway - never reported as
    "nothing changed"; listed, audited, and the same-key retry calls the gateway again."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            gateway.fail = TimeoutError
            response = await _post(env, "upgrade", UPGRADE, "subscription.upgrade", key="up-unknown")
            assert response.status_code == 502 and "unknown" in response.text, response.text[:200]
            assert (await _row(env))["plan_code"] == "dms_starter"
            listed = [op for op in await _open(env) if op["kind"] == "upgrade"]
            assert len(listed) == 1 and listed[0]["items"][0]["gateway"] == "unknown"
            assert await env.db.audit_events.count_documents(
                {"action": "subscription.upgrade_gateway_outcome_unknown", "metadata.requires_admin_review": True}) == 1
            gateway.fail = None
            retried = await _post(env, "upgrade", UPGRADE, "subscription.upgrade", key="up-unknown")
            assert retried.status_code == 200 and (await _row(env))["plan_code"] == "dms_professional"
            assert len([c for c in gateway.calls if c[0] == "update"]) == 1

    _run(scenario())


def test_a_lifecycle_change_and_a_plan_settings_change_never_share_a_scope(monkeypatch, gateway) -> None:
    """Review 7 G-1: while a plan-settings operation holds the scope, a lifecycle change waits (409)."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import subscription_operations as ops

            await _seed(env)
            await ops.lock_scope(env.db, ORG_A, PROJ_A1, "op_plan_settings_running")
            response = await _post(env, "upgrade", UPGRADE, "subscription.upgrade", key="up-locked")
            assert response.status_code == 409, response.text[:200]
            assert (await _row(env))["plan_code"] == "dms_starter" and gateway.calls == []

    _run(scenario())


def test_a_lifecycle_operation_is_not_retried_through_the_plan_settings_route(monkeypatch, gateway) -> None:
    """Review 7 R7-3: 409 with guidance, not a 500."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)

            async def concurrent_period_change() -> None:
                await env.db.subscriptions.update_one({"_id": SUB}, {"$set": {"billing_period": "annual"}})

            gateway.during = concurrent_period_change
            await _post(env, "upgrade", UPGRADE, "subscription.upgrade", key="up-r73")
            op_id = [op for op in await _open(env) if op["kind"] == "upgrade"][0]["id"]
            response = await env.client.post(f"{BASE}/plan-settings/operations/{op_id}/retry",
                                             headers=_headers("superadmin", None, action="subscription.entitlement.manage"))
            assert response.status_code == 409 and "Idempotency-Key" in response.text, response.text[:200]

    _run(scenario())


def test_keys_of_different_operation_kinds_never_collide(monkeypatch, gateway) -> None:
    """Review 7 R7-4: an operation recorded under a crafted key (here: what a plan-settings key
    "upgrade:<sub>:k1" hashed to before the kind was part of the hash) does not take the
    lifecycle operation's identity."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            import hashlib

            await _seed(env)
            kindless = "op_" + hashlib.sha256(f"{ORG_A}|{PROJ_A1}|upgrade:{SUB}:k1".encode()).hexdigest()[:40]
            await env.db.subscription_operations.insert_one({
                "_id": kindless, "kind": "plan_settings_replacement", "fingerprint": "someone-elses-request",
                "organization_id": ORG_A, "project_id": PROJ_A1, "status": "completed", "items": [], "request": {}})
            upgraded = await _post(env, "upgrade", UPGRADE, "subscription.upgrade", key="k1")
            assert upgraded.status_code == 200, upgraded.text[:200]
            assert (await _row(env))["plan_code"] == "dms_professional"

    _run(scenario())
