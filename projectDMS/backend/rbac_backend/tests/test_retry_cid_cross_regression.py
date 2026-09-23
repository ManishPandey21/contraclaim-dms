"""Where the cumulative run meets the unusable-text policy.

PR #25 says a page of pdfminer ``(cid:N)`` placeholders is not content: it is
an OCR candidate, it is never published, and OCR output is re-checked. PR #28
says an extraction run is cumulative: an attempt may change only the pages the
run still owes, and it carries everything else forward from the store.

Each rule is safe alone. Together they leave four gaps, and every one of them
ends with unusable text published as though it were the document:

* the cumulative candidate branch dropped the unusable-text rule, so a CID page
  with no retry list is simply never OCR'd;
* a stale checkpoint - written by an attempt that crashed after its pages but
  before its checkpoint - still names a page the store has already resolved,
  and a retry rewrites that resolved row from a fresh native read;
* a row written by older code carries ``(cid:N)`` text into the run, and
  nothing re-checks a carried row; and
* the fallback ladder's reconstruction is adopted as OCR_COMPLETED without
  passing the same usability policy.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.services.extraction.models import (
    Completeness,
    ExtractedPage,
    PageClass,
    PageClassification,
    PageExtractionPolicy,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.engine import PageExtractionEngine
from rbac_backend.services.extraction.text_quality import assess_native_text_quality
from rbac_backend.tests.fixtures.pdf_builders import build_page_text_map_pdf

CID_LINE = "".join(f"(cid:{code})" for code in range(65, 85))
READABLE = "Notice of claim for extension of time under clause 44"


def _classification(char_count: int) -> PageClassification:
    return PageClassification(
        page_class=PageClass.TEXT_NATIVE,
        char_count=char_count,
        image_count=0,
        image_coverage=0.0,
        table_count=0,
        width=595.0,
        height=842.0,
        rotation=0,
    )


def _stored_page(number: int, text: str, status: PageStatus) -> ExtractedPage:
    return ExtractedPage(
        number=number,
        text=text,
        source=PageSource.OCR
        if status is PageStatus.OCR_COMPLETED
        else PageSource.TEXT_LAYER,
        status=status,
        classification=_classification(len(text)),
    )


class _ResumableStore:
    """A store with the run-scoped read the document adapter provides."""

    def __init__(self, stored: List[ExtractedPage]) -> None:
        self.stored = stored
        self.recorded: List[ExtractedPage] = []
        self.extraction_run_id = "run-1"

    async def load_run_pages(self) -> List[ExtractedPage]:
        return list(self.stored)

    async def record_pages(self, pages: List[ExtractedPage]) -> None:
        self.recorded.extend(pages)

    async def begin_batch(self, *, page_start: int, page_end: int, retry: bool) -> str:
        return "batch-1"

    async def finish_batch(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def fail_batch(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def finalize_pages(self, *args: Any, **kwargs: Any) -> None:
        return None


class _Runner:
    def __init__(self, text_by_page: Optional[Dict[int, str]] = None) -> None:
        self.requested: List[List[int]] = []
        self.text_by_page = text_by_page or {}

    async def run(
        self, source: Path, page_numbers: Any, language: str
    ) -> Dict[int, str]:
        self.requested.append(sorted(int(page) for page in page_numbers))
        return {
            int(page): self.text_by_page.get(int(page), f"OCR PAGE {int(page)}")
            for page in page_numbers
        }


def _engine(store: Any, runner: Any, *, min_chars: int = 10) -> PageExtractionEngine:
    return PageExtractionEngine(
        policy=PageExtractionPolicy(
            ocr_enabled=True,
            min_text_chars_per_page=min_chars,
            batch_size=5,
            max_ocr_pages_per_attempt=0,
            ocr_language="eng",
        ),
        ocr_runner=runner,
        store=store,
    )


# --- B: a CID page with no retry list is still an OCR candidate ---------------


async def test_a_cid_page_is_ocrd_even_with_a_cumulative_run_and_no_retry_list(
    tmp_path: Path,
) -> None:
    source = build_page_text_map_pdf(
        tmp_path / "letter.pdf", page_lines=[[READABLE], [CID_LINE]]
    )
    # The run already holds page 1; page 2's placeholders are what this attempt
    # must notice without being told to retry it.
    store = _ResumableStore([_stored_page(1, READABLE, PageStatus.TEXT_LAYER)])
    runner = _Runner({2: "OCR PAGE TWO"})

    result = await _engine(store, runner).extract(source)

    assert runner.requested == [[2]], "the unusable page was never sent for OCR"
    page_two = next(page for page in result.pages if page.number == 2)
    assert page_two.text == "OCR PAGE TWO"
    assert "(cid:" not in result.combined_text


# --- C: a stale checkpoint must not overwrite a resolved row ------------------


async def test_a_stale_retry_list_does_not_rewrite_a_resolved_page(
    tmp_path: Path,
) -> None:
    """The crash window: pages written, checkpoint not, so the list is stale.

    Page 1 is a scan that an earlier attempt already OCR'd and recorded. The
    job's checkpoint still names it. Re-extracting it natively yields nothing,
    so a blind retry would replace recovered text with an empty page.
    """
    source = build_page_text_map_pdf(
        tmp_path / "letter.pdf", page_lines=[None, [READABLE]]
    )
    store = _ResumableStore(
        [
            _stored_page(1, "OCR PAGE ONE", PageStatus.OCR_COMPLETED),
            _stored_page(2, READABLE, PageStatus.TEXT_LAYER),
        ]
    )
    runner = _Runner()

    result = await _engine(store, runner).extract(source, retry_pages=[1])

    page_one = next(page for page in result.pages if page.number == 1)
    assert page_one.text == "OCR PAGE ONE"
    assert page_one.status is PageStatus.OCR_COMPLETED
    assert [page.number for page in store.recorded] == []
    assert "OCR PAGE ONE" in result.combined_text


async def test_a_retry_still_reworks_a_page_the_run_has_not_resolved(
    tmp_path: Path,
) -> None:
    """The control: an unresolved row is exactly what a retry is for."""
    source = build_page_text_map_pdf(
        tmp_path / "letter.pdf", page_lines=[None, [READABLE]]
    )
    store = _ResumableStore(
        [
            _stored_page(1, "", PageStatus.OCR_FAILED),
            _stored_page(2, READABLE, PageStatus.TEXT_LAYER),
        ]
    )
    runner = _Runner({1: "OCR PAGE ONE"})

    result = await _engine(store, runner).extract(source, retry_pages=[1])

    assert runner.requested == [[1]]
    page_one = next(page for page in result.pages if page.number == 1)
    assert page_one.text == "OCR PAGE ONE"


# --- D: a carried row written by older code is re-checked ---------------------


async def test_a_carried_row_of_placeholders_is_not_published(
    tmp_path: Path,
) -> None:
    """Rows predating the policy exist; carrying one forward re-publishes it."""
    source = build_page_text_map_pdf(
        tmp_path / "letter.pdf", page_lines=[[CID_LINE], [READABLE]]
    )
    # Page 1 was recorded as a settled text-layer page by older code.
    store = _ResumableStore(
        [
            _stored_page(1, CID_LINE, PageStatus.TEXT_LAYER),
            _stored_page(2, READABLE, PageStatus.TEXT_LAYER),
        ]
    )
    runner = _Runner()

    result = await _engine(store, runner).extract(source, retry_pages=[2])

    assert "(cid:" not in result.combined_text
    page_one = next(page for page in result.pages if page.number == 1)
    assert page_one.text == ""
    assert page_one.text_withheld is True
    assert page_one.raw_text is not None and "(cid:" in page_one.raw_text
    assert result.withheld_pages == [1]


# --- E: a reconstruction is resolved only if it is usable ---------------------


async def test_a_fallback_reconstruction_of_placeholders_is_not_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OCR_COMPLETED is a status, not evidence that the text can be read."""
    from rbac_backend.services.document_processor import DocumentProcessor
    from rbac_backend.services.extraction.fallback.models import FallbackOutcome

    page = _stored_page(1, "", PageStatus.OCR_FAILED)
    page.needs_review = False
    extraction = SimpleNamespace(
        pages=[page],
        combined_text="",
        completeness=None,
        withheld_pages=[],
    )

    class _Ladder:
        async def resolve(
            self, source: Any, page: Any, verdict: Any, *, document_id: str
        ) -> Any:
            return SimpleNamespace(
                outcome=FallbackOutcome.RESOLVED,
                page=SimpleNamespace(text=CID_LINE, source=PageSource.OCR),
            )

    class _Gate:
        def assess(self, page: Any, tables: Any = None) -> Any:
            return SimpleNamespace(
                verdict=SimpleNamespace(value="fail"),
                escalates=True,
                repairs=[],
                checks=[],
            )

    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.quality_gate = _Gate()
    processor.fallback_ladder = _Ladder()
    processor.fallback_max_pages_per_document = 5
    processor.intervention_ledger = SimpleNamespace(record=_record_nothing)

    needs_review = await processor._apply_quality_gate(
        tmp_path / "letter.pdf",
        extraction,
        document_id="doc-1",
        fallback_ladder=_Ladder(),
        page_store=None,
    )

    assert "(cid:" not in (page.text or "")
    assert page.status is not PageStatus.OCR_COMPLETED or page.text == ""
    assert needs_review == [1]
    assert assess_native_text_quality(extraction.combined_text or "").unusable is False


