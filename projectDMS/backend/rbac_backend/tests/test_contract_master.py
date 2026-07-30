"""Contract Master: completion/DLP/BG-required-up-to helpers, revise-completion,
value sync, and tenant scope."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.models.contract_master import ContractMasterCreate
from rbac_backend.routers.contract_master import create_contract_master
from rbac_backend.services.contract_master_service import (
    ContractMasterService,
    bg_required_up_to,
    dlp_end_date,
    effective_completion_date,
)
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.scope_service import ScopeService

COMP = datetime(2026, 12, 31)


# --- pure helpers ---------------------------------------------------------


def test_effective_completion_prefers_revised():
    assert effective_completion_date({"original_completion_date": COMP}) == COMP
    revised = {"original_completion_date": COMP, "revised_completion_date": datetime(2027, 6, 30)}
    assert effective_completion_date(revised) == datetime(2027, 6, 30)


def test_dlp_end_date():
    cm = {"original_completion_date": COMP, "defect_liability_period_days": 365}
    assert dlp_end_date(cm) == COMP + timedelta(days=365)
    assert dlp_end_date({"original_completion_date": COMP}) is None  # no DLP


def test_bg_required_up_to_by_basis():
    cm = {"original_completion_date": COMP, "defect_liability_period_days": 365}
    # performance → dlp_end + 0
    assert bg_required_up_to(cm, "performance") == COMP + timedelta(days=365)
    # mobilisation advance → completion + 0
    assert bg_required_up_to(cm, "mobilisation_advance") == COMP
    # unknown type → default rule (completion)
    assert bg_required_up_to(cm, "weird_type") == COMP


# --- multi-currency contract value ----------------------------------------


def test_total_contract_value_base_sums_converted_currencies():
    from rbac_backend.services.contract_master_service import total_contract_value_base

    cm = {
        "currency": "INR",
        "contract_currencies": [
            {"currency": "INR", "conversion_rate": 1.0, "contract_value": 1_000_000},
            {"currency": "USD", "conversion_rate": 83.0, "contract_value": 10_000},
            {"currency": "EUR", "conversion_rate": 90.0, "contract_value": 5_000},
        ],
    }
    # 1,000,000 + 10,000*83 + 5,000*90 = 1,000,000 + 830,000 + 450,000
    assert total_contract_value_base(cm) == 2_280_000.0


def test_total_contract_value_base_falls_back_to_single_currency():
    from rbac_backend.services.contract_master_service import total_contract_value_base

    assert total_contract_value_base({"current_contract_value": 750_000}) == 750_000.0
    assert total_contract_value_base({"original_contract_value": 600_000}) == 600_000.0
    assert total_contract_value_base({}) is None


def test_contract_currency_validation():
    import pytest as _pytest
    from rbac_backend.models.contract_master import ContractCurrency

    ok = ContractCurrency(currency="usd", conversion_rate=83.0, contract_value=10_000)
    assert ok.currency == "USD"  # normalised
    with _pytest.raises(Exception):
        ContractCurrency(currency="USD", conversion_rate=0)  # non-positive rate
    with _pytest.raises(Exception):
        ContractCurrency(currency="  ", conversion_rate=1.0)  # empty code


def test_bg_required_up_to_custom_rule_and_revised():
    cm = {
        "original_completion_date": COMP,
        "revised_completion_date": datetime(2027, 6, 30),
        "defect_liability_period_days": 90,
        "bg_validity_rules": {"performance": {"basis": "completion", "offset_days": 30}},
    }
    # custom: completion(revised) + 30
    assert bg_required_up_to(cm, "performance") == datetime(2027, 6, 30) + timedelta(days=30)


# --- fakes ----------------------------------------------------------------


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

    async def find_one_and_update(self, query, update, return_document=True):
        d = self.docs.get(query.get("_id"))
        if not d:
            return None
        d.update(update.get("$set", {}))
        return dict(d)

    async def update_one(self, query, update):
        d = await self.find_one(query)
        if d:
            self.docs[d["_id"]].update(update.get("$set", {}))
        return SimpleNamespace(modified_count=1 if d else 0)


class _DB:
    def __init__(self):
        self.contract_master = _Coll()


def _user(org="org-A"):
    return SimpleNamespace(
        id="u1", roles=["orgadmin"], organization_id=org, organizations=[org],
        projects=["proj-A"], account_type="client_user",
    )


# --- service --------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_defaults_current_value():
    svc = ContractMasterService(_DB())
    cm = await svc.create(
        ContractMasterCreate(project_id="proj-A", original_contract_value=5000,
                             original_completion_date=COMP, defect_liability_period_days=365),
        _user(),
    )
    assert cm["current_contract_value"] == 5000
    assert cm["effective_completion_date"] == COMP
    assert cm["dlp_end_date"] == COMP + timedelta(days=365)


@pytest.mark.asyncio
async def test_revise_completion_preserves_original_and_moves_required():
    db = _DB()
    svc = ContractMasterService(db)
    cm = await svc.create(
        ContractMasterCreate(project_id="proj-A", original_completion_date=COMP, defect_liability_period_days=0),
        _user(),
    )
    before_req = bg_required_up_to(cm, "performance")
    assert before_req == COMP
    revised = await svc.revise_completion(cm, datetime(2027, 6, 30), _user())
    assert revised["original_completion_date"] == COMP  # baseline preserved
    assert revised["revised_completion_date"] == datetime(2027, 6, 30)
    assert bg_required_up_to(revised, "performance") == datetime(2027, 6, 30)


@pytest.mark.asyncio
async def test_sync_current_value():
    db = _DB()
    svc = ContractMasterService(db)
    await svc.create(ContractMasterCreate(project_id="proj-A", contract_id="primary", original_contract_value=5000), _user())
    await svc.sync_current_value("org-A", "proj-A", "primary", 5200)
    cm = await svc.get_for_scope("org-A", "proj-A")
    assert cm["current_contract_value"] == 5200


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
async def test_create_denies_cross_tenant():
    payload = ContractMasterCreate(project_id="proj-B", organization_id="org-B")
    with pytest.raises(HTTPException) as exc:
        await create_contract_master(payload, db=_DB(), current_user=_user(org="org-A"), policy=_policy())
    assert exc.value.status_code == 403
