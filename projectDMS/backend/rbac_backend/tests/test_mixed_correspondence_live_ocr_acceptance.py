"""R1 acceptance through real OCRmyPDF and tesseract.

Skipped where the OCR binaries are absent (dev hosts, CI). Set
``RUN_LIVE_OCR_ACCEPTANCE=1`` to turn a missing binary into a failure instead,
so a run that was meant to exercise OCR cannot pass green without doing so.
Inside the backend image:

    docker run --rm --network none -e RUN_LIVE_OCR_ACCEPTANCE=1 \\
      -v <checkout>/backend:/app ... python -m pytest \\
      rbac_backend/tests/test_mixed_correspondence_live_ocr_acceptance.py

``EXTRACTION_AUDIT_FIXTURE_DIR`` may point at a directory holding the two real
audit PDFs (``glm.pdf``, ``claim.pdf``). They are customer documents and are
never committed; when present, the same recovery is asserted on them.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.extraction.models import PageSource, PageStatus
from rbac_backend.services.extraction.page_store import NullPageStore
from rbac_backend.services.ocr_service import OCRService, TextLayerAssessment
from rbac_backend.tests.fixtures.mixed_correspondence_pdfs import (
    CLAIM_CRITICAL_PHRASES,
    CLAIM_NATIVE_PAGES,
    CLAIM_SCANNED_PAGES,
    GLM_CRITICAL_PHRASES,
    GLM_NATIVE_PAGES,
    GLM_SCANNED_PAGES,
    build_claim_like_pdf,
    build_glm_like_pdf,
)

_REQUIRED = ("ocrmypdf", "tesseract", "gs", "qpdf")
_MISSING = [name for name in _REQUIRED if shutil.which(name) is None]
_DEMANDED = os.environ.get("RUN_LIVE_OCR_ACCEPTANCE") == "1"

if _MISSING and _DEMANDED:
    raise RuntimeError(
        f"RUN_LIVE_OCR_ACCEPTANCE=1 but OCR binaries are missing: {_MISSING}"
    )

pytestmark = pytest.mark.skipif(
    bool(_MISSING), reason=f"live OCR binaries unavailable: {_MISSING}"
)


def _normalise(text: str) -> str:
    """Whitespace-insensitive, case-insensitive form for presence checks.

    Whitespace is removed, not collapsed: tesseract reads the synthetic
    "Rs 3,40,11,265" as "Rs 3,40, 11,265". That is a numeric-integrity defect
    (tracked with R2), not the R1 question this module asks - whether the
    scanned page was read at all.
    """
    return re.sub(r"\s+", "", text).lower()


def _service(tmp_path: Path) -> OCRService:
    config = DocumentProcessingConfig()
    config.ocr_enabled = True
    config.contract_ocr_min_text_chars_per_page = 40
    config.process_dir = str(tmp_path / "processed")
    return OCRService(config)


CASES = {
    "glm_like": (build_glm_like_pdf, GLM_CRITICAL_PHRASES, GLM_SCANNED_PAGES, GLM_NATIVE_PAGES),
    "claim_like": (
        build_claim_like_pdf,
        CLAIM_CRITICAL_PHRASES,
        CLAIM_SCANNED_PAGES,
        CLAIM_NATIVE_PAGES,
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
async def test_live_legacy_loses_the_scanned_letter(name: str, tmp_path: Path) -> None:
    build, critical, _, _ = CASES[name]
    service = _service(tmp_path)
    source = build(tmp_path / "doc.pdf")

    assert service.assess_text_layer(source) is TextLayerAssessment.TEXTUAL
    _, text = await service.process_pdf(source)

    found = [p for p in critical if _normalise(p) in _normalise(text or "")]
    assert found == [], "legacy_v0 unexpectedly recovered scanned-page text"


@pytest.mark.parametrize("name", sorted(CASES))
async def test_live_unified_recovers_the_scanned_letter(name: str, tmp_path: Path) -> None:
    build, critical, scanned, native = CASES[name]
    service = _service(tmp_path)
    assert service._ocr_available, "OCR binaries present but service reports none"

    result = await service.process_pdf_pagewise(
        build(tmp_path / "doc.pdf"), store=NullPageStore(), document_id=f"live-{name}"
    )
    by_number = {page.number: page for page in result.pages}
    text = _normalise(result.combined_text)

    for number in scanned:
        assert by_number[number].source is PageSource.OCR
        assert by_number[number].status is PageStatus.OCR_COMPLETED
    for number in native:
        assert by_number[number].source is PageSource.TEXT_LAYER
    missing = [p for p in critical if _normalise(p) not in text]
    assert missing == []


@pytest.mark.parametrize("name", sorted(CASES))
async def test_live_unified_is_deterministic(name: str, tmp_path: Path) -> None:
    build, _, _, _ = CASES[name]
    service = _service(tmp_path)
    source = build(tmp_path / "doc.pdf")

    first = await service.process_pdf_pagewise(
        source, store=NullPageStore(), document_id="live-a"
    )
    second = await service.process_pdf_pagewise(
        source, store=NullPageStore(), document_id="live-b"
    )

    assert first.combined_text == second.combined_text


_AUDIT_DIR = os.environ.get("EXTRACTION_AUDIT_FIXTURE_DIR")


@pytest.mark.skipif(not _AUDIT_DIR, reason="real audit PDFs not provided")
@pytest.mark.parametrize(
    "filename,scanned_pages", [("glm.pdf", (1, 2)), ("claim.pdf", (1,))]
)
async def test_live_real_audit_documents_recover_their_scanned_pages(
    filename: str, scanned_pages: tuple, tmp_path: Path
) -> None:
    source = Path(_AUDIT_DIR or "") / filename
    if not source.exists():
        pytest.skip(f"{source} not present")
    service = _service(tmp_path)

    _, legacy_text = await service.process_pdf(source)
    result = await service.process_pdf_pagewise(
        source, store=NullPageStore(), document_id=f"audit-{filename}"
    )
    by_number = {page.number: page for page in result.pages}

    for number in scanned_pages:
        assert by_number[number].source is PageSource.OCR
        # A scanned letter page reads as hundreds of characters, not a stamp.
        assert len(by_number[number].text.strip()) > 500
    assert len(result.combined_text) > len(legacy_text or "")
