from __future__ import annotations

from typing import Any, Dict, List

from .runner import MigrationResult


VERSION = "20260722_0001"
NAME = "langgraph_checkpoint_ttl_compatibility"
DESCRIPTION = "Align checkpoint TTL index names with langgraph-checkpoint-mongodb runtime initialization."

TTL_SECONDS = 30 * 86400
DEFAULT_TTL_INDEX = "created_at_1"
LEGACY_CHECKPOINT_TTL_INDEX = "arb_checkpoint_created_ttl"
COLLECTIONS = (
    "arbitration_langgraph_checkpoints",
    "arbitration_langgraph_checkpoint_writes",
)


def _is_compatible_ttl(spec: Dict[str, Any]) -> bool:
    return list(spec.get("key") or []) == [("created_at", 1)] and spec.get("expireAfterSeconds") == TTL_SECONDS


async def _ensure_runtime_ttl_index(collection: Any) -> Dict[str, Any]:
    indexes = await collection.index_information()
    default = indexes.get(DEFAULT_TTL_INDEX)
    if default is not None:
        if not _is_compatible_ttl(default):
            raise RuntimeError(f"Incompatible {DEFAULT_TTL_INDEX} checkpoint TTL index")
        return {"operation": "reuse_index", "name": DEFAULT_TTL_INDEX}

    aliases = [name for name, spec in indexes.items() if list(spec.get("key") or []) == [("created_at", 1)]]
    for name in aliases:
        if not _is_compatible_ttl(indexes[name]):
            raise RuntimeError(f"Incompatible checkpoint TTL index: {name}")
    for name in aliases:
        await collection.drop_index(name)
    created = await collection.create_index("created_at", expireAfterSeconds=TTL_SECONDS, background=True)
    if created != DEFAULT_TTL_INDEX:
        raise RuntimeError(f"MongoDB created unexpected checkpoint TTL index: {created}")
    return {"operation": "replace_index" if aliases else "create_index", "name": created, "replaced": aliases}


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations: List[Dict[str, Any]] = []
    for collection_name in COLLECTIONS:
        operation: Dict[str, Any] = {
            "operation": "ensure_runtime_ttl_index",
            "collection": collection_name,
            "keys": "created_at",
            "name": DEFAULT_TTL_INDEX,
            "expire_after_seconds": TTL_SECONDS,
        }
        if not dry_run:
            operation.update(await _ensure_runtime_ttl_index(db[collection_name]))
        operations.append(operation)
    return MigrationResult(version=VERSION, name=NAME, status="dry_run" if dry_run else "applied", operations=operations)


async def downgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations: List[Dict[str, Any]] = [
        {"operation": "drop_index", "collection": name, "name": DEFAULT_TTL_INDEX}
        for name in COLLECTIONS
    ]
    operations.append(
        {
            "operation": "create_index",
            "collection": "arbitration_langgraph_checkpoints",
            "name": LEGACY_CHECKPOINT_TTL_INDEX,
            "keys": "created_at",
            "expire_after_seconds": TTL_SECONDS,
        }
    )
    if not dry_run:
        for collection_name in COLLECTIONS:
            try:
                await db[collection_name].drop_index(DEFAULT_TTL_INDEX)
            except Exception:
                pass
        await db.arbitration_langgraph_checkpoints.create_index(
            "created_at",
            name=LEGACY_CHECKPOINT_TTL_INDEX,
            expireAfterSeconds=TTL_SECONDS,
            background=True,
        )
    return MigrationResult(version=VERSION, name=NAME, status="dry_run" if dry_run else "rolled_back", operations=operations)
