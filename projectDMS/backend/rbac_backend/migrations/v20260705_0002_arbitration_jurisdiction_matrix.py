from __future__ import annotations

from typing import Any, Dict, List

from .runner import MigrationResult


VERSION = "20260705_0002"
NAME = "arbitration_jurisdiction_matrix_indexes"
DESCRIPTION = (
    "Create indexes for the arbitration jurisdiction/limitation/pre-arbitration "
    "matrix collection (ARB-102)."
)


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations: List[Dict[str, Any]] = [
        {
            "operation": "create_index",
            "collection": "arbitration_jurisdiction_matrix",
            "keys": [("case_id", 1), ("check_type", 1), ("updated_at", -1)],
        },
        {
            "operation": "create_index",
            "collection": "arbitration_jurisdiction_matrix",
            "keys": [("case_id", 1), ("readiness_status", 1), ("approval_status", 1), ("updated_at", -1)],
        },
    ]

    if not dry_run:
        await db.arbitration_jurisdiction_matrix.create_index(
            [("case_id", 1), ("check_type", 1), ("updated_at", -1)],
            background=True,
        )
        await db.arbitration_jurisdiction_matrix.create_index(
            [("case_id", 1), ("readiness_status", 1), ("approval_status", 1), ("updated_at", -1)],
            background=True,
        )

    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
    )