async def _record_nothing(*args: Any, **kwargs: Any) -> None:
    return None


# --- review: a run is not COMPLETE while a page's content is missing ---------


async def test_a_stored_unusable_page_is_reworked_without_a_retry_list(
    tmp_path: Path,
) -> None:
    """Found in review. The no-retry branch asked only about status.

    A row written before the text-quality policy is settled by status and
    unusable by content. It was therefore never mutable, never an OCR
    candidate, and was carried - blanked - into a run that reported COMPLETE
    with that page's content simply gone.
    """
    source = build_page_text_map_pdf(
        tmp_path / "letter.pdf", page_lines=[[READABLE], [CID_LINE]]
    )
    store = _ResumableStore(
        [
            _stored_page(1, READABLE, PageStatus.TEXT_LAYER),
            _stored_page(2, CID_LINE, PageStatus.TEXT_LAYER),
        ]
    )
    runner = _Runner({2: "OCR PAGE TWO"})

    result = await _engine(store, runner).extract(source)

    assert runner.requested == [[2]], "the unusable stored page was not re-OCR'd"
    page_two = next(page for page in result.pages if page.number == 2)
    assert page_two.text == "OCR PAGE TWO"
    assert result.completeness is Completeness.COMPLETE


async def test_a_carried_unusable_page_keeps_the_run_partial_when_ocr_cannot_run(
    tmp_path: Path,
) -> None:
    """With OCR off the page cannot be replaced, so the run must say so."""
    source = build_page_text_map_pdf(
        tmp_path / "letter.pdf", page_lines=[[CID_LINE], [READABLE]]
    )
    store = _ResumableStore(
        [
            _stored_page(1, CID_LINE, PageStatus.TEXT_LAYER),
            _stored_page(2, READABLE, PageStatus.TEXT_LAYER),
        ]
    )
    engine = PageExtractionEngine(
        policy=PageExtractionPolicy(
            ocr_enabled=False,
            min_text_chars_per_page=10,
            batch_size=5,
            max_ocr_pages_per_attempt=0,
            ocr_language="eng",
        ),
        ocr_runner=_Runner(),
        store=store,
    )

    result = await engine.extract(source, retry_pages=[2])

    page_one = next(page for page in result.pages if page.number == 1)
    assert page_one.text == ""
    assert page_one.status in {PageStatus.OCR_PENDING, PageStatus.OCR_DISABLED}
    assert result.completeness is Completeness.PARTIAL


