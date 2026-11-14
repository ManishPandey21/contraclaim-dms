"""Failure paths must be visible, never quietly successful.

Each case here is a file the pipeline cannot fully read. The requirement is not
that extraction succeeds - it is that the document never reports `completed`
while carrying content nobody extracted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Sequence

from PIL import Image

from rbac_backend.models.processing_state import (
    ProcessingState,
    build_attempt_outcome,
    derive_processing_state,
)
from rbac_backend.services.extraction.engine import PageExtractionEngine
from rbac_backend.services.extraction.image_extractor import extract_image
from rbac_backend.services.extraction.models import (
    Completeness,
    PageClass,
    PageExtractionPolicy,
    PageStatus,
)
from rbac_backend.services.extraction.page_store import NullPageStore
from rbac_backend.tests.fixtures.pdf_builders import (
    build_corrupt_pdf,
    build_mixed_pdf,
)


class _StubOcr:
    async def run(
        self, source: Path, page_numbers: Sequence[int], language: str
    ) -> Dict[int, str]:
        return {page: f"OCR {page}" for page in page_numbers}


class _EmptyImageOcr:
    async def run(self, source: Path, language: str) -> str:
        return ""


def _policy(**overrides) -> PageExtractionPolicy:
    defaults = dict(
        ocr_enabled=True,
        min_text_chars_per_page=40,
        batch_size=25,
        max_ocr_pages_per_attempt=0,
        ocr_language="eng",
    )
    defaults.update(overrides)
    return PageExtractionPolicy(**defaults)  # type: ignore[arg-type]


async def _extract(source: Path, policy=None, runner=None):
    engine = PageExtractionEngine(
        policy=policy or _policy(),
        ocr_runner=runner or _StubOcr(),
        store=NullPageStore(),
    )
    return await engine.extract(source)


# --- Corrupt document -------------------------------------------------------


async def test_corrupt_pdf_yields_an_unrenderable_page_not_a_crash(
    tmp_path: Path,
) -> None:
    source = build_corrupt_pdf(tmp_path / "bad.pdf")

    result = await _extract(source)

    assert result.unrenderable_pages == [1]
    assert result.pages[0].classification.page_class is PageClass.UNRENDERABLE
    assert result.pages[0].status is PageStatus.UNRENDERABLE
    assert result.completeness is Completeness.PARTIAL


async def test_corrupt_pdf_can_never_reach_completed(tmp_path: Path) -> None:
    source = build_corrupt_pdf(tmp_path / "bad.pdf")

    result = await _extract(source)

    for exhausted in (False, True):
        state = derive_processing_state(result, attempts_exhausted=exhausted)
        assert state is ProcessingState.HUMAN_REVIEW_REQUIRED
        assert state is not ProcessingState.COMPLETED


async def test_corrupt_pdf_records_an_error_a_human_can_read(
    tmp_path: Path,
) -> None:
    source = build_corrupt_pdf(tmp_path / "bad.pdf")

    result = await _extract(source)

    assert result.pages[0].error


# --- Image whose OCR finds nothing ------------------------------------------


async def test_png_with_empty_ocr_is_partial_not_complete(tmp_path: Path) -> None:
    source = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), "white").save(source)

    result = await extract_image(
        source,
        store=NullPageStore(),
        image_ocr_runner=_EmptyImageOcr(),
        language="eng",
    )

    assert result.pages[0].status is PageStatus.OCR_EMPTY
    assert result.completeness is Completeness.PARTIAL


async def test_png_with_empty_ocr_never_reaches_completed(tmp_path: Path) -> None:
    source = tmp_path / "scan.png"
    Image.new("RGB", (400, 300), "white").save(source)

    result = await extract_image(
        source,
        store=NullPageStore(),
        image_ocr_runner=_EmptyImageOcr(),
        language="eng",
    )
    outcome = build_attempt_outcome(
        result, prior_page_attempts={}, attempts_exhausted=False
    )

    assert outcome.state is not ProcessingState.COMPLETED
    assert outcome.remaining_page_numbers == [1]


# --- Resumable boundary -----------------------------------------------------


async def test_deferred_pages_survive_the_attempt_boundary(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    first = await _extract(source, policy=_policy(max_ocr_pages_per_attempt=1))

    assert first.ocr_deferred_pages == [2]
    assert first.completeness is Completeness.PARTIAL
    outcome = build_attempt_outcome(
        first, prior_page_attempts={}, attempts_exhausted=False
    )
    assert outcome.state is ProcessingState.PARTIALLY_PROCESSED
    assert outcome.remaining_page_numbers == [2]
    # A deferred page has not been tried, so it must not have burned an attempt.
    assert outcome.page_attempts == {}


async def test_a_resumed_attempt_completes_the_remaining_page(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    first = await _extract(source, policy=_policy(max_ocr_pages_per_attempt=1))
    remaining = build_attempt_outcome(
        first, prior_page_attempts={}, attempts_exhausted=False
    ).remaining_page_numbers

    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=_StubOcr(), store=NullPageStore()
    )
    second = await engine.extract(source, retry_pages=remaining)

    resumed = {page.number: page for page in second.pages}
    assert resumed[2].status is PageStatus.OCR_COMPLETED
    assert resumed[2].text.strip()
    assert second.ocr_deferred_pages == []
