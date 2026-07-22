from __future__ import annotations

from typing import Any

from .runner import MigrationResult


VERSION = "20260722_0003"
NAME = "arbitration_filing_export_effects"
DESCRIPTION = "Add unique filing-export effect keys and lease recovery indexes."

INDEXES = (
    (
        [("effect_key", 1)],
        {
            # Match the runtime ensure_indexes name so startup-before-migrate
            # remains idempotent on MongoDB.
            "name": "effect_key_1",
            "unique": True,
            "partialFilterExpression": {"effect_key": {"$type": "string"}},
            "background": True,
        },
    ),
    (
        [("status", 1), ("execution_lease_expires_at", 1)],
        {"name": "status_1_execution_lease_expires_at_1", "background": True},
    ),
)


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = [
        {
            "operation": "create_index",
            "collection": "arbitration_bundle_exports",
            "keys": keys,
            "name": options["name"],
            "unique": bool(options.get("unique", False)),
        }
        for keys, options in INDEXES
    ]
    if not dry_run:
        for keys, options in INDEXES:
            await db.arbitration_bundle_exports.create_index(keys, **options)
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
    )


async def downgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = [
        {
            "operation": "drop_index",
            "collection": "arbitration_bundle_exports",
            "name": options["name"],
        }
        for _keys, options in INDEXES
    ]
    if not dry_run:
        for _keys, options in INDEXES:
            try:
                await db.arbitration_bundle_exports.drop_index(options["name"])
            except Exception:
                pass
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "rolled_back",
        operations=operations,
    )
