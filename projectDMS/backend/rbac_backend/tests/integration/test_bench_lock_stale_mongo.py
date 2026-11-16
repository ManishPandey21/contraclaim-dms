"""4B-16 (D-22, C-17): lock, stale tests and unlock, over real HTTP, MongoDB and RBAC.

Before 4B-16:
- the lock gate counted stale tests but never blocked on them, so a board could be
  locked on tests that no longer matched the claim's facts;
- marking tests stale after a fact change was a compare-and-set on the board
  version read before the change: a board write in between made it miss, silently,
  and the board stayed locked;
- unlock needed claim-edit only, took no reason and wrote no audit event.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest

from rbac_backend.models.claim import ELEMENT_KEYS
from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import _add_actor
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env

pytestmark = pytest.mark.integration

BENCH = f"/api/claims/{CLAIM}/bench"
EDITOR = "claim_editor_only"
REASON = "The Engineer's letter of 3 May changes the event date."


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _ready(env: Any, *, stale: bool, locked: bool = False) -> int:
    """A board with one tested, fully decided, classified head. Returns its version."""
    opened = await env.call("POST", "org_admin", f"{BENCH}/open")
    assert opened.status_code == 200, opened.text[:300]
    decided = {
        key: {"state": "supported", "decided_by": "user-org_admin", "decided_at": datetime(2026, 5, 1)}
        for key in ELEMENT_KEYS
    }
    claim = await env.db.claims.find_one({"_id": CLAIM})
    version = int(claim["bench"]["board_version"]) + 1
    await env.db.claims.update_one(
        {"_id": CLAIM},
        {
            "$set": {
                "bench.heads.0.elements": decided,
                "bench.heads.0.classification": "compensable",
                "bench.heads.0.step_id": "step-h1",
                "bench.heads.0.test_stale": stale,
                "bench.stage": "locked" if locked else "heads",
                "bench.locked_by": "user-org_admin" if locked else None,
                "bench.locked_at": datetime(2026, 5, 2) if locked else None,
                "bench.board_version": version,
            }
        },
    )
    await env.db.skill_steps.insert_one({"_id": "step-h1", "step_id": "step-h1", "stale": stale})
    return version


async def _board(env: Any) -> dict:
    return (await env.db.claims.find_one({"_id": CLAIM}))["bench"]


def test_a_board_with_a_stale_test_cannot_be_locked(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            version = await _ready(env, stale=True)
            refused = await env.call("POST", "org_admin", f"{BENCH}/lock", json={"board_version": version})
            assert refused.status_code == 409, refused.text[:300]
            assert "re-test" in refused.text
            assert (await _board(env))["stage"] == "heads"
            gate = (await env.call("GET", "org_admin", BENCH)).json()["gate"]
            assert gate["stale_tests"] == 1 and gate["can_lock"] is False

            # The same board with a current test locks: the stale test was the only blocker.
            await env.db.claims.update_one({"_id": CLAIM}, {"$set": {"bench.heads.0.test_stale": False}})
            locked = await env.call("POST", "org_admin", f"{BENCH}/lock", json={"board_version": version})
            assert locked.status_code == 200, locked.text[:300]
            assert (await _board(env))["stage"] == "locked"

    _run(scenario())


def test_a_board_write_between_a_fact_change_and_staling_cannot_leave_the_board_locked(monkeypatch) -> None:
    """Deterministic interleaving: the claim the router read has board version v; another
    user's board write lands (v+1) before the stale marking runs."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.routers.claims import mark_bench_tests_stale

            version = await _ready(env, stale=False, locked=True)
            read_by_the_router = await env.db.claims.find_one({"_id": CLAIM})
            await env.db.claims.update_one(
                {"_id": CLAIM, "bench.board_version": version},
                {"$set": {"bench.heads.0.elements.event.note": "added by someone else"}, "$inc": {"bench.board_version": 1}},
            )
            updated = await mark_bench_tests_stale(env.db, read_by_the_router)
            assert updated is not None
            board = await _board(env)
            assert board["stage"] == "heads" and board["locked_by"] is None
            assert board["heads"][0]["test_stale"] is True
            assert board["heads"][0]["elements"]["event"]["note"] == "added by someone else"  # their write kept
            assert board["board_version"] == version + 2
            assert (await env.db.skill_steps.find_one({"step_id": "step-h1"}))["stale"] is True

    _run(scenario())


def test_a_fact_change_reopens_a_locked_board_and_concurrent_board_saves_conflict(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            version = await _ready(env, stale=False, locked=True)
            # A fact change and a board write prepared on the locked version, at once.
            fact, board_write = await asyncio.gather(
                env.call("PUT", "org_admin", f"/api/claims/{CLAIM}", json={"event_date": "2026-03-05T00:00:00"}),
                env.call(
                    "PUT",
                    "org_admin",
                    f"{BENCH}/heads/H1/elements/event",
                    json={"board_version": version, "state": "weak"},
                ),
            )
            assert fact.status_code == 200, fact.text[:300]
            assert board_write.status_code in (200, 409), board_write.text[:300]
            board = await _board(env)
            # Whichever order they landed in, the board is reopened and the test is stale.
            assert board["stage"] == "heads"
            assert board["heads"][0]["test_stale"] is True
            relock = await env.call("POST", "org_admin", f"{BENCH}/lock", json={"board_version": board["board_version"]})
            assert relock.status_code == 409 and "re-test" in relock.text, relock.text[:300]

    _run(scenario())


def test_unlock_needs_claim_manage_and_a_reason_and_is_audited(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _add_actor(env.db, EDITOR, EDITOR, ["dms.claim.view", "dms.claim.edit"])
            version = await _ready(env, stale=False)
            # Lock needs claim-edit only.
            locked = await env.call("POST", EDITOR, f"{BENCH}/lock", json={"board_version": version})
            assert locked.status_code == 200, locked.text[:300]
            version += 1

            denied = await env.call("POST", EDITOR, f"{BENCH}/unlock", json={"board_version": version, "reason": REASON})
            assert denied.status_code == 403, denied.text[:300]
            no_reason = await env.call("POST", "org_admin", f"{BENCH}/unlock", json={"board_version": version})
            assert no_reason.status_code == 422, no_reason.text[:300]
            blank = await env.call(
                "POST", "org_admin", f"{BENCH}/unlock", json={"board_version": version, "reason": "          .  "}
            )
            assert blank.status_code == 422, blank.text[:300]
            assert (await _board(env))["stage"] == "locked"

            reopened = await env.call(
                "POST", "org_admin", f"{BENCH}/unlock", json={"board_version": version, "reason": REASON}
            )
            assert reopened.status_code == 200, reopened.text[:300]
            assert (await _board(env))["stage"] == "heads"
            again = await env.call(
                "POST", "org_admin", f"{BENCH}/unlock", json={"board_version": version + 1, "reason": REASON}
            )
            assert again.status_code == 409, again.text[:300]

            events = await env.db.audit_events.find(
                {"resource_id": CLAIM, "action": {"$in": ["claim.bench.locked", "claim.bench.unlocked"]}}
            ).to_list(10)
            by_action = {event["action"]: event for event in events}
            assert len(events) == 2, [event["action"] for event in events]
            assert by_action["claim.bench.locked"]["actor_id"] == f"user-{EDITOR}"
            unlocked = by_action["claim.bench.unlocked"]
            assert unlocked["actor_id"] == "user-org_admin" and unlocked["reason"] == REASON
            assert unlocked["before"]["locked_by"] == f"user-{EDITOR}"

    _run(scenario())
