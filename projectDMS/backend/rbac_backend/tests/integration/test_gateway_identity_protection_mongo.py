"""Gateway identity protection (owner decision 2026-11-20).

The billing webhook finds the subscription a payment event applies to by
``payment_gateway_subscription_id``. An entitlement manager could write that id through
``PUT /subscriptions/{id}`` (and set it through ``POST /subscriptions``), so a tenant user who
learned another subscription's gateway id could redirect its payment events - activation
included - to their own subscription. Now:

- ordinary create and update refuse to set, replace or clear the gateway subscription id,
  customer id or provider, under any spelling (400), for everyone;
- only checkout binds it, from the gateway's response, refusing an id already bound (409),
  and records the binding's provenance;
- the webhook verifies the signature, refuses a signed body replayed under a new event id,
  and changes nothing unless the id names exactly one checkout-bound subscription of this
  provider.

Real HTTP through the production ``app`` (webhooks signed with a test Razorpay secret).
Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime
from typing import Any, Dict, Optional

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_security_remediation_mongo import _user
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_B1

pytestmark = pytest.mark.integration

BASE = "/api/rbac-monetization"
SECRET = "gw-test-webhook-secret"
VICTIM = "gw-victim"  # organisation B's paying subscription
ATTACKER = "gw-attacker"  # organisation A's cancelled subscription
VICTIM_GATEWAY_ID = "sub_rzp_victim_1"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _gateway_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from rbac_backend.core.config import settings
    from rbac_backend.routers import rbac_monetization

    monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", SECRET, raising=False)
    # The limiter is not under test here (as in the L3 and F3 suites).
    monkeypatch.setattr(rbac_monetization.monetization_mutation_limiter, "max_requests", 10_000)


def _headers(persona: str, user_id: Optional[str] = None, action: str = "subscription.entitlement.manage") -> Dict[str, str]:
    import jwt  # type: ignore[import-untyped]

    from rbac_backend.core.config import settings
    from rbac_backend.services.step_up_service import StepUpService

    now = int(time.time())
    token = jwt.encode({"sub": f"{persona}@example.com", "type": "access", "iat": now, "exp": now + 3600},
                       settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return {"Authorization": f"Bearer {token}",
            "X-Step-Up-Token": StepUpService().create_token(user_id=user_id or f"user-{persona}", action=action)}


async def _seed(env: Any) -> None:
    base = {"plan_code": "dms_enterprise", "billing_period": "monthly", "auto_renew": True, "updated_at": datetime.utcnow()}
    await env.db.subscriptions.insert_many([
        {"_id": VICTIM, "organization_id": ORG_B, "project_id": PROJ_B1, "status": "pending", "billing_status": "pending",
         "payment_provider": "razorpay", "payment_gateway_subscription_id": VICTIM_GATEWAY_ID,
         "payment_gateway_customer_id": "cust_victim", "payment_gateway_bound_by": "checkout", **base},
        {"_id": ATTACKER, "organization_id": ORG_A, "project_id": PROJ_A1, "status": "cancelled",
         "billing_status": "inactive", **base},
    ])


async def _webhook(env: Any, event: str, gateway_id: str, *, event_id: str, nonce: Any = None,
                   secret: str = SECRET, entity: Optional[Dict[str, Any]] = None, payment: Optional[Dict[str, Any]] = None,
                   path: str = "razorpay", signature_header: str = "x-razorpay-signature",
                   event_header: str = "x-razorpay-event-id") -> Any:
    # A real provider body is unique per event (its own id and timestamp, ``created_at`` in
    # seconds); ``nonce`` stands for that, and a replay reuses it.
    payload: Dict[str, Any] = {"subscription": {"entity": {"id": gateway_id, "status": "active", **(entity or {})}}}
    if payment is not None:
        payload["payment"] = {"entity": payment}
    body = {"event": event, "created_at": nonce if nonce is not None else event_id, "payload": payload}
    raw = json.dumps(body, sort_keys=True).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    response = await env.client.post(f"/api/billing/webhooks/{path}", content=raw,
                                     headers={signature_header: signature, event_header: event_id,
                                              "Content-Type": "application/json"})
    return response


ATTEMPTS = [
    {"payment_gateway_subscription_id": VICTIM_GATEWAY_ID},
    {"paymentGatewaySubscriptionId": VICTIM_GATEWAY_ID},
    {"gateway_subscription_id": VICTIM_GATEWAY_ID},
    {"Payment-Gateway-Subscription-ID": VICTIM_GATEWAY_ID},
    {"payment_gateway_customer_id": "cust_victim"},
    {"payment_provider": "razorpay"},
    {"provider": "razorpay"},
]


def test_an_authorised_tenant_user_cannot_redirect_another_subscriptions_payment_events(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            billing = await _user(env.db, "gw_billing_a", ["contraclaim_billing_admin"], organization_id=ORG_A, projects=[PROJ_A1])
            for persona, user_id in (("gw_billing_a", billing), ("superadmin", None)):
                for body in ATTEMPTS:
                    response = await env.client.put(f"{BASE}/subscriptions/{ATTACKER}", json={**body, "auto_renew": False},
                                                    headers=_headers(persona, user_id))
                    env.client.cookies.clear()
                    assert response.status_code == 400, (persona, body, response.status_code)
            attacker = await env.db.subscriptions.find_one({"_id": ATTACKER})
            assert "payment_gateway_subscription_id" not in attacker and attacker["auto_renew"] is True
            # Create cannot bind it either (a second binding the webhook could match).
            for body in ATTEMPTS:
                response = await env.client.post(
                    f"{BASE}/subscriptions", headers=_headers("gw_billing_a", billing),
                    json={"organization_id": ORG_A, "project_id": PROJ_A1, "plan_code": "dms_enterprise", **body},
                )
                env.client.cookies.clear()
                assert response.status_code == 400, (body, response.status_code, response.text[:200])
            assert await env.db.subscriptions.count_documents({"payment_gateway_subscription_id": VICTIM_GATEWAY_ID}) == 1
            # Replacing or clearing an existing binding is refused too (Super Admin included).
            for body in ({"payment_gateway_subscription_id": None}, {"payment_gateway_subscription_id": ""},
                         {"payment_gateway_subscription_id": "sub_rzp_other"}, {"gatewaySubscriptionId": None},
                         {"payment_gateway_customer_id": None}, {"payment_provider": "stripe"}):
                response = await env.client.put(f"{BASE}/subscriptions/{VICTIM}", json=body, headers=_headers("superadmin"))
                env.client.cookies.clear()
                assert response.status_code == 400, (body, response.status_code)
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["payment_gateway_subscription_id"] == VICTIM_GATEWAY_ID
            # The victim's activation still lands on the victim, and only there.
            result = await _webhook(env, "subscription.activated", VICTIM_GATEWAY_ID, event_id="evt-victim-1")
            assert result.status_code == 200 and result.json()["subscription_id"] == VICTIM, result.text
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["status"] == "active"
            assert (await env.db.subscriptions.find_one({"_id": ATTACKER}))["status"] == "cancelled"

    _run(scenario())


def test_an_ordinary_update_that_leaves_the_binding_alone_still_works(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            response = await env.client.put(f"{BASE}/subscriptions/{VICTIM}", headers=_headers("superadmin"),
                                            json={"auto_renew": False, "payment_gateway_subscription_id": VICTIM_GATEWAY_ID})
            assert response.status_code == 200, response.text[:200]
            victim = await env.db.subscriptions.find_one({"_id": VICTIM})
            assert victim["auto_renew"] is False and victim["payment_gateway_subscription_id"] == VICTIM_GATEWAY_ID

    _run(scenario())


def test_webhook_refuses_ambiguous_foreign_provider_and_non_checkout_bindings(monkeypatch) -> None:
    """Bindings written before 2026-11-20 (or by hand) are not trusted blindly: two
    subscriptions with one id, a different provider, or a binding recorded as not from
    checkout change nothing, and the refusal is audited."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            # A legacy duplicate binding, as an ordinary update could write before the fix.
            await env.db.subscriptions.update_one({"_id": ATTACKER}, {"$set": {"payment_gateway_subscription_id": VICTIM_GATEWAY_ID}})
            response = await _webhook(env, "subscription.activated", VICTIM_GATEWAY_ID, event_id="evt-ambiguous")
            assert response.status_code == 200 and response.json().get("refused") == "ambiguous_gateway_binding", response.text
            assert (await env.db.subscriptions.find_one({"_id": ATTACKER}))["status"] == "cancelled"
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["status"] == "pending"
            assert await env.db.audit_events.count_documents({"action": "billing.webhook.binding_refused",
                                                              "reason": "ambiguous_gateway_binding"}) == 2
            await env.db.subscriptions.update_one({"_id": ATTACKER}, {"$unset": {"payment_gateway_subscription_id": ""}})
            # Another provider's binding.
            await env.db.subscriptions.update_one({"_id": VICTIM}, {"$set": {"payment_provider": "stripe"}})
            response = await _webhook(env, "subscription.activated", VICTIM_GATEWAY_ID, event_id="evt-provider")
            assert response.json().get("refused") == "gateway_provider_mismatch"
            # A binding recorded as made outside checkout.
            await env.db.subscriptions.update_one({"_id": VICTIM}, {"$set": {"payment_provider": "razorpay",
                                                                             "payment_gateway_bound_by": "manual"}})
            response = await _webhook(env, "subscription.activated", VICTIM_GATEWAY_ID, event_id="evt-manual")
            assert response.json().get("refused") == "gateway_binding_not_from_checkout"
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["status"] == "pending"
            # Billing records are untouched by refusals.
            assert await env.db.billing_records.count_documents({"gateway_subscription_id": VICTIM_GATEWAY_ID}) == 0

    _run(scenario())


