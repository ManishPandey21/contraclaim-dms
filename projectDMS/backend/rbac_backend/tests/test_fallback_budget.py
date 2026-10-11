"""Per-document spend cap on the fallback ladder.

Without this, a pathological document escalates every page and there is no
ceiling on what one upload can cost. The cap is enforced where the pages are
iterated, not inside the ladder, because the ladder sees one page at a time and
cannot know how many siblings already spent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rbac_backend.core.config import Settings, settings
from rbac_backend.services.document_processor import DocumentProcessor
from rbac_backend.services.extraction.fallback.models import (
    FallbackOutcome,
    ResolvedPage,
)
from rbac_backend.services.extraction.models import (
    Completeness,
    ExtractedPage,
    PageClass,
    PageClassification,
    PageExtractionResult,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.quality.models import QualityVerdict, Verdict


def _page(number: int) -> ExtractedPage:
    text = "some text"
    return ExtractedPage(
        number=number,
        text=text,
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
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
    )


def _extraction(page_count: int) -> PageExtractionResult:
    return PageExtractionResult(
        pages=[_page(number) for number in range(1, page_count + 1)],
        combined_text="",
        ocr_pages_total=0,
        completeness=Completeness.COMPLETE,
    )


class _AlwaysFailGate:
    def assess(self, page: ExtractedPage, *, tables: Any = None) -> QualityVerdict:
        return QualityVerdict(verdict=Verdict.FAIL, reasons=["forced failure"])


class _CountingLadder:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, source, page, verdict, *, document_id, tables=None):
        self.calls += 1
        return ResolvedPage(page=page, outcome=FallbackOutcome.HUMAN_REVIEW_REQUIRED)


def _processor(gate: Any, ladder: Any, budget: int) -> DocumentProcessor:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.quality_gate = gate
    processor.fallback_ladder = ladder
    processor.fallback_max_pages_per_document = budget
    return processor


def test_the_budget_setting_exists_and_defaults_to_a_finite_cap() -> None:
    assert hasattr(settings, "EXTRACTION_FALLBACK_MAX_PAGES_PER_DOCUMENT")
    default = Settings.model_fields[
        "EXTRACTION_FALLBACK_MAX_PAGES_PER_DOCUMENT"
    ].default

    assert isinstance(default, int)
    assert default > 0, "an unbounded default would let one upload cost anything"


def test_the_ladder_is_disabled_by_default() -> None:
    # It is the only part of this pipeline that spends money at runtime.
    assert Settings.model_fields["EXTRACTION_FALLBACK_ENABLED"].default is False


async def test_model_calls_stop_at_the_budget_not_the_page_count(
    tmp_path: Path,
) -> None:
    ladder = _CountingLadder()
    extraction = _extraction(9)

    review = await _processor(_AlwaysFailGate(), ladder, budget=3)._apply_quality_gate(
        tmp_path / "doc.pdf", extraction, document_id="doc-1"
    )

    assert ladder.calls == 3
    assert review == list(range(1, 10))  # every page still needs a human


async def test_pages_beyond_the_budget_are_review_required_not_silently_passed(
    tmp_path: Path,
) -> None:
    ladder = _CountingLadder()
    extraction = _extraction(5)

    await _processor(_AlwaysFailGate(), ladder, budget=2)._apply_quality_gate(
        tmp_path / "doc.pdf", extraction, document_id="doc-1"
    )

    assert all(page.needs_review for page in extraction.pages)
    assert extraction.completeness is Completeness.PARTIAL


async def test_a_zero_budget_disables_the_ladder_entirely(tmp_path: Path) -> None:
    ladder = _CountingLadder()
    extraction = _extraction(4)

    review = await _processor(_AlwaysFailGate(), ladder, budget=0)._apply_quality_gate(
        tmp_path / "doc.pdf", extraction, document_id="doc-1"
    )

    assert ladder.calls == 0
    assert review == [1, 2, 3, 4]


async def test_budget_is_not_consumed_by_pages_that_do_not_escalate(
    tmp_path: Path,
) -> None:
    class _PassGate:
        def assess(self, page, *, tables=None) -> QualityVerdict:
            # Only page 3 is wrong; the budget must still be there for it.
            if page.number == 3:
                return QualityVerdict(verdict=Verdict.FAIL, reasons=["bad"])
            return QualityVerdict(verdict=Verdict.PASS)

    ladder = _CountingLadder()
    extraction = _extraction(5)

    review = await _processor(_PassGate(), ladder, budget=1)._apply_quality_gate(
        tmp_path / "doc.pdf", extraction, document_id="doc-1"
    )

    assert ladder.calls == 1
    assert review == [3]
