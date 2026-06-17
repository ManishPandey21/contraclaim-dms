"""SLA & time-bar tracker (Phase 4 / Module 2): compute logic + scan + scope."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from rbac_backend.services.sla_service import (
    compute_sla_items,
    scan_sla_deadlines,
    SlaService,
)


NOW = datetime(2026, 6, 17)


def _claim(**kw):
    base = {
        "_id": kw.pop("_id", "c1"),
        "title": kw.pop("title", "A claim"),
        "type": kw.pop("type", "eot"),
        "status": kw.pop("status", "submitted"),
        "organization_id": kw.pop("organization_id", "org-A"),
        "project_id": kw.pop("project_id", "proj-A"),
    }
    base.update(kw)
    return base


# --- pure compute ---------------------------------------------------------


def test_response_deadline_states():
    claims = [
        _claim(_id="ok", response_due_date=NOW + timedelta(days=40)),
        _claim(_id="soon", response_due_date=NOW + timedelta(days=5)),
        _claim(_id="late", response_due_date=NOW - timedelta(days=3)),
    ]
    items = compute_sla_items(claims, now=NOW, approaching_days=14)
    by_id = {(i["claim_id"], i["kind"]): i for i in items}
    assert by_id[("ok", "response")]["state"] == "ok"
    assert by_id[("soon", "response")]["state"] == "approaching"
    assert by_id[("late", "response")]["state"] == "breached"
    # sorted by urgency (most overdue first)
    assert items[0]["claim_id"] == "late"


def test_time_bar_uses_per_type_window_and_only_when_un_notified():
    # eot window = 28d; event 20d ago → 8d left → approaching
    pending = _claim(_id="tb", type="eot", status="draft", event_date=NOW - timedelta(days=20))
    items = compute_sla_items([pending], now=NOW, approaching_days=14)
    tb = [i for i in items if i["kind"] == "time_bar"]
    assert len(tb) == 1 and tb[0]["state"] == "approaching" and tb[0]["days_remaining"] == 8

    # once notified, the time-bar no longer applies
    notified = _claim(_id="tb2", type="eot", event_date=NOW - timedelta(days=20), notice_date=NOW)
    assert [i for i in compute_sla_items([notified], now=NOW) if i["kind"] == "time_bar"] == []


def test_org_override_window_applies():
    claim = _claim(_id="vo", type="variation", status="draft", event_date=NOW - timedelta(days=10))
    # default variation window = 14 → 4d left (approaching). Override to 7 → breached.
    default_items = compute_sla_items([claim], now=NOW)
    assert default_items[0]["state"] == "approaching"
    overridden = compute_sla_items([claim], now=NOW, notice_windows={"variation": 7})
    assert overridden[0]["state"] == "breached"


def test_terminal_claims_excluded():
    for terminal in ("agreed", "rejected", "closed"):
        claims = [_claim(status=terminal, response_due_date=NOW - timedelta(days=5))]
        assert compute_sla_items(claims, now=NOW) == []


# --- DB-backed service + scope -------------------------------------------


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _Claims:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query):
        def _match(d):
            for k, v in query.items():
                if isinstance(v, dict) and "$nin" in v:
                    if d.get(k) in v["$nin"]:
                        return False
                elif d.get(k) != v:
                    return False
            return True

        return _Cursor([d for d in self.docs if _match(d)])


class _SlaRules:
    async def find_one(self, _q):
        return None


class _DB:
    def __init__(self, docs):
        self.claims = _Claims(docs)
        self.sla_rules = _SlaRules()


@pytest.mark.asyncio
async def test_list_sla_respects_scope_and_breached_filter():
    docs = [
        _claim(_id="a", organization_id="org-A", response_due_date=NOW - timedelta(days=2)),
        _claim(_id="b", organization_id="org-A", response_due_date=NOW + timedelta(days=3)),
        _claim(_id="x", organization_id="org-B", response_due_date=NOW - timedelta(days=1)),
        _claim(_id="done", organization_id="org-A", status="closed", response_due_date=NOW - timedelta(days=9)),
    ]
    svc = SlaService(_DB(docs))

    # Org A scope: sees a (breached) + b (approaching), never org-B's x.
    items = await svc.list_sla({"organization_id": "org-A"}, now=NOW)
    ids = sorted(i["claim_id"] for i in items)
    assert ids == ["a", "b"]

    breached = await svc.list_sla({"organization_id": "org-A"}, now=NOW, only_breached=True)
    assert [i["claim_id"] for i in breached] == ["a"]


@pytest.mark.asyncio
async def test_scan_emits_notifications_for_due_claims():
    docs = [
        _claim(_id="late", responsible_party_id="u9", response_due_date=NOW - timedelta(days=1)),
        _claim(_id="fine", response_due_date=NOW + timedelta(days=90)),
    ]
    sent = []

    class _Notif:
        async def emit(self, event_type, resource_id, resource_type, **kw):
            sent.append((event_type, resource_id, kw.get("dedupe_key")))

    summary = await scan_sla_deadlines(_DB(docs), _Notif(), now=NOW)
    assert summary["breached"] == 1
    assert summary["emitted"] == 1
    assert sent[0][1] == "late"
