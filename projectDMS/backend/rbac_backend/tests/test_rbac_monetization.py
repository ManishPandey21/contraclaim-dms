from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from types import SimpleNamespace
from rbac_backend.models.rbac_monetization import (
    CancelSubscriptionRequest,
    UpgradeDowngradeRequest,
)
from rbac_backend.services.billing_receipt import build_receipt, receipt_to_html
from rbac_backend.services.monetization_service import MonetizationService
from rbac_backend.services.subscription_lifecycle_service import SubscriptionLifecycleService


class _FakeGateway:
    """Records the gateway calls the lifecycle ops make (C2)."""
    def __init__(self):
        self.calls = []

    async def cancel_subscription(self, *, gateway_subscription_id, immediate=False):
        self.calls.append(("cancel", gateway_subscription_id, immediate))
        return True

    async def reactivate_subscription(self, *, gateway_subscription_id):
        self.calls.append(("reactivate", gateway_subscription_id))
        return None

    async def update_subscription(self, *, gateway_subscription_id, new_plan_code=None, new_billing_period=None):
        self.calls.append(("update", gateway_subscription_id, new_plan_code, new_billing_period))
        return None

class FakeCollection:
    def __init__(self, data=None):
        self.data = data or []
        self.inserted = []
        self.updates = []
        self.deleted = []

    def find(self, query):
        # Filter on scalar equality and $in; ignore range operators ($lt/$lte/$gte).
        def matches(doc):
            for key, cond in (query or {}).items():
                if isinstance(cond, dict):
                    if "$in" in cond and doc.get(key) not in cond["$in"]:
                        return False
                elif doc.get(key) != cond:
                    return False
            return True

        items = [d for d in self.data if matches(d)]

        class FakeCursor:
            def __init__(self, items):
                self.items = items
                self.index = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.index >= len(self.items):
                    raise StopAsyncIteration
                item = self.items[self.index]
                self.index += 1
                return item

            def sort(self, *args, **kwargs):
                return self

            async def to_list(self, length=None):
                return self.items

        return FakeCursor(items)

    async def find_one(self, query):
        for d in self.data:
            if all(d.get(k) == v for k, v in (query or {}).items() if not isinstance(v, dict)):
                return d
        return None

    async def update_one(self, filter_doc, update_doc):
        self.updates.append((filter_doc, update_doc))
        for d in self.data:
            if all(d.get(k) == v for k, v in filter_doc.items() if not isinstance(v, dict)):
                d.update(update_doc.get("$set", {}))
                break
        return type("FakeResult", (), {"modified_count": 1})()

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return type("FakeResult", (), {"inserted_id": doc.get("_id", "1")})()

    async def insert_many(self, docs):
        self.inserted.extend(docs)
        return type("FakeResult", (), {"inserted_ids": ["1"] * len(docs)})()

    async def delete_many(self, query):
        self.deleted.append(query)
        return type("FakeResult", (), {"deleted_count": len(self.data)})()

class FakeDB:
    def __init__(self):
        self.subscriptions = FakeCollection()
        self.subscription_history = FakeCollection()
        self.usage_counters = FakeCollection()
        self.usage_counters_archive = FakeCollection()
        self.billing_records = FakeCollection()
        self.plans = FakeCollection()


@pytest.mark.asyncio
async def test_process_trial_expirations() -> None:
    db = FakeDB()
    now = datetime.utcnow()
    
    # Setup standard trial subscription that has already expired
    expired_trial = {
        "_id": "sub_trial_expired_1",
        "organization_id": "org_1",
        "project_id": None,
        "status": "trial",
        "trial": True,
        "trial_ends_at": now - timedelta(days=1),
        "plan_code": "premium",
    }
    db.subscriptions.data = [expired_trial]
    
    service = SubscriptionLifecycleService(db)
    expired_ids = await service.process_trial_expirations()
    
    assert expired_ids == ["sub_trial_expired_1"]
    assert len(db.subscriptions.updates) == 1
    assert db.subscriptions.updates[0][0]["_id"] == "sub_trial_expired_1"
    assert db.subscriptions.updates[0][1]["$set"]["status"] == "cancelled"
    assert db.subscriptions.updates[0][1]["$set"]["trial"] is False
    
    assert len(db.subscription_history.inserted) == 1
    history = db.subscription_history.inserted[0]
    assert history["subscription_id"] == "sub_trial_expired_1"
    assert history["change_type"] == "trial_expire"
    assert history["from_status"] == "trial"
    assert history["to_status"] == "cancelled"


