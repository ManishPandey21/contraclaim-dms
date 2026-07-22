from __future__ import annotations

from typing import Any

from .runner import MigrationResult


VERSION = "20260722_0002"
NAME = "arbitration_phase6_scope_index"
DESCRIPTION = "Index tenant/project arbitration workflow acceptance windows for Phase-6 cutover decisions."

COLLECTION = "arbitration_workflow_runs"
INDEX_NAME = "arb_wf_scope_time"
INDEX_KEYS = [("organization_id", 1), ("project_id", 1), ("created_at", -1)]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operation = {
        "operation": "create_index",
        "collection": COLLECTION,
        "keys": INDEX_KEYS,
        "name": INDEX_NAME,
        "unique": False,
    }
    if not dry_run:
        await db[COLLECTION].create_index(
            INDEX_KEYS,
            name=INDEX_NAME,
            background=True,
        )
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=[operation],
    )


async def downgrade(db: Any, dry_run: bool) -> MigrationResult:
    operation = {
        "operation": "drop_index",
        "collection": COLLECTION,
        "name": INDEX_NAME,
    }
    if not dry_run:
        try:
            await db[COLLECTION].drop_index(INDEX_NAME)
        except Exception:
            pass
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "rolled_back",
        operations=[operation],
    )
