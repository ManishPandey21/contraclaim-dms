from __future__ import annotations

from typing import Any

from ..initial_data.seed_catalog import SEED_CATALOG_ID, seed_catalog_record
from .runner import MigrationResult


VERSION = "20260629_0002"
NAME = "rbac_seed_catalog_version"
DESCRIPTION = "Record the deterministic RBAC permission/role seed catalog version and digest."


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    record = seed_catalog_record()
    operations = [
        {
            "operation": "upsert_one",
            "collection": "seed_catalog_versions",
            "id": SEED_CATALOG_ID,
            "version": record["version"],
            "digest": record["digest"],
            "permission_count": record["permission_count"],
            "role_count": record["role_count"],
            "validation_issues": record["validation_issues"],
        }
    ]
    warnings = list(record["validation_issues"])
    if not dry_run:
        await db.seed_catalog_versions.update_one(
            {"_id": SEED_CATALOG_ID},
            {
                "$set": record,
                "$setOnInsert": {"created_at": record["updated_at"]},
            },
            upsert=True,
        )
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
        warnings=warnings,
    )
