from __future__ import annotations

from typing import Any

from .runner import MigrationResult


VERSION = "20260630_0002"
NAME = "insurance_indexes"
DESCRIPTION = (
    "Create indexes for the Insurance Register: tenant/expiry lookup, the "
    "no-duplicate-policy unique constraint, the insurance type master, and the "
    "expiry-notification ledger."
)


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = [
        {"operation": "create_index", "collection": "insurance_policies",
         "keys": [("organization_id", 1), ("project_id", 1), ("date_of_expiry", 1)]},
        {"operation": "create_index", "collection": "insurance_policies", "keys": "contract_id"},
        {"operation": "create_index", "collection": "insurance_policies", "keys": "insurance_type"},
        {"operation": "create_index", "collection": "insurance_policies", "keys": "created_by"},
        {"operation": "create_index", "collection": "insurance_policies",
         "keys": [("project_id", 1), ("contract_id", 1), ("insurance_type", 1), ("policy_number", 1)],
         "unique": True, "note": "no duplicate policy number per contract + type"},
        {"operation": "create_index", "collection": "insurance_types",
         "keys": [("organization_id", 1), ("name", 1)]},
        {"operation": "create_index", "collection": "insurance_notifications",
         "keys": [("insurance_id", 1), ("notification_type", 1)]},
    ]
    if not dry_run:
        await db.insurance_policies.create_index(
            [("organization_id", 1), ("project_id", 1), ("date_of_expiry", 1)], background=True
        )
        await db.insurance_policies.create_index("contract_id", background=True)
        await db.insurance_policies.create_index("insurance_type", background=True)
        await db.insurance_policies.create_index("created_by", background=True)
        await db.insurance_policies.create_index(
            [("project_id", 1), ("contract_id", 1), ("insurance_type", 1), ("policy_number", 1)],
            unique=True,
            partialFilterExpression={"policy_number": {"$type": "string"}},
            background=True,
        )
        await db.insurance_types.create_index([("organization_id", 1), ("name", 1)], background=True)
        await db.insurance_notifications.create_index(
            [("insurance_id", 1), ("notification_type", 1)], background=True
        )
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
    )
