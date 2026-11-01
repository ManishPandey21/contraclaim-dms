"""R2: amounts must survive extraction digit for digit.

Two measured corruptions, one per reader:

* **Native text (Excel exports).** A padding space glyph drawn inside the first
  digit's box is read between the first and second digit: ``19,292,171``
  becomes ``1 9,292,171``, ``689,012`` becomes ``6 89,012``, ``950,000``
  becomes ``9 50,000`` (2026-10-07 audit claim, pages 5-6). Production
  (`legacy_v0`) publishes exactly that, and the quality gate, unable to
  establish column roles, cannot catch it.
* **OCR.** Tesseract can break a word inside a grouped number at a visible
  gap: ``3,40,11,265`` read as ``3,40, 11,265``. Reproduced on a synthetic
  raster; the real claim letter's page 1 OCRs correctly, so this guards a
  failure class rather than a measured production value.

The native fix is geometric and at the source (`readable_page`); the OCR fix
joins only a fragment that is not a valid number on its own into one that is.
Every published change keeps the reading it replaced as page evidence.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Dict, Sequence

import pdfplumber
import pytest

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.extraction.models import PageSource
from rbac_backend.services.extraction.page_store import NullPageStore
from rbac_backend.services.ocr_service import OCRService
from rbac_backend.tests.fixtures.pdf_builders import build_image_page_pdf
from rbac_backend.tests.fixtures.numeric_integrity_pdfs import (
    CONTROL_LINE,
    HEADING,
    PADDED_VALUES,
    build_excel_padded_numbers_pdf,
)

BACKEND = Path(__file__).resolve().parents[1]


def _service(tmp_path: Path) -> OCRService:
    config = DocumentProcessingConfig()
    config.ocr_enabled = True
    config.contract_ocr_min_text_chars_per_page = 40
    config.process_dir = str(tmp_path / "processed")
    service = OCRService(config)
    service._ocr_available = True
    return service


class _Runner:
    def __init__(self, text: Dict[int, str]) -> None:
        self.text = text

    async def run(self, source: Path, page_numbers: Sequence[int], language: str):
        return {page: self.text.get(page, "") for page in page_numbers}


# --- The defect, as pdfplumber reads it unaided ---------------------------------


def test_an_unaided_read_splits_the_padded_amounts(tmp_path: Path) -> None:
    source = build_excel_padded_numbers_pdf(tmp_path / "padded.pdf")
    with pdfplumber.open(source) as pdf:
        text = pdf.pages[0].extract_text()

    for correct, corrupted in PADDED_VALUES:
        assert corrupted in text
        assert correct not in text


# --- Native: the shared reader --------------------------------------------------


def test_readable_page_reads_every_padded_amount_whole(tmp_path: Path) -> None:
    from rbac_backend.services.extraction.numeric_integrity import readable_page

    source = build_excel_padded_numbers_pdf(tmp_path / "padded.pdf")
    with pdfplumber.open(source) as pdf:
        text = readable_page(pdf.pages[0]).extract_text()

    for correct, corrupted in PADDED_VALUES:
        assert correct in text
        assert corrupted not in text


def test_readable_page_keeps_real_spaces(tmp_path: Path) -> None:
    """A space in a gap is a word boundary: words and two-number cells stay apart."""
    from rbac_backend.services.extraction.numeric_integrity import readable_page

    source = build_excel_padded_numbers_pdf(tmp_path / "padded.pdf")
    with pdfplumber.open(source) as pdf:
        text = readable_page(pdf.pages[0]).extract_text()

    assert HEADING in text
    assert CONTROL_LINE in text


def test_readable_page_counts_what_it_dropped(tmp_path: Path) -> None:
    from rbac_backend.services.extraction.numeric_integrity import phantom_space_count

    source = build_excel_padded_numbers_pdf(tmp_path / "padded.pdf")
    with pdfplumber.open(source) as pdf:
        assert phantom_space_count(pdf.pages[0]) == len(PADDED_VALUES)


def test_a_page_without_phantom_glyphs_reads_exactly_as_before(tmp_path: Path) -> None:
    from rbac_backend.services.extraction.numeric_integrity import readable_page
    from rbac_backend.tests.fixtures.pdf_builders import build_clean_native_pdf

    source = build_clean_native_pdf(tmp_path / "clean.pdf")
    with pdfplumber.open(source) as pdf:
        for page in pdf.pages:
            assert readable_page(page).extract_text() == page.extract_text()


# --- Native: both production pipelines -------------------------------------------


async def test_unified_publishes_the_whole_amounts(tmp_path: Path) -> None:
    source = build_excel_padded_numbers_pdf(tmp_path / "padded.pdf")

    result = await _service(tmp_path).process_pdf_pagewise(
        source, store=NullPageStore(), document_id="doc-1", ocr_runner=_Runner({})
    )
    page = result.pages[0]

    for correct, corrupted in PADDED_VALUES:
        assert correct in page.text
        assert corrupted not in result.combined_text
    assert page.source is PageSource.TEXT_LAYER


async def test_unified_keeps_the_unaided_reading_as_evidence(tmp_path: Path) -> None:
    source = build_excel_padded_numbers_pdf(tmp_path / "padded.pdf")

    result = await _service(tmp_path).process_pdf_pagewise(
        source, store=NullPageStore(), document_id="doc-1", ocr_runner=_Runner({})
    )
    page = result.pages[0]

    assert page.raw_text is not None
    for _, corrupted in PADDED_VALUES:
        assert corrupted in page.raw_text
    assert page.text_withheld is False
    methods = [repair["method"] for repair in page.applied_repairs]
    assert methods == ["phantom_space_glyph"]
    assert page.applied_repairs[0]["count"] == len(PADDED_VALUES)


async def test_legacy_publishes_the_whole_amounts(tmp_path: Path) -> None:
    """Production runs legacy_v0: the fix must reach it, not only the canary."""
    source = build_excel_padded_numbers_pdf(tmp_path / "padded.pdf")

    _, text = await _service(tmp_path).process_pdf(source)

    for correct, corrupted in PADDED_VALUES:
        assert correct in text
        assert corrupted not in text
    assert CONTROL_LINE in text


# --- OCR: joining a grouped number split at a word gap ----------------------------


@pytest.mark.parametrize(
    "before,after",
    [
        ("Rs 3,40, 11,265/-", "Rs 3,40,11,265/-"),
        ("amount 12,34, 56,789.50 only", "amount 12,34,56,789.50 only"),
        ("total 1,00, 000", "total 1,00,000"),
    ],
)
def test_ocr_fragments_of_one_grouped_number_are_joined(before: str, after: str) -> None:
    from rbac_backend.services.extraction.numeric_integrity import repair_split_grouped_numbers

    repaired, repairs = repair_split_grouped_numbers(before)

    assert repaired == after
    assert len(repairs) == 1
    assert repairs[0]["method"] == "ocr_split_grouped_number"


@pytest.mark.parametrize(
    "text",
    [
        "items 20, 30, 40",  # a list of plain numbers
        "1,200, 3,400",  # two valid Western numbers in a list
        "19,292, 171",  # left part is a valid number on its own: not provably one
        "3,40, 11",  # joined value is not a valid grouping either way
        "Clause 8.4.1, 17.1",  # clause numbers, no thousands grouping
        "dated 24.12.2022, 11,265",  # a date, then an amount
        "3,40,11,265",  # already whole
    ],
)
def test_ocr_repair_leaves_anything_it_cannot_prove_alone(text: str) -> None:
    from rbac_backend.services.extraction.numeric_integrity import repair_split_grouped_numbers

    repaired, repairs = repair_split_grouped_numbers(text)

    assert repaired == text
    assert repairs == []


async def test_unified_repairs_ocr_text_and_keeps_what_ocr_read(tmp_path: Path) -> None:
    source = build_image_page_pdf(tmp_path / "scan.pdf")
    runner = _Runner({1: "The amount claimed is Rs 3,40, 11,265/- only."})

    result = await _service(tmp_path).process_pdf_pagewise(
        source, store=NullPageStore(), document_id="doc-1", ocr_runner=runner
    )
    page = result.pages[0]

    assert page.source is PageSource.OCR
    assert "Rs 3,40,11,265/-" in page.text
    assert page.raw_text == "The amount claimed is Rs 3,40, 11,265/- only."
    assert [r["method"] for r in page.applied_repairs] == ["ocr_split_grouped_number"]


async def test_legacy_repairs_ocr_text(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)

    async def _ocr(*args, **kwargs):
        return "The amount claimed is Rs 3,40, 11,265/- only."

    monkeypatch.setattr(service, "_run_ocr_with_sidecar", _ocr)

    _, text = await service.process_pdf(build_image_page_pdf(tmp_path / "scan.pdf"))

    assert "Rs 3,40,11,265/-" in text


async def test_native_text_is_never_put_through_the_ocr_repair(tmp_path: Path) -> None:
    """The OCR rule is for OCR output only; a native layer keeps its wording."""
    from rbac_backend.tests.fixtures.pdf_builders import build_page_text_map_pdf

    source = build_page_text_map_pdf(
        tmp_path / "native.pdf",
        page_lines=[["Schedule of rates reproduced from the tender documents", "3,40, 11,265"]],
    )

    result = await _service(tmp_path).process_pdf_pagewise(
        source, store=NullPageStore(), document_id="doc-1", ocr_runner=_Runner({})
    )

    assert "3,40, 11,265" in result.pages[0].text


# --- Guard: no pdfplumber read may bypass the shared reader -----------------------

_GUARDED_FILES = (
    "services/extraction/engine.py",
    "services/extraction/page_classifier.py",
    "services/extraction/ocrmypdf_runner.py",
    "services/ocr_service.py",
)
_PAGE_READS = {"extract_text", "extract_tables", "extract_table", "extract_words", "find_tables"}


@pytest.mark.parametrize("relative", _GUARDED_FILES)
def test_every_page_read_goes_through_readable_page(relative: str) -> None:
    tree = ast.parse((BACKEND / relative).read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in _PAGE_READS:
            continue
        receiver = node.func.value
        wrapped = (
            isinstance(receiver, ast.Call)
            and isinstance(receiver.func, ast.Name)
            and receiver.func.id == "readable_page"
        )
        if not wrapped:
            offenders.append(f"{relative}:{node.lineno} .{node.func.attr}()")
    assert offenders == [], (
        "pdfplumber page reads must go through readable_page(), or Excel padding "
        f"glyphs split amounts again: {offenders}"
    )


# --- Real audit document (customer PDF, never committed) --------------------------

_AUDIT_DIR = os.environ.get("EXTRACTION_AUDIT_FIXTURE_DIR")


@pytest.mark.skipif(not _AUDIT_DIR, reason="real audit PDFs not provided")
async def test_real_claim_amounts_survive_both_pipelines(tmp_path: Path) -> None:
    source = Path(_AUDIT_DIR or "") / "claim.pdf"
    if not source.exists():
        pytest.skip(f"{source} not present")
    service = _service(tmp_path)

    _, legacy = await service.process_pdf(source)
    unified = await service.process_pdf_pagewise(
        source, store=NullPageStore(), document_id="audit-claim",
        ocr_runner=_Runner({1: "scanned covering letter"}),
    )

    for text in (legacy or "", unified.combined_text):
        for correct, corrupted in (
            ("19,292,171", "1 9,292,171"),
            ("689,012", "6 89,012"),
            ("950,000", "9 50,000"),
        ):
            assert correct in text
            assert corrupted not in text


# --- Provenance survives the quality gate -----------------------------------------


def test_a_gate_repair_appends_to_extraction_repairs_instead_of_replacing_them() -> None:
    """`_apply_repairs` used to assign `page.applied_repairs`, erasing the
    record of anything extraction had already repaired on the same page."""
    from types import SimpleNamespace

    from rbac_backend.services.document_processor import DocumentProcessor
    from rbac_backend.services.extraction.models import ExtractedPage, PageStatus
    from rbac_backend.services.extraction.numeric_integrity import (
        phantom_space_repair_record,
    )

    page = ExtractedPage(
        number=1,
        text="Total 5 77,188",
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=None,
        raw_text="Tot al 5 77,188",
        applied_repairs=[phantom_space_repair_record(1)],
    )
    repair = SimpleNamespace(
        before="5 77,188", after="577,188", reason="split digit", method="split_digit",
        confirming_checks=["row_total"], page=1,
    )
    reassessed = SimpleNamespace(repairs=[], verdict=SimpleNamespace(value="pass"), checks=[])
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.quality_gate = SimpleNamespace(assess=lambda page, tables=None: reassessed)

    processor._apply_repairs(page, SimpleNamespace(repairs=[repair]))

    assert [r["method"] for r in page.applied_repairs] == ["phantom_space_glyph", "split_digit"]
    assert page.text == "Total 577,188"
    assert page.raw_text == "Tot al 5 77,188", "raw_text is written once, never overwritten"
