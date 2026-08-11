from __future__ import annotations

from typing import Any

from .runner import MigrationResult


VERSION = "20260811_0001"
NAME = "key_date_eot_revision_workflow"
DESCRIPTION = (
    "Create integrity and scope indexes for frozen Original Key Date baselines, "
    "successive EOT submissions, independent determinations, and milestone items."
)


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = [
        {
            "operation": "create_index", "collection": "key_date_baselines",
            "keys": [("organization_id", 1), ("project_id", 1), ("contract_id", 1)],
            "unique": True, "note": "one contractual baseline per project/contract",
        },
        {
            "operation": "create_index", "collection": "key_date_eot_submissions",
            "keys": [("organization_id", 1), ("project_id", 1), ("contract_id", 1), ("revision_number", 1)],
            "unique": True, "note": "no duplicate EOT-N allocation under concurrency",
        },
        {
            "operation": "create_index", "collection": "key_date_eot_submissions",
            "keys": [("organization_id", 1), ("project_id", 1), ("contract_id", 1), ("status", 1)],
        },
        {
            "operation": "create_index", "collection": "key_date_eot_submission_items",
            "keys": [("eot_submission_id", 1), ("key_date_id", 1)], "unique": True,
        },
        {
            "operation": "create_index", "collection": "key_date_eot_determinations",
            "keys": [("organization_id", 1), ("project_id", 1), ("contract_id", 1), ("frozen_at", 1)],
        },
        {
            "operation": "create_index", "collection": "key_date_eot_determinations",
            "keys": [("organization_id", 1), ("project_id", 1), ("contract_id", 1), ("determination_reference", 1)],
            "unique": True, "note": "no duplicate client determination reference per contract",
        },
        {
            "operation": "create_index", "collection": "key_date_eot_determination_items",
            "keys": [("determination_id", 1), ("key_date_id", 1)], "unique": True,
        },
    ]
    if not dry_run:
        await db.key_date_baselines.create_index(
            [("organization_id", 1), ("project_id", 1), ("contract_id", 1)],
            unique=True, background=True,
        )
        await db.key_date_eot_submissions.create_index(
            [("organization_id", 1), ("project_id", 1), ("contract_id", 1), ("revision_number", 1)],
            unique=True, background=True,
        )
        await db.key_date_eot_submissions.create_index(
            [("organization_id", 1), ("project_id", 1), ("contract_id", 1), ("status", 1)],
            background=True,
        )
        await db.key_date_eot_submission_items.create_index(
            [("eot_submission_id", 1), ("key_date_id", 1)], unique=True, background=True,
        )
        await db.key_date_eot_determinations.create_index(
            [("organization_id", 1), ("project_id", 1), ("contract_id", 1), ("frozen_at", 1)],
            background=True,
        )
        await db.key_date_eot_determinations.create_index(
            [("organization_id", 1), ("project_id", 1), ("contract_id", 1), ("determination_reference", 1)],
            unique=True,
            partialFilterExpression={"determination_reference": {"$type": "string"}},
            background=True,
        )
        await db.key_date_eot_determination_items.create_index(
            [("determination_id", 1), ("key_date_id", 1)], unique=True, background=True,
        )
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
    )