@pytest.mark.asyncio
async def test_execute_renewals() -> None:
    db = FakeDB()
    now = datetime.utcnow()
    
    # Setup active subscription that is due for renewal
    renewing_sub = {
        "_id": "sub_renew_1",
        "organization_id": "org_2",
        "project_id": None,
        "status": "active",
        "auto_renew": True,
        "current_period_end": now - timedelta(hours=2),
        "billing_period": "monthly",
        "plan_code": "professional",
    }
    db.subscriptions.data = [renewing_sub]
    
    service = SubscriptionLifecycleService(db)
    renewed_ids = await service.execute_renewals()
    
    assert renewed_ids == ["sub_renew_1"]
    assert len(db.subscriptions.updates) == 1
    assert db.subscriptions.updates[0][0]["_id"] == "sub_renew_1"
    assert "current_period_start" in db.subscriptions.updates[0][1]["$set"]
    assert "current_period_end" in db.subscriptions.updates[0][1]["$set"]
    
    assert len(db.subscription_history.inserted) == 1
    history = db.subscription_history.inserted[0]
    assert history["subscription_id"] == "sub_renew_1"
    assert history["change_type"] == "renewal"
    assert history["from_status"] == "active"
    assert history["to_status"] == "active"


@pytest.mark.asyncio
async def test_get_organization_billing_records() -> None:
    db = FakeDB()
    db.billing_records.data = [
        {"_id": "br1", "organization_id": "org_1", "record_status": "paid",
         "amount_minor": 50000, "currency": "INR", "event_type": "payment.succeeded"},
        {"_id": "br2", "organization_id": "org_1", "record_status": "amount_mismatch",
         "amount_minor": 4999, "currency": "INR", "validation_error": "amount 4999 != expected 50000"},
    ]
    rows = await MonetizationService(db).get_organization_billing_records("org_1")
    assert len(rows) == 2
    # ids normalised to id; mismatch row carries the validation detail for the queue.
    statuses = {r["record_status"] for r in rows}
    assert statuses == {"paid", "amount_mismatch"}
    mismatch = next(r for r in rows if r["record_status"] == "amount_mismatch")
    assert "validation_error" in mismatch


@pytest.mark.asyncio
async def test_billing_review_queue_filters_and_enriches() -> None:
    db = FakeDB()
    db.subscriptions.data = [
        {"_id": "sub_1", "organization_id": "org_1", "plan_code": "dms_pro", "billing_status": "review"},
    ]
    db.billing_records.data = [
        {"_id": "br_paid", "organization_id": "org_1", "record_status": "paid",
         "amount_minor": 50000, "subscription_id": "sub_1"},
        {"_id": "br_fail", "organization_id": "org_1", "record_status": "failed",
         "amount_minor": 50000, "subscription_id": "sub_1"},
        {"_id": "br_mismatch", "organization_id": "org_1", "record_status": "amount_mismatch",
         "amount_minor": 4999, "subscription_id": "sub_1", "validation_error": "amount 4999 != expected 50000"},
        {"_id": "br_other_org", "organization_id": "org_2", "record_status": "failed"},
    ]
    queue = await MonetizationService(db).get_billing_review_queue("org_1")

    ids = {r["id"] for r in queue}
    assert ids == {"br_fail", "br_mismatch"}  # paid + other-org excluded
    # enriched with the subscription's plan + current billing status.
    assert all(r["plan_code"] == "dms_pro" for r in queue)
    assert all(r["subscription_billing_status"] == "review" for r in queue)


@pytest.mark.asyncio
async def test_cancel_calls_gateway_and_updates_local() -> None:
    db = FakeDB()
    db.subscriptions.data = [{
        "_id": "sub_1", "organization_id": "org_1", "status": "active",
        "plan_code": "dms_pro", "payment_gateway_subscription_id": "gw_sub_1",
    }]
    gw = _FakeGateway()
    await MonetizationService(db, gateway=gw).cancel_subscription(
        "sub_1", CancelSubscriptionRequest(reason="x", immediate=True), SimpleNamespace(id="u1"),
    )
    assert ("cancel", "gw_sub_1", True) in gw.calls
    sub = await db.subscriptions.find_one({"_id": "sub_1"})
    assert sub["status"] == "cancelled"


