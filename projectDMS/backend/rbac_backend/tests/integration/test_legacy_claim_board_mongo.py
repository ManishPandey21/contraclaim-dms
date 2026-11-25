"""4B-19 (D-10, C-23): a claim that predates the board opens safely.
Real HTTP, real MongoDB replica set, real RBAC.

The stored values are written straight to the collection, as a legacy record
would hold them: the API would not accept them today.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from bson.decimal128 import Decimal128

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env

pytestmark = pytest.mark.integration

BENCH = f"/api/claims/{CLAIM}/bench"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _legacy(env: Any, **fields: Any) -> None:
    await env.db.claims.update_one({"_id": CLAIM}, {"$set": fields})


def test_a_legacy_claim_with_unclear_figures_opens_with_them_empty_and_listed(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _legacy(
                env, amount_claimed="12,50,000", eot_days_claimed=30.5, type="variation"
            )
            opened = await env.call("POST", "org_admin", f"{BENCH}/open")
            assert opened.status_code == 200, opened.text[:400]
            head = opened.json()["bench"]["heads"][0]
            assert head["amount_claimed"] is None and head["eot_days_claimed"] is None
            assert head["figures_to_reenter"] == [
                {"field": "amount_claimed", "recorded": "12,50,000"},
                {"field": "eot_days_claimed", "recorded": "30.5"},
            ]
            gate = opened.json()["gate"]
            assert gate["can_lock"] is False
            assert "2 figures from the claim record need re-entry" in gate["blockers"]
            refused = await env.call(
                "POST", "org_admin", f"{BENCH}/lock", json={"board_version": 0}
            )
            assert refused.status_code in (409, 422), refused.text[:300]

            # The text survives on the stored head; the register figure follows the heads.
            stored = await env.db.claims.find_one({"_id": CLAIM})
            assert (
                stored["bench"]["heads"][0]["figures_to_reenter"][0]["recorded"]
                == "12,50,000"
            )
            assert stored["amount_claimed"] is None
            assert stored["eot_days_claimed"] == 30.5, (
                "the register's days are not rewritten by opening the board"
            )

            entered = await env.call(
                "PATCH",
                "org_admin",
                f"{BENCH}/heads/H1",
                json={
                    "board_version": 0,
                    "amount_claimed": 1250000,
                    "figures_not_needed": ["eot_days_claimed"],
                },
            )
            assert entered.status_code == 200, entered.text[:300]
            assert entered.json()["bench"]["heads"][0]["figures_to_reenter"] == []
            assert not any(
                "re-entry" in item for item in entered.json()["gate"]["blockers"]
            )
            assert (await env.db.claims.find_one({"_id": CLAIM}))[
                "amount_claimed"
            ] == 1250000.0

    _run(scenario())


def test_a_legacy_claim_with_clear_figures_in_other_types_carries_them(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _legacy(
                env, amount_claimed=Decimal128("4200000.50"), eot_days_claimed=45.0
            )
            opened = await env.call("POST", "org_admin", f"{BENCH}/open")
            assert opened.status_code == 200, opened.text[:400]
            head = opened.json()["bench"]["heads"][0]
            assert (
                head["amount_claimed"] == 4200000.5 and head["eot_days_claimed"] == 45
            )
            assert head["figures_to_reenter"] == []
            assert not any(
                "re-entry" in item for item in opened.json()["gate"]["blockers"]
            )

    _run(scenario())


def test_a_figure_cannot_be_set_aside_by_an_unknown_name(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _legacy(env, amount_claimed="about 5 lakh")
            assert (
                await env.call("POST", "org_admin", f"{BENCH}/open")
            ).status_code == 200
            refused = await env.call(
                "PATCH",
                "org_admin",
                f"{BENCH}/heads/H1",
                json={"board_version": 0, "figures_not_needed": ["title"]},
            )
            assert refused.status_code == 422
            stored = await env.db.claims.find_one({"_id": CLAIM})
            assert len(stored["bench"]["heads"][0]["figures_to_reenter"]) == 1

    _run(scenario())


def test_a_claim_with_a_status_outside_the_list_opens_read_only_with_the_reason(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _legacy(env, status="settled")
            shown = await env.call("GET", "org_admin", BENCH)
            assert shown.status_code == 200, shown.text[:400]
            body = shown.json()
            assert body["read_only"] is True and body["exists"] is False
            assert '"settled"' in body["read_only_reason"]
            refused = await env.call("POST", "org_admin", f"{BENCH}/open")
            assert refused.status_code == 409, refused.text[:300]
            assert (await env.db.claims.find_one({"_id": CLAIM})).get("bench") is None

    _run(scenario())


def test_a_board_already_open_stops_changing_when_the_status_is_unrecognised(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            assert (
                await env.call("POST", "org_admin", f"{BENCH}/open")
            ).status_code == 200
            await _legacy(env, status="Open")
            shown = await env.call("GET", "org_admin", BENCH)
            assert (
                shown.status_code == 200
                and shown.json()["read_only"] is True
                and shown.json()["exists"] is True
            )
            before = (await env.db.claims.find_one({"_id": CLAIM}))["bench"]
            for method, path, body in (
                (
                    "PATCH",
                    f"{BENCH}/heads/H1",
                    {"board_version": 0, "title": "Changed"},
                ),
                (
                    "POST",
                    f"{BENCH}/heads",
                    {"board_version": 0, "title": "Second head"},
                ),
                ("POST", f"{BENCH}/lock", {"board_version": 0}),
            ):
                refused = await env.call(method, "org_admin", path, json=body)
                assert refused.status_code == 409, (
                    path,
                    refused.status_code,
                    refused.text[:200],
                )
            assert (await env.db.claims.find_one({"_id": CLAIM}))["bench"] == before

    _run(scenario())


# ------------------------------------------------------------- the register
#
# Before 4B-19 one such row made ``GET /api/claims`` answer 422 for the whole
# project, so the claim could not be reached to be opened or corrected.


@pytest.mark.parametrize(
    "fields, amount, days, status, note",
    [
        ({"amount_claimed": "12,50,000"}, None, 146, "draft", '"12,50,000"'),
        ({"eot_days_claimed": 30.5}, 184500000.0, None, "draft", '"30.5"'),
        ({"status": "settled"}, 184500000.0, 146, "settled", '"settled"'),
        (
            {"amount_claimed": "1250000", "eot_days_claimed": 30.0},
            1250000.0,
            30,
            "draft",
            None,
        ),
    ],
)
def test_the_register_lists_a_legacy_claim_and_says_what_needs_attention(
    monkeypatch, fields: dict, amount: Any, days: Any, status: str, note: Any
) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _legacy(
                env, amount_claimed=184500000.0, eot_days_claimed=146, status="draft"
            )
            await _legacy(env, **fields)
            listed = await env.call(
                "GET", "org_admin", "/api/claims", params={"project_id": "proj-a1"}
            )
            assert listed.status_code == 200, listed.text[:400]
            row = next(item for item in listed.json() if item["_id"] == CLAIM)
            one = await env.call("GET", "org_admin", f"/api/claims/{CLAIM}")
            assert one.status_code == 200, one.text[:400]
            for shown in (row, one.json()):
                assert (
                    shown["amount_claimed"] == amount
                    and shown["eot_days_claimed"] == days
                )
                assert shown["status"] == status
                if note is None:
                    assert shown["record_notes"] == []
                else:
                    assert (
                        len(shown["record_notes"]) == 1
                        and note in shown["record_notes"][0]
                    )
            # Reading changes nothing in the stored record.
            stored = await env.db.claims.find_one({"_id": CLAIM})
            for key, value in fields.items():
                assert stored[key] == value

    _run(scenario())


def test_a_claim_with_an_unrecognised_status_is_offered_no_move_and_none_is_accepted(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _legacy(env, status="settled")
            shown = await env.call("GET", "org_admin", f"/api/claims/{CLAIM}")
            assert shown.json()["allowed_status_transitions"] == []
            moved = await env.call(
                "POST",
                "org_admin",
                f"/api/claims/{CLAIM}/status",
                json={"status": "notified"},
            )
            assert moved.status_code in (409, 422), moved.text[:300]
            assert (await env.db.claims.find_one({"_id": CLAIM}))["status"] == "settled"

    _run(scenario())
