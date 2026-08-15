"""Indexes for the page-extraction collections.

The unique (document_id, extraction_run_id, page_number) index is the one that
matters: DocumentPageStore.record_pages upserts by that triple, and without the
constraint two concurrent workers reclaiming the same job can each insert a row
for the same page. The page would then have two contradictory pieces of
evidence and publish_run's count would pass while the run was inconsistent.
"""

from __future__ import annotations

from typing import Any, Dict, List

from rbac_backend.migrations import catalog
from rbac_backend.migrations.v20260814_0001_document_extraction_indexes import (
    NAME,
    VERSION,
    upgrade,
)
from rbac_backend.services.extraction_adapters.document_page_store import (
    DOCUMENT_EXTRACTION_HEADS,
    DOCUMENT_OCR_BATCHES,
    DOCUMENT_OCR_PAGES,
)


class _FakeCollection:
    def __init__(self) -> None:
        self.indexes: List[Dict[str, Any]] = []

    async def create_index(self, keys: Any, **kwargs: Any) -> str:
        self.indexes.append({"keys": keys, **kwargs})
        return "idx"


class _FakeDb:
    def __init__(self) -> None:
        self.collections: Dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection())

    def __getattr__(self, name: str) -> _FakeCollection:
        # Motor supports both styles; the migration uses subscript so the
        # collection names can stay as shared constants.
        if name.startswith("_"):
            raise AttributeError(name)
        return self.collections.setdefault(name, _FakeCollection())


def test_migration_is_registered_in_the_catalog() -> None:
    versions = [migration.version for migration in catalog.MIGRATIONS]

    assert VERSION in versions


def test_catalog_stays_in_lexical_version_order() -> None:
    versions = [migration.version for migration in catalog.MIGRATIONS]

    assert versions == sorted(versions)


async def test_dry_run_reports_operations_without_touching_the_database() -> None:
    db = _FakeDb()

    result = await upgrade(db, dry_run=True)

    assert result.status == "dry_run"
    assert result.operations
    assert db.collections == {}


async def test_page_rows_get_the_unique_run_scoped_index() -> None:
    db = _FakeDb()

    await upgrade(db, dry_run=False)

    pages = db.collections[DOCUMENT_OCR_PAGES]
    unique = [index for index in pages.indexes if index.get("unique")]

    assert len(unique) == 1
    assert unique[0]["keys"] == [
        ("document_id", 1),
        ("extraction_run_id", 1),
        ("page_number", 1),
    ]


async def test_extraction_heads_are_unique_per_document() -> None:
    db = _FakeDb()

    await upgrade(db, dry_run=False)

    heads = db.collections[DOCUMENT_EXTRACTION_HEADS]

    assert any(
        index.get("unique") and index["keys"] == "document_id"
        for index in heads.indexes
    )


async def test_batches_are_indexed_by_document_and_run() -> None:
    db = _FakeDb()

    await upgrade(db, dry_run=False)

    batches = db.collections[DOCUMENT_OCR_BATCHES]

    assert any(
        index["keys"] == [("document_id", 1), ("extraction_run_id", 1)]
        for index in batches.indexes
    )


async def test_pages_are_indexed_for_tenant_scoped_reads() -> None:
    db = _FakeDb()

    await upgrade(db, dry_run=False)

    pages = db.collections[DOCUMENT_OCR_PAGES]

    assert any(
        index["keys"] == [("organization_id", 1), ("project_id", 1)]
        for index in pages.indexes
    )


async def test_every_index_is_built_in_the_background() -> None:
    # These collections grow with every processed page; a foreground build
    # would block writes on a live deployment.
    db = _FakeDb()

    await upgrade(db, dry_run=False)

    for collection in db.collections.values():
        assert all(index.get("background") for index in collection.indexes)


async def test_applied_result_names_the_migration() -> None:
    db = _FakeDb()

    result = await upgrade(db, dry_run=False)

    assert result.status == "applied"
    assert result.version == VERSION
    assert result.name == NAME
