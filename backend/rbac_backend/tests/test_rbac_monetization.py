from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from rbac_backend.services.subscription_lifecycle_service import SubscriptionLifecycleService

class FakeCollection:
    def __init__(self, data=None):
        self.data = data or []
        self.inserted = []
        self.updates = []
        self.deleted = []

    def find(self, query):
        # Simply return elements or everything for mock purposes
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

            async def to_list(self, length=None):
                return self.items

        return FakeCursor(self.data)

    async def update_one(self, filter_doc, update_doc):
        self.updates.append((filter_doc, update_doc))
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
