"""Key Date / Milestone Tracker: calculation, current-date, EOT + immutable
extension history, achievement, status, dashboard, and tenant scope."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.models.key_date import (
    AchievementRecord,
    EOTApplicationCreate,
    EOTReview,
    KeyDateMilestoneCreate,
)
from rbac_backend.routers.key_dates import create_milestone
from rbac_backend.services.key_date_service import (
    KeyDateError,
    KeyDateService,
    build_dashboard,
    calculate_key_date,
    compute_achievement,
    current_key_date,
    derive_status,
)
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.scope_service import ScopeService


NOW = datetime(2026, 1, 1)
START = datetime(2026, 1, 5)


# --- pure functions -------------------------------------------------------


def test_calculate_key_date_from_week():
    # week 1 → start; week 5 → start + 28 days
    assert calculate_key_date(START, 1) == START
    assert calculate_key_date(START, 5) == START + timedelta(days=28)


def test_current_key_date_prefers_approved_revision():
    base = {"original_planned_key_date": START}
    assert current_key_date(base) == START
    revised = {"original_planned_key_date": START, "current_approved_key_date": START + timedelta(days=14)}
    assert current_key_date(revised) == START + timedelta(days=14)


def test_compute_achievement_late_early_ontime():
    cur = datetime(2026, 3, 1)
    assert compute_achievement(datetime(2026, 3, 6), cur) == (False, 5, 0)   # late
    assert compute_achievement(datetime(2026, 2, 24), cur) == (False, 0, 5)  # early
    assert compute_achievement(cur, cur) == (True, 0, 0)                     # on time


def test_derive_status_cases():
    def m(days, **extra):
        return {"current_approved_key_date": NOW + timedelta(days=days), **extra}
    assert derive_status(m(-2), NOW) == "overdue"
    assert derive_status(m(0), NOW) == "due_today"
    assert derive_status(m(5), NOW) == "due_soon"
    assert derive_status(m(20), NOW) == "upcoming"
    assert derive_status(m(60), NOW) == "not_started"
    assert derive_status(m(60, eot_status="submitted"), NOW) == "eot_submitted"
    assert derive_status({"actual_achievement_date": NOW}, NOW) == "achieved"


def test_build_dashboard_counts():
    ms = [
        {"current_approved_key_date": NOW + timedelta(days=-1)},  # overdue
        {"current_approved_key_date": NOW + timedelta(days=8)},   # due_30/15/10
        {"current_approved_key_date": NOW + timedelta(days=40), "eot_status": "submitted"},
        {"actual_achievement_date": NOW, "delay_days": 3},        # achieved late
        {"actual_achievement_date": NOW, "early_completion_days": 2},  # achieved early
    ]
    d = build_dashboard(ms, NOW)
    assert d["total"] == 5
    assert d["overdue"] == 1
    assert d["due_10"] == 1 and d["due_15"] == 1 and d["due_30"] == 1
    assert d["eot_submitted"] == 1
    assert d["achieved"] == 2 and d["achieved_late"] == 1 and d["achieved_early"] == 1


# --- fakes ----------------------------------------------------------------


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_k):
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
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


class _Coll:
    def __init__(self, seed=None):
        self.docs = {d["_id"]: dict(d) for d in (seed or [])}

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = dict(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    async def find_one(self, query):
        if "_id" in query and not isinstance(query["_id"], dict):
            d = self.docs.get(query["_id"])
            return dict(d) if d else None
        for d in self.docs.values():
            if all(d.get(k) == v for k, v in query.items() if not isinstance(v, dict)):
                return dict(d)
        return None

    async def find_one_and_update(self, query, update, return_document=True):
        d = self.docs.get(query.get("_id"))
        if not d:
            return None
        d.update(update.get("$set", {}))
        return dict(d)

    async def update_one(self, query, update):
        d = self.docs.get(query.get("_id"))
        if d:
            d.update(update.get("$set", {}))
        return SimpleNamespace(modified_count=1 if d else 0)

    async def replace_one(self, query, doc, upsert=False):
        self.docs[query.get("_id")] = dict(doc)
        return SimpleNamespace(modified_count=1)

    async def delete_one(self, query):
        existed = query.get("_id") in self.docs
        self.docs.pop(query.get("_id"), None)
        return SimpleNamespace(deleted_count=1 if existed else 0)

    def find(self, query):
        scalar = {k: v for k, v in query.items() if not isinstance(v, dict)}
        return _Cursor([d for d in self.docs.values() if all(d.get(k) == v for k, v in scalar.items())])


class _DB:
    def __init__(self):
        self.key_date_milestones = _Coll()
        self.key_date_eot_applications = _Coll()
        self.key_date_extension_history = _Coll()
        self.key_date_achievements = _Coll()
        self.key_date_notifications = _Coll()
        self.projects = _Coll()


def _user(org="org-A"):
    return SimpleNamespace(
        id="u1", roles=["orgadmin"], organization_id=org, organizations=[org],
        projects=["proj-A"], account_type="client_user",
    )


# --- service: create + EOT + history + achievement ------------------------


@pytest.mark.asyncio
async def test_create_milestone_calculates_dates():
    svc = KeyDateService(_DB())
    payload = KeyDateMilestoneCreate(
        title="Foundation complete", project_id="proj-A", contractual_week_number=5,
        project_start_date=START,
    )
    m = await svc.create_milestone(payload, _user())
    assert m["calculated_key_date"] == START + timedelta(days=28)
    assert m["original_planned_key_date"] == START + timedelta(days=28)
    assert m["current_approved_key_date"] == m["original_planned_key_date"]


@pytest.mark.asyncio
async def test_submit_eot_requires_letter_reference():
    db = _DB()
    svc = KeyDateService(db)
    m = await svc.create_milestone(
        KeyDateMilestoneCreate(title="M", project_id="proj-A", contractual_week_number=2, project_start_date=START),
        _user(),
    )
    with pytest.raises(KeyDateError):
        await svc.submit_eot(m, EOTApplicationCreate(requested_extension_days=14, submit=True), _user())
    # draft is allowed without a letter ref
    draft = await svc.submit_eot(m, EOTApplicationCreate(requested_extension_days=14, submit=False), _user())
    assert draft["status"] == "draft"


@pytest.mark.asyncio
async def test_eot_approval_extends_and_preserves_original():
    db = _DB()
    svc = KeyDateService(db)
    m = await svc.create_milestone(
        KeyDateMilestoneCreate(title="M", project_id="proj-A", contractual_week_number=1, project_start_date=START),
        _user(),
    )
    original = m["original_planned_key_date"]
    eot = await svc.submit_eot(
        m, EOTApplicationCreate(requested_extension_days=14, eot_letter_reference="EOT/001",
                                requested_revised_key_date=START + timedelta(days=14), submit=True),
        _user(),
    )
    # approval requires the approval letter ref + revised date
    with pytest.raises(KeyDateError):
        await svc.review_eot(m, eot, EOTReview(decision="approved", approved_revised_key_date=START + timedelta(days=14)), _user())

    fresh = await svc.get(m["_id"])
    await svc.review_eot(
        fresh, eot,
        EOTReview(decision="approved", approved_extension_days=14,
                  approved_revised_key_date=START + timedelta(days=14),
                  approval_letter_reference="APP/001"),
        _user(),
    )
    after = await svc.get(m["_id"])
    assert after["current_approved_key_date"] == START + timedelta(days=14)
    assert after["original_planned_key_date"] == original  # never overwritten
    assert after["eot_status"] == "approved"
    history = await svc.list_extension_history(m["_id"])
    assert len(history) == 1 and history[0]["revision_number"] == 1
    assert history[0]["original_key_date"] == original
    assert history[0]["approved_revised_key_date"] == START + timedelta(days=14)


@pytest.mark.asyncio
async def test_eot_rejection_records_history_without_changing_date():
    db = _DB()
    svc = KeyDateService(db)
    m = await svc.create_milestone(
        KeyDateMilestoneCreate(title="M", project_id="proj-A", contractual_week_number=1, project_start_date=START),
        _user(),
    )
    eot = await svc.submit_eot(m, EOTApplicationCreate(requested_extension_days=7, eot_letter_reference="EOT/2", submit=True), _user())
    fresh = await svc.get(m["_id"])
    await svc.review_eot(fresh, eot, EOTReview(decision="rejected", approval_remarks="not justified"), _user())
    after = await svc.get(m["_id"])
    assert after["current_approved_key_date"] == after["original_planned_key_date"]
    assert after["eot_status"] == "extension_rejected" or after["eot_status"] == "rejected"
    history = await svc.list_extension_history(m["_id"])
    assert len(history) == 1 and history[0]["status"] == "rejected"


@pytest.mark.asyncio
async def test_record_achievement_delay_and_client_notice_rule():
    db = _DB()
    svc = KeyDateService(db)
    m = await svc.create_milestone(
        KeyDateMilestoneCreate(title="M", project_id="proj-A", contractual_week_number=1, project_start_date=START),
        _user(),
    )
    # client notification required but no ref → rejected
    with pytest.raises(KeyDateError):
        await svc.record_achievement(m, AchievementRecord(actual_achievement_date=START + timedelta(days=3), client_notification_required=True), _user())
    updated = await svc.record_achievement(m, AchievementRecord(actual_achievement_date=START + timedelta(days=3)), _user())
    assert updated["delay_days"] == 3 and updated["early_completion_days"] == 0
    assert updated["status"] == "achieved"


# --- router scope ---------------------------------------------------------


class _Allow:
    async def user_has_permission(self, *_a, **_k):
        return True


class _EntAllow:
    async def check_permission_entitlement(self, **_k):
        return True, "ok"


class _ScopeCursor:
    async def to_list(self, length=None):
        return []


class _ScopeColl:
    def find(self, *_a, **_k):
        return _ScopeCursor()


class _ScopeDB:
    organization_memberships = _ScopeColl()
    project_memberships = _ScopeColl()


class _Audit:
    async def emit(self, **_k):
        return None


def _policy():
    return PolicyService(
        permission_service=_Allow(), scope_service=ScopeService(db=_ScopeDB()),
        entitlement_service=_EntAllow(), audit_service=_Audit(),
    )


@pytest.mark.asyncio
async def test_create_milestone_denies_cross_tenant():
    payload = KeyDateMilestoneCreate(
        title="X", project_id="proj-B", organization_id="org-B",
        contractual_week_number=1, project_start_date=START,
    )
    with pytest.raises(HTTPException) as exc:
        await create_milestone(payload, db=_DB(), current_user=_user(org="org-A"), policy=_policy())
    assert exc.value.status_code == 403


# --- notifications + export (KDT-3) ---------------------------------------


def test_due_notification_types_thresholds():
    from rbac_backend.services.key_date_service import due_notification_types

    def m(days):
        return {"current_approved_key_date": NOW + timedelta(days=days)}
    assert due_notification_types(m(30), NOW) == ["T-30"]
    assert due_notification_types(m(15), NOW) == ["T-15"]
    assert due_notification_types(m(1), NOW) == ["T-1"]
    assert due_notification_types(m(0), NOW) == ["T-0"]
    assert due_notification_types(m(-2), NOW) == ["overdue"]
    assert due_notification_types(m(7), NOW) == []  # not a threshold day
    assert due_notification_types({"actual_achievement_date": NOW, "current_approved_key_date": NOW}, NOW) == []


@pytest.mark.asyncio
async def test_scan_emits_and_dedupes():
    from rbac_backend.services.key_date_service import scan_key_date_notifications

    db = _DB()
    db.key_date_milestones.docs["m1"] = {
        "_id": "m1", "title": "Due in 15", "organization_id": "org-A", "project_id": "proj-A",
        "responsible_party_id": "u9", "current_approved_key_date": NOW + timedelta(days=15),
        "actual_achievement_date": None,
    }
    sent = []

    class _Notif:
        async def emit(self, event_type, rid, rtype, **kw):
            sent.append((event_type, rid))

    first = await scan_key_date_notifications(db, _Notif(), now=NOW)
    assert first["emitted"] == 1 and len(sent) == 1
    # second run on the same day is deduped via the notification log
    second = await scan_key_date_notifications(db, _Notif(), now=NOW)
    assert second["emitted"] == 0 and len(sent) == 1


def test_csv_export_has_header_and_rows():
    from rbac_backend.services.key_date_export import milestones_to_csv

    rows = [{"milestone_ref": "MS-1", "title": "Foundation", "status": "overdue", "days_remaining": -3}]
    csv_text = milestones_to_csv(rows)
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("Ref,Title,Week")
    assert "MS-1" in lines[1] and "Foundation" in lines[1] and "overdue" in lines[1]
