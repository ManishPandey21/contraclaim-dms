"""Reconcile duplicate current subscriptions and enforce one row per exact scope."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from ..services.subscription_scope_key import (
    CURRENT_SUBSCRIPTION_STATUSES,
    current_subscription_scope_key,
)
from .runner import MigrationResult


VERSION = "20260806_0001"
NAME = "subscription_current_scope_unique"
DESCRIPTION = (
    "Deterministically cancel duplicate current subscriptions, backfill the current "
    "scope key, and create a partial unique index."
)
INDEX_NAME = "subscription_one_current_per_scope"

_STATUS_PRIORITY = {"active": 3, "pilot": 2, "trial": 1}


async def _to_list(cursor: Any) -> List[Dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=None)
    return [row async for row in cursor]


def _timestamp(value: Any) -> float:
    if not isinstance(value, datetime):
        return float("-inf")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _winner_sort_key(row: Dict[str, Any]) -> tuple[int, float, float, str]:
    return (
        _STATUS_PRIORITY.get(str(row.get("status") or "").lower(), 0),
        _timestamp(row.get("updated_at")),
        _timestamp(row.get("created_at")),
        str(row.get("_id") or ""),
    )


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    subscriptions = db.subscriptions
    current_rows = await _to_list(
        subscriptions.find({"status": {"$in": sorted(CURRENT_SUBSCRIPTION_STATUSES)}})
    )
    stale_key_rows = await _to_list(
        subscriptions.find(
            {
                "status": {"$nin": sorted(CURRENT_SUBSCRIPTION_STATUSES)},
                "current_scope_key": {"$exists": True},
            }
        )
    )

    groups: Dict[str, List[Dict[str, Any]]] = {}
    invalid_rows: List[Dict[str, Any]] = []
    for row in current_rows:
        try:
            key = current_subscription_scope_key(row)
        except ValueError:
            invalid_rows.append(row)
            continue
        if key:
            groups.setdefault(key, []).append(row)

    operations: List[Dict[str, Any]] = [
        {
            "operation": "scan_current_subscriptions",
            "documents": len(current_rows),
            "exact_scopes": len(groups),
            "duplicate_scopes": sum(1 for rows in groups.values() if len(rows) > 1),
            "invalid_current_rows": len(invalid_rows),
            "stale_scope_keys": len(stale_key_rows),
        }
    ]
    warnings: List[str] = []
    now = datetime.now(timezone.utc)
    actor = f"system:migration:{VERSION}"

    for row in stale_key_rows:
        row_id = row.get("_id")
        operations.append(
            {"operation": "unset_stale_current_scope_key", "subscription_id": str(row_id)}
        )
        if not dry_run:
            await subscriptions.update_one(
                {"_id": row_id}, {"$unset": {"current_scope_key": ""}}
            )

    for row in invalid_rows:
        row_id = row.get("_id")
        previous_status = str(row.get("status") or "")
        operations.append(
            {
                "operation": "cancel_invalid_unscoped_current_subscription",
                "subscription_id": str(row_id),
                "from_status": previous_status,
            }
        )
        warnings.append(
            f"Cancelled current subscription {row_id}: organization_id is missing"
        )
        if not dry_run:
            await subscriptions.update_one(
                {"_id": row_id},
                {
                    "$set": {
                        "status": "cancelled",
                        "billing_status": "inactive",
                        "reconciliation_reason": "missing_organization_scope",
                        "reconciled_at": now,
                        "reconciled_by": actor,
                        "updated_at": now,
                        "updated_by": actor,
                    },
                    "$unset": {"current_scope_key": ""},
                },
            )
            await db.subscription_history.insert_one(
                {
                    "subscription_id": str(row_id),
                    "organization_id": "",
                    "project_id": row.get("project_id"),
                    "change_type": "invalid_scope_reconciliation",
                    "from_status": previous_status,
                    "to_status": "cancelled",
                    "metadata": {
                        "reason": "missing_organization_scope",
                        "migration_version": VERSION,
                    },
                    "changed_by": actor,
                    "changed_at": now,
                }
            )

    for key, rows in sorted(groups.items()):
        ordered = sorted(rows, key=_winner_sort_key, reverse=True)
        winner = ordered[0]
        losers = ordered[1:]
        winner_id = winner.get("_id")
        operations.append(
            {
                "operation": "backfill_current_scope_key",
                "scope_key": key,
                "winner_subscription_id": str(winner_id),
                "cancelled_duplicate_ids": [str(row.get("_id")) for row in losers],
            }
        )
        if dry_run:
            continue

        for loser in losers:
            loser_id = loser.get("_id")
            previous_status = str(loser.get("status") or "")
            await subscriptions.update_one(
                {"_id": loser_id},
                {
                    "$set": {
                        "status": "cancelled",
                        "billing_status": "inactive",
                        "duplicate_of_subscription_id": str(winner_id),
                        "reconciliation_reason": "duplicate_current_subscription_scope",
                        "reconciled_at": now,
                        "reconciled_by": actor,
                        "updated_at": now,
                        "updated_by": actor,
                    },
                    "$unset": {"current_scope_key": ""},
                },
            )
            await db.subscription_history.insert_one(
                {
                    "subscription_id": str(loser_id),
                    "organization_id": str(loser.get("organization_id") or ""),
                    "project_id": loser.get("project_id"),
                    "change_type": "duplicate_reconciliation",
                    "from_status": previous_status,
                    "to_status": "cancelled",
                    "metadata": {
                        "winner_subscription_id": str(winner_id),
                        "scope_key": key,
                        "migration_version": VERSION,
                    },
                    "changed_by": actor,
                    "changed_at": now,
                }
            )

        if str(winner.get("current_scope_key") or "") == key:
            # Already backfilled by an earlier run. Re-writing the row here would
            # be a no-op except for the audit stamps, and moving those on every
            # re-run would misreport when the backfill actually happened.
            continue

        await subscriptions.update_one(
            {"_id": winner_id},
            {
                "$set": {
                    "current_scope_key": key,
                    "scope_key_backfilled_at": now,
                    "scope_key_backfilled_by": actor,
                }
            },
        )

    operations.append(
        {
            "operation": "create_index",
            "collection": "subscriptions",
            "name": INDEX_NAME,
            "keys": [("current_scope_key", 1)],
            "unique": True,
            "partial_filter": {"current_scope_key": {"$type": "string"}},
        }
    )
    if not dry_run:
        await subscriptions.create_index(
            [("current_scope_key", 1)],
            name=INDEX_NAME,
            unique=True,
            partialFilterExpression={"current_scope_key": {"$type": "string"}},
            background=True,
        )

    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
        warnings=warnings,
    )


async def downgrade(db: Any, dry_run: bool) -> MigrationResult:
    """Remove the uniqueness invariant and the derived scope key.

    Rollback deliberately does **not** resurrect the subscriptions that
    reconciliation cancelled. Restoring them would recreate exactly the
    conflicting-current-subscription state the application now rejects, and the
    winner/loser choice cannot be un-made safely once billing has observed it.
    Every cancellation is individually reversible by an operator instead: each
    loser retains ``duplicate_of_subscription_id`` and
    ``reconciliation_reason``, and ``subscription_history`` holds a
    ``duplicate_reconciliation`` (or ``invalid_scope_reconciliation``) record
    with the prior status.
    """

    subscriptions = db.subscriptions
    operations: List[Dict[str, Any]] = [
        {"operation": "drop_index", "collection": "subscriptions", "name": INDEX_NAME},
        {"operation": "unset_current_scope_key", "collection": "subscriptions"},
    ]

    if not dry_run:
        try:
            await subscriptions.drop_index(INDEX_NAME)
        except Exception:
            # Index absent (already rolled back, or upgrade never applied).
            pass
        await subscriptions.update_many(
            {"current_scope_key": {"$exists": True}},
            {"$unset": {"current_scope_key": "", "scope_key_backfilled_at": "", "scope_key_backfilled_by": ""}},
        )

    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "rolled_back",
        operations=operations,
        warnings=[
            "Cancelled duplicate subscriptions are intentionally retained; "
            "restore individually from subscription_history using "
            "duplicate_of_subscription_id if a reconciliation decision must be undone.",
        ],
    )