def test_webhook_signature_and_replay_are_checked_before_any_change(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            forged = await _webhook(env, "subscription.activated", VICTIM_GATEWAY_ID, event_id="evt-forged", secret="wrong")
            assert forged.status_code == 400
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["status"] == "pending"
            first = await _webhook(env, "subscription.activated", VICTIM_GATEWAY_ID, event_id="evt-1")
            assert first.json()["status"] == "processed"
            cancelled = await _webhook(env, "subscription.cancelled", VICTIM_GATEWAY_ID, event_id="evt-2")
            assert cancelled.json()["status"] == "processed"
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["status"] == "cancelled"
            # The same signed activation body replayed under the same, then a new, event id.
            for event_id in ("evt-1", "evt-1-replayed-under-a-new-id"):
                replay = await _webhook(env, "subscription.activated", VICTIM_GATEWAY_ID, event_id=event_id, nonce="evt-1")
                assert replay.json()["status"] == "duplicate", (event_id, replay.text)
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["status"] == "cancelled"
            records = await env.db.billing_records.count_documents({"subscription_id": VICTIM})
            assert records == 2, "one billing record per processed event, none for replays"

    _run(scenario())


def test_checkout_binds_once_and_refuses_an_id_already_bound(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import monetization_service as module
            from rbac_backend.services.monetization_service import GatewayIdentityConflict, MonetizationService
            from rbac_backend.services.payment_gateway import GatewaySubscription, NoOpPaymentGateway

            await _seed(env)
            actor = type("Actor", (), {"id": "gw-actor"})()
            service = MonetizationService(env.db)
            # Two checkouts for the same plan (two projects): two distinct bindings.
            first = await service.start_checkout(organization_id=ORG_A, project_id=PROJ_A1, plan_code="dms_enterprise",
                                                 billing_period="monthly", current_user=actor)
            second = await service.start_checkout(organization_id=ORG_A, project_id=None, plan_code="dms_enterprise",
                                                  billing_period="monthly", current_user=actor)
            assert first["gateway_subscription_id"] != second["gateway_subscription_id"]
            bound = await env.db.subscriptions.find_one({"payment_gateway_subscription_id": first["gateway_subscription_id"]})
            assert bound["payment_gateway_bound_by"] == "checkout" and bound["payment_gateway_bound_at"]

            # A gateway that returns an id already bound (the victim's): refused, nothing inserted.
            class _Replaying(NoOpPaymentGateway):
                async def create_subscription(self, **kwargs: Any) -> GatewaySubscription:
                    sub = await super().create_subscription(**kwargs)
                    sub.gateway_subscription_id = VICTIM_GATEWAY_ID
                    return sub

            monkeypatch.setattr(module, "get_payment_gateway", lambda *a, **k: _Replaying())
            before = await env.db.subscriptions.count_documents({})
            with pytest.raises(GatewayIdentityConflict):
                await service.start_checkout(organization_id=ORG_A, project_id=PROJ_A1, plan_code="dms_enterprise",
                                             billing_period="monthly", current_user=actor)
            assert await env.db.subscriptions.count_documents({}) == before

    _run(scenario())


def test_concurrent_checkouts_of_one_gateway_id_bind_once_with_the_index(monkeypatch) -> None:
    """With the migration's unique index, two checkouts racing on one gateway id bind once."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.migrations.v20261124_0001_subscription_gateway_identity_unique import upgrade
            from rbac_backend.services import monetization_service as module
            from rbac_backend.services.monetization_service import GatewayIdentityConflict, MonetizationService
            from rbac_backend.services.payment_gateway import GatewaySubscription, NoOpPaymentGateway

            await _seed(env)
            result = await upgrade(env.db, dry_run=False)
            assert result.warnings == [], result.warnings

            class _Fixed(NoOpPaymentGateway):
                async def create_subscription(self, **kwargs: Any) -> GatewaySubscription:
                    sub = await super().create_subscription(**kwargs)
                    sub.gateway_subscription_id = "sub_rzp_race"
                    await asyncio.sleep(0)  # let the other checkout pass its pre-check
                    return sub

            monkeypatch.setattr(module, "get_payment_gateway", lambda *a, **k: _Fixed())
            actor = type("Actor", (), {"id": "gw-actor"})()

            async def checkout() -> str:
                try:
                    await MonetizationService(env.db).start_checkout(
                        organization_id=ORG_A, project_id=PROJ_A1, plan_code="dms_enterprise",
                        billing_period="monthly", current_user=actor)
                    return "bound"
                except GatewayIdentityConflict:
                    return "refused"

            outcomes = sorted(await asyncio.gather(checkout(), checkout()))
            assert outcomes == ["bound", "refused"], outcomes
            assert await env.db.subscriptions.count_documents({"payment_gateway_subscription_id": "sub_rzp_race"}) == 1

    _run(scenario())


def test_the_migration_reports_duplicate_bindings_and_creates_no_index_then(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.migrations.v20261124_0001_subscription_gateway_identity_unique import upgrade

            await _seed(env)
            await env.db.subscriptions.update_one({"_id": ATTACKER}, {"$set": {"payment_gateway_subscription_id": VICTIM_GATEWAY_ID}})
            dry = await upgrade(env.db, dry_run=True)
            assert dry.warnings and "subscriptions: 1 gateway subscription id" in dry.warnings[0]
            # Applying with duplicates refuses (the runner then records nothing; it stays pending).
            with pytest.raises(RuntimeError):
                await upgrade(env.db, dry_run=False)
            names = {index["name"] for index in await env.db.subscriptions.list_indexes().to_list(length=None)}
            assert "subscription_gateway_identity_unique" not in names
            # Through the runner: the apply raises and nothing is recorded, so it stays pending.
            from rbac_backend.migrations.catalog import MIGRATIONS
            from rbac_backend.migrations.runner import LEDGER_COLLECTION, MigrationRunner

            gateway_migration = [m for m in MIGRATIONS if m.version == "20261124_0001"]
            with pytest.raises(RuntimeError):
                await MigrationRunner(env.db, gateway_migration).run(apply=True)
            assert await env.db[LEDGER_COLLECTION].count_documents({"_id": "20261124_0001"}) == 0
            # Nothing was changed or rebound.
            assert await env.db.subscriptions.count_documents({"payment_gateway_subscription_id": VICTIM_GATEWAY_ID}) == 2
            # Once a person resolves the duplicate, the migration applies and the index exists.
            await env.db.subscriptions.update_one({"_id": ATTACKER}, {"$unset": {"payment_gateway_subscription_id": ""}})
            assert (await upgrade(env.db, dry_run=False)).warnings == []
            names = {index["name"] for index in await env.db.subscriptions.list_indexes().to_list(length=None)}
            assert "subscription_gateway_identity_unique" in names

    _run(scenario())


def test_service_level_writers_refuse_the_binding_whatever_the_caller(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.models.rbac_monetization import SubscriptionCreate, SubscriptionUpdate
            from rbac_backend.services.monetization_service import GatewayIdentityChangeRefused, MonetizationService

            await _seed(env)
            actor = type("Actor", (), {"id": "gw-internal"})()
            service = MonetizationService(env.db)
            for update in (SubscriptionUpdate(payment_gateway_subscription_id=VICTIM_GATEWAY_ID),
                           SubscriptionUpdate(payment_gateway_subscription_id=None)):
                target = ATTACKER if update.payment_gateway_subscription_id else VICTIM
                with pytest.raises(GatewayIdentityChangeRefused):
                    await service.update_subscription(target, update, actor)
            with pytest.raises(GatewayIdentityChangeRefused):
                await service.create_subscription(
                    SubscriptionCreate(organization_id=ORG_A, project_id=PROJ_A1, plan_code="dms_enterprise",
                                       payment_gateway_subscription_id=VICTIM_GATEWAY_ID), actor)
            assert await env.db.subscriptions.count_documents({"payment_gateway_subscription_id": VICTIM_GATEWAY_ID}) == 1

    _run(scenario())



# --------------------------------------------------------------------------- #
# Independent review of da322ae: F1, F2, F4, F5, F6
# --------------------------------------------------------------------------- #


def test_f1_a_case_changed_provider_path_cannot_replay_a_signed_body(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            assert (await _webhook(env, "subscription.charged", VICTIM_GATEWAY_ID, event_id="e1", nonce=100)).json()["status"] == "processed"
            assert (await _webhook(env, "subscription.cancelled", VICTIM_GATEWAY_ID, event_id="e2", nonce=200)).json()["status"] == "processed"
            for path in ("RazorPay", "RAZORPAY", " razorpay"):
                replay = await _webhook(env, "subscription.charged", VICTIM_GATEWAY_ID, event_id=f"e1-{path}", nonce=100, path=path,
                                        signature_header="x-webhook-signature", event_header="x-webhook-event-id")
                assert replay.status_code in (200, 400), (path, replay.status_code)
                assert replay.status_code == 400 or replay.json()["status"] == "duplicate", (path, replay.text)
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["status"] == "cancelled"
            assert (await _webhook(env, "subscription.charged", VICTIM_GATEWAY_ID, event_id="e9", path="paypal")).status_code == 404
            assert await env.db.billing_records.count_documents({"subscription_id": VICTIM}) == 2

    _run(scenario())


def test_f2_stale_and_reactivating_events_and_repeated_payments_change_nothing(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            assert (await _webhook(env, "subscription.activated", VICTIM_GATEWAY_ID, event_id="o1", nonce=100)).json()["status"] == "processed"
            assert (await _webhook(env, "subscription.halted", VICTIM_GATEWAY_ID, event_id="o2", nonce=300)).json()["status"] == "processed"
            # A delayed activation older than the last event applied: refused.
            late = await _webhook(env, "subscription.charged", VICTIM_GATEWAY_ID, event_id="o3", nonce=200)
            assert late.json().get("refused") == "stale_event", late.text
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["billing_status"] == "past_due"
            # Cancelled, then a fresh payment event: never a reactivation by webhook.
            await _webhook(env, "subscription.cancelled", VICTIM_GATEWAY_ID, event_id="o4", nonce=400)
            fresh = await _webhook(env, "subscription.activated", VICTIM_GATEWAY_ID, event_id="o5", nonce=500)
            assert fresh.json().get("refused") == "status_not_activatable_by_webhook", fresh.text
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["status"] == "cancelled"
            # A legacy event row with no body hash: the replayed old body is stale anyway.
            await env.db.billing_webhook_events.update_many({}, {"$unset": {"body_sha256": ""}})
            legacy = await _webhook(env, "subscription.activated", VICTIM_GATEWAY_ID, event_id="o1-again", nonce=100)
            assert legacy.json().get("refused") in {"stale_event", "status_not_activatable_by_webhook"}, legacy.text
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["status"] == "cancelled"

    _run(scenario())


def test_f2_one_payment_one_effect(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            payment = {"id": "pay_1", "subscription_id": VICTIM_GATEWAY_ID, "amount": 0, "currency": "INR"}
            first = await _webhook(env, "payment.captured", VICTIM_GATEWAY_ID, event_id="p1", nonce=100, payment=payment)
            assert first.json()["status"] == "processed"
            again = await _webhook(env, "payment.captured", VICTIM_GATEWAY_ID, event_id="p1-other", nonce=101, payment=payment)
            assert again.json().get("duplicate_payment") is True, again.text
            assert await env.db.billing_records.count_documents({"gateway_payment_id": "pay_1"}) == 1

    _run(scenario())


def test_f4_customer_and_checkout_scope_must_match_the_subscription(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            wrong_customer = await _webhook(env, "subscription.activated", VICTIM_GATEWAY_ID, event_id="c1",
                                            entity={"customer_id": "cust_attacker"})
            assert wrong_customer.json().get("refused") == "gateway_customer_mismatch"
            wrong_scope = await _webhook(env, "subscription.activated", VICTIM_GATEWAY_ID, event_id="c2",
                                         entity={"customer_id": "cust_victim", "notes": {"organization_id": ORG_A}})
            assert wrong_scope.json().get("refused") == "gateway_notes_scope_mismatch"
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["status"] == "pending"
            # F6: the refused payload is kept for the admin who resolves it.
            row = await env.db.billing_webhook_events.find_one({"event_id": "c2"})
            assert row["refused"] == "gateway_notes_scope_mismatch" and row["refused_payload"]["event"] == "subscription.activated"
            ok = await _webhook(env, "subscription.activated", VICTIM_GATEWAY_ID, event_id="c3",
                                entity={"customer_id": "cust_victim", "notes": {"organization_id": ORG_B, "project_id": PROJ_B1}})
            assert ok.json()["status"] == "processed" and ok.json()["subscription_id"] == VICTIM

    _run(scenario())


def test_f5_unknown_keys_are_refused_never_dropped(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            for body in ({"razorpay_subscription_id": VICTIM_GATEWAY_ID}, {"rzp_sub_id": "x"}, {"subscription_gw_id": "x"},
                         {"payment_customer_id": "x"}, {"nonsense": 1}):
                put = await env.client.put(f"{BASE}/subscriptions/{VICTIM}", json={**body, "auto_renew": False},
                                           headers=_headers("superadmin"))
                env.client.cookies.clear()
                assert put.status_code == 400, (body, put.status_code)
                post = await env.client.post(f"{BASE}/subscriptions", headers=_headers("superadmin"),
                                             json={"organization_id": ORG_A, "plan_code": "dms_enterprise", **body})
                env.client.cookies.clear()
                assert post.status_code == 400, (body, post.status_code)
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["auto_renew"] is True

    _run(scenario())



# --------------------------------------------------------------------------- #
# Re-review of 7cabfdf: R1-R4
# --------------------------------------------------------------------------- #


def test_r1_a_refused_payment_is_still_recorded_for_review(monkeypatch) -> None:
    """Activated and charged arrive out of order (normal for Razorpay): the late charge does
    not change status, but the payment is in the financial trail, flagged for review."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            assert (await _webhook(env, "subscription.activated", VICTIM_GATEWAY_ID, event_id="r1a", nonce=501)).json()["status"] == "processed"
            payment = {"id": "pay_r1", "subscription_id": VICTIM_GATEWAY_ID, "amount": 0, "currency": "INR"}
            late = await _webhook(env, "subscription.charged", VICTIM_GATEWAY_ID, event_id="r1b", nonce=500, payment=payment)
            assert late.json().get("refused") == "stale_event", late.text
            records = await env.db.billing_records.find({"gateway_payment_id": "pay_r1"}).to_list(length=None)
            assert len(records) == 1 and records[0]["record_status"] == "refused_stale_event" and records[0]["requires_admin_review"]
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["status"] == "active"
            # The same payment again: one record only.
            again = await _webhook(env, "subscription.charged", VICTIM_GATEWAY_ID, event_id="r1c", nonce=502, payment=payment)
            assert again.json().get("duplicate_payment") is True
            assert await env.db.billing_records.count_documents({"gateway_payment_id": "pay_r1"}) == 1

    _run(scenario())


@pytest.mark.parametrize("status_value,activatable", [
    ("pending", True), ("trial", True), ("past_due", True), ("active", True),
    ("cancelled", False), ("paused", False), ("offboarding", False), ("archive", False), ("pilot", False), ("mystery", False),
])
def test_r2_a_payment_activates_only_from_activatable_statuses(monkeypatch, status_value: str, activatable: bool) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            await env.db.subscriptions.update_one({"_id": VICTIM}, {"$set": {"status": status_value}})
            payment = {"id": f"pay_{status_value}", "subscription_id": VICTIM_GATEWAY_ID, "amount": 0, "currency": "INR"}
            response = await _webhook(env, "subscription.charged", VICTIM_GATEWAY_ID, event_id=f"r2-{status_value}", nonce=100, payment=payment)
            after = (await env.db.subscriptions.find_one({"_id": VICTIM}))["status"]
            if activatable:
                assert response.json().get("changed") is True and after == "active", response.text
            else:
                assert response.json().get("refused") == "status_not_activatable_by_webhook" and after == status_value
            # Either way the payment is recorded.
            assert await env.db.billing_records.count_documents({"gateway_payment_id": f"pay_{status_value}"}) == 1

    _run(scenario())


def test_r3_an_old_body_cannot_be_replayed_before_the_first_new_event(monkeypatch) -> None:
    """No high-water mark yet (no event since deploy): the last processed event of the gateway
    id is the baseline, so a pre-deploy signed body replayed under a new id is stale."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            await env.db.subscriptions.update_one({"_id": VICTIM}, {"$set": {"status": "cancelled"}})
            # A processed pre-deploy event (no body hash, no high-water mark on the subscription).
            await env.db.billing_webhook_events.insert_one({"event_id": "legacy-1", "provider": "razorpay", "processed": True,
                                                            "gateway_subscription_id": VICTIM_GATEWAY_ID,
                                                            "received_at": datetime.utcnow()})
            replay = await _webhook(env, "subscription.halted", VICTIM_GATEWAY_ID, event_id="legacy-1-again", nonce=1000)
            assert replay.json().get("refused") == "stale_event", replay.text
            assert (await env.db.subscriptions.find_one({"_id": VICTIM}))["status"] == "cancelled"

    _run(scenario())


def test_r4_the_refused_payload_is_redacted(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            payment = {"id": "pay_pii", "subscription_id": VICTIM_GATEWAY_ID, "amount": 0, "currency": "INR",
                       "email": "payer@example.com", "contact": "+910000000000", "vpa": "payer@bank",
                       "card": {"last4": "4242", "network": "Visa"}}
            await env.db.subscriptions.update_one({"_id": VICTIM}, {"$set": {"status": "paused"}})
            await _webhook(env, "subscription.charged", VICTIM_GATEWAY_ID, event_id="pii", nonce=100, payment=payment)
            row = await env.db.billing_webhook_events.find_one({"event_id": "pii"})
            stored = json.dumps(row["refused_payload"], default=str)
            for secret in ("payer@example.com", "+910000000000", "payer@bank", "4242", "Visa"):
                assert secret not in stored, secret
            assert row["refused_payload"]["entities"]["payment"]["id"] == "pay_pii"

    _run(scenario())
