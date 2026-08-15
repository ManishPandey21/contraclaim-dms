"""End-to-end: a page that cannot be resolved must not reach COMPLETED."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from rbac_backend.models.document_metadata import ProcessingResult
from rbac_backend.models.processing_state import (
    ProcessingState,
    derive_processing_state,
)
from rbac_backend.services.document_processor import DocumentProcessor
from rbac_backend.services.extraction.fallback.models import FallbackOutcome, ResolvedPage
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


def _result(**overrides: object) -> PageExtractionResult:
    defaults = dict(
        pages=[],
        combined_text="",
        ocr_pages_total=1,
        ocr_failed_pages=[],
        ocr_deferred_pages=[],
        completeness=Completeness.COMPLETE,
        engine_version="1",
    )
    defaults.update(overrides)
    return PageExtractionResult(**defaults)  # type: ignore[arg-type]


def _page(number: int, text: str = "some text") -> ExtractedPage:
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


class _CountingGate:
    def __init__(self, verdict: Verdict = Verdict.PASS) -> None:
        self.page_numbers: list[int] = []
        self.verdict = verdict

    def assess(self, page: ExtractedPage, *, tables: Any = None) -> QualityVerdict:
        self.page_numbers.append(page.number)
        return QualityVerdict(verdict=self.verdict)


class _RecordingLadder:
    def __init__(self, outcome: FallbackOutcome) -> None:
        self.outcome = outcome
        self.calls = 0

    async def resolve(self, source, page, verdict, *, document_id, tables=None):
        self.calls += 1
        return ResolvedPage(page=page, outcome=self.outcome)


def _processor(gate: Any, ladder: Any = None) -> DocumentProcessor:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.quality_gate = gate
    processor.fallback_ladder = ladder
    return processor


def test_a_page_needing_human_review_blocks_completed() -> None:
    state = derive_processing_state(
        _result(completeness=Completeness.PARTIAL, ocr_failed_pages=[1]),
        attempts_exhausted=True,
    )

    assert state is ProcessingState.HUMAN_REVIEW_REQUIRED
    assert state is not ProcessingState.COMPLETED


def test_processing_result_reports_human_review_pages() -> None:
    result = ProcessingResult(success=True, pages_human_review=[1, 2])

    assert result.pages_human_review == [1, 2]


def test_processing_result_defaults_to_no_human_review_pages() -> None:
    assert ProcessingResult(success=True).pages_human_review == []


def test_processing_results_do_not_share_a_mutable_default() -> None:
    first = ProcessingResult(success=True)
    second = ProcessingResult(success=True)

    first.pages_human_review.append(9)

    assert second.pages_human_review == []


async def test_gate_runs_for_every_page(tmp_path: Path) -> None:
    gate = _CountingGate()
    extraction = _result(pages=[_page(1), _page(2), _page(3)])

    await _processor(gate)._apply_quality_gate(
        tmp_path / "doc.pdf", extraction, document_id="doc-1"
    )

    assert gate.page_numbers == [1, 2, 3]


async def test_passing_pages_need_no_review_and_no_ladder(tmp_path: Path) -> None:
    ladder = _RecordingLadder(FallbackOutcome.UNCHANGED)
    extraction = _result(pages=[_page(1), _page(2)])

    review = await _processor(_CountingGate(Verdict.PASS), ladder)._apply_quality_gate(
        tmp_path / "doc.pdf", extraction, document_id="doc-1"
    )

    assert review == []
    assert ladder.calls == 0


async def test_not_checkable_pages_are_marked_unverified_not_escalated(
    tmp_path: Path,
) -> None:
    ladder = _RecordingLadder(FallbackOutcome.UNCHANGED)
    extraction = _result(pages=[_page(1)])

    review = await _processor(
        _CountingGate(Verdict.NOT_CHECKABLE), ladder
    )._apply_quality_gate(tmp_path / "doc.pdf", extraction, document_id="doc-1")

    assert review == []
    assert ladder.calls == 0
    assert extraction.pages[0].quality_verdict == "not_checkable"


async def test_failing_page_without_a_ladder_needs_human_review(
    tmp_path: Path,
) -> None:
    extraction = _result(pages=[_page(1), _page(2)])

    review = await _processor(_CountingGate(Verdict.FAIL), None)._apply_quality_gate(
        tmp_path / "doc.pdf", extraction, document_id="doc-1"
    )

    assert review == [1, 2]
    assert extraction.completeness is Completeness.PARTIAL


async def test_ladder_resolution_clears_the_review_requirement(
    tmp_path: Path,
) -> None:
    ladder = _RecordingLadder(FallbackOutcome.RESOLVED)
    extraction = _result(pages=[_page(1)])

    review = await _processor(_CountingGate(Verdict.FAIL), ladder)._apply_quality_gate(
        tmp_path / "doc.pdf", extraction, document_id="doc-1"
    )

    assert ladder.calls == 1
    assert review == []


async def test_unresolved_ladder_outcome_requires_human_review(
    tmp_path: Path,
) -> None:
    ladder = _RecordingLadder(FallbackOutcome.HUMAN_REVIEW_REQUIRED)
    extraction = _result(pages=[_page(1)])

    review = await _processor(_CountingGate(Verdict.FAIL), ladder)._apply_quality_gate(
        tmp_path / "doc.pdf", extraction, document_id="doc-1"
    )

    assert review == [1]
    assert extraction.pages[0].needs_review is True
    assert extraction.completeness is Completeness.PARTIAL


async def test_quality_verdict_is_persisted_on_the_page(tmp_path: Path) -> None:
    extraction = _result(pages=[_page(1)])

    await _processor(_CountingGate(Verdict.FAIL), None)._apply_quality_gate(
        tmp_path / "doc.pdf", extraction, document_id="doc-1"
    )

    assert extraction.pages[0].quality_verdict == "fail"
