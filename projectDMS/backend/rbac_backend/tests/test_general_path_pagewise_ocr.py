"""The general path must OCR thin pages individually, not skip the document.

Before this change: is_pdf_textual(max_pages=5) found text on page 3 of
fixture #1 and suppressed OCR for all nine pages, leaving pages 1-2 indexed as
empty. The golden file records that legacy behaviour so the regression is
explicit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Sequence

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.extraction.models import PageSource, PageStatus
from rbac_backend.services.extraction.page_store import NullPageStore
from rbac_backend.services.ocr_service import OCRService
from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "golden_page_routing.json").read_text(
        encoding="utf-8"
    )
)


class _StubOcrRunner:
    def __init__(self) -> None:
        self.requested: list[list[int]] = []

    async def run(
        self, source: Path, page_numbers: Sequence[int], language: str
    ) -> Dict[int, str]:
        self.requested.append(list(page_numbers))
        return {page: f"RECOVERED PAGE {page}" for page in page_numbers}


def _service() -> OCRService:
    config = DocumentProcessingConfig()
    config.ocr_enabled = True
    config.contract_ocr_min_text_chars_per_page = GOLDEN["min_text_chars_per_page"]
    service = OCRService(config)
    # OCR binaries are absent on the dev host; the stub runner stands in for
    # them, so the availability gate must not suppress the page-wise path.
    service._ocr_available = True
    return service


def test_legacy_document_level_decision_would_have_skipped_ocr(tmp_path: Path) -> None:
    # Documents the defect this task fixes.
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    assert _service().is_pdf_textual(source, max_pages=5) is True
    assert (
        GOLDEN["legacy_general_path_behaviour"]["decision"]
        == "skip_ocr_entire_document"
    )


async def test_pagewise_path_ocrs_only_the_thin_pages(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    runner = _StubOcrRunner()

    result = await _service().process_pdf_pagewise(
        source, store=NullPageStore(), document_id="doc-1", ocr_runner=runner
    )

    assert runner.requested == GOLDEN["expected_batches"]
    assert result.ocr_pages_total == 2


async def test_previously_lost_pages_now_carry_text(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    result = await _service().process_pdf_pagewise(
        source, store=NullPageStore(), document_id="doc-1", ocr_runner=_StubOcrRunner()
    )
    by_number = {page.number: page for page in result.pages}

    for number in GOLDEN["legacy_general_path_behaviour"]["pages_silently_empty"]:
        assert by_number[number].text.strip()
        assert by_number[number].source is PageSource.OCR
        assert by_number[number].status is PageStatus.OCR_COMPLETED


async def test_native_pages_are_not_reocred(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    result = await _service().process_pdf_pagewise(
        source, store=NullPageStore(), document_id="doc-1", ocr_runner=_StubOcrRunner()
    )
    by_number = {page.number: page for page in result.pages}

    for number in GOLDEN["pages_expected_native"]:
        assert "RECOVERED PAGE" not in by_number[number].text


async def test_pages_are_persisted_through_the_store(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    store = NullPageStore()

    await _service().process_pdf_pagewise(
        source, store=store, document_id="doc-1", ocr_runner=_StubOcrRunner()
    )

    assert len(store.recorded_pages) == GOLDEN["pages_total"]


async def test_combined_text_contains_the_recovered_pages(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    result = await _service().process_pdf_pagewise(
        source, store=NullPageStore(), document_id="doc-1", ocr_runner=_StubOcrRunner()
    )

    assert "RECOVERED PAGE 1" in result.combined_text
    assert "RECOVERED PAGE 2" in result.combined_text


async def test_unavailable_ocr_marks_pages_rather_than_pretending(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    service = _service()
    service._ocr_available = False
    runner = _StubOcrRunner()

    result = await service.process_pdf_pagewise(
        source, store=NullPageStore(), document_id="doc-1", ocr_runner=runner
    )
    by_number = {page.number: page for page in result.pages}

    assert runner.requested == []
    assert by_number[1].status is PageStatus.OCR_DISABLED
    assert result.completeness.value == "partial"


async def test_retry_pages_are_forwarded_to_the_engine(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    runner = _StubOcrRunner()

    await _service().process_pdf_pagewise(
        source,
        store=NullPageStore(),
        document_id="doc-1",
        ocr_runner=runner,
        retry_pages=[6],
    )

    assert runner.requested == [[6]]
