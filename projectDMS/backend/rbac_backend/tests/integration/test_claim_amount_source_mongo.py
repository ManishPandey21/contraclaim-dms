"""4B-17 (D-14, C-07): one authoritative amount, over real HTTP, MongoDB and RBAC.

Before 4B-17:
- PUT /claims accepted ``amount_claimed`` on a claim with a board (the register sends
  it on every save), so the register and the heads could disagree;
- the board's roll-up ran only ``if amounts``: clearing every head amount left the
  register's old total in place;
- removing a head's last quantum line left the head with the old build-up total;
- a typed head amount was accepted over a quantum build-up.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env

pytestmark = pytest.mark.integration

BENCH = f"/api/claims/{CLAIM}/bench"
LINES = f"{BENCH}/heads/H1/quantum/lines"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


async def _open(env: Any) -> int:
    opened = await env.call("POST", "org_admin", f"{BENCH}/open")
    assert opened.status_code == 200, opened.text[:300]
    return int(opened.json()["bench"]["board_version"])


async def _claim(env: Any) -> dict:
    return await env.db.claims.find_one({"_id": CLAIM})


async def _head(env: Any) -> dict:
    return (await _claim(env))["bench"]["heads"][0]


async def _version(env: Any) -> int:
    return int((await _claim(env))["bench"]["board_version"])


def _line(version: int, amount: float) -> dict:
    return {"board_version": version, "category": "direct", "description": "Idle plant, March", "amount": amount}


def test_the_register_cannot_change_the_amount_or_days_of_a_claim_with_a_board(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _open(env)
            stored = await _claim(env)
            for change in ({"amount_claimed": 1.0}, {"eot_days_claimed": 10}):
                refused = await env.call("PUT", "org_admin", f"/api/claims/{CLAIM}", json=change)
                assert refused.status_code == 409, (change, refused.text[:300])
                assert _code(refused) == "claim_amount_from_board"
            # The register sends every field on save: unchanged values pass, and nothing moves.
            same = await env.call(
                "PUT",
                "org_admin",
                f"/api/claims/{CLAIM}",
                json={
                    "title": "Delayed handover of Site (revised)",
                    "amount_claimed": stored["amount_claimed"],
                    "eot_days_claimed": stored["eot_days_claimed"],
                },
            )
            assert same.status_code == 200, same.text[:300]
            after = await _claim(env)
            assert (after["amount_claimed"], after["eot_days_claimed"]) == (
                stored["amount_claimed"],
                stored["eot_days_claimed"],
            )
            assert after["title"] == "Delayed handover of Site (revised)"

            # A claim with no board is still edited in the register.
            await env.db.claims.update_one({"_id": CLAIM}, {"$set": {"bench": None}})
            free = await env.call("PUT", "org_admin", f"/api/claims/{CLAIM}", json={"amount_claimed": 1.0})
            assert free.status_code == 200, free.text[:300]

    _run(scenario())


def test_a_frozen_claim_refuses_every_register_edit(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _open(env)
            await env.db.claims.update_one({"_id": CLAIM}, {"$set": {"evidence_frozen_at": datetime(2026, 11, 1)}})
            for change in ({"amount_claimed": 1.0}, {"description": "changed"}):
                refused = await env.call("PUT", "org_admin", f"/api/claims/{CLAIM}", json=change)
                assert refused.status_code == 409, refused.text[:300]

    _run(scenario())


def test_clearing_every_head_amount_clears_the_claims_amount(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            version = await _open(env)
            assert (await _claim(env))["amount_claimed"] == 184500000.0
            cleared = await env.call(
                "PATCH", "org_admin", f"{BENCH}/heads/H1", json={"board_version": version, "clear_amount": True}
            )
            assert cleared.status_code == 200, cleared.text[:300]
            assert (await _head(env))["amount_claimed"] is None
            assert (await _claim(env))["amount_claimed"] is None

    _run(scenario())


def test_the_head_amount_follows_its_lines_and_clears_with_the_last_one(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _open(env)
            first = await env.call("POST", "org_admin", LINES, json=_line(await _version(env), 250000))
            assert first.status_code == 200, first.text[:300]
            second = await env.call("POST", "org_admin", LINES, json=_line(await _version(env), 50000.5))
            assert second.status_code == 200, second.text[:300]
            assert (await _head(env))["amount_claimed"] == 300000.5
            assert (await _claim(env))["amount_claimed"] == 300000.5

            line_ids = [line["line_id"] for line in (await _head(env))["quantum_lines"]]
            for line_id in line_ids:
                removed = await env.call(
                    "POST", "org_admin", f"{LINES}/{line_id}/remove", json={"board_version": await _version(env)}
                )
                assert removed.status_code == 200, removed.text[:300]
            assert (await _head(env))["quantum_lines"] == []
            assert (await _head(env))["amount_claimed"] is None
            assert (await _claim(env))["amount_claimed"] is None

    _run(scenario())


def test_a_typed_head_amount_is_refused_while_the_head_has_lines(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _open(env)
            added = await env.call("POST", "org_admin", LINES, json=_line(await _version(env), 250000))
            assert added.status_code == 200, added.text[:300]
            for change in ({"amount_claimed": 999.0}, {"clear_amount": True}):
                refused = await env.call(
                    "PATCH", "org_admin", f"{BENCH}/heads/H1", json={"board_version": await _version(env), **change}
                )
                assert refused.status_code == 409, (change, refused.text[:300])
                assert _code(refused) == "head_amount_from_lines"
            assert (await _head(env))["amount_claimed"] == 250000.0
            # Other fields of the head can still be edited.
            renamed = await env.call(
                "PATCH",
                "org_admin",
                f"{BENCH}/heads/H1",
                json={"board_version": await _version(env), "title": "Delayed handover (revised)", "amount_claimed": 250000.0},
            )
            assert renamed.status_code == 200, renamed.text[:300]

    _run(scenario())
