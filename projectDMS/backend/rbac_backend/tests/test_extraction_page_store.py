"""The PageStore seam: the engine returns records, callers persist them."""

from __future__ import annotations

from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.page_store import NullPageStore, PageStore


def _page(number: int) -> ExtractedPage:
    return ExtractedPage(
        number=number,
        text=f"page {number}",
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=PageClass.TEXT_NATIVE,
            char_count=6,
            image_count=0,
            image_coverage=0.0,
            table_count=0,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
    )


def test_null_store_satisfies_the_protocol() -> None:
    assert isinstance(NullPageStore(), PageStore)


async def test_null_store_records_batch_lifecycle() -> None:
    store = NullPageStore()

    batch_id = await store.begin_batch(page_start=1, page_end=2, retry=False)
    await store.finish_batch(batch_id, status="completed")

    assert store.batches == [
        {
            "batch_id": batch_id,
            "page_start": 1,
            "page_end": 2,
            "retry": False,
            "status": "completed",
            "error": None,
        }
    ]


async def test_null_store_records_failed_batch_with_error() -> None:
    store = NullPageStore()

    batch_id = await store.begin_batch(page_start=3, page_end=3, retry=True)
    await store.finish_batch(batch_id, status="failed", error="boom")

    assert store.batches[0]["status"] == "failed"
    assert store.batches[0]["error"] == "boom"
    assert store.batches[0]["retry"] is True


async def test_null_store_collects_pages() -> None:
    store = NullPageStore()

    await store.record_pages([_page(1), _page(2)])

    assert [page.number for page in store.recorded_pages] == [1, 2]


async def test_repeated_record_pages_calls_accumulate() -> None:
    store = NullPageStore()

    await store.record_pages([_page(1)])
    await store.record_pages([_page(2)])

    assert [page.number for page in store.recorded_pages] == [1, 2]


async def test_batch_ids_are_unique() -> None:
    store = NullPageStore()

    first = await store.begin_batch(page_start=1, page_end=1, retry=False)
    second = await store.begin_batch(page_start=2, page_end=2, retry=False)

    assert first != second


async def test_finishing_an_unknown_batch_does_not_raise() -> None:
    # The engine must be able to report a failure even if the store never
    # registered the batch, rather than masking the original error.
    store = NullPageStore()

    await store.finish_batch("never-started", status="failed", error="boom")

    assert store.batches == []


def test_two_null_stores_do_not_share_state() -> None:
    first = NullPageStore()
    second = NullPageStore()

    first.batches.append({"batch_id": "x"})

    assert second.batches == []
