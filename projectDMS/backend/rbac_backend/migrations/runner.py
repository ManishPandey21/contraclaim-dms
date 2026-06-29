"""Small forward-only MongoDB migration runner.

The app still keeps startup index creation as a safety net, but production
promotion needs an explicit dry-run/apply command and an applied-migration
ledger.  This runner supplies that control without introducing a heavy external
dependency.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Sequence


MigrationUpgrade = Callable[[Any, bool], "MigrationResult | Awaitable[MigrationResult]"]

LEDGER_COLLECTION = "schema_migrations"


@dataclass(frozen=True)
class MigrationResult:
    version: str
    name: str
    status: str
    operations: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "status": self.status,
            "operations": self.operations,
            "warnings": self.warnings,
        }


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    description: str
    upgrade: MigrationUpgrade


def _collection(db: Any, name: str) -> Any:
    try:
        return db[name]
    except Exception:
        return getattr(db, name)


async def _to_list(cursor: Any) -> List[Dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=None)

    rows: List[Dict[str, Any]] = []
    async for row in cursor:
        rows.append(row)
    return rows


class MigrationRunner:
    """Run versioned migrations against a Motor-style database object."""

    def __init__(self, db: Any, migrations: Sequence[Migration] | None = None):
        self.db = db
        if migrations is None:
            from .catalog import MIGRATIONS

            migrations = MIGRATIONS
        self.migrations = sorted(migrations, key=lambda migration: migration.version)

    async def _ensure_ledger_index(self) -> None:
        await _collection(self.db, LEDGER_COLLECTION).create_index("version", unique=True, background=True)

    async def applied_versions(self) -> set[str]:
        cursor = _collection(self.db, LEDGER_COLLECTION).find({})
        docs = await _to_list(cursor)
        return {str(doc.get("version") or doc.get("_id")) for doc in docs if doc.get("version") or doc.get("_id")}

    def available_versions(self) -> List[str]:
        return [migration.version for migration in self.migrations]

    def selected_migrations(self, target: str | None = None) -> List[Migration]:
        if target is None:
            return list(self.migrations)
        selected = [migration for migration in self.migrations if migration.version <= target]
        if not any(migration.version == target for migration in self.migrations):
            raise ValueError(f"Unknown migration target: {target}")
        return selected

    async def plan(self, target: str | None = None) -> List[Dict[str, Any]]:
        applied = await self.applied_versions()
        rows = []
        for migration in self.selected_migrations(target):
            rows.append(
                {
                    "version": migration.version,
                    "name": migration.name,
                    "description": migration.description,
                    "status": "applied" if migration.version in applied else "pending",
                }
            )
        return rows

    async def run(self, *, apply: bool = False, target: str | None = None) -> List[MigrationResult]:
        if apply:
            await self._ensure_ledger_index()

        applied = await self.applied_versions()
        results: List[MigrationResult] = []
        for migration in self.selected_migrations(target):
            if migration.version in applied:
                results.append(
                    MigrationResult(
                        version=migration.version,
                        name=migration.name,
                        status="skipped",
                        operations=[],
                    )
                )
                continue

            raw_result = migration.upgrade(self.db, not apply)
            result = await raw_result if inspect.isawaitable(raw_result) else raw_result
            status = "applied" if apply else "dry_run"
            result = MigrationResult(
                version=result.version,
                name=result.name,
                status=status,
                operations=result.operations,
                warnings=result.warnings,
            )
            results.append(result)

            if apply:
                await _collection(self.db, LEDGER_COLLECTION).update_one(
                    {"_id": migration.version},
                    {
                        "$setOnInsert": {
                            "_id": migration.version,
                            "version": migration.version,
                            "name": migration.name,
                            "description": migration.description,
                            "operations": result.operations,
                            "warnings": result.warnings,
                            "applied_at": datetime.now(timezone.utc),
                        }
                    },
                    upsert=True,
                )
                applied.add(migration.version)

        return results
