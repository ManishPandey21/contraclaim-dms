"""M-03 (plan 4B-07, D-06): indexes for ``reviewer_role_assignments``.

One live assignment per person, role and scope: a partial unique index over the
rows that carry ``active: true``. A grant sets the flag, and a revocation or a
re-grant after expiry removes it, so history is kept and the slot is freed.
Two lookup indexes serve the resolver (by person) and the admin lists (by scope).

Indexes only. The collection is new; no document is written or changed.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..models.reviewer_assignment import REVIEWER_ROLE_ASSIGNMENTS
from .runner import MigrationResult
from .v20261114_0001_skill_registry import apply_indexes

VERSION = "20261121_0002"
NAME = "reviewer_role_assignment_indexes"
DESCRIPTION = "Indexes for reviewer role assignments: one live assignment per person, role and scope."

INDEXES: List[Dict[str, Any]] = [
    {
        "collection": REVIEWER_ROLE_ASSIGNMENTS,
        "keys": [("user_id", 1), ("role_key", 1), ("scope_type", 1), ("organization_id", 1), ("project_id", 1)],
        "name": "reviewer_assignment_one_live_per_scope",
        "unique": True,
        "partialFilterExpression": {"active": True},
    },
    {
        "collection": REVIEWER_ROLE_ASSIGNMENTS,
        "keys": [("user_id", 1), ("revoked_at", 1)],
        "name": "reviewer_assignment_by_user",
    },
    {
        "collection": REVIEWER_ROLE_ASSIGNMENTS,
        "keys": [("scope_type", 1), ("organization_id", 1), ("project_id", 1), ("granted_at", -1)],
        "name": "reviewer_assignment_by_scope",
    },
]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=await apply_indexes(db, INDEXES, dry_run),
        notices=["Indexes only. No document is written or changed."],
    )
