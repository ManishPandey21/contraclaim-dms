from __future__ import annotations

from typing import Any

from .runner import MigrationResult
from .v20261114_0001_skill_registry import apply_indexes


VERSION = "20261117_0001"
NAME = "claim_head_link_indexes"
DESCRIPTION = (
    "Index the links from a head of claim to the hindrances and key-date EOT records it relies on."
)

INDEXES = [
    {
        # One active link per record and head. Removed links stay as history.
        "collection": "claim_head_links",
        "keys": [("claim_id", 1), ("head_id", 1), ("target_type", 1), ("target_id", 1)],
        "name": "claim_head_link_active_unique",
        "unique": True,
        "partialFilterExpression": {"status": "active"},
    },
    {
        "collection": "claim_head_links",
        "keys": [("claim_id", 1), ("status", 1), ("created_at", 1)],
        "name": "claim_head_link_claim_time",
        "unique": False,
    },
    {
        # "Which heads rely on this record": one hindrance may serve several heads.
        "collection": "claim_head_links",
        "keys": [("organization_id", 1), ("target_type", 1), ("target_id", 1), ("status", 1)],
        "name": "claim_head_link_target",
        "unique": False,
    },
]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=await apply_indexes(db, INDEXES, dry_run),
        notices=[
            "No hindrance, key-date record or claim is changed. A link is a new record that "
            "points at one of them.",
        ],
    )
