"""Insurance Register service: status logic, summary, alerts, duplicate guard."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from backend.rbac_backend.models.insurance import InsuranceCreate, InsuranceStatus
from backend.rbac_backend.services.insurance_service import (
    InsuranceService,
    days_remaining,
    due_alert_types,
    insurance_summary,
    status_of,
)

pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend():
    return "asyncio"


NOW = datetime(2026, 6, 30)


def _policy(days: int) -> Dict[str, Any]:
    return {"date_of_expiry": NOW + timedelta(days=days), "sum_insured": 100.0}


# --- pure logic -----------------------------------------------------------

def test_status_active_beyond_30_days():
    assert status_of(_policy(40), NOW) == InsuranceStatus.ACTIVE.value


def test_status_expiring_within_30_days():
    assert status_of(_policy(20), NOW) == InsuranceStatus.EXPIRING_SOON.value
    assert status_of(_policy(0), NOW) == InsuranceStatus.EXPIRING_SOON.value


def test_status_expired():
    assert status_of(_policy(-1), NOW) == InsuranceStatus.EXPIRED.value


def test_days_remaining():
    assert days_remaining(_policy(18), NOW) == 18
    assert days_remaining(_policy(-3), NOW) == -3
    assert days_remaining({}, NOW) is None


def test_alert_thresholds_fire_on_exact_days():
    assert due_alert_types(_policy(90), NOW) == ["T-90"]
    assert due_alert_types(_policy(30), NOW) == ["T-30"]
    assert due_alert_types(_policy(7), NOW) == ["T-7"]
    assert due_alert_types(_policy(0), NOW) == ["expiry"]
    assert due_alert_types(_policy(-1), NOW) == ["expired"]
    assert due_alert_types(_policy(45), NOW) == []  # not a threshold


def test_summary_counts_by_status():
    items = [_policy(40), _policy(20), _policy(5), _policy(-2)]
    out = insurance_summary(items, NOW)
    assert out["total"] == 4
    assert out["active"] == 1
    assert out["expiring_soon"] == 2
    assert out["expired"] == 1
    assert out["total_sum_insured"] == 400.0


# --- duplicate guard (fake db) --------------------------------------------

class _Insurance:
    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []

    async def find_one(self, query: Dict[str, Any]):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None

    async def insert_one(self, doc: Dict[str, Any]):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("_id"))


class _DB:
    def __init__(self) -> None:
        self.insurance_policies = _Insurance()


def _user():
    return SimpleNamespace(id="u1", organization_id="org-A", first_name="Mei", last_name="Lopez", email="m@x.io")


async def test_create_then_duplicate_policy_is_rejected():
    from fastapi import HTTPException

    svc = InsuranceService(_DB())
    # Relative to the REAL clock, not to the frozen `NOW`.
    #
    # The pure-logic rows above pass `NOW` into `status_of` and are therefore
    # time-independent. `svc.create` takes no `now` and uses `datetime.utcnow()`,
    # so an expiry built from `NOW` drifts towards the wall clock at a day per
    # day. `NOW + 100 days` is 2026-10-08, which on 2026-09-08 is exactly
    # `EXPIRING_SOON_DAYS` away: this assertion flipped from `active` to
    # `expiring_soon` overnight and would have stayed red for good. Found by
    # R-A8N's full-suite run, unrelated to that phase's findings, and fixed
    # where it lives rather than left to fail every suite from now on.
    payload = InsuranceCreate(
        project_id="p1", contract_id="C1", insurance_type="Marine Cargo Insurance",
        policy_number="POL-1", date_of_expiry=datetime.utcnow() + timedelta(days=365),
    )
    created = await svc.create(payload, _user())
    assert created["status"] == InsuranceStatus.ACTIVE.value
    assert created["created_by_name"] == "Mei Lopez"

    # Same contract + type + number -> 409.
    with pytest.raises(HTTPException) as exc:
        await svc.create(payload, _user())
    assert exc.value.status_code == 409


async def test_same_number_different_type_is_allowed():
    svc = InsuranceService(_DB())
    base = dict(project_id="p1", contract_id="C1", policy_number="POL-9",
                date_of_expiry=NOW + timedelta(days=100))
    await svc.create(InsuranceCreate(insurance_type="Marine Cargo Insurance", **base), _user())
    # Different insurance_type with the same number must not collide.
    out = await svc.create(InsuranceCreate(insurance_type="Labour Licence", **base), _user())
    assert out["policy_number"] == "POL-9"
