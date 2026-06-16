"""Audit export pack (Phase 3 / M7): CSV builder + tenant-scoped query."""

from __future__ import annotations

from datetime import datetime

import pytest

from rbac_backend.services.audit_event_service import AuditEventService
from rbac_backend.services.audit_export import AUDIT_CSV_COLUMNS, audit_events_to_csv


# --- CSV builder ----------------------------------------------------------


def test_csv_has_header_row():
    csv_text = audit_events_to_csv([])
    lines = csv_text.strip().split("\n")
    assert lines[0] == ",".join(AUDIT_CSV_COLUMNS)
    assert len(lines) == 1  # header only


def test_csv_renders_row_and_serializes_metadata():
    csv_text = audit_events_to_csv(
        [
            {
                "created_at": "2026-06-01T10:00:00",
                "action": "policy.authorize",
                "result": "deny",
                "actor_id": "user-1",
                "organization_id": "org-A",
                "metadata": {"permission": "dms.document.delete"},
            }
        ]
    )
    lines = csv_text.strip().split("\n")
    assert len(lines) == 2
    assert "policy.authorize" in lines[1]
    assert "org-A" in lines[1]
    # dict metadata is JSON-encoded into a single cell
    assert "dms.document.delete" in csv_text


# --- tenant-scoped query --------------------------------------------------


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _FakeAudit:
    def __init__(self, docs):
        self.docs = docs
        self.last_query = None

    def find(self, query):
        self.last_query = query
        scalar = {k: v for k, v in query.items() if not isinstance(v, dict)}
        matched = [d for d in self.docs if all(d.get(k) == v for k, v in scalar.items())]
        return _FakeCursor(matched)


class _FakeDB:
    def __init__(self, docs):
        self.audit_events = _FakeAudit(docs)


@pytest.mark.asyncio
async def test_query_events_is_org_scoped():
    docs = [
        {"_id": 1, "organization_id": "org-A", "action": "policy.authorize", "created_at": datetime(2026, 6, 1)},
        {"_id": 2, "organization_id": "org-B", "action": "policy.authorize", "created_at": datetime(2026, 6, 2)},
    ]
    db = _FakeDB(docs)
    svc = AuditEventService(db=db)

    events = await svc.query_events(organization_id="org-A")

    assert len(events) == 1
    assert events[0]["organization_id"] == "org-A"
    assert db.audit_events.last_query["organization_id"] == "org-A"
    # normalization: _id stringified, created_at iso
    assert events[0]["_id"] == "1"
    assert events[0]["created_at"] == "2026-06-01T00:00:00"


@pytest.mark.asyncio
async def test_query_events_applies_project_and_action_filters():
    db = _FakeDB([])
    svc = AuditEventService(db=db)
    await svc.query_events(organization_id="org-A", project_id="proj-A", action="document.downloaded")
    q = db.audit_events.last_query
    assert q["organization_id"] == "org-A"
    assert q["project_id"] == "proj-A"
    assert q["action"] == "document.downloaded"
