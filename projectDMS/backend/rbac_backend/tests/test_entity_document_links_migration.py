from __future__ import annotations

from typing import Any

from rbac_backend.migrations import catalog
from rbac_backend.migrations.v20260820_0001_entity_document_links import (
    NAME,
    VERSION,
    upgrade,
)


class _Collection:
    def __init__(self) -> None:
        self.indexes: list[dict[str, Any]] = []

    async def create_index(self, keys: Any, **kwargs: Any) -> str:
        self.indexes.append({"keys": keys, **kwargs})
        return "idx"


class _Database:
    def __init__(self) -> None:
        self.collections: dict[str, _Collection] = {}

    def __getitem__(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection())


def test_relationship_migration_is_registered_in_lexical_order() -> None:
    versions = [migration.version for migration in catalog.MIGRATIONS]

    assert VERSION in versions
    assert versions == sorted(versions)


async def test_relationship_migration_dry_run_has_no_database_effect() -> None:
    db = _Database()

    result = await upgrade(db, dry_run=True)

    assert result.status == "dry_run"
    assert len(result.operations) == 6
    assert db.collections == {}


async def test_relationship_migration_builds_active_unique_and_traversal_indexes() -> None:
    db = _Database()

    await upgrade(db, dry_run=False)

    indexes = db.collections["entity_document_links"].indexes
    unique = [index for index in indexes if index.get("unique")]
    assert unique == [
        {
            "keys": [
                ("organization_id", 1),
                ("project_id", 1),
                ("target_type", 1),
                ("target_id", 1),
                ("document_id", 1),
                ("relationship_role", 1),
            ],
            "name": "uq_entity_document_links_active",
            "unique": True,
            "partialFilterExpression": {"removed_at": None},
            "background": True,
        }
    ]
    assert len(indexes) == 6
    assert all(index.get("background") for index in indexes)
