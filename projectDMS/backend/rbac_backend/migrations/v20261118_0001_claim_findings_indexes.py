from __future__ import annotations

from typing import Any

from .runner import MigrationResult
from .v20261114_0001_skill_registry import apply_indexes


VERSION = "20261118_0001"
NAME = "claim_findings_indexes"
DESCRIPTION = (
    "Index claim determinations, the privileged claim store (simulations and strategy notes) "
    "and claim submissions."
)

INDEXES = [
    {
        "collection": "claim_determinations",
        "keys": [("claim_id", 1), ("origin", 1), ("revision", 1)],
        "name": "claim_determination_revision_unique",
        "unique": True,
    },
    {
        "collection": "claim_determinations",
        "keys": [("organization_id", 1), ("project_id", 1), ("status", 1)],
        "name": "claim_determination_scope_status",
        "unique": False,
    },
    {
        "collection": "claim_privileged",
        "keys": [("claim_id", 1), ("kind", 1), ("created_at", -1)],
        "name": "claim_privileged_claim_kind_time",
        "unique": False,
    },
    {
        # One strategy note per claim. Simulations are many.
        "collection": "claim_privileged",
        "keys": [("claim_id", 1)],
        "name": "claim_privileged_one_strategy_note",
        "unique": True,
        "partialFilterExpression": {"kind": "strategy_note"},
    },
    {
        "collection": "claim_submissions",
        "keys": [("claim_id", 1)],
        "name": "claim_submission_claim_unique",
        "unique": True,
    },
]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=await apply_indexes(db, INDEXES, dry_run),
        notices=[
            "No claim, board or role is changed. The permissions dms.claim.determine and "
            "dms.claim.strategy.read are not granted to any existing role by this migration.",
        ],
    )
