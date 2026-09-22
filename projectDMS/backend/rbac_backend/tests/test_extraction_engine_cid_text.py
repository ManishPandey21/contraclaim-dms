"""A text layer pdfminer cannot map is not a text layer.

When a font carries no usable Unicode mapping pdfminer emits one ``(cid:N)``
placeholder per glyph. Six glyphs of that are already past the 40-character
threshold, so before this guard a page of placeholders was accepted as native
text, never sent to OCR, and reported COMPLETE. Two real sources produce it:

* a composite (Type0, Identity-H) font without ``/ToUnicode`` - placeholders
  in every interpreter; and
* a Standard-14 simple font without ``/Encoding`` or ``/ToUnicode`` - after
  OCRmyPDF has been imported anywhere in the process.

These tests drive the real engine over real PDFs of both shapes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Sequence

from rbac_backend.services.extraction.engine import PageExtractionEngine
from rbac_backend.services.extraction.models import (
    Completeness,
    PageClass,
    PageExtractionPolicy,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.page_store import NullPageStore
from rbac_backend.tests.fixtures.child_python import run_python_child
from rbac_backend.tests.fixtures.pdf_builders import (
    build_composite_font_pdf,
    build_mixed_pdf,
)

_BODY = [
    "The Contractor hereby gives notice of a claim for additional payment",
    "arising from the suspension of the works instructed by the Engineer",
    "under Sub-Clause 8.8, with particulars to follow within 28 days.",
]
_READABLE = [
    "The Contractor hereby submits its monthly interim payment application",
    "in respect of the permanent works executed at the Nayaganj Station",
    "site during the period under review, prepared in accordance with the",
    "measurement provisions of the Contract and supported by joint records.",
]


class _Runner:
    def __init__(self, text: str | None = None) -> None:
        self.requested: list[list[int]] = []
        self.text = text

    async def run(
        self, source: Path, page_numbers: Sequence[int], language: str
    ) -> Dict[int, str]:
        self.requested.append(list(page_numbers))
        return {
            page: self.text if self.text is not None else f"OCR TEXT PAGE {page}"
            for page in page_numbers
        }


def _engine(runner: _Runner, *, ocr_enabled: bool = True) -> PageExtractionEngine:
    return PageExtractionEngine(
        policy=PageExtractionPolicy(
            ocr_enabled=ocr_enabled,
            min_text_chars_per_page=40,
            batch_size=25,
            max_ocr_pages_per_attempt=100,
            ocr_language="eng",
        ),
        ocr_runner=runner,
        store=NullPageStore(),
    )


async def test_cid_dominated_page_is_sent_to_ocr(tmp_path: Path) -> None:
    source = build_composite_font_pdf(tmp_path / "cid.pdf", composite_lines=_BODY)
    runner = _Runner()

    result = await _engine(runner).extract(source)
    page = result.pages[0]

    assert runner.requested == [[1]]
    assert page.source is PageSource.OCR
    assert page.status is PageStatus.OCR_COMPLETED
    assert page.text == "OCR TEXT PAGE 1"
    assert result.completeness is Completeness.COMPLETE


async def test_cid_dominated_page_with_ocr_disabled_is_partial_not_complete(
    tmp_path: Path,
) -> None:
    source = build_composite_font_pdf(tmp_path / "cid.pdf", composite_lines=_BODY)

    result = await _engine(_Runner(), ocr_enabled=False).extract(source)
    page = result.pages[0]

    assert page.status is PageStatus.OCR_DISABLED
    assert result.completeness is Completeness.PARTIAL
    assert page.error is not None and "(cid:N)" in page.error
    # Fail-visible, not laundered: what the text layer actually held is kept
    # as evidence rather than stripped into a page that looks merely short.
    assert "(cid:" in page.text


async def test_structural_class_stays_textual_while_quality_forces_ocr(
    tmp_path: Path,
) -> None:
    """The classifier describes structure; native-text quality routes.

    The page genuinely carries a text layer - its glyphs are there, pdfminer
    just cannot name them - so it is classified as text. That classification
    must not keep it away from OCR.
    """
    source = build_composite_font_pdf(tmp_path / "cid.pdf", composite_lines=_BODY)
    runner = _Runner()

    result = await _engine(runner).extract(source)

    assert result.pages[0].classification.page_class is PageClass.TEXT_NATIVE
    assert runner.requested == [[1]]


async def test_readable_text_with_one_unmapped_glyph_keeps_its_text_layer(
    tmp_path: Path,
) -> None:
    source = build_composite_font_pdf(
        tmp_path / "mostly.pdf", text_lines=_READABLE, composite_lines=["*"]
    )
    runner = _Runner()

    result = await _engine(runner).extract(source)
    page = result.pages[0]

    assert runner.requested == []
    assert page.status is PageStatus.TEXT_LAYER
    assert page.source is PageSource.TEXT_LAYER
    assert "Nayaganj Station" in page.text
    assert "(cid:42)" in page.text
    assert result.completeness is Completeness.COMPLETE


async def test_readable_header_over_an_unmapped_body_is_sent_to_ocr(
    tmp_path: Path,
) -> None:
    source = build_composite_font_pdf(
        tmp_path / "header.pdf", text_lines=["Letter No. CC/UPMRC/0412"], composite_lines=_BODY
    )
    runner = _Runner()

    await _engine(runner).extract(source)

    assert runner.requested == [[1]]


async def test_readable_letterhead_does_not_hide_an_unreadable_body(
    tmp_path: Path,
) -> None:
    """Fewer placeholders than readable characters, but the body is all lost.

    A page-wide ratio alone passed this page as COMPLETE TEXT_LAYER (found in
    review: 128 placeholders against 168 readable characters).
    """
    source = build_composite_font_pdf(
        tmp_path / "letter.pdf",
        text_lines=[
            "Gulermak-Sam India Kanpur Metro Joint Venture, Kanpur",
            "Kanpur Metro Rail Project, Package KNPCC-05, Nayaganj",
            "Ref: CC/UPMRC/0412 dated 14 March 2025, by hand",
        ],
        composite_lines=["Subject: Notice of claim", "under Sub-Clause 20.1"],
    )
    runner = _Runner()

    result = await _engine(runner).extract(source)

    assert runner.requested == [[1]]
    assert result.pages[0].status is PageStatus.OCR_COMPLETED


async def test_unicode_mapped_composite_font_keeps_its_text_layer(
    tmp_path: Path,
) -> None:
    source = build_composite_font_pdf(
        tmp_path / "mapped.pdf",
        composite_lines=["Amount payable ₹ 9,600,000 to Société Générale", *_BODY],
        to_unicode=True,
    )
    runner = _Runner()

    result = await _engine(runner).extract(source)

    assert runner.requested == []
    assert result.pages[0].status is PageStatus.TEXT_LAYER
    assert "₹ 9,600,000 to Société Générale" in result.pages[0].text


async def test_ocr_output_dominated_by_placeholders_is_not_accepted(
    tmp_path: Path,
) -> None:
    source = build_composite_font_pdf(tmp_path / "cid.pdf", composite_lines=_BODY)
    garbage = "".join(f"(cid:{ord(character)})" for character in _BODY[0])

    result = await _engine(_Runner(text=garbage)).extract(source)
    page = result.pages[0]

    assert page.status is PageStatus.OCR_EMPTY
    assert page.source is not PageSource.OCR
    assert page.error is not None and "(cid:N)" in page.error
    assert result.completeness is Completeness.PARTIAL


async def test_encoded_fixture_routing_is_unchanged(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    runner = _Runner()

    result = await _engine(runner).extract(source)

    assert runner.requested == [[1, 2]]
    assert [page.status for page in result.pages[2:]] == [PageStatus.TEXT_LAYER] * 7
    assert result.completeness is Completeness.COMPLETE


_PATCHED_CHILD = r"""
import asyncio, json, sys
from pathlib import Path

