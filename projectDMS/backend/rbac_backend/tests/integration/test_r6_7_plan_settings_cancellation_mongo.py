"""R6-7 (owner decision 2026-11-21): plan-settings cancellation keeps the gateway consistent.

Before: ``PUT /plan-settings/scope`` cancelled the scope's subscriptions with one local
``update_many`` and never told the gateway, so a paid gateway subscription kept billing while
the DMS showed it cancelled; nothing recorded the intent, a retry repeated everything, and a
failure in the middle left no trace.

Now each change is a durable operation: intent recorded under an idempotency key, the scope
locked, each gateway-bound subscription cancelled through the provider API (end of the paid
cycle), the result recorded per subscription, the local cancellation applied only to what the
gateway confirmed (or what has no binding), and the replacement installed only when every old
subscription is resolved. Unfinished operations are listed for Super Admins and can be
retried; confirmed gateway calls are never repeated.

Real HTTP; the gateway's outbound calls are a recording fake; webhooks signed with a test
secret. Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_gateway_identity_protection_mongo import SECRET, _headers, _webhook
from rbac_backend.tests.integration.test_security_remediation_mongo import _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"
STEP = "subscription.entitlement.manage"
BOUND = "r67-bound"
UNBOUND = "r67-unbound"
PLAN_BODY = {"organization_id": ORG_A, "project_id": PROJ_A1, "mode": "plan", "plan_code": "dms_enterprise",
             "billing_period": "monthly"}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class FakeGateway:
    def __init__(self) -> None:
        self.cancels: List[tuple] = []
        self.failing: Set[str] = set()
        self.delay = 0.0
        self.on_cancel: Any = None

    async def cancel_subscription(self, *, gateway_subscription_id: str, immediate: bool = False) -> bool:
        if self.delay:
            await asyncio.sleep(self.delay)
        if gateway_subscription_id in self.failing:
            raise TimeoutError("gateway timed out")
        if self.on_cancel:
            await self.on_cancel(gateway_subscription_id)
        self.cancels.append((gateway_subscription_id, immediate))
        return True


@pytest.fixture()
def gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    from rbac_backend.core.config import settings
    from rbac_backend.routers import rbac_monetization
    from rbac_backend.services import monetization_service

    fake = FakeGateway()
    monkeypatch.setattr(monetization_service, "get_payment_gateway", lambda *a, **k: fake)
    monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", SECRET, raising=False)
    monkeypatch.setattr(rbac_monetization.monetization_mutation_limiter, "max_requests", 10_000)
    return fake


def _row(sub_id: str, *, bound: bool, project: str = PROJ_A1, status: str = "active") -> Dict[str, Any]:
    now = datetime.utcnow()
    row = {"_id": sub_id, "organization_id": ORG_A, "project_id": project, "package_id": None, "plan_code": "dms_starter",
           "billing_period": "monthly", "status": status, "billing_status": "active", "auto_renew": True,
           "governing_since": now - timedelta(days=5), "current_period_end": now + timedelta(days=20), "updated_at": now}
    if bound:
        row.update({"payment_provider": "razorpay", "payment_gateway_subscription_id": f"gw_{sub_id}",
                    "payment_gateway_customer_id": "cust_r67", "payment_gateway_bound_by": "checkout"})
    return row


async def _seed(env: Any) -> None:
    await env.db.subscriptions.insert_many([_row(BOUND, bound=True), _row(UNBOUND, bound=False)])


async def _put(env: Any, body: Dict[str, Any] = PLAN_BODY, key: Optional[str] = "key-1", persona: str = "superadmin") -> Any:
    headers = _headers(persona, None, action=STEP)
    if key:
        headers["Idempotency-Key"] = key
    response = await env.client.put(f"{BASE}/plan-settings/scope", json=body, headers=headers)
    env.client.cookies.clear()
    return response


async def _status(env: Any, sub_id: str) -> str:
    return (await env.db.subscriptions.find_one({"_id": sub_id}))["status"]


async def _replacements(env: Any) -> int:
    return await env.db.subscriptions.count_documents({"created_by_operation": {"$exists": True}, "organization_id": ORG_A})


def test_the_gateway_is_cancelled_before_the_local_record_and_the_operation_is_recorded(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            response = await _put(env)
            assert response.status_code == 200, response.text[:300]
            operation = response.json()["operation"]
            assert operation["status"] == "completed"
            assert gateway.cancels == [(f"gw_{BOUND}", False)], "cancelled at the end of the paid cycle, once"
            assert {item["subscription_id"]: (item["gateway"], item["local"]) for item in operation["items"]} == {
                BOUND: ("confirmed", "cancelled"), UNBOUND: ("not_bound", "cancelled")}
            assert await _status(env, BOUND) == "cancelled" and await _status(env, UNBOUND) == "cancelled"
            new = await env.db.subscriptions.find_one({"_id": operation["result"]["new_subscription_id"]})
            assert (new["plan_code"], new["status"], new["billing_status"]) == ("dms_enterprise", "active", "plan_settings")
            history = await env.db.subscription_history.count_documents(
                {"metadata.operation_id": operation["id"], "change_type": "cancellation"})
            assert history == 2
            # A signed payment event replayed for the cancelled gateway subscription revives nothing.
            replay = await _webhook(env, "subscription.activated", f"gw_{BOUND}", event_id="evt-r67-replay",
                                    entity={"customer_id": "cust_r67"})
            assert replay.status_code == 200 and await _status(env, BOUND) == "cancelled", replay.text[:200]

    _run(scenario())


def test_a_gateway_failure_leaves_the_paid_subscription_and_installs_nothing_until_retried(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            gateway.failing = {f"gw_{BOUND}"}
            failed = await _put(env)
            assert failed.status_code == 502, failed.text[:300]
            operation_id = failed.json()["detail"]["operation_id"]
            assert await _status(env, BOUND) == "active", "never cancelled locally without gateway confirmation"
            assert await _replacements(env) == 0
            open_ops = await env.client.get(f"{BASE}/plan-settings/operations", headers=_headers("superadmin", None, action=STEP))
            env.client.cookies.clear()
            listed = {op["id"]: op for op in open_ops.json()}
            assert operation_id in listed and listed[operation_id]["status"] == "needs_review"
            item = next(i for i in listed[operation_id]["items"] if i["subscription_id"] == BOUND)
            # A timeout may have reached the provider: its outcome is unknown (workstream 4),
            # never "failed" (which would mean nothing changed there).
            assert (item["gateway"], item["last_error"]) == ("outcome_unknown", "TimeoutError")
            assert await env.db.audit_events.count_documents(
                {"action": "subscription.plan_settings_operation_needs_review", "resource_id": operation_id,
                 "metadata.requires_admin_review": True, "metadata.unresolved": 1}) == 1
            # Retried with the same key once the gateway recovers: only the failed call is repeated.
            gateway.failing = set()
            retried = await _put(env)
            assert retried.status_code == 200 and retried.json()["operation"]["id"] == operation_id, retried.text[:200]
            assert gateway.cancels == [(f"gw_{BOUND}", False)]
            assert await _status(env, BOUND) == "cancelled" and await _replacements(env) == 1
            assert (await env.db.subscription_operations.find_one({"_id": operation_id}))["attempts"] == 2

    _run(scenario())


def test_a_completed_operation_is_never_repeated_and_a_key_is_bound_to_its_request(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            assert (await _put(env)).status_code == 200
            again = await _put(env)
            assert again.status_code == 200 and again.json()["operation"]["status"] == "completed"
            assert len(gateway.cancels) == 1 and await _replacements(env) == 1
            other = await _put(env, {**PLAN_BODY, "plan_code": "dms_professional"})
            assert other.status_code == 409, other.text[:200]
            assert await _replacements(env) == 1
            # A subscription bought after the operation completed is untouched by a replay of it.
            await env.db.subscriptions.insert_one(_row("r67-later", bound=True))
            replay = await _put(env)
            assert replay.status_code == 200 and await _status(env, "r67-later") == "active"
            assert len(gateway.cancels) == 1

    _run(scenario())


def test_concurrent_changes_to_one_scope_do_one_thing(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            gateway.delay = 0.2
            same_key = await asyncio.gather(*[_put(env) for _ in range(4)])
            assert sorted(r.status_code for r in same_key).count(200) >= 1
            assert set(r.status_code for r in same_key) <= {200, 409}
            assert len(gateway.cancels) == 1 and await _replacements(env) == 1
            await env.db.subscriptions.update_many({"_id": {"$in": [BOUND, UNBOUND]}}, {"$set": {"status": "active"}})
            different = await asyncio.gather(_put(env, key="key-a"), _put(env, key="key-b"))
            codes = sorted(r.status_code for r in different)
            assert codes == [200, 409], codes
            assert len(gateway.cancels) == 2, "the losing request never reached the gateway"

    _run(scenario())


def test_a_subscription_changed_meanwhile_is_left_alone_and_flagged(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)

            async def payment_failed_meanwhile(gateway_id: str) -> None:
                await env.db.subscriptions.update_one({"_id": BOUND}, {"$set": {"status": "past_due"}})

            gateway.on_cancel = payment_failed_meanwhile
            response = await _put(env)
            assert response.status_code == 502, response.text[:200]
            op = await env.db.subscription_operations.find_one({"_id": response.json()["detail"]["operation_id"]})
            item = next(i for i in op["items"] if i["subscription_id"] == BOUND)
            assert (item["gateway"], item["local"], op["status"]) == ("confirmed", "changed_meanwhile", "needs_review")
            assert await _status(env, BOUND) == "past_due" and await _replacements(env) == 0
            # Review 7 R7-2: a retry re-decides the changed row from its current state - the gateway
            # already cancelled it (confirmed, never repeated), so the local cancellation follows and
            # the operation completes instead of staying listed for ever.
            gateway.on_cancel = None
            retried = await _put(env)
            assert retried.status_code == 200 and gateway.cancels == [(f"gw_{BOUND}", False)], retried.text[:200]
            assert await _status(env, BOUND) == "cancelled" and await _replacements(env) == 1
            assert retried.json()["operation"]["status"] == "completed"

    _run(scenario())


def test_the_review_queue_and_retry_are_super_admin_only_and_resume_an_interrupted_operation(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            gateway.failing = {f"gw_{BOUND}"}
            failed = await _put(env, key=None)  # no key: retried by operation id
            operation_id = failed.json()["detail"]["operation_id"]
            billing = await _user(env.db, "r67_billing", ["contraclaim_billing_admin"], organization_id=ORG_A, projects=[PROJ_A1])
            listed = await env.client.get(f"{BASE}/plan-settings/operations", headers=_headers("r67_billing", billing, action=STEP))
            env.client.cookies.clear()
            assert listed.status_code == 403
            denied = await env.client.post(f"{BASE}/plan-settings/operations/{operation_id}/retry",
                                           headers=_headers("r67_billing", billing, action=STEP))
            env.client.cookies.clear()
            assert denied.status_code == 403
            # Interrupted while running (lease lapsed): still resumable.
            await env.db.subscription_operations.update_one(
                {"_id": operation_id}, {"$set": {"status": "in_progress", "lease_until": datetime.utcnow() - timedelta(minutes=5)}})
            gateway.failing = set()
            resumed = await env.client.post(f"{BASE}/plan-settings/operations/{operation_id}/retry",
                                            headers=_headers("superadmin", None, action=STEP))
            env.client.cookies.clear()
            assert resumed.status_code == 200 and resumed.json()["operation"]["status"] == "completed", resumed.text[:200]
            assert await _status(env, BOUND) == "cancelled" and await _replacements(env) == 1
            missing = await env.client.post(f"{BASE}/plan-settings/operations/op_nope/retry",
                                            headers=_headers("superadmin", None, action=STEP))
            assert missing.status_code == 404

    _run(scenario())


def test_inherit_cancels_the_project_override_at_the_gateway_too(monkeypatch, gateway) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            response = await _put(env, {"organization_id": ORG_A, "project_id": PROJ_A1, "mode": "inherit"}, key="inherit-1")
            assert response.status_code == 200, response.text[:200]
            assert gateway.cancels == [(f"gw_{BOUND}", False)]
            assert await _status(env, BOUND) == "cancelled" and await _replacements(env) == 0

    _run(scenario())


def test_an_operation_that_lost_its_scope_stops_before_its_next_gateway_call(monkeypatch, gateway) -> None:
    """Review 7 R7-1: leases are renewed and fenced; an operation whose lease lapsed and whose
    scope another operation took stops (listed for review) instead of working alongside it."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_many([_row(BOUND, bound=True), _row("r67-bound-2", bound=True)])

            async def scope_taken_over(gateway_id: str) -> None:
                if gateway_id == f"gw_{BOUND}":
                    await env.db.subscription_operation_locks.update_one(
                        {"_id": f"{ORG_A}|{PROJ_A1}"}, {"$set": {"holder": "op_someone_else"}})

            gateway.on_cancel = scope_taken_over
            response = await _put(env)
            assert response.status_code == 409, response.text[:200]
            assert len(gateway.cancels) == 1, "no gateway call after the scope was lost"
            op = await env.db.subscription_operations.find_one({"kind": "plan_settings_replacement"})
            assert op["status"] == "needs_review" and await _replacements(env) == 0

    _run(scenario())