@pytest.mark.asyncio
async def test_reactivate_calls_gateway() -> None:
    db = FakeDB()
    db.subscriptions.data = [{
        "_id": "sub_3", "organization_id": "org_1", "status": "cancelled",
        "plan_code": "dms_pro", "payment_gateway_subscription_id": "gw_3",
    }]
    gw = _FakeGateway()
    await MonetizationService(db, gateway=gw).reactivate_subscription("sub_3", SimpleNamespace(id="u1"))
    assert ("reactivate", "gw_3") in gw.calls
    sub = await db.subscriptions.find_one({"_id": "sub_3"})
    assert sub["status"] == "active"


@pytest.mark.asyncio
async def test_upgrade_calls_gateway_update() -> None:
    db = FakeDB()
    db.plans.data = [
        {"code": "dms_basic", "tier": 1, "pricing_tiers": {"monthly": 1000}, "base_price_minor": 1000},
        {"code": "dms_pro", "tier": 2, "pricing_tiers": {"monthly": 2000}, "base_price_minor": 2000},
    ]
    db.subscriptions.data = [{
        "_id": "sub_u", "organization_id": "org_1", "status": "active", "plan_code": "dms_basic",
        "billing_period": "monthly", "payment_gateway_subscription_id": "gw_u",
    }]
    gw = _FakeGateway()
    await MonetizationService(db, gateway=gw).upgrade_subscription(
        "sub_u", UpgradeDowngradeRequest(new_plan_code="dms_pro", billing_period="monthly"),
        SimpleNamespace(id="u1"),
    )
    assert any(c[0] == "update" and c[1] == "gw_u" and c[2] == "dms_pro" for c in gw.calls)


@pytest.mark.asyncio
async def test_cancel_without_gateway_id_skips_gateway_but_updates_local() -> None:
    db = FakeDB()
    db.subscriptions.data = [{
        "_id": "sub_2", "organization_id": "org_1", "status": "active", "plan_code": "dms_pro",
    }]  # no payment_gateway_subscription_id (trial / manual)
    gw = _FakeGateway()
    await MonetizationService(db, gateway=gw).cancel_subscription(
        "sub_2", CancelSubscriptionRequest(reason="x", immediate=False), SimpleNamespace(id="u1"),
    )
    assert gw.calls == []  # no gateway call when there is no gateway subscription
    sub = await db.subscriptions.find_one({"_id": "sub_2"})
    assert sub["status"] == "cancelled"


def test_build_receipt_assembles_fields() -> None:
    record = {
        "_id": "br1", "organization_id": "org_1", "record_status": "paid",
        "amount_minor": 50000, "currency": "INR", "gateway_payment_id": "pay_abc",
        "provider": "razorpay",
    }
    data = build_receipt(
        record,
        subscription={"plan_code": "dms_pro", "billing_period": "monthly"},
        plan={"name": "DMS Pro"},
        organization={"name": "Acme Infra", "gstin": "29ABCDE1234F1Z5"},
    )
    assert data["receipt_no"] == "pay_abc"
    assert data["amount_display"] == "INR 500.00"
    assert data["description"] == "DMS Pro (monthly)"
    assert data["buyer_name"] == "Acme Infra"
    assert data["buyer_gstin"] == "29ABCDE1234F1Z5"
    assert data["status"] == "paid"


def test_receipt_to_html_renders_amount_and_payment() -> None:
    html = receipt_to_html(build_receipt(
        {"amount_minor": 50000, "currency": "INR", "gateway_payment_id": "pay_x",
         "organization_id": "org_1", "record_status": "paid"},
        plan={"name": "DMS Pro"},
    ))
    assert "<html" in html and "INR 500.00" in html
    assert "pay_x" in html  # receipt no + payment reference
    assert "Tax Invoice" in html


@pytest.mark.asyncio
async def test_reset_monthly_usage_counters() -> None:
    db = FakeDB()
    now = datetime.utcnow()
    last_month = now.replace(day=1) - timedelta(days=2)
    
    # Setup old usage counters
    old_counter = {
        "_id": "counter_1",
        "organization_id": "org_1",
        "period_start": last_month,
        "drafting_requests_count": 12,
    }
    db.usage_counters.data = [old_counter]
    
    service = SubscriptionLifecycleService(db)
    archived_count = await service.reset_monthly_usage_counters()
    
    assert archived_count == 1
    assert len(db.usage_counters_archive.inserted) == 1
    assert db.usage_counters_archive.inserted[0]["drafting_requests_count"] == 12
    assert "archived_at" in db.usage_counters_archive.inserted[0]
    assert len(db.usage_counters.deleted) == 1
