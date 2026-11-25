"""4B-12 (C-13): claim creation and status integrity, over real HTTP, MongoDB and RBAC.

Before 4B-12:
- POST /claims accepted any status and a client-supplied ``origin``: a claim could
  be created already ``agreed``, and the origin unique index could be squatted to
  hide a real potential-claim signal;
- a status change read the claim, checked the move, then wrote on ``{_id}`` alone,
  so two concurrent moves from ``submitted`` both succeeded;
- an update dropped every null, so a wrong notice date could not be removed;
- a claim whose evidence was frozen by escalation could still be edited by PUT;
- a string notice window in ``sla_rules`` ("28") made the SLA routes raise.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _new(**extra: Any) -> dict:
    return {"title": "Late access to Site", "type": "eot", "organization_id": ORG_A, "project_id": PROJ_A1, **extra}


def test_a_claim_cannot_be_created_settled_or_with_an_origin(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            for status in ("agreed", "submitted", "closed", "potential", "disputed"):
                response = await env.call("POST", "org_admin", "/api/claims", json=_new(status=status))
                assert response.status_code == 422, (status, response.status_code, response.text[:200])
            squat = await env.call(
                "POST", "org_admin", "/api/claims", json=_new(origin={"type": "hindrance", "id": "h-1"})
            )
            assert squat.status_code == 422, squat.text[:200]
            assert await env.db.claims.count_documents({"origin.id": "h-1"}) == 0
            for status in ("draft", "notified"):
                created = await env.call("POST", "org_admin", "/api/claims", json=_new(status=status))
                assert created.status_code == 201, created.text[:200]
                assert created.json()["status"] == status

    _run(scenario())


def test_two_concurrent_moves_from_submitted_give_one_success_and_one_conflict(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            assert (await env.db.claims.find_one({"_id": CLAIM}))["status"] == "submitted"
            results = await asyncio.gather(
                env.call("POST", "org_admin", f"/api/claims/{CLAIM}/status", json={"status": "agreed"}),
                env.call("POST", "org_admin", f"/api/claims/{CLAIM}/status", json={"status": "rejected"}),
            )
            codes = sorted(item.status_code for item in results)
            assert codes == [200, 409], [(r.status_code, r.text[:200]) for r in results]
            winner = next(item for item in results if item.status_code == 200).json()["status"]
            assert (await env.db.claims.find_one({"_id": CLAIM}))["status"] == winner

    _run(scenario())


def test_a_cleared_date_is_stored_as_null(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            assert (await env.db.claims.find_one({"_id": CLAIM}))["notice_date"] is not None
            cleared = await env.call("PUT", "org_admin", f"/api/claims/{CLAIM}", json={"notice_date": None})
            assert cleared.status_code == 200, cleared.text[:200]
            assert cleared.json()["notice_date"] is None
            assert (await env.db.claims.find_one({"_id": CLAIM}))["notice_date"] is None
            # A null for a field that cannot be empty is ignored, not stored.
            kept = await env.call("PUT", "org_admin", f"/api/claims/{CLAIM}", json={"title": None})
            assert kept.status_code == 200
            assert (await env.db.claims.find_one({"_id": CLAIM}))["title"] == "Delayed handover of Site"

    _run(scenario())


def test_a_claim_whose_evidence_is_frozen_refuses_edits(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.claims.update_one({"_id": CLAIM}, {"$set": {"evidence_frozen_at": datetime(2026, 11, 1)}})
            response = await env.call("PUT", "org_admin", f"/api/claims/{CLAIM}", json={"description": "changed"})
            assert response.status_code == 409, response.text[:200]
            assert (await env.db.claims.find_one({"_id": CLAIM}))["description"] == "Right of way not handed over."

    _run(scenario())


def test_a_string_notice_window_does_not_break_the_sla_routes_and_is_applied(monkeypatch) -> None:
    """C-13 and C-27. The SLA pages call these routes with no organisation; before 4B-12 the
    subscription check then refused every tenant user (403), and the organisation's own
    windows were never looked up. A string window ("40") raised in timedelta."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.sla_rules.insert_one(
                {"organization_id": ORG_A, "notice_windows": {"eot": "40", "variation": "fourteen", "other": 30.0}}
            )
            event = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=20)
            await env.db.claims.update_one(
                {"_id": CLAIM}, {"$set": {"notice_date": None, "status": "draft", "event_date": event, "awareness_date": None}}
            )
            for persona in ("org_admin", "project_admin"):
                for path in ("/api/sla/upcoming?days=30", "/api/sla/breached"):
                    response = await env.call("GET", persona, path)
                    assert response.status_code == 200, (persona, path, response.status_code, response.text[:200])
            upcoming = (await env.call("GET", "org_admin", "/api/sla/upcoming?days=30")).json()
            time_bar = [item for item in upcoming if item.get("claim_id") == CLAIM and item.get("kind") == "time_bar"]
            assert time_bar, upcoming
            # The organisation's "40" applies (20 days left), not the default window.
            assert time_bar[0]["days_remaining"] in (19, 20), time_bar[0]
            # Another organisation's user still sees nothing of this claim.
            foreign = await env.call("GET", "foreign_org_admin", "/api/sla/upcoming?days=30")
            assert foreign.status_code in (200, 403)
            if foreign.status_code == 200:
                assert all(item.get("claim_id") != CLAIM for item in foreign.json())

    _run(scenario())
