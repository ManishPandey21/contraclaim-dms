from __future__ import annotations

from typing import Any

from .runner import MigrationResult
from .v20261114_0001_skill_registry import apply_indexes, drop_indexes


VERSION = "20261114_0003"
NAME = "letter_bench_indexes"
DESCRIPTION = "Index Letter Bench position boards and skill steps (one board per letter, idempotent steps)."

INDEXES = [
    {
        "collection": "letter_position_boards",
        "keys": [("letter_id", 1)],
        "name": "bench_board_letter_unique",
        "unique": True,
    },
    {
        "collection": "letter_position_boards",
        "keys": [("organization_id", 1), ("project_id", 1), ("updated_at", -1)],
        "name": "bench_board_scope_time",
        "unique": False,
    },
    {
        # Opening the Bench on a document is idempotent: one board per document.
        "collection": "letter_position_boards",
        "keys": [("organization_id", 1), ("incoming_document_id", 1)],
        "name": "bench_board_document_unique",
        "unique": True,
        "partialFilterExpression": {"incoming_document_id": {"$type": "string"}},
    },
    {
        "collection": "letter_skill_steps",
        "keys": [("letter_id", 1), ("created_at", 1)],
        "name": "bench_step_letter_time",
        "unique": False,
    },
    {
        "collection": "letter_skill_steps",
        "keys": [("letter_id", 1), ("skill_key", 1), ("skill_hash", 1), ("input_hash", 1)],
        "name": "bench_step_idempotency_unique",
        "unique": True,
    },
    {
        "collection": "letter_skill_steps",
        "keys": [("step_id", 1)],
        "name": "bench_step_id_unique",
        "unique": True,
    },
]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=await apply_indexes(db, INDEXES, dry_run),
    )


async def downgrade(db: Any, dry_run: bool) -> MigrationResult:
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "rolled_back",
        operations=await drop_indexes(db, INDEXES, dry_run),
    )
