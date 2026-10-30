"""The extraction fixtures must read the same whether or not OCRmyPDF was imported.

Importing ``ocrmypdf`` (16.10.4, ``ocrmypdf/pdfinfo/layout.py``) replaces
``pdfminer.pdffont.PDFSimpleFont.__init__`` for the whole interpreter. The
replacement empties ``cid2unicode`` for any simple font that has neither
``/Encoding`` nor ``/ToUnicode``, so pdfminer - and pdfplumber above it - then
emits ``(cid:67)(cid:108)...`` for every glyph of that font.

The fixtures used to declare exactly that font (Type1 Helvetica, no encoding),
so three extraction tests passed or failed depending on whether an earlier test
in the same process had imported OCRmyPDF. The fixtures now name
``/WinAnsiEncoding`` explicitly. These tests pin both halves:

* every font the builders emit declares an encoding, so nobody simplifies the
  fixture back into the ambiguous shape; and
* the text extracted in a clean interpreter equals the text extracted in one
  that imported OCRmyPDF first.

The comparison runs in two subprocesses, because the monkeypatch is
interpreter-global: a test that imported OCRmyPDF in the pytest process would
itself become order-dependent for every test collected after it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pikepdf

from rbac_backend.tests.fixtures.child_python import BACKEND_ROOT, run_python_child
from rbac_backend.tests.fixtures.pdf_builders import (
    build_clean_native_pdf,
    build_mixed_pdf,
    build_scanned_only_pdf,
    build_text_pdf,
)

#: Builds every text-bearing fixture and prints its per-page text as JSON.
#: ``argv[1]`` is the output directory, ``argv[2]`` is ``clean`` or
#: ``ocrmypdf-first``. Nothing pdfminer-related is imported before the branch.
_CHILD = r"""
import json, sys
from pathlib import Path

out_dir, mode = Path(sys.argv[1]), sys.argv[2]
if mode == "ocrmypdf-first":
    import ocrmypdf  # noqa: F401 - the import is the point

import pdfplumber
from pdfminer.pdffont import PDFSimpleFont
from rbac_backend.tests.fixtures.pdf_builders import (
    build_clean_native_pdf, build_mixed_pdf, build_scanned_only_pdf, build_text_pdf,
)

def pages(path):
    with pdfplumber.open(path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]

out_dir.mkdir(parents=True, exist_ok=True)
print(json.dumps({
    "simple_font_init_module": PDFSimpleFont.__init__.__module__,
    "text": pages(build_text_pdf(out_dir / "text.pdf", pages=2, text="Batch page")),
    "mixed": pages(build_mixed_pdf(out_dir / "mixed.pdf")),
    "clean": pages(build_clean_native_pdf(out_dir / "clean.pdf")),
    "scanned": pages(build_scanned_only_pdf(out_dir / "scan.pdf", pages=2)),
}))
"""


def _extract_in_fresh_interpreter(out_dir: Path, mode: str) -> Dict[str, Any]:
    result: Dict[str, Any] = run_python_child(_CHILD, str(out_dir), mode, cwd=BACKEND_ROOT)
    return result


def test_every_fixture_font_declares_an_encoding(tmp_path: Path) -> None:
    built = [
        build_text_pdf(tmp_path / "text.pdf", pages=2),
        build_mixed_pdf(tmp_path / "mixed.pdf"),
        build_clean_native_pdf(tmp_path / "clean.pdf"),
        build_scanned_only_pdf(tmp_path / "scan.pdf", pages=2),
    ]

    fonts_seen = 0
    for path in built:
        with pikepdf.open(path) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                for name, font in page.Resources.Font.items():
                    fonts_seen += 1
                    # A simple font with neither entry is the shape OCRmyPDF's
                    # patch strips of its Unicode mapping. Either entry is an
                    # intentional mapping; the builders use /Encoding.
                    assert "/Encoding" in font or "/ToUnicode" in font, (
                        f"{path.name} page {number} font {name} declares no "
                        "/Encoding and no /ToUnicode"
                    )
                    assert font.Encoding == pikepdf.Name.WinAnsiEncoding

    assert fonts_seen > 0


def test_fixture_text_does_not_depend_on_ocrmypdf_import_order(
    tmp_path: Path,
) -> None:
    clean = _extract_in_fresh_interpreter(tmp_path / "clean", "clean")
    patched = _extract_in_fresh_interpreter(tmp_path / "patched", "ocrmypdf-first")

    # The control: the second interpreter really is running with OCRmyPDF's
    # patch installed. If a future OCRmyPDF stops patching pdfminer this fails,
    # and the guard below has to be re-pointed at whatever replaced it rather
    # than silently passing in an environment that no longer proves anything.
    assert clean["simple_font_init_module"] == "pdfminer.pdffont"
    assert patched["simple_font_init_module"] == "ocrmypdf.pdfinfo.layout"

    for fixture in ("text", "mixed", "clean", "scanned"):
        assert patched[fixture] == clean[fixture], fixture

    combined = "\n".join(
        text for fixture in ("text", "mixed", "clean") for text in patched[fixture]
    )
    assert "(cid:" not in combined
    assert "Claim summary page 3" in patched["mixed"][2]
    assert "Page 1 of 2" in patched["text"][0]
    assert "Page 2 of 2" in patched["text"][1]
    assert "Nayaganj Station" in patched["clean"][0]

    # Structure is unchanged by the encoding: the scan stays textless and the
    # mixed fixture keeps its two textless pages ahead of seven text pages.
    assert all(not text.strip() for text in patched["scanned"])
    assert [bool(text.strip()) for text in patched["mixed"]] == [False] * 2 + [True] * 7
