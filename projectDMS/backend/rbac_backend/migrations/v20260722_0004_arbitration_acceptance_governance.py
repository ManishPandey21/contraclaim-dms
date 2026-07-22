from __future__ import annotations

from typing import Any

from .runner import MigrationResult


VERSION = "20260722_0004"
NAME = "arbitration_acceptance_governance"
DESCRIPTION = "Add formal production-acceptance, reviewer-receipt, and scoped fan-out indexes."

INDEXES = (
    (
        "arbitration_production_acceptance_receipts",
        [("status", 1), ("expires_at", 1)],
        {"name": "arb_acceptance_status_expiry", "background": True},
    ),
    (
        "arbitration_production_acceptance_receipts",
        [("organization_ids", 1), ("project_ids", 1), ("accepted_at", -1)],
        {"name": "arb_acceptance_scope_time", "background": True},
    ),
    (
        "arbitration_workflow_approvals",
        [("run_id", 1), ("gate", 1), ("receipt_status", 1), ("artifact_hash", 1)],
        {"name": "arb_approval_committed_gate", "background": True},
    ),
    (
        "arbitration_analysis_leases",
        [("scope_hash", 1), ("lease_expires_at", 1)],
        {"name": "arb_analysis_scope_lease", "background": True},
    ),
)


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = [
        {"operation": "create_index", "collection": collection, "keys": keys, "name": options["name"]}
        for collection, keys, options in INDEXES
    ]
    if not dry_run:
        for collection, keys, options in INDEXES:
            await db[collection].create_index(keys, **options)
    return MigrationResult(version=VERSION, name=NAME, status="dry_run" if dry_run else "applied", operations=operations)


async def downgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = [
        {"operation": "drop_index", "collection": collection, "name": options["name"]}
        for collection, _keys, options in INDEXES
    ]
    if not dry_run:
        for collection, _keys, options in INDEXES:
            try:
                await db[collection].drop_index(options["name"])
            except Exception:
                pass
    return MigrationResult(version=VERSION, name=NAME, status="dry_run" if dry_run else "rolled_back", operations=operations)
