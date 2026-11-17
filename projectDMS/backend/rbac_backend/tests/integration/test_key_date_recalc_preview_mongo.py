"""KD-1 dry-run (owner decision 2026-11-17): what "Recalculate project" would change, changing nothing.
Real HTTP, real MongoDB replica set, real RBAC.

A project with a main contract and a depot contract (own LOA and FIDIC-style basis), and a
third contract with no LOA. Stored depot dates were calculated from the main contract (the
KD-1 defect). The dry-run must show stored and proposed dates, each contract's basis and its
source, what relies on each milestone, skip the contract with no LOA - and write nothing. A
recalculation run afterwards writes exactly what the dry-run proposed.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict

import pytest
from bson import json_util

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

LOA = datetime(2026, 1, 5)
DEPOT_LOA = datetime(2026, 4, 15)
PREVIEW = f"/api/key-dates/recalculate/preview?project_id={PROJ_A1}&organization_id={ORG_A}"
RUN = f"/api/key-dates/recalculate?project_id={PROJ_A1}&organization_id={ORG_A}"
# Data only: the policy layer writes an authorisation audit record for every authorised request.
COLLECTIONS = ("key_date_milestones", "contract_master", "key_date_eot_submission_items", "claim_head_links", "projects")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _fingerprint(db: Any) -> Dict[str, str]:
    out = {}
    for name in COLLECTIONS:
        rows = await db[name].find({}).sort("_id", 1).to_list(length=None)
        out[name] = hashlib.sha256(json_util.dumps(rows, sort_keys=True).encode()).hexdigest()
    return out


def _milestone(_id: str, contract: str, calculated: datetime, revision: int = 0) -> Dict[str, Any]:
    return {"_id": _id, "organization_id": ORG_A, "project_id": PROJ_A1, "contract_id": contract, "title": _id,
            "contractual_week_number": 4, "calculated_key_date": calculated, "original_planned_key_date": calculated,
            "current_approved_key_date": calculated, "current_revision": revision}


def test_the_dry_run_shows_every_change_and_writes_nothing(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.contract_master.insert_many([
                {"_id": "cm-main", "organization_id": ORG_A, "project_id": PROJ_A1, "contract_id": "primary",
                 "contract_start_date": LOA, "week_basis": "loa_plus_weeks"},
                {"_id": "cm-depot", "organization_id": ORG_A, "project_id": PROJ_A1, "contract_id": "pkg-depot",
                 "contract_start_date": DEPOT_LOA, "week_basis": "loa_plus_weeks_minus_1"},
            ])
            wrong = LOA + timedelta(days=28)  # from the main contract: the KD-1 defect
            await env.db.key_date_milestones.insert_many([
                _milestone("kd-main", "primary", LOA + timedelta(days=28)),
                _milestone("kd-depot", "pkg-depot", wrong),
                _milestone("kd-depot-revised", "pkg-depot", wrong, revision=1),
                _milestone("kd-civil", "pkg-civil", wrong),
            ])
            await env.db.key_date_eot_submission_items.insert_one({"_id": "item-1", "key_date_id": "kd-depot", "eot_submission_id": "eot-7"})
            await env.db.claim_head_links.insert_one({"_id": "link-1", "claim_id": "c", "head_id": "H1", "target_type": "eot_submission",
                                                      "target_id": "eot-7", "status": "active"})
            before = await _fingerprint(env.db)

            response = await env.call("GET", "org_admin", PREVIEW)
            assert response.status_code == 200, response.text[:400]
            preview = response.json()
            assert await _fingerprint(env.db) == before, "the dry-run changed the database"

            assert preview["dry_run"] is True
            assert preview["bases"]["primary"]["source"] == "contract_master"
            assert preview["bases"]["pkg-depot"]["week_basis"] == "loa_plus_weeks_minus_1"
            assert preview["bases"]["pkg-depot"]["start_date"].startswith("2026-04-15")
            assert "no contract start date" in preview["bases"]["pkg-civil"]["skipped"]
            # The fixture project already holds one milestone with no week number: skipped, not a crash.
            assert preview["summary"] == {"scanned": 5, "would_change": 2, "unchanged": 1, "skipped": 2,
                                          "baseline_kept_under_revision": 1}
            rows = {row["milestone_id"]: row for row in preview["milestones"]}
            assert any(row["skipped_reason"] == "No contractual week number: nothing to calculate from."
                       for row in preview["milestones"])
            rows = {key: row for key, row in rows.items() if key.startswith("kd-")}
            depot = rows["kd-depot"]
            assert depot["proposed"]["calculated_key_date"].startswith("2026-05-06")  # DEPOT_LOA + (4 - 1) weeks
            assert set(depot["changes"]) == {"calculated_key_date", "original_planned_key_date", "current_approved_key_date"}
            assert depot["dependencies"]["eot_submission_items"] == 1 and depot["dependencies"]["claim_links"] == 1
            revised = rows["kd-depot-revised"]
            assert set(revised["proposed"]) == {"calculated_key_date"} and revised["changes"] == ["calculated_key_date"]
            assert rows["kd-main"]["changes"] == [] and rows["kd-civil"]["proposed"] is None

            # A recalculation writes exactly what the dry-run proposed.
            ran = await env.call("POST", "org_admin", RUN)
            assert ran.status_code == 200, ran.text[:400]
            for milestone_id, row in rows.items():
                stored = await env.db.key_date_milestones.find_one({"_id": milestone_id})
                for field, value in (row["proposed"] or {}).items():
                    assert stored[field] == datetime.fromisoformat(value.replace("Z", "")), (milestone_id, field)
            assert (await env.db.key_date_milestones.find_one({"_id": "kd-civil"}))["calculated_key_date"] == wrong

    _run(scenario())


def test_the_dry_run_needs_the_recalculation_authority(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            refused = await env.call("GET", "viewer", PREVIEW)
            assert refused.status_code in (401, 403), refused.text[:300]

    _run(scenario())