async def test_a_stale_retry_list_spends_no_ocr(tmp_path: Path) -> None:
    """The discarded batch: OCR ran, was metered, and its output thrown away."""
    source = build_page_text_map_pdf(
        tmp_path / "letter.pdf", page_lines=[None, [READABLE]]
    )
    store = _ResumableStore(
        [
            _stored_page(1, "OCR PAGE ONE", PageStatus.OCR_COMPLETED),
            _stored_page(2, READABLE, PageStatus.TEXT_LAYER),
        ]
    )
    runner = _Runner()

    result = await _engine(store, runner).extract(source, retry_pages=[1])

    assert runner.requested == []
    assert result.ocr_pages_total == 0


async def test_a_rescued_page_lets_the_document_complete(tmp_path: Path) -> None:
    """Found in review: the ladder resolved a page the result still called failed.

    The engine freezes ocr_failed_pages and completeness before the gate runs,
    so a page the ladder rescued stayed in ocr_failed_pages. The job then read
    PARTIAL with nothing outstanding and sent a finished document to a human.
    """
    from rbac_backend.models.processing_state import (
        ProcessingState,
        derive_processing_state,
    )
    from rbac_backend.services.document_processor import DocumentProcessor
    from rbac_backend.services.extraction.fallback.models import FallbackOutcome
    from rbac_backend.services.extraction.models import (
        Completeness as _Completeness,
        PageExtractionResult,
    )

    page = _stored_page(1, "", PageStatus.OCR_FAILED)
    extraction = PageExtractionResult(
        pages=[page],
        combined_text="",
        ocr_pages_total=1,
        ocr_failed_pages=[1],
        completeness=_Completeness.PARTIAL,
    )

    class _Ladder:
        async def resolve(self, source, page, verdict, *, document_id):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                outcome=FallbackOutcome.RESOLVED,
                page=SimpleNamespace(
                    text="RECONSTRUCTED PAGE ONE", source=PageSource.OCR
                ),
            )

    class _Gate:
        def assess(self, page, tables=None):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                verdict=SimpleNamespace(value="fail"),
                escalates=True,
                repairs=[],
                checks=[],
            )

    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.quality_gate = _Gate()
    processor.fallback_ladder = _Ladder()
    processor.fallback_max_pages_per_document = 5
    processor.intervention_ledger = SimpleNamespace(record=_record_nothing)

    needs_review = await processor._apply_quality_gate(
        tmp_path / "letter.pdf",
        extraction,
        document_id="doc-1",
        fallback_ladder=_Ladder(),
        page_store=None,
    )

    assert needs_review == []
    assert page.text == "RECONSTRUCTED PAGE ONE"
    assert page.text_withheld is False
    assert extraction.ocr_failed_pages == []
    assert extraction.completeness is _Completeness.COMPLETE
    assert derive_processing_state(extraction, attempts_exhausted=False) is (
        ProcessingState.COMPLETED
    )
