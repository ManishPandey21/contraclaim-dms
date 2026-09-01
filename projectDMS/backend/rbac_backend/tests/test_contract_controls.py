"""Variation Register + Bank Guarantee Register: calculations, BG extension /
alerts, summaries, and tenant scope."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.models.bank_guarantee import BankGuaranteeCreate, BGExtendRequest
from rbac_backend.models.variation import VariationCreate
from rbac_backend.routers.bank_guarantees import create_bg
from rbac_backend.routers.variations import create_variation
from rbac_backend.services.bank_guarantee_service import (
    BankGuaranteeService,
    bg_summary,
    due_alert_types,
    extension_required,
    scan_bg_expiry_alerts,
)
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.scope_service import ScopeService
from rbac_backend.services.variation_service import signed_approved, variation_summary

NOW = datetime(2026, 1, 1)


# --- Variation pure logic -------------------------------------------------


def test_signed_approved_respects_type():
    assert signed_approved({"approved_amount": 100, "variation_type": "positive"}) == 100
    assert signed_approved({"approved_amount": 100, "variation_type": "negative"}) == -100
    assert signed_approved({"approved_amount": None, "variation_type": "positive"}) == 0.0


def test_variation_summary_rollup():
    variations = [
        {"submitted_amount": 300, "approved_amount": 250, "variation_type": "positive", "status": "approved", "original_contract_value": 5000},
        {"submitted_amount": 100, "approved_amount": 50, "variation_type": "negative", "status": "approved"},
        {"submitted_amount": 80, "approved_amount": 0, "variation_type": "positive", "status": "submitted"},
        {"submitted_amount": 40, "approved_amount": 0, "variation_type": "positive", "status": "rejected"},
    ]
    s = variation_summary(variations)
    assert s["original_contract_value"] == 5000
    assert s["cumulative_approved_variation"] == 200  # +250 - 50
    assert s["revised_contract_value"] == 5200
    assert s["percentage_variation"] == 4.0
    assert s["pending_variation_count"] == 1
    assert s["approved_variation_count"] == 2
    assert s["rejected_variation_count"] == 1


# --- BG pure logic --------------------------------------------------------


def _bg(exp_days, req_days, status="valid"):
    return {
        "bg_expiry_date": NOW + timedelta(days=exp_days),
        "contractual_required_up_to": NOW + timedelta(days=req_days),
        "bg_status": status,
    }


def test_extension_required_rules():
    assert extension_required(_bg(30, 90), NOW) is True       # expires before required
    assert extension_required(_bg(120, 90), NOW) is False     # expires after required
    assert extension_required(_bg(30, 90, "released"), NOW) is False


def test_due_alert_types():
    assert due_alert_types(_bg(45, 90), NOW) == ["T-45"]
    assert due_alert_types(_bg(30, 90), NOW) == ["T-30"]
    assert due_alert_types(_bg(-2, 90), NOW) == ["expired"]
    assert due_alert_types(_bg(45, 90, "released"), NOW) == []
    assert due_alert_types(_bg(120, 90), NOW) == []  # expiry after required → no alert


def test_bg_summary_counts():
    bgs = [
        _bg(45, 90), _bg(30, 90), _bg(-1, 90), {"bg_status": "released", "bg_amount": 10},
        {**_bg(120, 90), "bg_amount": 5},
    ]
    for b in bgs:
        b.setdefault("bg_amount", 1)
    s = bg_summary(bgs, NOW)
    assert s["total"] == 5
    assert s["released"] == 1
    assert s["expired"] == 1
    assert s["expiring_45"] == 2 and s["expiring_30"] == 1
    assert s["extension_required"] == 3  # the three with expiry<required & not released


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
    def __init__(self):
        self.docs = {}

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

    async def update_one(self, query, update):
        d = self.docs.get(query.get("_id"))
        if d:
            d.update(update.get("$set", {}))
        return SimpleNamespace(modified_count=1 if d else 0)

    def find(self, query):
        def _match(d):
            for k, v in (query or {}).items():
                if isinstance(v, dict) and "$nin" in v:
                    if d.get(k) in v["$nin"]:
                        return False
                elif not isinstance(v, dict) and d.get(k) != v:
                    return False
            return True
        return _Cursor([d for d in self.docs.values() if _match(d)])


class _DB:
    def __init__(self):
        self.variations = _Coll()
        self.bank_guarantees = _Coll()
        self.bank_guarantee_events = _Coll()
        self.bg_extension_history = _Coll()
        self.bg_notifications = _Coll()


def _user(org="org-A"):
    return SimpleNamespace(
        id="u1", roles=["orgadmin"], organization_id=org, organizations=[org],
        projects=["proj-A"], account_type="client_user",
    )


# --- BG extend + scan -----------------------------------------------------


@pytest.mark.asyncio
async def test_extend_creates_history_and_updates_expiry():
    db = _DB()
    svc = BankGuaranteeService(db)
    bg = await svc.create(
        BankGuaranteeCreate(project_id="proj-A", bg_number="BG-1", bg_amount=1000,
                            bg_expiry_date=NOW + timedelta(days=30),
                            contractual_required_up_to=NOW + timedelta(days=120)),
        _user(),
    )
    assert bg["extension_required"] is True
    extended = await svc.extend(
        bg, BGExtendRequest(revised_expiry_date=NOW + timedelta(days=150), extension_letter_reference="EXT/1"),
        _user(),
    )
    assert extended["bg_status"] == "extended"
    assert extended["current_revision"] == 1
    assert extended["extension_required"] is False  # now expires after required
    history = await svc.list_history(bg)
    assert len(history) == 1 and history[0]["revision_number"] == 1
    assert history[0]["previous_expiry_date"] == NOW + timedelta(days=30)


@pytest.mark.asyncio
async def test_release_then_no_extension_required():
    db = _DB()
    svc = BankGuaranteeService(db)
    bg = await svc.create(
        BankGuaranteeCreate(project_id="proj-A", bg_number="BG-2", bg_expiry_date=NOW + timedelta(days=10),
                            contractual_required_up_to=NOW + timedelta(days=90)),
        _user(),
    )
    released = await svc.release(bg, _user())
    assert released["bg_status"] == "released"
    assert released["extension_required"] is False


@pytest.mark.asyncio
async def test_scan_emits_and_dedupes():
    db = _DB()
    svc = BankGuaranteeService(db)
    await svc.create(
        BankGuaranteeCreate(project_id="proj-A", bg_number="BG-3", bg_expiry_date=NOW + timedelta(days=45),
                            contractual_required_up_to=NOW + timedelta(days=120)),
        _user(),
    )
    sent = []

    class _Notif:
        async def emit(self, et, rid, rt, **kw):
            sent.append(rid)

    first = await scan_bg_expiry_alerts(db, _Notif(), now=NOW)
    assert first["emitted"] == 1 and len(sent) == 1
    second = await scan_bg_expiry_alerts(db, _Notif(), now=NOW)
    assert second["emitted"] == 0 and len(sent) == 1


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


class _ScopeProjects:
    """Minimal projects collection so tenant-isolation checks resolve.

    ScopeService.project_belongs_to_organization fails closed when the
    projects collection is absent, which otherwise masks the control under
    test with an unrelated 403 scope_denied.
    """

    _DOCS = {"proj-A": "org-A", "proj-B": "org-B"}

    async def find_one(self, query):
        raw = (query or {}).get("_id")
        candidates = raw.get("$in", []) if isinstance(raw, dict) else [raw]
        for candidate in candidates:
            org = self._DOCS.get(str(candidate))
            if org:
                return {"_id": str(candidate), "organization_id": org}
        return None


class _ScopeDB:
    organization_memberships = _ScopeColl()
    project_memberships = _ScopeColl()
    projects = _ScopeProjects()


class _Audit:
    async def emit(self, **_k):
        return None


def _policy():
    return PolicyService(
        permission_service=_Allow(), scope_service=ScopeService(db=_ScopeDB()),
        entitlement_service=_EntAllow(), audit_service=_Audit(),
    )


@pytest.mark.asyncio
async def test_create_variation_denies_cross_tenant():
    payload = VariationCreate(project_id="proj-B", organization_id="org-B", variation_number="VO-1")
    with pytest.raises(HTTPException) as exc:
        await create_variation(payload, db=_DB(), current_user=_user(org="org-A"), policy=_policy())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_create_bg_denies_cross_tenant():
    payload = BankGuaranteeCreate(project_id="proj-B", organization_id="org-B", bg_number="BG-X")
    with pytest.raises(HTTPException) as exc:
        await create_bg(payload, db=_DB(), current_user=_user(org="org-A"), policy=_policy())
    assert exc.value.status_code == 403
