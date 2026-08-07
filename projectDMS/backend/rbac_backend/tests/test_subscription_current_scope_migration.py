from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from rbac_backend.migrations.v20260806_0001_subscription_current_scope_unique import (
    INDEX_NAME,
    downgrade,
    upgrade,
)
from rbac_backend.services.subscription_scope_key import subscription_scope_key


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length=None):
        return deepcopy(self.rows)


def _matches(row, query):
    for field, condition in query.items():
        value = row.get(field)
        if isinstance(condition, dict):
            if "$in" in condition and value not in condition["$in"]:
                return False
            if "$nin" in condition and value in condition["$nin"]:
                return False
            if "$exists" in condition and (field in row) != condition["$exists"]:
                return False
        elif value != condition:
            return False
    return True


class _Collection:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or [])
        self.indexes = []
        self.dropped_indexes = []

    def find(self, query):
        return _Cursor([row for row in self.rows if _matches(row, query)])

    async def update_one(self, query, operation):
        row = next(item for item in self.rows if _matches(item, query))
        row.update(operation.get("$set", {}))
        for field in operation.get("$unset", {}):
            row.pop(field, None)
        return SimpleNamespace(matched_count=1)

    async def insert_one(self, document):
        self.rows.append(deepcopy(document))
        return SimpleNamespace(inserted_id=len(self.rows))

    async def update_many(self, query, operation):
        matched = 0
        for row in self.rows:
            if not _matches(row, query):
                continue
            matched += 1
            row.update(operation.get("$set", {}))
            for field in operation.get("$unset", {}):
                row.pop(field, None)
        return SimpleNamespace(matched_count=matched)

    async def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))
        return kwargs.get("name")

    async def drop_index(self, name):
        self.dropped_indexes.append(name)


class _DB:
    def __init__(self, rows):
        self.subscriptions = _Collection(rows)
        self.subscription_history = _Collection()


def _rows():
    now = datetime.now(timezone.utc)
    return [
        {
            "_id": "active-winner",
            "organization_id": "org-a",
            "project_id": None,
            "package_id": None,
            "status": "active",
            "updated_at": now - timedelta(days=2),
        },
        {
            "_id": "trial-loser",
            "organization_id": "org-a",
            "project_id": None,
            "package_id": None,
            "status": "trial",
            "updated_at": now,
        },
        {
            "_id": "project-current",
            "organization_id": "org-a",
            "project_id": "project-1",
            "package_id": None,
            "status": "pilot",
            "updated_at": now,
        },
        {
            "_id": "stale-key",
            "organization_id": "org-a",
            "status": "cancelled",
            "current_scope_key": "stale",
        },
        {"_id": "invalid-current", "status": "active", "updated_at": now},
    ]


@pytest.mark.asyncio
async def test_dry_run_reports_duplicates_without_mutation():
    db = _DB(_rows())
    before = deepcopy(db.subscriptions.rows)

    result = await upgrade(db, dry_run=True)

    assert db.subscriptions.rows == before
    assert result.operations[0]["duplicate_scopes"] == 1
    assert result.operations[0]["invalid_current_rows"] == 1
    assert db.subscriptions.indexes == []


@pytest.mark.asyncio
async def test_apply_reconciles_and_creates_partial_unique_index():
    db = _DB(_rows())

    await upgrade(db, dry_run=False)

    by_id = {row["_id"]: row for row in db.subscriptions.rows}
    assert by_id["active-winner"]["current_scope_key"] == subscription_scope_key(
        "org-a", None, None
    )
    assert by_id["trial-loser"]["status"] == "cancelled"
    assert by_id["trial-loser"]["duplicate_of_subscription_id"] == "active-winner"
    assert "current_scope_key" not in by_id["trial-loser"]
    assert by_id["project-current"]["current_scope_key"] == subscription_scope_key(
        "org-a", "project-1", None
    )
    assert "current_scope_key" not in by_id["stale-key"]
    assert by_id["invalid-current"]["status"] == "cancelled"
    assert len(db.subscription_history.rows) == 2

    keys, options = db.subscriptions.indexes[-1]
    assert keys == [("current_scope_key", 1)]
    assert options == {
        "name": INDEX_NAME,
        "unique": True,
        "partialFilterExpression": {"current_scope_key": {"$type": "string"}},
        "background": True,
    }


@pytest.mark.asyncio
async def test_upgrade_is_idempotent():
    """Re-running must not re-cancel, re-record history, or change any winner."""

    db = _DB(_rows())
    await upgrade(db, dry_run=False)
    after_first = deepcopy(db.subscriptions.rows)
    history_after_first = len(db.subscription_history.rows)

    result = await upgrade(db, dry_run=False)

    assert db.subscriptions.rows == after_first
    assert len(db.subscription_history.rows) == history_after_first
    assert result.operations[0]["duplicate_scopes"] == 0
    assert result.operations[0]["invalid_current_rows"] == 0


@pytest.mark.asyncio
async def test_second_dry_run_after_apply_reports_nothing_left_to_do():
    db = _DB(_rows())
    await upgrade(db, dry_run=False)

    result = await upgrade(db, dry_run=True)

    scan = result.operations[0]
    assert scan["duplicate_scopes"] == 0
    assert scan["invalid_current_rows"] == 0
    assert scan["stale_scope_keys"] == 0


@pytest.mark.asyncio
async def test_downgrade_drops_index_and_scope_keys_without_resurrecting_duplicates():
    db = _DB(_rows())
    await upgrade(db, dry_run=False)

    result = await downgrade(db, dry_run=False)

    assert db.subscriptions.dropped_indexes == [INDEX_NAME]
    assert all("current_scope_key" not in row for row in db.subscriptions.rows)
    by_id = {row["_id"]: row for row in db.subscriptions.rows}
    # Reconciliation decisions survive rollback, with their audit trail intact.
    assert by_id["trial-loser"]["status"] == "cancelled"
    assert by_id["trial-loser"]["duplicate_of_subscription_id"] == "active-winner"
    assert len(db.subscription_history.rows) == 2
    assert result.warnings


@pytest.mark.asyncio
async def test_downgrade_dry_run_changes_nothing():
    db = _DB(_rows())
    await upgrade(db, dry_run=False)
    before = deepcopy(db.subscriptions.rows)

    await downgrade(db, dry_run=True)

    assert db.subscriptions.rows == before
    assert db.subscriptions.dropped_indexes == []


def test_conflicting_current_subscriptions_produce_one_identical_key():
    """The unique index can only work if conflicting scopes collide on the key.

    This is what makes the partial unique index an actual invariant rather than
    a decoration: two current subscriptions for the same exact scope must derive
    the *same* ``current_scope_key``, while a different scope must not.
    """

    same_a = subscription_scope_key("org-a", None, None)
    same_b = subscription_scope_key("org-a", None, None)
    assert same_a == same_b

    assert subscription_scope_key("org-a", "project-1", None) != same_a
    assert subscription_scope_key("org-b", None, None) != same_a
    assert subscription_scope_key("org-a", None, "package-1") != same_a
