from __future__ import annotations

from typing import Any, Dict, List

from .runner import MigrationResult


VERSION = "20260705_0003"
NAME = "arbitration_expert_alignment_indexes"
DESCRIPTION = (
    "Create indexes for the arbitration expert alignment matrix collection (ARB-103)."
)


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations: List[Dict[str, Any]] = [
        {
            "operation": "create_index",
            "collection": "arbitration_expert_alignment",
            "keys": [("case_id", 1), ("expert_type", 1), ("claim_no", 1)],
        },
        {
            "operation": "create_index",
            "collection": "arbitration_expert_alignment",
            "keys": [("case_id", 1), ("readiness_status", 1), ("approval_status", 1), ("updated_at", -1)],
        },
    ]

    if not dry_run:
        await db.arbitration_expert_alignment.create_index(
            [("case_id", 1), ("expert_type", 1), ("claim_no", 1)],
            background=True,
        )
        await db.arbitration_expert_alignment.create_index(
            [("case_id", 1), ("readiness_status", 1), ("approval_status", 1), ("updated_at", -1)],
            background=True,
        )

    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
    )