import ocrmypdf  # noqa: F401 - installs OCRmyPDF's pdfminer patch first

from rbac_backend.services.extraction.engine import PageExtractionEngine
from rbac_backend.services.extraction.models import PageExtractionPolicy
from rbac_backend.services.extraction.page_store import NullPageStore
from rbac_backend.tests.fixtures.pdf_builders import build_unencoded_font_pdf

source = build_unencoded_font_pdf(
    Path(sys.argv[1]) / "unencoded.pdf",
    lines=[
        "The Contractor hereby gives notice of a claim for additional payment",
        "arising from the suspension of the works instructed by the Engineer",
    ],
)
engine = PageExtractionEngine(
    policy=PageExtractionPolicy(
        ocr_enabled=False, min_text_chars_per_page=40, batch_size=25,
        max_ocr_pages_per_attempt=100, ocr_language="eng",
    ),
    ocr_runner=None,
    store=NullPageStore(),
)
result = asyncio.run(engine.extract(source))
page = result.pages[0]
print(json.dumps({
    "status": page.status.value,
    "completeness": result.completeness.value,
    "has_placeholders": "(cid:" in page.text,
}))
"""


def test_ocrmypdf_imported_first_cannot_make_placeholders_complete(
    tmp_path: Path,
) -> None:
    """The production shape: OCRmyPDF already imported, an unencoded font.

    Runs in a fresh interpreter so the patch is really installed and cannot
    leak into the rest of the suite.
    """
    state = run_python_child(_PATCHED_CHILD, str(tmp_path), cwd=tmp_path)

    assert state == {
        "status": "ocr_disabled",
        "completeness": "partial",
        "has_placeholders": True,
    }
