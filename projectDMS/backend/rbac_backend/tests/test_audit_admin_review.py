from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import rbac_backend.services.audit_event_service as audit_event_module
from rbac_backend.core.permissions import Permissions
from rbac_backend.services.audit_event_service import AuditEventService


class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, field, direction):
        self.docs.sort(key=lambda item: item.get(field) or datetime.min, reverse=direction < 0)
        return self

    def limit(self, limit):
        self.docs = self.docs[:limit]
        return self

    def __aiter__(self):
        self._iter = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return dict(next(self._iter))
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Collection:
    def __init__(self):
        self.docs = []

    def _match(self, doc, query):
        for key, expected in (query or {}).items():
            if doc.get(key) != expected:
                return False
        return True

    async def insert_one(self, doc):
        saved = dict(doc)
        saved.setdefault("_id", f"id-{len(self.docs) + 1}")
        self.docs.append(saved)
        return SimpleNamespace(inserted_id=saved["_id"])

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if self._match(doc, query):
                for field, value in update.get("$set", {}).items():
                    doc[field] = value
                for field, value in update.get("$inc", {}).items():
                    doc[field] = doc.get(field, 0) + value
                return SimpleNamespace(matched_count=1, modified_count=1)
        if not upsert:
            return SimpleNamespace(matched_count=0, modified_count=0)
        doc = dict(query)
        for field, value in update.get("$setOnInsert", {}).items():
            doc[field] = value
        for field, value in update.get("$set", {}).items():
            doc[field] = value
        for field, value in update.get("$inc", {}).items():
            doc[field] = doc.get(field, 0) + value
        doc.setdefault("_id", f"id-{len(self.docs) + 1}")
        self.docs.append(doc)
        return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=doc["_id"])

    def find(self, query):
        return _Cursor([doc for doc in self.docs if self._match(doc, query)])


class _DB:
    def __init__(self):
        self.audit_events = _Collection()
        self.admin_review_items = _Collection()


class _Observability:
    def __init__(self):
        self.audit_events = []
        self.review_items = []

    async def record_audit_event(self, **kwargs):
        self.audit_events.append(kwargs)

    async def record_admin_review_item(self, **kwargs):
        self.review_items.append(kwargs)


@pytest.mark.asyncio
async def test_policy_deny_audit_creates_admin_review_item(monkeypatch):
    db = _DB()
    observed = _Observability()
    monkeypatch.setattr(audit_event_module, "observability_registry", observed)

    await AuditEventService(db).emit(
        action="policy.authorize",
        actor_id="user-1",
        resource_type="document",
        resource_id="doc-1",
        organization_id="org-1",
        project_id="project-1",
        result="deny",
        reason="scope_denied",
        metadata={"permission": Permissions.DOCUMENT_VIEW},
    )

    assert db.audit_events.docs[0]["action"] == "policy.authorize"
    review = db.admin_review_items.docs[0]
    assert review["status"] == "open"
    assert review["severity"] == "critical"
    assert review["source"] == "authorization"
    assert review["occurrence_count"] == 1
    assert review["organization_id"] == "org-1"
    assert review["project_id"] == "project-1"
    assert Permissions.DOCUMENT_VIEW in review["title"]
    assert observed.audit_events == [
        {"action": "policy.authorize", "result": "deny", "resource_type": "document"}
    ]
    assert observed.review_items == [
        {"status": "open", "severity": "critical", "source": "authorization"}
    ]


@pytest.mark.asyncio
async def test_non_reviewable_missing_permission_denial_is_only_audited(monkeypatch):
    db = _DB()
    observed = _Observability()
    monkeypatch.setattr(audit_event_module, "observability_registry", observed)

    await AuditEventService(db).emit(
        action="policy.authorize",
        actor_id="user-1",
        organization_id="org-1",
        result="deny",
        reason="missing_permission",
        metadata={"permission": Permissions.DOCUMENT_DELETE},
    )

    assert len(db.audit_events.docs) == 1
    assert db.admin_review_items.docs == []
    assert observed.audit_events[0]["result"] == "deny"
    assert observed.review_items == []


@pytest.mark.asyncio
async def test_admin_review_query_is_tenant_scoped_and_normalized():
    db = _DB()
    now = datetime.utcnow()
    db.admin_review_items.docs.extend(
        [
            {
                "_id": "review-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "status": "open",
                "severity": "high",
                "reason": "quota_exceeded",
                "updated_at": now,
            },
            {
                "_id": "review-2",
                "organization_id": "org-2",
                "project_id": "project-2",
                "status": "open",
                "severity": "high",
                "reason": "quota_exceeded",
                "updated_at": now - timedelta(days=1),
            },
        ]
    )

    items = await AuditEventService(db).query_admin_review_items(
        organization_id="org-1",
        status="open",
        severity="high",
    )

    assert len(items) == 1
    assert items[0]["_id"] == "review-1"
    assert items[0]["updated_at"] == now.isoformat()


@pytest.mark.asyncio
async def test_admin_review_item_status_update_is_org_scoped():
    db = _DB()
    db.admin_review_items.docs.append(
        {
            "_id": "review-1",
            "organization_id": "org-1",
            "status": "open",
        }
    )

    updated = await AuditEventService(db).update_admin_review_item(
        "review-1",
        organization_id="org-1",
        status="resolved",
        reviewer_id="admin-1",
        note="Reviewed",
    )

    assert updated is True
    assert db.admin_review_items.docs[0]["status"] == "resolved"
    assert db.admin_review_items.docs[0]["reviewed_by"] == "admin-1"
    assert db.admin_review_items.docs[0]["review_note"] == "Reviewed"

    not_updated = await AuditEventService(db).update_admin_review_item(
        "review-1",
        organization_id="org-2",
        status="dismissed",
        reviewer_id="admin-2",
    )

    assert not_updated is False
    assert db.admin_review_items.docs[0]["status"] == "resolved"
