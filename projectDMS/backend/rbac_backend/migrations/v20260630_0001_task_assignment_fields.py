from __future__ import annotations

from typing import Any

from .runner import MigrationResult


VERSION = "20260630_0001"
NAME = "task_assignment_fields"
DESCRIPTION = (
    "Add indexes backing the task assignment/workflow linkage fields "
    "(task_type, resource_type, resource_id). The fields themselves are additive "
    "and need no backfill: existing tasks default to task_type='general'."
)


# (keys, options) for each index this migration guarantees. Compound keys mirror
# rbac_backend.core.database.ensure_indexes so startup and migrations agree.
_TASK_INDEXES = [
    ([("resource_type", 1), ("resource_id", 1)], {"background": True}),
    ([("assigned_to", 1), ("status", 1)], {"background": True}),
    ([("organization_id", 1), ("project_id", 1), ("task_type", 1)], {"background": True}),
]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = [
        {
            "operation": "create_index",
            "collection": "tasks",
            "keys": keys,
            "options": options,
        }
        for keys, options in _TASK_INDEXES
    ]
    if not dry_run:
        for keys, options in _TASK_INDEXES:
            await db.tasks.create_index(keys, **options)
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
    )
