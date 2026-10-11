"""`permissions.name` is the catalogue's identity, and nothing enforced it.

R-A8I's Gate 8 restore drill produced **396 permission rows for 198 distinct
names**. The backend restarted before the Mongo restore had run, re-seeded the
catalogue, and the subsequent `mongorestore` inserted the dumped rows on top. The
collection carried only its `_id_` index, so the collision was not a conflict -
it was two rows, and the only sign anything had happened was
`1 document failed to restore` in the restore log.

Identity here is not in doubt. `PermissionService.get_permission_by_name` reads
`find_one({"name": name})`, `create_permission` refuses a name that already
exists, and role documents grant permissions **by name**. So `name` is the key the
whole system already treats as unique - it just had no constraint saying so, and a
uniqueness rule that lives only in a read-then-write in application code is not a
rule, it is a race.

Three things are asserted here, and each closes one way the duplicate got in:

* the constraint exists, and exists in both places a collection is set up - the
  startup index pass for a new deployment and a migration for the ones already
  running;
* the seeder no longer decides by reading first, so two seeders racing produce one
  row rather than two;
* the migration collapses pre-existing duplicates **deterministically** before
  creating the index, because a unique index over a collection that already
  contains duplicates simply fails to build, and a migration that reported that as
  success would leave the constraint absent and the operator believing otherwise.

The fake collection below implements exactly the Mongo semantics under test -
unique-index enforcement and `DuplicateKeyError` on insert - and nothing else. The
real-engine counterpart lives in the integration suite, which needs a replica set;
the point of the fake is that these can run in ordinary CI where the constraint
regression would otherwise be invisible until a restore drill.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest
from pymongo.errors import DuplicateKeyError

from rbac_backend.migrations import v20260906_0001_permission_name_unique as migration
from rbac_backend.models.permission import DEFAULT_PERMISSIONS

#: The catalogue as it stands. Asserted as a number so a silent halving is a test
#: failure, and read from the source so growing the catalogue is not.
CATALOGUE_SIZE = len(DEFAULT_PERMISSIONS)


class FakeCollection:
    """Just enough Mongo to have a unique index and to break it."""

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None) -> None:
        self.rows: List[Dict[str, Any]] = [dict(row) for row in (rows or [])]
        self.unique_keys: List[str] = []
        self.indexes: List[Dict[str, Any]] = []
        self._next_id = 1

    # -- indexes ---------------------------------------------------------- #

    async def create_index(self, keys, **options):
        field = keys[0][0] if isinstance(keys, list) else keys
        if options.get("unique"):
            seen = set()
            for row in self.rows:
                value = row.get(field)
                if value in seen:
                    raise DuplicateKeyError(f"E11000 duplicate key error: {field}: {value!r}")
                seen.add(value)
            self.unique_keys.append(field)
        self.indexes.append({"field": field, **options})
        return options.get("name", field)

    # -- reads ------------------------------------------------------------ #

    def _matches(self, row, query):
        for key, expected in (query or {}).items():
            if isinstance(expected, dict) and "$ne" in expected:
                if row.get(key) == expected["$ne"]:
                    return False
                continue
            if row.get(key) != expected:
                return False
        return True

    async def find_one(self, query=None, *_a, **_k):
        # A real read suspends. Without this the two "concurrent" seeders below
        # run to completion one after the other and the race they exist to
        # reproduce never happens - the test would pass against the very code
        # that produced 396 rows.
        await asyncio.sleep(0)
        for row in self.rows:
            if self._matches(row, query):
                return dict(row)
        return None

    async def count_documents(self, query=None):
        return len([row for row in self.rows if self._matches(row, query)])

    def find(self, query=None, *_a, **_k):
        matched = [dict(row) for row in self.rows if self._matches(row, query)]

        class _Cursor:
            def __aiter__(self_inner):
                async def gen():
                    for row in matched:
                        yield row

                return gen()

            async def to_list(self_inner, length=None):
                return matched[:length] if length else matched

        return _Cursor()

    def aggregate(self, pipeline):
        """One pipeline shape: group by a field, keep the groups with >1 row."""
        group = next(stage["$group"] for stage in pipeline if "$group" in stage)
        field = group["_id"].lstrip("$")
        buckets: Dict[Any, List[Dict[str, Any]]] = {}
        for row in self.rows:
            buckets.setdefault(row.get(field), []).append(dict(row))
        results = [
            {"_id": key, "count": len(rows), "docs": rows}
            for key, rows in buckets.items()
            if len(rows) > 1
        ]

        class _Cursor:
            def __aiter__(self_inner):
                async def gen():
                    for row in results:
                        yield row

                return gen()

            async def to_list(self_inner, length=None):
                return results

        return _Cursor()

    # -- writes ----------------------------------------------------------- #

    def _would_duplicate(self, candidate, ignore_index=None):
        for field in self.unique_keys:
            for index, row in enumerate(self.rows):
                if index == ignore_index:
                    continue
                if row.get(field) == candidate.get(field):
                    return field
        return None

    async def insert_one(self, document):
        await asyncio.sleep(0)
        document = dict(document)
        document.setdefault("_id", f"fake-{self._next_id}")
        self._next_id += 1
        field = self._would_duplicate(document)
        if field:
            raise DuplicateKeyError(
                f"E11000 duplicate key error collection: permissions index: {field}"
            )
        self.rows.append(document)
        return type("Result", (), {"inserted_id": document["_id"]})()

    async def update_one(self, query, update, upsert=False):
        for index, row in enumerate(self.rows):
            if self._matches(row, query):
                merged = {**row, **update.get("$set", {}), **update.get("$setOnInsert", {})}
                field = self._would_duplicate(merged, ignore_index=index)
                if field:
                    raise DuplicateKeyError(f"E11000 duplicate key error: {field}")
                self.rows[index] = merged
                return type("Result", (), {"matched_count": 1, "upserted_id": None})()
        if not upsert:
            return type("Result", (), {"matched_count": 0, "upserted_id": None})()
        document = {**{k: v for k, v in (query or {}).items() if not isinstance(v, dict)}}
        document.update(update.get("$setOnInsert", {}))
        document.update(update.get("$set", {}))
        result = await self.insert_one(document)
        return type("Result", (), {"matched_count": 0, "upserted_id": result.inserted_id})()

    async def delete_one(self, query):
        for index, row in enumerate(self.rows):
            if self._matches(row, query):
                del self.rows[index]
                return type("Result", (), {"deleted_count": 1})()
        return type("Result", (), {"deleted_count": 0})()

    async def delete_many(self, query):
        before = len(self.rows)
        self.rows = [row for row in self.rows if not self._matches(row, query)]
        return type("Result", (), {"deleted_count": before - len(self.rows)})()


class FakeDatabase:
    def __init__(self) -> None:
        self._collections: Dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collections.setdefault(name, FakeCollection())

    def __getattr__(self, name: str) -> FakeCollection:
        return self[name]


@asynccontextmanager
async def _service(db):
    """A `PermissionService` bound to a fake database.

    An async fixture would be simpler and is unsupported here: each test gets its
    own `asyncio.run` loop, so a fixture-created service outlives the loop it was
    built on (`backend/conftest.py`).
    """
    from rbac_backend.services.permission_service import PermissionService

    service = PermissionService()
    service.db = db
    yield service


def _names(collection: FakeCollection) -> List[str]:
    return [row["name"] for row in collection.rows]


# --------------------------------------------------------------------------- #
# The constraint exists, in both places a collection gets set up
# --------------------------------------------------------------------------- #


def test_the_startup_index_pass_declares_the_unique_name_index() -> None:
    """A new deployment never runs the migration; it runs `ensure_indexes`.

    Putting the constraint only in the migration would leave every fresh install
    without it, which is the shape of gap this whole finding is.
    """
    from pathlib import Path

    source = Path(
        __file__
    ).resolve().parents[1].joinpath("core", "database.py").read_text(encoding="utf-8")

    assert "db.permissions.create_index" in source, (
        "ensure_indexes creates no index on `permissions` at all - the collection "
        "carried only `_id_`, which is how a restore produced 396 rows for 198 names"
    )
    assert 'unique=True' in source.split("db.permissions.create_index", 1)[1][:400], (
        "the permissions index is not unique, so it constrains nothing"
    )


def test_the_migration_is_registered_in_the_catalog() -> None:
    from rbac_backend.migrations.catalog import MIGRATIONS

    versions = [m.version for m in MIGRATIONS]
    assert migration.VERSION in versions, (
        "an unregistered migration never runs, so the constraint would never reach "
        "a deployment that already exists"
    )


def test_the_migration_dry_run_writes_nothing() -> None:
    async def scenario():
        db = FakeDatabase()
        db.permissions.rows = [
            {"_id": "1", "name": "users:read"},
            {"_id": "2", "name": "users:read"},
        ]

        result = await migration.upgrade(db, dry_run=True)

        assert result.status == "dry_run"
        assert len(db.permissions.rows) == 2, "a dry run deleted a row"
        assert db.permissions.indexes == [], "a dry run created an index"

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# Fresh seed
# --------------------------------------------------------------------------- #


def test_a_fresh_seed_produces_one_row_per_catalogue_entry() -> None:
    async def scenario():
        db = FakeDatabase()
        await migration.upgrade(db, dry_run=False)

        async with _service(db) as service:
            await service.create_default_permissions()

        names = _names(db.permissions)
        assert len(names) == CATALOGUE_SIZE
        assert len(set(names)) == CATALOGUE_SIZE
        assert len(names) - len(set(names)) == 0

    asyncio.run(scenario())


def test_seeding_twice_changes_nothing() -> None:
    async def scenario():
        db = FakeDatabase()
        await migration.upgrade(db, dry_run=False)

        async with _service(db) as service:
            await service.create_default_permissions()
            first = sorted(_names(db.permissions))
            await service.create_default_permissions()
            second = sorted(_names(db.permissions))

        assert first == second
        assert len(second) == CATALOGUE_SIZE

    asyncio.run(scenario())


def test_a_restore_over_a_seeded_catalogue_cannot_double_it() -> None:
    """R-A8I's exact sequence: seed, then a restore inserts the dumped rows.

    Under the constraint the second insert is refused instead of accepted, which
    is the difference between `1 document failed to restore` in a log nobody reads
    and 198 refusals an operator cannot miss.
    """
    async def scenario():
        db = FakeDatabase()
        await migration.upgrade(db, dry_run=False)

        async with _service(db) as service:
            await service.create_default_permissions()

            restored = 0
            refused = 0
            for row in list(db.permissions.rows):
                dumped = {**row, "_id": f"restored-{row['name']}"}
                try:
                    await db.permissions.insert_one(dumped)
                    restored += 1
                except DuplicateKeyError:
                    refused += 1

            assert restored == 0
            assert refused == CATALOGUE_SIZE

            # And a seed afterwards is still idempotent.
            await service.create_default_permissions()

        names = _names(db.permissions)
        assert len(names) == CATALOGUE_SIZE
        assert len(set(names)) == CATALOGUE_SIZE

    asyncio.run(scenario())


def test_two_seeders_racing_produce_one_row_per_name() -> None:
    """The seeder read first and wrote second, with nothing in between.

    Two startups against one database - a rolling restart, a worker and the web
    tier - is enough. A `DuplicateKeyError` on a name that already exists is the
    seeder finding its own work already done, so it is absorbed; any other error
    still surfaces.
    """
    async def scenario():
        db = FakeDatabase()
        await migration.upgrade(db, dry_run=False)

        async with _service(db) as first:
            async with _service(db) as second:
                outcomes = await asyncio.gather(
                    first.create_default_permissions(),
                    second.create_default_permissions(),
                )

        names = _names(db.permissions)
        assert len(names) == CATALOGUE_SIZE, f"{len(names)} rows for {CATALOGUE_SIZE} names"
        assert len(set(names)) == CATALOGUE_SIZE
        # Both must report success. The count alone is not enough: a seeder that
        # aborted its loop on the first collision leaves a correct-looking
        # catalogue only because the other one happened to finish first, and the
        # next entry added to the catalogue would be the one that goes missing.
        assert outcomes == [True, True], outcomes

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# The migration, over a collection that already has duplicates
# --------------------------------------------------------------------------- #


def test_the_migration_collapses_duplicates_before_creating_the_index() -> None:
    """A unique index over a collection with duplicates does not build.

    So the collapse has to happen first, and it has to be *deterministic*: which
    of two rows survives decides which description and which `_id` the role
    grants point at, and "whichever Mongo returned first" is the same
    order-dependence that `graph_codes_denied` documents one layer down.
    """
    async def scenario():
        old = datetime(2026, 1, 1)
        db = FakeDatabase()
        db.permissions.rows = [
            {"_id": "b", "name": "users:read", "created_at": old + timedelta(days=1)},
            {"_id": "a", "name": "users:read", "created_at": old},
            {"_id": "c", "name": "users:read", "created_at": old},
            {"_id": "d", "name": "users:create", "created_at": old},
        ]

        result = await migration.upgrade(db, dry_run=False)

        assert result.status == "applied"
        survivors = {row["name"]: row["_id"] for row in db.permissions.rows}
        assert survivors == {"users:read": "a", "users:create": "d"}, (
            "the survivor must be the earliest row, tie-broken by _id - not "
            "whichever the engine happened to return"
        )
        assert "name" in db.permissions.unique_keys

    asyncio.run(scenario())


def test_the_migration_is_idempotent() -> None:
    async def scenario():
        db = FakeDatabase()
        db.permissions.rows = [
            {"_id": "a", "name": "users:read"},
            {"_id": "b", "name": "users:read"},
        ]

        await migration.upgrade(db, dry_run=False)
        first = [dict(row) for row in db.permissions.rows]
        await migration.upgrade(db, dry_run=False)

        assert db.permissions.rows == first

    asyncio.run(scenario())


def test_the_migration_reports_what_it_removed() -> None:
    """A silent de-duplication is a silent data deletion.

    The operator has to be able to see, from the migration output alone, that rows
    were removed and how many - otherwise the only record of it is the row count
    changing.
    """
    async def scenario():
        db = FakeDatabase()
        db.permissions.rows = [
            {"_id": "a", "name": "users:read"},
            {"_id": "b", "name": "users:read"},
            {"_id": "c", "name": "users:create"},
        ]

        result = await migration.upgrade(db, dry_run=False)

        removals = [op for op in result.operations if op.get("operation") == "delete_duplicate"]
        assert len(removals) == 1
        assert removals[0]["name"] == "users:read"
        assert removals[0]["collection"] == "permissions"

    asyncio.run(scenario())


def test_a_dry_run_names_the_duplicates_it_would_remove() -> None:
    """The dry run is the only look an operator gets before the deletion."""
    async def scenario():
        db = FakeDatabase()
        db.permissions.rows = [
            {"_id": "a", "name": "users:read"},
            {"_id": "b", "name": "users:read"},
        ]

        result = await migration.upgrade(db, dry_run=True)

        removals = [op for op in result.operations if op.get("operation") == "delete_duplicate"]
        assert len(removals) == 1
        assert removals[0]["name"] == "users:read"

    asyncio.run(scenario())
