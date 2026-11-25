from __future__ import annotations

from typing import Any

from .runner import MigrationResult
from .v20261114_0001_skill_registry import apply_indexes


VERSION = "20261116_0001"
NAME = "claim_bench_indexes"
DESCRIPTION = (
    "Index skill steps run for claims, the notice periods recorded from each project's "
    "contract, and the record a potential claim was raised from."
)

INDEXES = [
    {
        # Replaying a step with the same inputs updates it; it never duplicates.
        "collection": "skill_steps",
        "keys": [
            ("subject_type", 1),
            ("subject_id", 1),
            ("skill_key", 1),
            ("skill_hash", 1),
            ("input_hash", 1),
        ],
        "name": "skill_step_idempotency_unique",
        "unique": True,
    },
    {
        "collection": "skill_steps",
        "keys": [("step_id", 1)],
        "name": "skill_step_id_unique",
        "unique": True,
    },
    {
        "collection": "skill_steps",
        "keys": [("parent_id", 1), ("created_at", 1)],
        "name": "skill_step_parent_time",
        "unique": False,
    },
    {
        # One period in force per project and claim type. Superseded records stay as history.
        "collection": "claim_notice_requirements",
        "keys": [("organization_id", 1), ("project_id", 1), ("claim_type", 1)],
        "name": "notice_requirement_current_unique",
        "unique": True,
        "partialFilterExpression": {"status": "current"},
    },
    {
        "collection": "claim_notice_requirements",
        "keys": [("organization_id", 1), ("project_id", 1), ("claim_type", 1), ("created_at", -1)],
        "name": "notice_requirement_history",
        "unique": False,
    },
    {
        # Accepting a signal is idempotent: one claim per hindrance or letter it was raised from.
        "collection": "claims",
        "keys": [("organization_id", 1), ("origin.type", 1), ("origin.id", 1)],
        "name": "claim_origin_unique",
        "unique": True,
        "partialFilterExpression": {"origin.id": {"$type": "string"}},
    },
]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=await apply_indexes(db, INDEXES, dry_run),
        notices=[
            "No claim is changed. A claim gets its board the first time someone opens the "
            "Claim Bench on it, with one head carried over from its register fields.",
        ],
    )
