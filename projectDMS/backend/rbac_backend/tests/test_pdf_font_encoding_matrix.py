"""Which font shapes survive OCRmyPDF's pdfminer patch, measured per shape.

Each case is extracted in two fresh interpreters - one clean, one that imported
``ocrmypdf`` first - so the patch is really installed and cannot leak into the
rest of the suite.

* A  simple Type1 Helvetica with ``/WinAnsiEncoding``  - text either way.
* B  simple Type1 Helvetica, no encoding, no ToUnicode - text when clean,
  ``(cid:N)`` once OCRmyPDF is imported. The shape the fixtures used to have.
* C  composite Identity-H font with ``/ToUnicode``     - real Unicode either
  way. This is the shape of OCRmyPDF's own output text layer
  (``GlyphLessFont``, Type0, Identity-H, ToUnicode - measured on 16.10.4), so
  re-reading OCR output in a patched parent is safe.
* C-  composite Identity-H font without ``/ToUnicode``  - ``(cid:N)`` either
  way. Nothing about import order causes it and nothing about import order
  fixes it; only the engine's quality check catches it.

The OCR runner's page mapping is also exercised in the patched interpreter,
because OcrMyPdfRunner runs OCRmyPDF in a subprocess but re-reads its output
with pdfplumber in the parent - the process that may already be patched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

from rbac_backend.tests.fixtures.child_python import BACKEND_ROOT, run_python_child

_CHILD = r"""
import json, sys
from pathlib import Path

out_dir, mode = Path(sys.argv[1]), sys.argv[2]
if mode == "ocrmypdf-first":
    import ocrmypdf  # noqa: F401 - the import is the point

import pdfplumber
from rbac_backend.services.extraction.ocrmypdf_runner import OcrMyPdfRunner
from rbac_backend.tests.fixtures.pdf_builders import (
    build_composite_font_pdf, build_mixed_pdf, build_text_pdf, build_unencoded_font_pdf,
)

LINES = ["Amount payable Rs. 9,600,000 under Clause 14.2", "Letter No. CC/UPMRC/0412"]
UNICODE = ["Amount payable ₹ 9,600,000 to Société Générale"]

def text(path):
    with pdfplumber.open(path) as pdf:
        return pdf.pages[0].extract_text() or ""

out_dir.mkdir(parents=True, exist_ok=True)
runner = OcrMyPdfRunner(work_dir=out_dir)
print(json.dumps({
    "A": text(build_text_pdf(out_dir / "a.pdf", pages=1, text=LINES[0])),
    "B": text(build_unencoded_font_pdf(out_dir / "b.pdf", lines=LINES)),
    "C": text(build_composite_font_pdf(out_dir / "c.pdf", composite_lines=UNICODE, to_unicode=True)),
    "C_no_tounicode": text(build_composite_font_pdf(out_dir / "cp.pdf", composite_lines=LINES)),
    "full_length": runner._extract_mapped_pages(
        output_path=build_mixed_pdf(out_dir / "full.pdf"), page_numbers=[3, 4], input_page_count=9,
    ),
    "trimmed": runner._extract_mapped_pages(
        output_path=build_text_pdf(out_dir / "trim.pdf", pages=2, text="Batch page"),
        page_numbers=[8, 9], input_page_count=9,
    ),
}))
"""


def _run(out_dir: Path, mode: str) -> Dict[str, Any]:
    result: Dict[str, Any] = run_python_child(_CHILD, str(out_dir), mode, cwd=BACKEND_ROOT)
    return result


@pytest.fixture(scope="module")
def extractions(
    tmp_path_factory: pytest.TempPathFactory,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    root = tmp_path_factory.mktemp("font_matrix")
    return _run(root / "clean", "clean"), _run(root / "patched", "ocrmypdf-first")


def test_font_shapes_before_and_after_ocrmypdf_import(
    extractions: Tuple[Dict[str, Any], Dict[str, Any]],
) -> None:
    clean, patched = extractions

    # A: an explicit encoding is immune.
    assert clean["A"] == patched["A"]
    assert "Amount payable Rs. 9,600,000 under Clause 14.2" in patched["A"]

    # B: the ambiguous simple font is exactly what the patch breaks.
    assert "Letter No. CC/UPMRC/0412" in clean["B"]
    assert "(cid:" not in clean["B"]
    assert "(cid:" in patched["B"] and "Letter No." not in patched["B"]

    # C: a ToUnicode CMap keeps real Unicode - including non-Latin glyphs.
    assert clean["C"] == patched["C"]
    assert "₹ 9,600,000 to Société Générale" in patched["C"]

    # C-: no Unicode mapping at all is placeholders regardless of order.
    assert clean["C_no_tounicode"] == patched["C_no_tounicode"]
    assert clean["C_no_tounicode"].startswith("(cid:")


def test_ocr_output_mapping_is_unchanged_in_a_patched_parent(
    extractions: Tuple[Dict[str, Any], Dict[str, Any]],
) -> None:
    clean, patched = extractions

    for shape in ("full_length", "trimmed"):
        assert patched[shape] == clean[shape], shape

    # JSON object keys are strings.
    assert set(patched["full_length"]) == {"3", "4"}
    assert "Claim summary page 3" in patched["full_length"]["3"]
    assert "Claim summary page 4" in patched["full_length"]["4"]
    assert set(patched["trimmed"]) == {"8", "9"}
    assert "Page 1 of 2" in patched["trimmed"]["8"]
    assert "Page 2 of 2" in patched["trimmed"]["9"]
