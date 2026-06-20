"""Billing adapter + webhook tests (Week 3.1/3.2/3.3).

Covers the parts that are deterministic without the Razorpay SDK or live keys:
signature verification, event normalization, the factory, and the idempotent
webhook reconciliation (subscription state transitions + billing records).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from rbac_backend.services.billing_webhook_service import BillingWebhookService
from rbac_backend.services.payment_gateway import (
    NoOpPaymentGateway,
    RazorpayGateway,
    get_payment_gateway,
)

WEBHOOK_SECRET = "whsec_test_123"


def _razorpay() -> RazorpayGateway:
    return RazorpayGateway(key_id="rzp_key", key_secret="rzp_secret", webhook_secret=WEBHOOK_SECRET)


def _sign(raw: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _event(event="subscription.charged", sub_id="sub_rzp_1", pay_id="pay_1", amount=50000):
    return {
        "event": event,
        "payload": {
            "subscription": {"entity": {"id": sub_id, "customer_id": "cust_1", "status": "active"}},
            "payment": {"entity": {"id": pay_id, "amount": amount, "currency": "INR", "status": "captured"}},
        },
    }


# --- Fake Mongo -----------------------------------------------------------


class _FakeColl:
    def __init__(self, unique_key=None):
        self.docs = []
        self.unique_key = unique_key

    async def insert_one(self, doc):
        if self.unique_key and any(d.get(self.unique_key) == doc.get(self.unique_key) for d in self.docs):
            raise DuplicateKeyError("duplicate key")
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=f"id-{len(self.docs)}")

    async def find_one(self, query):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None

    async def update_one(self, query, update):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                d.update(update.get("$set", {}))
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


class _FakeDB:
    def __init__(self):
        self.subscriptions = _FakeColl()
        self.billing_webhook_events = _FakeColl(unique_key="event_id")
        self.billing_records = _FakeColl()
        self.subscription_history = _FakeColl()
        self.plans = _FakeColl()


def _seed_subscription(db, *, sub_id="sub-1", gateway_sub_id="sub_rzp_1", status="pending"):
    db.subscriptions.docs.append(
        {
            "_id": sub_id,
            "organization_id": "org-A",
            "project_id": "proj-A",
            "plan_code": "dms_pro",
            "billing_period": "monthly",
            "status": status,
            "billing_status": "pending",
            "payment_gateway_subscription_id": gateway_sub_id,
        }
    )


def _seed_plan(db, *, code="dms_pro", monthly=50000, currency="INR"):
    db.plans.docs.append(
        {"code": code, "currency": currency, "base_price_minor": monthly,
         "pricing_tiers": {"monthly": monthly, "quarterly": monthly * 3, "annual": monthly * 12}},
    )


# --- W3.1: signature verification + normalization + factory ---------------


def test_razorpay_signature_valid_and_invalid():
    gw = _razorpay()
    raw = json.dumps(_event()).encode()
    assert gw.verify_webhook_signature(payload=raw, signature=_sign(raw)) is True
    assert gw.verify_webhook_signature(payload=raw, signature="deadbeef") is False
    assert gw.verify_webhook_signature(payload=raw, signature=None) is False


def test_razorpay_signature_denies_when_no_secret():
    gw = RazorpayGateway(key_id="k", key_secret="s", webhook_secret="")
    raw = b"{}"
    assert gw.verify_webhook_signature(payload=raw, signature="anything") is False


def test_razorpay_parse_normalizes_event():
    gw = _razorpay()
    ev = gw.parse_webhook_event(payload=_event("subscription.charged"), event_id="evt_1")
    assert ev.event_type == "payment.succeeded"
    assert ev.gateway_subscription_id == "sub_rzp_1"
    assert ev.gateway_payment_id == "pay_1"
    assert ev.amount_minor == 50000
    assert ev.event_id == "evt_1"

    cancelled = gw.parse_webhook_event(payload=_event("subscription.cancelled"), event_id="evt_2")
    assert cancelled.event_type == "subscription.cancelled"

    unknown = gw.parse_webhook_event(payload=_event("contact.created"), event_id="evt_3")
    assert unknown.event_type == "ignored"


def test_noop_gateway_denies_webhooks_by_default():
    gw = NoOpPaymentGateway()
    assert gw.verify_webhook_signature(payload=b"{}", signature="x") is False
    assert gw.parse_webhook_event(payload={"event": "x"}) is None


def test_factory_resolves_providers():
    assert isinstance(get_payment_gateway("noop"), NoOpPaymentGateway)
    assert isinstance(get_payment_gateway("razorpay"), RazorpayGateway)
    with pytest.raises(ValueError):
        get_payment_gateway("paypal")


# --- W3.2/W3.3: idempotent webhook reconciliation -------------------------


@pytest.mark.asyncio
async def test_webhook_payment_success_activates_subscription():
    db = _FakeDB()
    _seed_subscription(db, status="pending")
    svc = BillingWebhookService(db=db, gateway=_razorpay(), provider="razorpay")

    raw = json.dumps(_event("subscription.charged")).encode()
    result = await svc.process(raw_body=raw, signature=_sign(raw), event_id_header="evt_100")

    assert result["status"] == "processed"
    assert result["applied"] == {"status": "active", "billing_status": "active"}
    sub = await db.subscriptions.find_one({"_id": "sub-1"})
    assert sub["status"] == "active"
    assert len(db.billing_records.docs) == 1
    assert db.billing_records.docs[0]["record_status"] == "paid"


@pytest.mark.asyncio
async def test_webhook_is_idempotent_on_replay():
    db = _FakeDB()
    _seed_subscription(db, status="pending")
    svc = BillingWebhookService(db=db, gateway=_razorpay(), provider="razorpay")
    raw = json.dumps(_event("subscription.charged")).encode()

    first = await svc.process(raw_body=raw, signature=_sign(raw), event_id_header="evt_dup")
    second = await svc.process(raw_body=raw, signature=_sign(raw), event_id_header="evt_dup")

    assert first["status"] == "processed"
    assert second["status"] == "duplicate"
    # No double-processing: exactly one billing record.
    assert len(db.billing_records.docs) == 1


@pytest.mark.asyncio
async def test_webhook_payment_failed_marks_past_due():
    db = _FakeDB()
    _seed_subscription(db, status="active")
    svc = BillingWebhookService(db=db, gateway=_razorpay(), provider="razorpay")
    raw = json.dumps(_event("payment.failed")).encode()

    await svc.process(raw_body=raw, signature=_sign(raw), event_id_header="evt_fail")
    sub = await db.subscriptions.find_one({"_id": "sub-1"})
    assert sub["billing_status"] == "past_due"


@pytest.mark.asyncio
async def test_webhook_cancellation_cancels_subscription():
    db = _FakeDB()
    _seed_subscription(db, status="active")
    svc = BillingWebhookService(db=db, gateway=_razorpay(), provider="razorpay")
    raw = json.dumps(_event("subscription.cancelled")).encode()

    await svc.process(raw_body=raw, signature=_sign(raw), event_id_header="evt_cancel")
    sub = await db.subscriptions.find_one({"_id": "sub-1"})
    assert sub["status"] == "cancelled"
    assert sub["billing_status"] == "inactive"


@pytest.mark.asyncio
async def test_webhook_bad_signature_is_rejected():
    db = _FakeDB()
    svc = BillingWebhookService(db=db, gateway=_razorpay(), provider="razorpay")
    raw = json.dumps(_event()).encode()
    with pytest.raises(HTTPException) as exc:
        await svc.process(raw_body=raw, signature="not-a-valid-signature", event_id_header="evt_x")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_webhook_unknown_subscription_is_recorded_but_unmatched():
    db = _FakeDB()  # no subscription seeded
    svc = BillingWebhookService(db=db, gateway=_razorpay(), provider="razorpay")
    raw = json.dumps(_event("subscription.charged", sub_id="sub_unknown")).encode()
    result = await svc.process(raw_body=raw, signature=_sign(raw), event_id_header="evt_unknown")
    assert result["status"] == "processed"
    assert result["matched"] is False


# --- Phase 4: webhook financial validation (money-safety) -----------------


@pytest.mark.asyncio
async def test_webhook_matching_amount_activates():
    db = _FakeDB()
    _seed_subscription(db, status="pending")
    _seed_plan(db, monthly=50000)  # matches the _event amount
    svc = BillingWebhookService(db=db, gateway=_razorpay(), provider="razorpay")
    raw = json.dumps(_event("subscription.charged", amount=50000)).encode()
    result = await svc.process(raw_body=raw, signature=_sign(raw), event_id_header="evt_ok")
    assert result["applied"] == {"status": "active", "billing_status": "active"}
    sub = await db.subscriptions.find_one({"_id": "sub-1"})
    assert sub["status"] == "active"


@pytest.mark.asyncio
async def test_webhook_amount_mismatch_does_not_activate():
    db = _FakeDB()
    _seed_subscription(db, status="pending")
    _seed_plan(db, monthly=50000)  # plan price 50000…
    svc = BillingWebhookService(db=db, gateway=_razorpay(), provider="razorpay")
    raw = json.dumps(_event("subscription.charged", amount=4999)).encode()  # …but only 4999 captured
    result = await svc.process(raw_body=raw, signature=_sign(raw), event_id_header="evt_short")

    assert result["validation"] == "amount_mismatch"
    sub = await db.subscriptions.find_one({"_id": "sub-1"})
    # Entitlement is never enabled: status stays pending; flagged for review.
    assert sub["status"] == "pending"
    assert sub["billing_status"] == "review"
    assert sub["payment_review_required"] is True
    assert db.billing_records.docs[-1]["record_status"] == "amount_mismatch"


@pytest.mark.asyncio
async def test_webhook_skips_validation_when_plan_unresolvable():
    # No plan seeded → expected charge can't be resolved → activation not blocked.
    db = _FakeDB()
    _seed_subscription(db, status="pending")
    svc = BillingWebhookService(db=db, gateway=_razorpay(), provider="razorpay")
    raw = json.dumps(_event("subscription.charged", amount=4999)).encode()
    result = await svc.process(raw_body=raw, signature=_sign(raw), event_id_header="evt_noplan")
    assert result["applied"] == {"status": "active", "billing_status": "active"}
