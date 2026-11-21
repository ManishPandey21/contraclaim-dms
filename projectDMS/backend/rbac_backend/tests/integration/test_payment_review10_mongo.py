"""Independent review 10 of the eleventh pass (6913a01..21ac55c): each finding reproduced at
da10baf (outputs kept with the session's evidence), then fixed and pinned here.

Real HTTP and real MongoDB; the provider is a fake implementing the adapter interface. No
real provider is called. Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pytest

from rbac_backend.services.payment_gateway_reliability import GatewayOutcomeUnknown, GatewayRejected
from rbac_backend.tests.integration.test_checkout_replacement_mongo import (
    REPLACE,
    SOURCE,
    ScheduledProvider,
    _cancels,
    _creates,
    _oid,
    _seed,
    _source,
)
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_gateway_identity_protection_mongo import _webhook
from rbac_backend.tests.integration.test_p05_payment_integrity_mongo import _legacy, _sub
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


# --- R10-1 / R10-15: a replacement retried with the same key never creates again -----------------


def test_r10_1_a_same_key_retry_after_an_unknown_create_never_creates_again(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            provider.create_failure = GatewayOutcomeUnknown("timeout", cause_type="ReadTimeout")  # applied there
            first = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-same")
            assert first.status_code == 502, first.text[:200]
            provider.create_failure = None
            again = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-same")
            assert again.status_code == 200, again.text[:200]
            assert len(_creates(provider)) == 1, "the earlier create is found by reference, not repeated"
            (op,) = await _ops(env, "replacement")
            assert op["status"] == "awaiting_payment"
            assert await env.db.subscriptions.count_documents({"replaces_subscription_id": SOURCE}) == 1

    _run(scenario())


def test_r10_1_an_unanswered_create_not_at_the_provider_stays_blocked_on_retry(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            provider.create_applies = False
            provider.create_failure = GatewayOutcomeUnknown("timeout", cause_type="ReadTimeout")
            assert (await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-b")).status_code == 502
            provider.create_failure = None
            for key in ("k-b", "k-b", "k-other"):
                again = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key=key)
                assert again.status_code == 409, (key, again.text[:200])
            assert len(_creates(provider)) == 1

    _run(scenario())


def test_r10_15_an_abandoned_replacement_is_not_restarted_under_its_key(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            assert (await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-ab")).status_code == 200
            await env.db.subscription_operations.update_many({"kind": "replacement"}, {"$set": {"status": "abandoned"}})
            again = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-ab")
            assert again.status_code == 409 and "abandoned" in again.text, again.text[:200]
            assert len(_creates(provider)) == 1

    _run(scenario())


# --- R10-2: a purchase interrupted mid-create ------------------------------------------------------


def test_r10_2_an_interrupted_checkout_is_reconciled_before_any_new_create(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            past = datetime.utcnow() - timedelta(minutes=5)
            await env.db.subscription_operations.insert_one({
                "_id": "op_crashed", "kind": "checkout", "organization_id": ORG_A, "project_id": PROJ_A1,
                "status": "in_progress", "lease_until": past, "created_at": past, "updated_at": past,
                "items": [], "request": {"plan_code": "dms_starter", "billing_period": "monthly"}, "token": "t"})
            provider.subscriptions["sub_crashed"] = {"status": "created", "customer_id": "c", "plan_id": "dms_starter",
                                                     "notes": {"checkout_reference": "op_crashed"}}
            again = await _call(env, "/subscriptions/checkout", CHECKOUT, "subscription.checkout")
            assert again.status_code == 409 and "awaiting payment" in again.text, again.text[:200]
            assert _creates(provider) == []
            recorded = await env.db.subscriptions.find_one({"checkout_reference": "op_crashed"})
            assert recorded is not None and recorded["payment_gateway_subscription_id"] == "sub_crashed"
            # Interrupted, and not at the provider: blocked until a Super Admin resolves it.
            await env.db.subscriptions.delete_many({"checkout_reference": "op_crashed"})
            await env.db.subscription_operations.insert_one({
                "_id": "op_crashed_2", "kind": "checkout", "organization_id": ORG_A, "project_id": "rv10-p2",
                "status": "in_progress", "lease_until": past, "created_at": past, "updated_at": past,
                "items": [], "request": {"plan_code": "dms_starter", "billing_period": "monthly"}, "token": "t2"})
            await env.db.projects.update_one({"_id": "rv10-p2"}, {"$set": {"organization_id": ORG_A, "name": "p2",
                                                                           "is_active": True}}, upsert=True)
            blocked = await _call(env, "/subscriptions/checkout", {**CHECKOUT, "project_id": "rv10-p2"}, "subscription.checkout")
            assert blocked.status_code == 409 and "unknown outcome" in blocked.text, blocked.text[:200]
            listed = await _call(env, "/plan-settings/operations", None, "x", method="GET")
            assert "op_crashed_2" in {row["id"] for row in listed.json()}
            assert _creates(provider) == []

    _run(scenario())


# --- R10-3: the source is cancelled on the target's authorisation, before it renews ----------------


def test_r10_3_authorisation_of_the_scheduled_target_cancels_the_source_before_its_renewal(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            body = (await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-auth")).json()
            target = await env.db.subscriptions.find_one({"_id": _oid(body["subscription_id"])})
            authorised = await _webhook(env, "subscription.authenticated", target["payment_gateway_subscription_id"],
                                        event_id="evt-r10-auth", entity={"customer_id": target["payment_gateway_customer_id"],
                                                                         "status": "authenticated"})
            assert authorised.status_code == 200 and authorised.json().get("replacement") == "completed", authorised.text[:200]
            assert _cancels(provider) == [("cancel", f"gw_{SOURCE}", False)]
            target = await env.db.subscriptions.find_one({"_id": target["_id"]})
            assert target["status"] == "pending", "authorisation is not payment: the target is not activated"
            source = await _source(env)
            assert source["status"] == "active" and abs((source["ends_at"] - provider.cycle_end).total_seconds()) < 1

    _run(scenario())


def test_r10_3_a_source_that_renewed_before_confirmation_is_reviewed_not_cancelled(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)
            body = (await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-ren")).json()
            target = await env.db.subscriptions.find_one({"_id": _oid(body["subscription_id"])})
            provider.cycle_end = provider.cycle_end + timedelta(days=30)  # the source renewed meanwhile
            await _webhook(env, "subscription.authenticated", target["payment_gateway_subscription_id"],
                           event_id="evt-r10-ren", entity={"customer_id": target["payment_gateway_customer_id"]})
            assert _cancels(provider) == []
            (op,) = await _ops(env, "replacement")
            assert op["status"] == "needs_review"
            assert await env.db.audit_events.count_documents(
                {"action": "subscription.replacement_needs_review", "reason": "source_renewed_before_confirmation"}) == 1

    _run(scenario())


def test_r10_12_authorisation_alone_never_activates_or_flags_an_ordinary_checkout(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.core.config import settings

            monkeypatch.setattr(settings, "PAYMENT_MISMATCH_POLICY", "enforce", raising=False)
            monkeypatch.setattr(settings, "PAYMENT_MISMATCH_GRACE_DAYS", 7, raising=False)
            await env.db.subscriptions.insert_one({**_bound("rv10-ord"), "status": "pending", "billing_status": "pending"})
            response = await _webhook(env, "subscription.authenticated", "gw_rv10-ord", event_id="evt-r10-ord",
                                      entity={"customer_id": "cust"})
            assert response.status_code == 200 and response.json().get("authenticated") is True
            row = await env.db.subscriptions.find_one({"_id": "rv10-ord"})
            assert (row["status"], row["billing_status"]) == ("pending", "pending")
            assert await env.db.payment_discrepancies.count_documents({}) == 0

    _run(scenario())


# --- R10-5: a cancel refused because the provider already ended it ---------------------------------


def test_r10_5_a_cancel_refused_because_the_provider_already_ended_it_is_confirmed(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.subscriptions.insert_one(_bound("rv10-pc"))
            provider.subscriptions["gw_rv10-pc"] = {"status": "cancelled", "notes": {}}
            response = await _call(env, "/subscriptions/rv10-pc/cancel", {"immediate": False}, "subscription.cancel", key="r10-k1")
            assert response.status_code == 200, response.text[:200]
            row = await env.db.subscriptions.find_one({"_id": "rv10-pc"})
            assert row["status"] == "cancelled" and row["gateway_cancellation"]["state"] == "cancelled"
            # A refusal while the provider still shows it billable stays a refusal (nothing changed).
            await env.db.subscriptions.insert_one(_bound("rv10-pc2", project_id="rv10-pc2-p"))
            provider.subscriptions["gw_rv10-pc2"] = {"status": "active", "notes": {}}
            provider.cancel_applies = False
            provider.cancel_failures = [GatewayRejected("final cycle", cause_type="BadRequestError")]
            refused = await _call(env, "/subscriptions/rv10-pc2/cancel", {"immediate": False}, "subscription.cancel", key="r10-k2")
            assert refused.status_code == 502
            assert (await env.db.subscriptions.find_one({"_id": "rv10-pc2"}))["status"] == "active"

    _run(scenario())


# --- R10-7: created at the provider, not recorded ----------------------------------------------------


def test_r10_7_a_replacement_created_but_not_recorded_is_recorded_by_retry(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider)

            async def lease_taken(**kwargs: Any) -> None:
                await env.db.subscription_operation_locks.update_one({"_id": f"{ORG_A}|{PROJ_A1}"},
                                                                     {"$set": {"holder": "someone-else"}})

            original = provider.create_subscription

            async def create_then_lose_lease(**kwargs: Any) -> Any:
                result = await original(**kwargs)
                await lease_taken()
                return result

            provider.create_subscription = create_then_lose_lease  # type: ignore[method-assign]
            lost = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-lost")
            assert lost.status_code == 409, lost.text[:200]
            provider.create_subscription = original  # type: ignore[method-assign]
            await env.db.subscription_operation_locks.delete_many({})
            (op,) = await _ops(env, "replacement")
            assert op["status"] == "needs_review" and op["items"][0]["local"] == "not_recorded_lease_lost"
            # Recorded from the provider's own answer, even where the provider cannot be searched.
            provider.supports_reference_search = False
            retried = await _call(env, f"/plan-settings/operations/{op['_id']}/retry", None, STEP)
            assert retried.status_code == 200 and retried.json()["reconciled"] is True, retried.text[:200]
            assert await env.db.subscriptions.count_documents({"replacement_operation_id": op["_id"]}) == 1
            assert len(_creates(provider)) == 1

    _run(scenario())


# --- R10-8: replaced sources are reconciled too -------------------------------------------------------


def test_r10_8_a_replaced_source_still_billable_after_its_end_is_listed(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            now = datetime.utcnow()
            await env.db.subscriptions.insert_one({**_bound("rv10-src"), "replaced_by_subscription_id": "x",
                                                   "ends_at": now - timedelta(days=3),
                                                   "gateway_cancellation": {"mode": "cycle_end", "request_confirmed": True,
                                                                            "effective_at": now - timedelta(days=3)}})
            provider.subscriptions["gw_rv10-src"] = {"status": "active", "notes": {}}
            result = (await _call(env, "/plan-settings/reconciliation/cancellations", None, "x", method="GET")).json()
            assert {row["subscription_id"]: row["interpretation"] for row in result["requires_review"]}.get(
                "rv10-src") == "still_billable_after_effective_date"

    _run(scenario())


# --- R10-4: P-05 hides no charging customer -------------------------------------------------------------


def test_r10_4_refused_charges_after_cancellation_and_unrelated_confirmations_are_not_hidden(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            db = env.db
            now = datetime.utcnow()
            base = _legacy(db.name)
            await db.subscriptions.insert_many([
                _sub("rv10-c-1", "x1", gid="gw-rv10-1", status="cancelled", cancelled_at=now - timedelta(days=40),
                     gateway_cancellation={"mode": "cycle_end", "request_confirmed": True,
                                           "effective_at": now - timedelta(days=35)}),
                _sub("rv10-c-2", "x2", gid="gw-rv10-2", status="cancelled", cancelled_at=now - timedelta(days=10),
                     cancellation_reason="customer_request"),
            ])
            await db.billing_records.insert_one({
                "subscription_id": "rv10-c-1", "gateway_subscription_id": "gw-rv10-1", "provider": "razorpay",
                "gateway_payment_id": "pay-rv10-1", "event_type": "payment.succeeded",
                "record_status": "refused_status_not_activatable_by_webhook", "requires_review": True,
                "created_at": now - timedelta(days=5)})
            await db.subscription_operations.insert_one({
                "_id": "op-rv10-react", "kind": "reactivation", "status": "completed",
                "items": [{"subscription_id": "rv10-c-2", "gateway": "confirmed"}]})
            after = _legacy(db.name)
            delta = {k: after["cancelled_bound_by_class"][k] - base["cancelled_bound_by_class"][k]
                     for k in after["cancelled_bound_by_class"]}
            assert delta["charged_after_cancellation"] == 1 and delta["other_without_gateway_evidence"] == 1, delta
            assert delta["confirmed_at_gateway"] == 0
            assert after["cancellations_needing_a_provider_check"] - base["cancellations_needing_a_provider_check"] == 2

    _run(scenario())


# --- R10-13: replacement needs verified provenance -----------------------------------------------------


def test_r10_13_a_legacy_binding_without_provenance_is_not_replaced(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env, provider, payment_gateway_bound_by=None)
            refused = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-legacy")
            assert refused.status_code == 409 and "configured payment gateway" in refused.text, refused.text[:200]
            await env.db.subscriptions.update_one({"_id": SOURCE}, {"$set": {"payment_gateway_reconciled_at": datetime.utcnow()}})
            accepted = await _call(env, "/subscriptions/checkout", REPLACE, "subscription.checkout", key="k-legacy-2")
            assert accepted.status_code == 200, accepted.text[:200]

    _run(scenario())


# --- R10-9 / R10-10: discrepancy settlement ----------------------------------------------------------------


def test_r10_9_r10_10_settlement_label_and_a_discrepancy_racing_a_settlement(monkeypatch, provider) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.core.config import settings
            from rbac_backend.services import payment_discrepancy_service as pd

            monkeypatch.setattr(settings, "PAYMENT_MISMATCH_POLICY", "enforce", raising=False)
            monkeypatch.setattr(settings, "PAYMENT_MISMATCH_GRACE_DAYS", 7, raising=False)
            await env.db.subscriptions.insert_one(_bound("rv10-d"))
            now = datetime.utcnow()
            stale = await env.db.subscriptions.find_one({"_id": "rv10-d"})

            async def record(payment: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
                return await pd.record(env.db, subscription=snapshot, provider="razorpay", kind="renewal", reason="x",
                                       event_id=f"evt-{payment}", gateway_payment_id=payment, amount_minor=1,
                                       currency="INR", expected={"amount_minor": 2, "currency": "INR"}, now=now)

            first = await record("pay-d-1", stale)
            snapshot_with_open_review = await env.db.subscriptions.find_one({"_id": "rv10-d"})
            await env.db.subscriptions.update_one({"_id": "rv10-d"}, {"$set": {"status": "past_due"}})
            settled = await pd.resolve(env.db, first["discrepancy_id"], outcome="settled", reason="r", actor="sa")
            assert settled["review_closed"] is True
            row = await env.db.subscriptions.find_one({"_id": "rv10-d"})
            assert row["billing_status"] == "past_due", "R10-9: the label follows the subscription as it is now"
            # R10-10: a discrepancy decided on a snapshot taken while the review was open.
            await record("pay-d-2", snapshot_with_open_review)
            row = await env.db.subscriptions.find_one({"_id": "rv10-d"})
            assert row["financial_review"]["state"] == "open", "reopened, never missed"

    _run(scenario())


# --- R10-11: the shadow evaluator reads projects once ---------------------------------------------------------


def test_r10_11_the_shadow_reads_the_users_projects_in_one_query(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import role_scope_shadow
            from rbac_backend.tests.integration.test_security_remediation_mongo import _user

            projects = [f"rv10-sp-{n}" for n in range(40)]
            await env.db.projects.insert_many([{"_id": p, "organization_id": ORG_A, "name": p, "is_active": True}
                                               for p in projects])
            user_id = await _user(env.db, "rv10_shadow", ["projectadmin"], organization_id=ORG_A, projects=projects)
            calls: List[str] = []

            class Counting:
                def __init__(self, db: Any) -> None:
                    self._db = db

                def __getattr__(self, name: str) -> Any:
                    collection = getattr(self._db, name)
                    if name != "projects":
                        return collection

                    class Wrapped:
                        def find_one(self, *a: Any, **k: Any) -> Any:
                            calls.append("find_one")
                            return collection.find_one(*a, **k)

                        def find(self, *a: Any, **k: Any) -> Any:
                            calls.append("find")
                            return collection.find(*a, **k)

                    return Wrapped()

                def __getitem__(self, name: str) -> Any:
                    return self._db[name]

            user = await env.db.users.find_one({"username": "rv10_shadow"})
            allowed, _held = await role_scope_shadow.proposed_decision(
                Counting(env.db), user, "dms.claim.view", organization_id=ORG_A, project_id=projects[-1])
            assert allowed is True and user_id
            assert calls.count("find_one") <= 1 and calls.count("find") <= 1, calls

    _run(scenario())
