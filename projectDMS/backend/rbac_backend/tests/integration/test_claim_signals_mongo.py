"""4B-13 (C-12): potential-claim signals, over real HTTP, MongoDB and RBAC.

Before 4B-13:
- a notice letter drafted from claim CLM-7 is routed to the claims skill, so its
  Letter Bench was listed as "a letter may be a claim not yet raised", and
  accepting it created a second claim;
- raising re-read nothing: a hindrance taken off the critical path (or moved to
  the Contractor, or archived) after the list was read still became a claim;
- for a board with no organisation, the duplicate lookup searched ``""`` while
  the create stored the user's organisation, so a second accept made a second
  claim (or, with the unique index, a 500).

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest

from rbac_backend.services.skills.system_skills import CLAIMS_SKILL_KEY, EOT_SKILL_KEY
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import (
    ORG_A,
    PROJ_A1,
)

pytestmark = pytest.mark.integration

SIGNALS = "/api/claims/bench/signals"
RAISE = "/api/claims/bench/potential"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _board(
    letter_id: str,
    *,
    trigger: Any = None,
    organization_id: Any = ORG_A,
    skill: str = CLAIMS_SKILL_KEY,
) -> dict:
    return {
        "letter_id": letter_id,
        "organization_id": organization_id,
        "project_id": PROJ_A1,
        "role": "contractor",
        "chain": [{"skill_key": skill}],
        "trigger": trigger,
        "incoming_meta": {
            "letter_no": f"IN/{letter_id}",
            "subject": "Access to Site",
            "letter_date": datetime(2026, 9, 1),
        },
        "stage": "dissecting",
    }


def _hindrance(_id: str, **extra: Any) -> dict:
    return {
        "_id": _id,
        "hindrance_ref": f"H-{_id}",
        "title": "Right of way not handed over",
        "start_date": datetime(2026, 9, 1),
        "critical_path_impact": True,
        "responsibility": "employer",
        "archived_at": None,
        "organization_id": ORG_A,
        "project_id": PROJ_A1,
        **extra,
    }


async def _as_superadmin(env: Any, method: str, path: str, org: Any = ORG_A, **kwargs: Any) -> Any:
    """Letter Bench boards are read with ``drafting.request.view``, which a tenant role holds
    only through a drafting-expert allocation; Super Admin reads them."""
    headers = env.headers("superadmin")
    if org:
        headers["X-Org-Id"] = org
    else:
        headers.pop("X-Proj-Id", None)  # nothing selected in the navbar
    return await env.client.request(method, path, headers=headers, **kwargs)


def _claim_id(response: Any) -> str:
    claim = response.json()["claim"]
    return str(claim.get("id") or claim.get("_id"))


def _code(response: Any) -> str:
    body = response.json()
    detail = body.get("detail", body)
    return (detail.get("code") if isinstance(detail, dict) else None) or body.get("code") or ""


def test_a_notice_letter_drafted_from_a_claim_is_not_a_signal_and_cannot_be_raised(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            claim_trigger = {
                "type": "claim",
                "id": CLAIM,
                "label": "Claim CLM-001",
                "claim_type": "eot",
            }
            await env.db.letter_position_boards.insert_many(
                [
                    _board("L-from-claim", trigger=claim_trigger, skill=EOT_SKILL_KEY),
                    _board("L-incoming"),
                    _board(
                        "L-variation",
                        trigger={
                            "type": "variation",
                            "id": "v1",
                            "label": "Variation 1",
                        },
                    ),
                ]
            )
            listed = await _as_superadmin(env, "GET", SIGNALS)
            assert listed.status_code == 200, listed.text[:300]
            ids = {item["source_id"] for item in listed.json() if item["source_type"] == "letter_bench"}
            assert ids == {"L-incoming", "L-variation"}, ids

            before = await env.db.claims.count_documents({})
            refused = await _as_superadmin(
                env,
                "POST",
                RAISE,
                json={"source_type": "letter_bench", "source_id": "L-from-claim"},
            )
            assert refused.status_code == 422, refused.text[:300]
            assert _code(refused) == "signal_not_eligible"
            assert await env.db.claims.count_documents({}) == before

    _run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        {"critical_path_impact": False},
        {"responsibility": "contractor"},
        {"archived_at": datetime(2026, 10, 1)},
    ],
    ids=["off-critical-path", "contractor-risk", "archived"],
)
def test_a_hindrance_that_is_no_longer_eligible_is_refused_on_raise(monkeypatch, change: dict) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.delay_events.insert_one(_hindrance("h-1"))
            listed = await env.call("GET", "org_admin", SIGNALS)
            assert "h-1" in {item["source_id"] for item in listed.json()}, listed.text[:300]
            # The hindrance changes after the list was read.
            await env.db.delay_events.update_one({"_id": "h-1"}, {"$set": change})
            refused = await env.call(
                "POST",
                "org_admin",
                RAISE,
                json={"source_type": "hindrance", "source_id": "h-1"},
            )
            assert refused.status_code == 422, refused.text[:300]
            assert _code(refused) == "signal_not_eligible"
            assert await env.db.claims.count_documents({"origin.id": "h-1"}) == 0

    _run(scenario())


def test_an_eligible_hindrance_is_raised_once(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.delay_events.insert_one(_hindrance("h-2", responsibility="concurrent"))
            first = await env.call(
                "POST",
                "org_admin",
                RAISE,
                json={"source_type": "hindrance", "source_id": "h-2"},
            )
            assert first.status_code == 200, first.text[:300]
            assert first.json()["created"] is True
            second = await env.call(
                "POST",
                "project_admin",
                RAISE,
                json={"source_type": "hindrance", "source_id": "h-2"},
            )
            assert second.status_code == 200, second.text[:300]
            assert second.json()["created"] is False
            assert _claim_id(second) == _claim_id(first)
            assert await env.db.claims.count_documents({"origin.id": "h-2"}) == 1
            listed = await env.call("GET", "org_admin", SIGNALS)
            assert "h-2" not in {item["source_id"] for item in listed.json()}

    _run(scenario())


def test_two_accepts_of_an_organisation_less_board_return_one_claim(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.letter_position_boards.insert_many(
                [
                    _board("L-no-org", organization_id=None),
                    _board("L-no-org-2", organization_id=None),
                ]
            )
            # With an organisation selected: the claim takes it, and a second accept finds it.
            payload = {"source_type": "letter_bench", "source_id": "L-no-org"}
            first = await _as_superadmin(env, "POST", RAISE, json=payload)
            assert first.status_code == 200, first.text[:300]
            second = await _as_superadmin(env, "POST", RAISE, json=payload)
            assert second.status_code == 200, second.text[:300]
            assert (first.json()["created"], second.json()["created"]) == (True, False)
            assert _claim_id(second) == _claim_id(first)
            rows = await env.db.claims.find({"origin.id": "L-no-org"}).to_list(10)
            assert len(rows) == 1 and rows[0]["organization_id"] == ORG_A

            # With none selected there is no organisation to give the claim: the selection gate
            # refuses first (400 selection_required); the service refuses too (422, unit test).
            payload = {"source_type": "letter_bench", "source_id": "L-no-org-2"}
            unscoped = await _as_superadmin(env, "POST", RAISE, org=None, json=payload)
            assert unscoped.status_code == 400, unscoped.text[:300]
            assert await env.db.claims.count_documents({"origin.id": "L-no-org-2"}) == 0

            # Two concurrent accepts, then a third: still one claim. The origin unique index is
            # what production has (migration 20261116_0001); apply the real migration.
            from rbac_backend.migrations.v20261116_0001_claim_bench_indexes import (
                upgrade,
            )

            await upgrade(env.db, False)
            results = await asyncio.gather(*(_as_superadmin(env, "POST", RAISE, json=payload) for _ in range(2)))
            assert all(item.status_code == 200 for item in results), [item.text[:300] for item in results]
            again = await _as_superadmin(env, "POST", RAISE, json=payload)
            assert again.status_code == 200 and again.json()["created"] is False, again.text[:300]
            assert sorted(item.json()["created"] for item in results) == [False, True]
            assert len({_claim_id(item) for item in [*results, again]}) == 1
            assert await env.db.claims.count_documents({"origin.id": "L-no-org-2"}) == 1

    _run(scenario())


def test_signal_routes_are_off_when_the_bench_is_off(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, mode="off") as env:
            await env.db.delay_events.insert_one(_hindrance("h-4"))
            assert (await env.call("GET", "org_admin", SIGNALS)).status_code == 404
            raised = await env.call(
                "POST",
                "org_admin",
                RAISE,
                json={"source_type": "hindrance", "source_id": "h-4"},
            )
            assert raised.status_code == 404, raised.text[:200]
            assert await env.db.claims.count_documents({"origin.id": "h-4"}) == 0

    _run(scenario())
