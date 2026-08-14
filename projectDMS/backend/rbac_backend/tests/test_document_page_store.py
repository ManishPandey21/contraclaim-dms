"""DocumentPageStore: per-page OCR evidence for general documents."""

from __future__ import annotations

from typing import Any

import pytest
from bson import ObjectId

from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction_adapters.document_page_store import (
    DOCUMENT_EXTRACTION_HEADS,
    DOCUMENT_OCR_BATCHES,
    DOCUMENT_OCR_PAGES,
    DocumentPageStore,
    to_document_page_record,
)


class _FakeCollection:
    def __init__(self) -> None:
        self.operations: list[tuple[str, Any, Any]] = []
        self.count_result = 0

    async def insert_one(self, document: dict[str, Any]) -> Any:
        self.operations.append(("insert_one", document, None))

        class _Result:
            inserted_id = ObjectId()

        return _Result()

    async def update_one(self, query: dict, update: dict, **kwargs: Any) -> None:
        self.operations.append(("update_one", query, update))

    async def bulk_write(self, requests: list[Any]) -> None:
        self.operations.append(("bulk_write", requests, None))

    async def count_documents(self, query: dict, **kwargs: Any) -> int:
        self.operations.append(("count_documents", query, kwargs))
        return self.count_result


class _FakeDb:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection())


def _page(number: int, page_class: PageClass = PageClass.TEXT_NATIVE) -> ExtractedPage:
    return ExtractedPage(
        number=number,
        text=f"text {number}",
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=page_class,
            char_count=6,
            image_count=3,
            image_coverage=0.12,
            table_count=1,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
    )


def _store(db: _FakeDb, run_id: str = "run-1") -> DocumentPageStore:
    return DocumentPageStore(
        db=db,
        document_id="doc-1",
        organization_id="org-1",
        project_id=None,
        extraction_run_id=run_id,
    )


def test_record_carries_page_geometry_and_class() -> None:
    record = to_document_page_record(
        _page(4, PageClass.TABLE_HEAVY),
        document_id="doc-1",
        organization_id="org-1",
        project_id="proj-1",
        extraction_run_id="run-1",
    )

    assert record["page_number"] == 4
    assert record["page_class"] == "table_heavy"
    assert record["width"] == 595.0
    assert record["height"] == 842.0
    assert record["rotation"] == 0
    assert record["image_count"] == 3
    assert record["table_count"] == 1
    assert record["source"] == "text_layer"
    assert record["status"] == "text_layer"
    assert record["source_pdf_page_link"] == "document:doc-1#page=4"


def test_record_scopes_to_org_and_project() -> None:
    record = to_document_page_record(
        _page(1),
        document_id="doc-1",
        organization_id="org-1",
        project_id=None,
        extraction_run_id="run-1",
    )

    assert record["organization_id"] == "org-1"
    assert record["project_id"] is None


def test_record_carries_the_extraction_run_id() -> None:
    record = to_document_page_record(
        _page(1),
        document_id="doc-1",
        organization_id="org-1",
        project_id=None,
        extraction_run_id="run-7",
    )

    assert record["extraction_run_id"] == "run-7"


async def test_store_writes_batches_to_the_batches_collection() -> None:
    db = _FakeDb()
    store = _store(db)

    batch_id = await store.begin_batch(page_start=1, page_end=2, retry=False)
    await store.finish_batch(batch_id, status="completed")

    operations = db[DOCUMENT_OCR_BATCHES].operations
    assert operations[0][0] == "insert_one"
    assert operations[0][1]["status"] == "running"
    assert operations[1][0] == "update_one"
    assert isinstance(operations[1][1]["_id"], ObjectId)


async def test_finishing_a_batch_from_another_process_converts_the_id() -> None:
    # A reclaimed job finishes a batch it did not create, so the id arrives as
    # a plain string and must be converted back to the persisted BSON type.
    db = _FakeDb()
    store = _store(db)
    orphan = str(ObjectId())

    await store.finish_batch(orphan, status="failed", error="boom")

    query = db[DOCUMENT_OCR_BATCHES].operations[0][1]
    assert isinstance(query["_id"], ObjectId)
    assert str(query["_id"]) == orphan


async def test_record_pages_upserts_the_version_without_deleting_evidence() -> None:
    db = _FakeDb()
    store = _store(db, run_id="run-2")

    await store.record_pages([_page(1), _page(2)])

    operations = db[DOCUMENT_OCR_PAGES].operations
    assert [operation[0] for operation in operations] == ["bulk_write"]
    requests = operations[0][1]
    assert all(request._filter["extraction_run_id"] == "run-2" for request in requests)


async def test_record_pages_with_no_pages_writes_nothing() -> None:
    db = _FakeDb()
    store = _store(db)

    await store.record_pages([])

    assert db[DOCUMENT_OCR_PAGES].operations == []


async def test_new_run_never_deletes_the_previous_run_before_publish() -> None:
    db = _FakeDb()
    first = _store(db, run_id="run-old")
    second = _store(db, run_id="run-new")

    await first.record_pages([_page(1), _page(2)])
    await second.record_pages([_page(1)])  # simulate a crash before page 2/publish

    assert all(op[0] != "delete_many" for op in db[DOCUMENT_OCR_PAGES].operations)


async def test_publish_requires_the_complete_page_set_before_advancing_head() -> None:
    db = _FakeDb()
    store = _store(db, run_id="run-new")
    db[DOCUMENT_OCR_PAGES].count_result = 1

    with pytest.raises(RuntimeError, match="1/2 pages"):
        await store.publish_run(expected_page_numbers=[1, 2])
    assert db[DOCUMENT_EXTRACTION_HEADS].operations == []

    db[DOCUMENT_OCR_PAGES].count_result = 2
    await store.publish_run(expected_page_numbers=[1, 2])
    assert db[DOCUMENT_EXTRACTION_HEADS].operations[-1][0] == "update_one"


async def test_published_head_points_at_this_run() -> None:
    db = _FakeDb()
    store = _store(db, run_id="run-new")
    db[DOCUMENT_OCR_PAGES].count_result = 2

    await store.publish_run(expected_page_numbers=[1, 2])

    _, query, update = db[DOCUMENT_EXTRACTION_HEADS].operations[-1]
    assert query["document_id"] == "doc-1"
    assert update["$set"]["extraction_run_id"] == "run-new"
    assert update["$set"]["expected_page_numbers"] == [1, 2]
