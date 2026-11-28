from __future__ import annotations

from typing import Any, Dict, List

from .runner import MigrationResult


VERSION = "20261114_0001"
NAME = "skill_registry"
DESCRIPTION = "Create indexes for the versioned drafting skill registry (skills, versions, bindings)."

INDEXES: List[Dict[str, Any]] = [
    {
        "collection": "skills",
        "keys": [("owner_scope", 1), ("skill_key", 1)],
        "name": "skill_owner_key_unique",
        "unique": True,
    },
    {
        "collection": "skill_versions",
        "keys": [("skill_key", 1), ("version", 1)],
        "name": "skill_version_unique",
        "unique": True,
    },
    {
        "collection": "skill_versions",
        "keys": [("skill_key", 1), ("status", 1)],
        "name": "skill_version_status",
        "unique": False,
    },
    {
        "collection": "skill_bindings",
        "keys": [("organization_id", 1), ("project_id", 1), ("skill_key", 1)],
        "name": "skill_binding_scope_unique",
        "unique": True,
    },
]


async def apply_indexes(db: Any, indexes: List[Dict[str, Any]], dry_run: bool) -> List[Dict[str, Any]]:
    operations: List[Dict[str, Any]] = []
    for index in indexes:
        operations.append({"operation": "create_index", **index})
        if not dry_run:
            options = {
                key: value
                for key, value in index.items()
                if key not in {"collection", "keys"}
            }
            await db[index["collection"]].create_index(index["keys"], background=True, **options)
    return operations


async def drop_indexes(db: Any, indexes: List[Dict[str, Any]], dry_run: bool) -> List[Dict[str, Any]]:
    operations: List[Dict[str, Any]] = []
    for index in indexes:
        operations.append(
            {"operation": "drop_index", "collection": index["collection"], "name": index["name"]}
        )
        if not dry_run:
            try:
                await db[index["collection"]].drop_index(index["name"])
            except Exception:
                pass
    return operations


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
