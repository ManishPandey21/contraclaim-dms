"""4B-25: the net-EOT table, through the production app. Real HTTP, real MongoDB, real RBAC.

The Delay tab's table must add up to the engine's figure at every step: with the
overlap undecided (a range), once decided, and with days already granted.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1
from rbac_backend.tests.test_claim_delay_quantum import _hindrance

pytestmark = pytest.mark.integration

BASE = f"/api/claims/{CLAIM}/bench/heads/H1/delay"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _version(env: Any) -> int:
    return int((await env.db.claims.find_one({"_id": CLAIM}))["bench"]["board_version"])


async def _ok(response: Any) -> dict:
    assert response.status_code == 200, (response.status_code, response.text[:400])
    return response.json()


def _adds_up(view: dict) -> tuple[int, int]:
    rows = view["net_table"]
    low = sum(row["days"] for row in rows)
    high = low + sum(row["up_to"] for row in rows)
    assert (low, high) == (view["net"]["low"], view["net"]["high"]), (rows, view["net"])
    return low, high


def test_the_net_eot_table_adds_up_to_the_figure_at_every_step(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            # Employer 1-31 March, Contractor 20 March-10 April: 19 Employer-only days and a 12-day overlap.
            await env.db.delay_events.insert_many(
                [_hindrance("E1", "2026-03-01", "2026-03-31", organization_id=ORG_A, project_id=PROJ_A1),
                 _hindrance("C1", "2026-03-20", "2026-04-10", "contractor", organization_id=ORG_A, project_id=PROJ_A1)]
            )
            await _ok(await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open"))
            for event_id, owner in (("E1", "employer"), ("C1", "contractor")):
                await _ok(await env.call("POST", "org_admin", f"{BASE}/links",
                                         json={"board_version": await _version(env), "target_type": "hindrance", "target_id": event_id}))
                await _ok(await env.call("PUT", "org_admin", f"{BASE}/events/{event_id}",
                                         json={"board_version": await _version(env), "risk_owner": owner, "criticality": "critical"}))
            view = await _ok(await env.call("GET", "org_admin", BASE))
            assert _adds_up(view) == (19, 31)
            assert [row["key"] for row in view["net_table"]] == ["employer_only", "undecided"]

            (overlap,) = view["overlaps"]
            view = await _ok(await env.call(
                "PUT", "org_admin", f"{BASE}/overlaps/{overlap['overlap_id']}",
                json={"board_version": await _version(env), "treatment": "apportioned", "days_to_eot": 5,
                      "basis": "Half the overlap is the Employer's on the programme."},
            ))
            assert _adds_up(view) == (24, 24) and view["net"]["days"] == 24
            apportioned = next(row for row in view["net_table"] if row["key"] == "overlap_apportioned")
            assert apportioned["overlap_id"] == overlap["overlap_id"] and apportioned["days"] == 5

            # The same table, read by another person with the claim permissions.
            other = await _ok(await env.call("GET", "project_admin", BASE))
            assert other["net_table"] == view["net_table"]

    _run(scenario())


def test_each_event_decision_can_be_cleared_on_its_own(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.delay_events.insert_one(
                _hindrance("E1", "2026-03-01", "2026-03-31", organization_id=ORG_A, project_id=PROJ_A1)
            )
            await _ok(await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open"))
            await _ok(await env.call("POST", "org_admin", f"{BASE}/links",
                                     json={"board_version": await _version(env), "target_type": "hindrance", "target_id": "E1"}))
            view = await _ok(await env.call("PUT", "org_admin", f"{BASE}/events/E1",
                                            json={"board_version": await _version(env), "risk_owner": "employer", "criticality": "critical"}))
            assert _adds_up(view) == (31, 31)

            view = await _ok(await env.call("PUT", "org_admin", f"{BASE}/events/E1",
                                            json={"board_version": await _version(env), "clear_fields": ["criticality"]}))
            decision = view["events"][0]["decision"]
            assert decision["risk_owner"] == "employer" and decision["criticality"] is None
            assert _adds_up(view) == (0, 0)  # an event not decided critical counts nothing

            both = await env.call("PUT", "org_admin", f"{BASE}/events/E1",
                                  json={"board_version": await _version(env), "risk_owner": "contractor", "clear_fields": ["risk_owner"]})
            assert both.status_code == 422, both.text[:300]

            view = await _ok(await env.call("PUT", "org_admin", f"{BASE}/events/E1",
                                            json={"board_version": await _version(env), "clear_fields": ["risk_owner"]}))
            decision = view["events"][0]["decision"]
            assert decision["risk_owner"] is None and decision["decided_by"] is None

    _run(scenario())
