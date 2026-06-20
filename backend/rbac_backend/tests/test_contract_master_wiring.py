"""CM-2: Variation + Bank Guarantee read from / write back to the Contract Master."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from rbac_backend.models.bank_guarantee import BankGuaranteeCreate
from rbac_backend.models.contract_master import ContractMasterCreate
from rbac_backend.models.variation import VariationCreate
from rbac_backend.services.bank_guarantee_service import BankGuaranteeService, extension_required
from rbac_backend.services.contract_master_service import ContractMasterService
from rbac_backend.services.variation_service import VariationService

NOW = datetime(2026, 1, 1)
COMP = datetime(2026, 12, 31)


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_k):
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
        d = await self.find_one(query)
        if d:
            self.docs[d["_id"]].update(update.get("$set", {}))
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
        self.contract_master = _Coll()
        self.variations = _Coll()
        self.bank_guarantees = _Coll()


def _user(org="org-A"):
    return SimpleNamespace(id="u1", roles=["orgadmin"], organization_id=org, organizations=[org], projects=["proj-A"], account_type="client_user")


async def _seed_cm(db, **fields):
    await ContractMasterService(db).create(
        ContractMasterCreate(project_id="proj-A", contract_id="primary", organization_id="org-A", **fields),
        _user(),
    )


@pytest.mark.asyncio
async def test_variation_defaults_value_and_syncs_current():
    db = _DB()
    await _seed_cm(db, original_contract_value=5000)
    v = await VariationService(db).create(
        VariationCreate(project_id="proj-A", variation_number="VO-1", variation_type="positive",
                        approved_amount=200, status="approved"),
        _user(),
    )
    assert v["original_contract_value"] == 5000  # defaulted from the master
    cm = await ContractMasterService(db).get_for_scope("org-A", "proj-A")
    assert cm["current_contract_value"] == 5200  # original + approved variation


@pytest.mark.asyncio
async def test_bg_computes_required_up_to_from_master():
    db = _DB()
    await _seed_cm(db, original_completion_date=COMP, defect_liability_period_days=0)
    bg = await BankGuaranteeService(db).create(
        BankGuaranteeCreate(project_id="proj-A", bg_type="performance", bg_number="BG-1"),
        _user(),
    )
    # performance → dlp_end basis, DLP 0 → completion date
    assert bg["contractual_required_up_to"] == COMP


@pytest.mark.asyncio
async def test_revise_completion_cascades_to_bg():
    db = _DB()
    await _seed_cm(db, original_completion_date=NOW + timedelta(days=5), defect_liability_period_days=0)
    svc = BankGuaranteeService(db)
    bg = await svc.create(
        BankGuaranteeCreate(project_id="proj-A", bg_type="performance", bg_number="BG-2",
                            bg_expiry_date=NOW + timedelta(days=10)),
        _user(),
    )
    # required-up-to computed = completion (NOW+5); expiry NOW+10 >= required → no extension
    assert extension_required(bg, NOW) is False

    # Now move the contract completion forward (EOT) → required-up-to moves past expiry.
    cm = await ContractMasterService(db).get_for_scope("org-A", "proj-A")
    await ContractMasterService(db).revise_completion(cm, NOW + timedelta(days=120), _user())
    changed = await svc.recompute_required_dates("org-A", "proj-A", "primary", _user())
    assert changed == 1
    refreshed = await svc.get(bg["_id"])
    assert refreshed["contractual_required_up_to"] == NOW + timedelta(days=120)
    assert refreshed["extension_required"] is True  # expiry now before required
