"""ContractPageStore must produce byte-identical records to the old inline code.

The contract path is the reference implementation. Phase 1 generalises it; it
must not change it. The record shape here is frozen to what
contracts_ingest wrote inline before the refactor - changing a key is a data
migration, not a rename.
"""

from __future__ import annotations

from typing import Any

from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction_adapters.contract_page_store import (
    ContractPageStore,
    to_contract_page_record,
)


class _FakeDbService:
    def __init__(self) -> None:
        self.batches: list[dict[str, Any]] = []
        self.pages: list[list[dict[str, Any]]] = []
        self._next_id = 0

    async def upsert_ocr_batch(self, **kwargs: Any) -> str:
        self.batches.append(dict(kwargs))
        self._next_id += 1
        return f"batch-{self._next_id}"

    async def upsert_ocr_pages(self, records: list[dict[str, Any]]) -> None:
        self.pages.append(records)


def _page(number: int, status: PageStatus, text: str) -> ExtractedPage:
    return ExtractedPage(
        number=number,
        text=text,
        source=(
            PageSource.OCR
            if status is PageStatus.OCR_COMPLETED
            else PageSource.TEXT_LAYER
        ),
        status=status,
        classification=PageClassification(
            page_class=PageClass.TEXT_NATIVE,
            char_count=len(text),
            image_count=0,
            image_coverage=0.0,
            table_count=0,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
        batch_id="batch-1" if status is PageStatus.OCR_COMPLETED else None,
    )


def test_record_shape_matches_the_legacy_contract_record() -> None:
    record = to_contract_page_record(
        _page(2, PageStatus.OCR_COMPLETED, "page two text"),
        document_id="doc-1",
        upload_id="up-1",
        organization_id="org-1",
        project_id="proj-1",
        source_pdf_page_link="contract:doc-1#page=2",
    )

    # Every legacy key is still written with its legacy meaning; the last three
    # are additive, so an OCR retry can rebuild the run an earlier attempt left.
    assert set(record) == {
        "document_id",
        "upload_id",
        "organization_id",
        "project_id",
        "page_number",
        "batch_id",
        "status",
        "error",
        "raw_text",
        "raw_text_length",
        "cleaned_text",
        "cleaned_text_length",
        "source_pdf_page_link",
        "text_withheld",
        "source",
        "page_class",
    }
    assert record["text_withheld"] is False
    assert (record["source"], record["page_class"]) == ("ocr", "text_native")
    assert record["status"] == "ocr_completed"
    assert record["raw_text"] == "page two text"
    assert record["raw_text_length"] == len("page two text")
    assert record["cleaned_text"] == ""
    assert record["cleaned_text_length"] == 0
    assert record["source_pdf_page_link"] == "contract:doc-1#page=2"


def test_status_values_are_plain_strings_not_enum_reprs() -> None:
    record = to_contract_page_record(
        _page(1, PageStatus.TEXT_LAYER, "native"),
        document_id="doc-1",
        upload_id="up-1",
        organization_id="org-1",
        project_id=None,
        source_pdf_page_link="contract:doc-1#page=1",
    )

    assert record["status"] == "text_layer"
    assert isinstance(record["status"], str)
    assert record["project_id"] is None


def test_organization_id_is_stringified_like_the_legacy_record() -> None:
    record = to_contract_page_record(
        _page(1, PageStatus.TEXT_LAYER, "native"),
        document_id="doc-1",
        upload_id="up-1",
        organization_id=12345,  # type: ignore[arg-type]
        project_id=None,
        source_pdf_page_link="contract:doc-1#page=1",
    )

    assert record["organization_id"] == "12345"


async def test_store_opens_and_closes_batches_through_the_db_service() -> None:
    db = _FakeDbService()
    store = ContractPageStore(
        db_service=db,
        document_id="doc-1",
        upload_id="up-1",
        organization_id="org-1",
        project_id="proj-1",
    )

    batch_id = await store.begin_batch(page_start=1, page_end=2, retry=False)
    await store.finish_batch(batch_id, status="completed")

    assert db.batches[0]["status"] == "running"
    assert db.batches[0]["page_start"] == 1
    assert db.batches[0]["page_end"] == 2
    assert db.batches[0]["retry_count"] == 0
    assert db.batches[1]["status"] == "completed"


async def test_retry_sets_retry_count_one() -> None:
    db = _FakeDbService()
    store = ContractPageStore(
        db_service=db,
        document_id="doc-1",
        upload_id="up-1",
        organization_id="org-1",
        project_id=None,
    )

    await store.begin_batch(page_start=3, page_end=3, retry=True)

    assert db.batches[0]["retry_count"] == 1


async def test_failed_batch_carries_a_truncated_error() -> None:
    db = _FakeDbService()
    store = ContractPageStore(
        db_service=db,
        document_id="doc-1",
        upload_id="up-1",
        organization_id="org-1",
        project_id=None,
    )

    batch_id = await store.begin_batch(page_start=1, page_end=1, retry=False)
    await store.finish_batch(batch_id, status="failed", error="x" * 900)

    assert db.batches[1]["status"] == "failed"
    assert len(db.batches[1]["error"]) == 500


async def test_successful_batch_sends_no_error_key() -> None:
    db = _FakeDbService()
    store = ContractPageStore(
        db_service=db,
        document_id="doc-1",
        upload_id="up-1",
        organization_id="org-1",
        project_id=None,
    )

    batch_id = await store.begin_batch(page_start=1, page_end=1, retry=False)
    await store.finish_batch(batch_id, status="completed")

    assert "error" not in db.batches[1]


async def test_recorded_pages_become_contract_records_with_page_links() -> None:
    db = _FakeDbService()
    store = ContractPageStore(
        db_service=db,
        document_id="doc-1",
        upload_id="up-1",
        organization_id="org-1",
        project_id="proj-1",
    )

    await store.record_pages(
        [
            _page(1, PageStatus.TEXT_LAYER, "native one"),
            _page(2, PageStatus.OCR_COMPLETED, "ocr two"),
        ]
    )

    assert [record["page_number"] for record in store.records] == [1, 2]
    assert store.records[0]["source_pdf_page_link"] == "contract:doc-1#page=1"
    assert store.records[1]["source_pdf_page_link"] == "contract:doc-1#page=2"
