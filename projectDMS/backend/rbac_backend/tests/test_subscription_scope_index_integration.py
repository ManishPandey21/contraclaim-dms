"""The subscription scope invariant, verified against a real MongoDB.

``test_subscription_current_scope_migration.py`` drives the migration through
hand-written collection fakes. Those fakes prove the reconciliation *logic* but
they cannot prove the thing the migration actually relies on: that a partial
unique index on ``current_scope_key`` is what stops two concurrent writers from
both creating a current subscription for the same scope.

The service's pre-flight check is check-then-act -- both racers can observe a
free scope before either inserts -- so the index is the only real serialisation
point. These tests exercise it against a live server, and skip when none is
reachable so the unit suite still runs anywhere.

The database is opened per test rather than via a fixture: ``conftest.py`` runs
each coroutine test in its own ``asyncio.run`` loop, and a Motor client is bound
to the loop that created it.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
from pymongo.errors import DuplicateKeyError

from rbac_backend.migrations.v20260806_0001_subscription_current_scope_unique import (
    INDEX_NAME,
    upgrade,
)
from rbac_backend.services.subscription_scope_key import current_subscription_scope_key

motor = pytest.importorskip("motor.motor_asyncio")

MONGO_URL = "mongodb://localhost:27017"
_SELECTION_TIMEOUT_MS = 1500


@asynccontextmanager
async def temp_db():
    """A throwaway database, dropped whatever the test does."""
    client = motor.AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=_SELECTION_TIMEOUT_MS)
    try:
        await client.admin.command("ping")
    except Exception as exc:  # server down, wrong port, auth required
        client.close()
        pytest.skip(f"no MongoDB at {MONGO_URL}: {exc}")

    name = f"contraclaim_test_{uuid.uuid4().hex[:12]}"
    try:
        yield client[name]
    finally:
        await client.drop_database(name)
        client.close()


def _sub(org, *, project=None, package=None, status="active"):
    return {
        "organization_id": org,
        "project_id": project,
        "package_id": package,
        "status": status,
    }


# --- the index itself -----------------------------------------------------


async def test_migration_creates_a_partial_unique_index():
    async with temp_db() as db:
        await upgrade(db, dry_run=False)

        info = await db.subscriptions.index_information()
        assert INDEX_NAME in info, "the migration must leave the invariant enforced"
        spec = info[INDEX_NAME]
        assert spec.get("unique") is True
        assert spec.get("partialFilterExpression") == {
            "current_scope_key": {"$type": "string"}
        }


async def test_index_rejects_a_second_current_row_for_the_same_scope():
    async with temp_db() as db:
        await upgrade(db, dry_run=False)
        key = current_subscription_scope_key(_sub("org-A"))

        await db.subscriptions.insert_one({**_sub("org-A"), "current_scope_key": key})

        with pytest.raises(DuplicateKeyError):
            await db.subscriptions.insert_one({**_sub("org-A"), "current_scope_key": key})


async def test_concurrent_writers_cannot_both_win_the_same_scope():
    """The race the pre-flight check cannot close: both racers see a free scope."""
    async with temp_db() as db:
        await upgrade(db, dry_run=False)
        key = current_subscription_scope_key(_sub("org-A"))

        async def _claim():
            try:
                await db.subscriptions.insert_one(
                    {**_sub("org-A"), "current_scope_key": key}
                )
                return "won"
            except DuplicateKeyError:
                return "lost"

        results = await asyncio.gather(*(_claim() for _ in range(12)))

        assert results.count("won") == 1, f"exactly one writer may take a scope, got {results}"
        assert await db.subscriptions.count_documents({"current_scope_key": key}) == 1


async def test_distinct_scopes_do_not_collide():
    """Uniqueness must bind the scope, not the collection."""
    async with temp_db() as db:
        await upgrade(db, dry_run=False)
        rows = [
            _sub("org-A"),
            _sub("org-B"),
            _sub("org-A", project="proj-1"),
            _sub("org-A", project="proj-1", package="pkg-1"),
        ]
        for row in rows:
            await db.subscriptions.insert_one(
                {**row, "current_scope_key": current_subscription_scope_key(row)}
            )

        assert await db.subscriptions.count_documents({}) == len(rows)


async def test_non_current_rows_are_exempt_from_the_index():
    """Cancelled rows carry no scope key, so any number may pile up.

    This is the half a non-partial unique index would break: reconciliation
    cancels losers and unsets their key, and an outright unique index would then
    reject the second cancellation.
    """
    async with temp_db() as db:
        await upgrade(db, dry_run=False)

        for _ in range(5):
            await db.subscriptions.insert_one(_sub("org-A", status="cancelled"))

        assert await db.subscriptions.count_documents({"status": "cancelled"}) == 5


# --- reconciliation against a real server ---------------------------------


async def test_migration_reconciles_pre_existing_duplicates_then_enforces():
    """The ordering that matters: collapse duplicates *before* building the index.

    A unique index cannot be created over data that already violates it, so a
    migration that indexed first would fail outright on any real deployment
    carrying duplicates.
    """
    async with temp_db() as db:
        await db.subscriptions.insert_many(
            [
                {**_sub("org-A"), "_id": "keep-me"},
                {**_sub("org-A", status="trial"), "_id": "loser-1"},
                {**_sub("org-A", status="pilot"), "_id": "loser-2"},
                {**_sub("org-B"), "_id": "other-scope"},
            ]
        )

        result = await upgrade(db, dry_run=False)
        assert result.status == "applied"

        key = current_subscription_scope_key(_sub("org-A"))
        survivors = await db.subscriptions.count_documents({"current_scope_key": key})
        assert survivors == 1, "reconciliation must leave exactly one current row per scope"

        # 'active' outranks 'pilot' and 'trial', so the active row is the winner.
        winner = await db.subscriptions.find_one({"current_scope_key": key})
        assert winner["_id"] == "keep-me"

        for loser_id in ("loser-1", "loser-2"):
            loser = await db.subscriptions.find_one({"_id": loser_id})
            assert loser["status"] == "cancelled"
            assert loser["duplicate_of_subscription_id"] == "keep-me"
            assert "current_scope_key" not in loser

        # And the invariant is live afterwards.
        with pytest.raises(DuplicateKeyError):
            await db.subscriptions.insert_one({**_sub("org-A"), "current_scope_key": key})


async def test_re_running_the_migration_changes_nothing():
    """Re-running must be a genuine no-op, audit stamps included.

    Creating the unique index a second time is harmless, so the churn to watch
    for is on the rows: an unconditional backfill would move
    ``scope_key_backfilled_at`` on every run and misreport when the backfill
    actually happened. The equivalent fake-based test only caught this when both
    runs happened to land in the same coarse clock tick.
    """
    async with temp_db() as db:
        await db.subscriptions.insert_many(
            [
                {**_sub("org-A"), "_id": "keep-me"},
                {**_sub("org-A", status="trial"), "_id": "loser-1"},
                {**_sub("org-B"), "_id": "other-scope"},
            ]
        )

        await upgrade(db, dry_run=False)
        after_first = sorted(
            await db.subscriptions.find({}).to_list(length=None), key=lambda r: r["_id"]
        )
        history_after_first = await db.subscription_history.count_documents({})

        await upgrade(db, dry_run=False)

        after_second = sorted(
            await db.subscriptions.find({}).to_list(length=None), key=lambda r: r["_id"]
        )
        assert after_second == after_first, "a re-run must not rewrite any row"
        assert await db.subscription_history.count_documents({}) == history_after_first
