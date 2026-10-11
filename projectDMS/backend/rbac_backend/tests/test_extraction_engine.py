"""The shared page extraction engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Sequence

from rbac_backend.services.extraction.engine import (
    PageExtractionEngine,
    group_contiguous_pages,
)
from rbac_backend.services.extraction.models import (
    Completeness,
    PageExtractionPolicy,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.page_store import NullPageStore
from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "golden_page_routing.json").read_text(
        encoding="utf-8"
    )
)


class _RecordingOcrRunner:
    """Returns synthetic OCR text and records exactly which pages were asked for."""

    def __init__(self, *, fail_pages: set[int] | None = None) -> None:
        self.requested: list[list[int]] = []
        self.fail_pages = fail_pages or set()

    async def run(
        self, source: Path, page_numbers: Sequence[int], language: str
    ) -> Dict[int, str]:
        pages = list(page_numbers)
        self.requested.append(pages)
        if self.fail_pages & set(pages):
            raise RuntimeError("ocr batch failed")
        return {page: f"OCR TEXT PAGE {page}" for page in pages}


class _CountingMeter:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def __call__(
        self, *, page_count: int, page_numbers: Sequence[int], retry: bool
    ) -> None:
        self.calls.append(
            {
                "page_count": page_count,
                "page_numbers": list(page_numbers),
                "retry": retry,
            }
        )


def _policy(**overrides: object) -> PageExtractionPolicy:
    defaults = dict(
        ocr_enabled=True,
        min_text_chars_per_page=GOLDEN["min_text_chars_per_page"],
        batch_size=25,
        max_ocr_pages_per_attempt=100,
        ocr_language="eng",
    )
    defaults.update(overrides)
    return PageExtractionPolicy(**defaults)  # type: ignore[arg-type]


def test_group_contiguous_pages_splits_on_gaps() -> None:
    assert group_contiguous_pages([1, 2, 5, 6, 9], batch_size=25) == [
        [1, 2],
        [5, 6],
        [9],
    ]


def test_group_contiguous_pages_respects_batch_size() -> None:
    assert group_contiguous_pages([1, 2, 3, 4], batch_size=2) == [[1, 2], [3, 4]]


def test_group_contiguous_pages_deduplicates_and_sorts() -> None:
    assert group_contiguous_pages([3, 1, 2, 2], batch_size=25) == [[1, 2, 3]]


def test_group_contiguous_pages_handles_empty_input() -> None:
    assert group_contiguous_pages([], batch_size=25) == []


async def test_only_thin_pages_are_sent_to_ocr(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    runner = _RecordingOcrRunner()
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=runner, store=NullPageStore()
    )

    result = await engine.extract(source)

    assert runner.requested == GOLDEN["expected_batches"]
    assert result.ocr_pages_total == len(GOLDEN["pages_expected_ocr"])
    assert result.completeness is Completeness.COMPLETE


async def test_native_pages_keep_their_own_text(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_RecordingOcrRunner(), store=NullPageStore()
    )

    result = await engine.extract(source)
    by_number = {page.number: page for page in result.pages}

    for number in GOLDEN["pages_expected_native"]:
        assert by_number[number].source is PageSource.TEXT_LAYER
        assert by_number[number].status is PageStatus.TEXT_LAYER
        assert "OCR TEXT PAGE" not in by_number[number].text


async def test_ocr_pages_take_the_ocr_text(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_RecordingOcrRunner(), store=NullPageStore()
    )

    result = await engine.extract(source)
    by_number = {page.number: page for page in result.pages}

    for number in GOLDEN["pages_expected_ocr"]:
        assert by_number[number].source is PageSource.OCR
        assert by_number[number].status is PageStatus.OCR_COMPLETED
        assert by_number[number].text == f"OCR TEXT PAGE {number}"


async def test_ocr_returning_nothing_is_marked_empty_not_completed(
    tmp_path: Path,
) -> None:
    class _EmptyRunner:
        async def run(
            self, source: Path, page_numbers: Sequence[int], language: str
        ) -> Dict[int, str]:
            return {page: "   " for page in page_numbers}

    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_EmptyRunner(), store=NullPageStore()
    )

    result = await engine.extract(source)
    by_number = {page.number: page for page in result.pages}

    assert by_number[1].status is PageStatus.OCR_EMPTY
    assert by_number[1].error


async def test_failed_batch_marks_its_pages_without_failing_the_document(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    store = NullPageStore()
    engine = PageExtractionEngine(
        policy=_policy(),
        ocr_runner=_RecordingOcrRunner(fail_pages={1}),
        store=store,
    )

    result = await engine.extract(source)
    by_number = {page.number: page for page in result.pages}

    assert result.ocr_failed_pages == [1, 2]
    assert by_number[1].status is PageStatus.OCR_FAILED
    assert by_number[1].error
    assert result.completeness is Completeness.PARTIAL
    assert store.batches[0]["status"] == "failed"
    # Native pages survive a failed OCR batch.
    assert by_number[3].source is PageSource.TEXT_LAYER


async def test_ocr_disabled_marks_pages_rather_than_pretending_success(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    runner = _RecordingOcrRunner()
    engine = PageExtractionEngine(
        policy=_policy(ocr_enabled=False), ocr_runner=runner, store=NullPageStore()
    )

    result = await engine.extract(source)
    by_number = {page.number: page for page in result.pages}

    assert runner.requested == []
    assert by_number[1].status is PageStatus.OCR_DISABLED
    assert result.completeness is Completeness.PARTIAL


async def test_attempt_boundary_defers_rather_than_truncating(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    runner = _RecordingOcrRunner()
    engine = PageExtractionEngine(
        policy=_policy(max_ocr_pages_per_attempt=1),
        ocr_runner=runner,
        store=NullPageStore(),
    )

    result = await engine.extract(source)
    by_number = {page.number: page for page in result.pages}

    assert runner.requested == [[1]]
    assert result.ocr_deferred_pages == [2]
    assert by_number[2].status is PageStatus.OCR_DEFERRED
    assert result.completeness is Completeness.PARTIAL


async def test_retry_pages_override_the_threshold(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    runner = _RecordingOcrRunner()
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=runner, store=NullPageStore()
    )

    await engine.extract(source, retry_pages=[7])

    assert runner.requested == [[7]]


async def test_meter_is_called_once_with_the_pages_actually_attempted(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    meter = _CountingMeter()
    engine = PageExtractionEngine(
        policy=_policy(),
        ocr_runner=_RecordingOcrRunner(),
        store=NullPageStore(),
        meter=meter,
    )

    await engine.extract(source)

    assert meter.calls == [{"page_count": 2, "page_numbers": [1, 2], "retry": False}]


async def test_meter_is_not_called_when_no_pages_need_ocr(tmp_path: Path) -> None:
    from rbac_backend.tests.fixtures.pdf_builders import build_text_pdf

    source = build_text_pdf(tmp_path / "text.pdf", pages=2)
    meter = _CountingMeter()
    engine = PageExtractionEngine(
        policy=_policy(),
        ocr_runner=_RecordingOcrRunner(),
        store=NullPageStore(),
        meter=meter,
    )

    await engine.extract(source)

    assert meter.calls == []


async def test_pages_are_recorded_through_the_store(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    store = NullPageStore()
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_RecordingOcrRunner(), store=store
    )

    await engine.extract(source)

    assert [page.number for page in store.recorded_pages] == list(range(1, 10))


async def test_combined_text_is_page_ordered(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_RecordingOcrRunner(), store=NullPageStore()
    )

    result = await engine.extract(source)

    assert result.combined_text.index("OCR TEXT PAGE 1") < result.combined_text.index(
        "OCR TEXT PAGE 2"
    )
    assert result.combined_text.index("OCR TEXT PAGE 2") < result.combined_text.index(
        "Claim summary page 3"
    )


async def test_every_page_carries_a_classification(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_RecordingOcrRunner(), store=NullPageStore()
    )

    result = await engine.extract(source)

    assert len(result.pages) == 9
    assert all(page.classification is not None for page in result.pages)
    # Page 5 of the fixture is landscape.
    assert result.pages[4].classification.is_landscape is True


# --- Cumulative runs: a retry must not rewrite what it did not re-extract --

#: Every page of the nine-page mixed fixture, in order.
_ALL_PAGES = list(range(1, GOLDEN["pages_total"] + 1))


class _ResumableStore(NullPageStore):
    """A NullPageStore that can also hand the run's pages back.

    Stands in for DocumentPageStore's run-scoped read without pulling Mongo
    into an engine test.
    """

    extraction_run_id = "run-1"

    def __init__(self) -> None:
        super().__init__()
        self.run: dict[int, object] = {}
        self.write_batches: list[list[int]] = []

    async def record_pages(self, pages) -> None:  # type: ignore[no-untyped-def]
        await super().record_pages(pages)
        self.write_batches.append([page.number for page in pages])
        for page in pages:
            self.run[page.number] = page

    async def load_run_pages(self):  # type: ignore[no-untyped-def]
        return [self.run[number] for number in sorted(self.run)]


async def test_a_retry_writes_only_the_pages_it_re_extracted(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    store = _ResumableStore()
    # Page 2 is the page this run still owes: the first attempt's OCR failed
    # on it. A retry of a page the run had already resolved is a different
    # case - see test_a_stale_retry_list_leaves_a_resolved_page_alone.
    engine = PageExtractionEngine(
        policy=_policy(batch_size=1),
        ocr_runner=_RecordingOcrRunner(fail_pages={2}),
        store=store,
    )

    await engine.extract(source)
    first_write = list(store.write_batches)
    engine.ocr_runner = _RecordingOcrRunner()
    await engine.extract(source, retry_pages=[2])

    assert first_write == [_ALL_PAGES]
    assert store.write_batches[-1] == [2]


async def test_a_stale_retry_list_leaves_a_resolved_page_alone(
    tmp_path: Path,
) -> None:
    """An attempt can write its pages and die before recording the checkpoint.

    The job then still asks for a page the run has resolved. Re-extracting it
    would put the native read back over recovered OCR text, so the durable row
    wins and nothing is written.
    """
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    store = _ResumableStore()
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_RecordingOcrRunner(), store=store
    )

    first = await engine.extract(source)
    resolved = {page.number: page.text for page in first.pages}

    second = await engine.extract(source, retry_pages=[2])

    assert store.write_batches == [_ALL_PAGES]
    assert {page.number: page.text for page in second.pages} == resolved
    assert all(page.carried_forward for page in second.pages)


async def test_a_retry_returns_the_whole_run_in_page_order(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    store = _ResumableStore()
    # One page per batch, so page 2's failure is page 2's alone.
    engine = PageExtractionEngine(
        policy=_policy(batch_size=1),
        ocr_runner=_RecordingOcrRunner(fail_pages={2}),
        store=store,
    )

    first = await engine.extract(source)
    assert first.completeness is Completeness.PARTIAL

    engine.ocr_runner = _RecordingOcrRunner()
    second = await engine.extract(source, retry_pages=[2])

    assert [page.number for page in second.pages] == _ALL_PAGES
    assert second.completeness is Completeness.COMPLETE
    by_number = {page.number: page for page in second.pages}
    # Page 1 was resolved by the first attempt and not retried: its text,
    # source and status survive untouched.
    assert by_number[1].text == "OCR TEXT PAGE 1"
    assert by_number[1].source is PageSource.OCR
    assert by_number[1].status is PageStatus.OCR_COMPLETED
    assert by_number[1].carried_forward is True
    assert by_number[2].text == "OCR TEXT PAGE 2"
    assert by_number[2].carried_forward is False
    assert "OCR TEXT PAGE 1" in second.combined_text
    assert second.combined_text.index("OCR TEXT PAGE 1") < second.combined_text.index(
        "OCR TEXT PAGE 2"
    )


async def test_a_retry_whose_run_lost_a_page_fails_closed(tmp_path: Path) -> None:
    from rbac_backend.services.extraction.page_store import (
        InconsistentExtractionRunError,
    )

    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    store = _ResumableStore()
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_RecordingOcrRunner(), store=store
    )

    await engine.extract(source)
    del store.run[1]  # the row an earlier attempt wrote is gone

    try:
        await engine.extract(source, retry_pages=[2])
    except InconsistentExtractionRunError as error:
        assert error.missing_page_numbers == [1]
        assert error.expected_page_numbers == _ALL_PAGES
        assert 1 not in error.persisted_page_numbers
        # Page numbers only: nothing customer-readable in the message.
        assert "OCR TEXT PAGE" not in str(error)
    else:  # pragma: no cover - the whole point is that it raises
        raise AssertionError("a missing prior page must not be silently regenerated")


async def test_a_retry_against_a_store_without_run_reads_is_unchanged(
    tmp_path: Path,
) -> None:
    # The contract adapter has no run-scoped read; it must keep the behaviour
    # it had rather than be forced to fake document-run semantics.
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    store = NullPageStore()
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_RecordingOcrRunner(), store=store
    )

    result = await engine.extract(source, retry_pages=[2])

    assert [page.number for page in result.pages] == _ALL_PAGES
    assert len(store.recorded_pages) == GOLDEN["pages_total"]


async def test_a_first_attempt_still_records_every_page(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    store = _ResumableStore()
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_RecordingOcrRunner(), store=store
    )

    result = await engine.extract(source)

    assert store.write_batches == [_ALL_PAGES]
    assert all(page.carried_forward is False for page in result.pages)
