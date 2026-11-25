"""M-04 (plan 4B-18, D-23): an index for claims by contract.

Claims gain ``contract_id``, the project's Contract Master entry the claim is
made under. This adds the lookup index used when records are checked against a
claim's contract and when a register is filtered by it.

Index only. No claim is read, changed or backfilled: a claim created before
this carries no ``contract_id`` and keeps none until a person gives it one.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .runner import MigrationResult
from .v20261114_0001_skill_registry import apply_indexes

VERSION = "20261122_0001"
NAME = "claim_contract_index"
DESCRIPTION = "Index claims by organisation, project and contract. No claim is changed or backfilled."

INDEXES: List[Dict[str, Any]] = [
    {
        "collection": "claims",
        "keys": [("organization_id", 1), ("project_id", 1), ("contract_id", 1)],
        "name": "claim_scope_contract",
    },
]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=await apply_indexes(db, INDEXES, dry_run),
        notices=[
            "Index only. Existing claims keep no contract until a person sets one; nothing is backfilled.",
        ],
    )
