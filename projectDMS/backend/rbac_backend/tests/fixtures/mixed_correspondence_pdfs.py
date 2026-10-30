"""Synthetic stand-ins for the two 2026-10-07 extraction-audit documents.

The audit measured two real customer PDFs that production (`legacy_v0`) reads
almost entirely wrong. Customer correspondence may not be committed (the
repository ignores ``*.pdf`` and tracks none), so these builders reproduce the
*shape* that triggers the failure, with invented text:

* ``build_glm_like_pdf`` - two scanned letter pages (full-page rasters, no
  text layer) followed by three photo pages with native captions. The audit
  document kept 150 of 3,555 characters under ``legacy_v0``: one caption page
  made the document "textual", so neither letter page was OCR'd.
* ``build_claim_like_pdf`` - one scanned covering letter, then native text and
  table pages. The audit document lost its whole first page, including the
  claimed amount.
* ``build_hybrid_page_pdf`` - one page with a real text layer *and* a large
  raster inset carrying text of its own.

The scanned pages are real rasters with the letter text drawn into them, so
the env-gated live-OCR acceptance test can read them with tesseract. Tests that
use a stub OCR runner never depend on the raster bytes: they are deterministic
for a given Pillow build, not across Pillow/FreeType versions.

Every expected string is declared here beside the builder, so tests assert
against declared values rather than re-deriving them.
"""

from __future__ import annotations

import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pikepdf

from rbac_backend.tests.fixtures.pdf_builders import A4_PORTRAIT, _helvetica, _save, _show_lines

#: Raster resolution for drawn pages. 150 DPI is the pipeline's own
#: rasterisation default and is enough for tesseract on 14pt-equivalent text.
_DPI = 150
_PT_TO_PX = _DPI / 72.0

GLM_LETTER_PAGE_1: List[str] = [
    "SYNTHETIC JOINT VENTURE - METRO CORRIDOR PROJECT",
    "Ref: CC-05/EMP/OL/2022/0001",
    "Date: 14 March 2022",
    "To: The Engineer, Synthetic Metro Rail Corporation",
    "Subject: Notice of hindrance at Station Alpha access road",
    "Dear Sir,",
    "We refer to the site instruction dated 2 March 2022 and record",
    "that access to the working area remains obstructed by utility",
    "diversion works which are outside the control of the Contractor.",
]
GLM_LETTER_PAGE_2: List[str] = [
    "The hindrance has delayed the piling activity by twenty one days.",
    "We reserve our right to an extension of time under Clause 8.4",
    "and to the associated costs, which will be notified separately.",
    "Photographs of the obstruction are enclosed as Annexure A.",
    "Yours faithfully,",
    "Project Director",
]
#: Native captions. Pages 3 and 4 clear the 40-character threshold and stay
#: native; page 5's caption is thin, so the engine OCRs that page - the shape
#: the audit measured (92, 47 and 9 native characters).
GLM_CAPTIONS: Dict[int, List[str]] = {
    3: [
        "Annexure A - Photograph 1: utility trench blocking the access road",
        "at Station Alpha, taken on 10 March 2022",
    ],
    4: ["Photograph 2: diversion works at chainage 4+250"],
    5: ["Photo 3"],
}
#: Strings whose absence is the R1 defect: the reference, the subject, and the
#: operative sentence of the letter. All live only on the scanned pages.
GLM_CRITICAL_PHRASES: Tuple[str, ...] = (
    "CC-05/EMP/OL/2022/0001",
    "Notice of hindrance at Station Alpha access road",
    "extension of time under Clause 8.4",
)
GLM_SCANNED_PAGES: Tuple[int, ...] = (1, 2)
GLM_NATIVE_PAGES: Tuple[int, ...] = (3, 4)
#: Page 5 is a photo with a nine-character caption: thin, so it is OCR'd.
GLM_THIN_PHOTO_PAGE = 5

CLAIM_LETTER_PAGE_1: List[str] = [
    "SYNTHETIC JOINT VENTURE - METRO CORRIDOR PROJECT",
    "Ref: CC-05/EMP/CL/2023/0042",
    "Date: 3 July 2023",
    "To: The Engineer, Synthetic Metro Rail Corporation",
    "Subject: Interim claim for idling of plant and machinery",
    "Dear Sir,",
    "Please find enclosed our interim claim for the idling of the",
    "piling rigs during the hindrance period recorded in our letter",
    "CC-05/EMP/OL/2022/0001. The amount claimed is Rs 3,40,11,265.",
    "Yours faithfully, Project Director",
]
CLAIM_NATIVE_PAGES_TEXT: Dict[int, List[str]] = {
    2: [
        "Annexure 1 - Basis of claim",
        "The idling period is computed from the joint records signed by the",
        "Engineer's representative and the daily progress reports.",
    ],
    3: [
        "Annexure 2 - Cost of idle plant",
        "S/N  Description               Days  Rate      Amount",
        "1    Hydraulic piling rig       52   184,615   9,600,000",
        "2    Crawler crane 80 t         52   110,000   5,720,000",
    ],
    4: [
        "Annexure 3 - Overheads and summary",
        "Site overheads for the hindrance period are claimed at actual cost",
        "as recorded in the audited monthly accounts of the joint venture.",
    ],
}
CLAIM_CRITICAL_PHRASES: Tuple[str, ...] = (
    "CC-05/EMP/CL/2023/0042",
    "Interim claim for idling of plant and machinery",
    "Rs 3,40,11,265",
)
CLAIM_SCANNED_PAGES: Tuple[int, ...] = (1,)
CLAIM_NATIVE_PAGES: Tuple[int, ...] = (2, 3, 4)

HYBRID_NATIVE_LINES: List[str] = [
    "Site record sheet - native header carried by the PDF text layer",
    "Prepared by the Contractor's planning department for the record",
]
HYBRID_INSET_LINES: List[str] = [
    "SCANNED INSET",
    "Joint measurement record",
    "signed on site 9 May 2023",
]
HYBRID_INSET_PHRASE = "Joint measurement record"


def _render_lines(
    size_px: Tuple[int, int], lines: List[str], *, font_px: int = 30
) -> bytes:
    """Grayscale raster with `lines` drawn black on white, as raw 8-bit bytes."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("L", size_px, color=255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=font_px)
    y = int(font_px * 2)
    for line in lines:
        draw.text((int(font_px * 2), y), line, fill=0, font=font)
        y += int(font_px * 1.6)
    return image.tobytes()


def _render_photo(size_px: Tuple[int, int]) -> bytes:
    """A deterministic 'photograph': concentric shapes, no text."""
    from PIL import Image, ImageDraw

    width, height = size_px
    image = Image.new("L", size_px, color=170)
    draw = ImageDraw.Draw(image)
    for step in range(0, min(width, height) // 2, 12):
        shade = 60 + (step * 3) % 150
        draw.ellipse((step, step, width - step, height - step), outline=shade, width=6)
    return image.tobytes()


def _image_xobject(pdf: pikepdf.Pdf, raw: bytes, size_px: Tuple[int, int]) -> pikepdf.Object:
    width, height = size_px
    return pdf.make_stream(
        zlib.compress(raw, 9),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=width,
        Height=height,
        ColorSpace=pikepdf.Name.DeviceGray,
        BitsPerComponent=8,
        Filter=pikepdf.Name.FlateDecode,
    )


def _add_composed_page(
    pdf: pikepdf.Pdf,
    *,
    image: Optional[Tuple[bytes, Tuple[int, int]]] = None,
    image_box_pt: Optional[Tuple[float, float, float, float]] = None,
    lines: Optional[List[str]] = None,
    text_origin_pt: Tuple[float, float] = (72, 770),
) -> None:
    """One A4 page with an optional raster at `image_box_pt` and optional text.

    `image_box_pt` is (x, y, width, height) in PDF points, origin bottom-left.
    """
    resources = pikepdf.Dictionary()
    parts: List[bytes] = []
    if image is not None and image_box_pt is not None:
        raw, size_px = image
        resources.XObject = pikepdf.Dictionary(Im1=_image_xobject(pdf, raw, size_px))
        x, y, w, h = image_box_pt
        parts.append(f"q {w} 0 0 {h} {x} {y} cm /Im1 Do Q".encode("ascii"))
    if lines:
        resources.Font = pikepdf.Dictionary(F1=_helvetica(pdf))
        tx, ty = text_origin_pt
        parts += [b"BT", b"/F1 12 Tf", f"{tx} {ty} Td".encode("ascii"), b"14 TL"]
        parts += _show_lines(lines)
        parts.append(b"ET")
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=list(A4_PORTRAIT),
        Resources=resources,
        Contents=pdf.make_stream(b"\n".join(parts)),
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))


def _px(width_pt: float, height_pt: float) -> Tuple[int, int]:
    return int(round(width_pt * _PT_TO_PX)), int(round(height_pt * _PT_TO_PX))


def _add_scanned_letter_page(pdf: pikepdf.Pdf, lines: List[str]) -> None:
    """A full-page raster with no text layer: the shape of a scanned letter."""
    _, _, page_w, page_h = A4_PORTRAIT
    size = _px(page_w, page_h)
    _add_composed_page(
        pdf,
        image=(_render_lines(size, lines), size),
        image_box_pt=(0, 0, page_w, page_h),
    )


def _add_photo_page(pdf: pikepdf.Pdf, caption: List[str]) -> None:
    """A photograph covering ~22% of the page above a native caption."""
    box = (147.0, 420.0, 300.0, 370.0)
    size = _px(box[2], box[3])
    _add_composed_page(
        pdf,
        image=(_render_photo(size), size),
        image_box_pt=box,
        lines=caption,
        text_origin_pt=(72, 380),
    )


def build_glm_like_pdf(path: Path) -> Path:
    """Two scanned letter pages, then three photo pages with native captions."""
    pdf = pikepdf.new()
    _add_scanned_letter_page(pdf, GLM_LETTER_PAGE_1)
    _add_scanned_letter_page(pdf, GLM_LETTER_PAGE_2)
    for number in (3, 4, 5):
        _add_photo_page(pdf, GLM_CAPTIONS[number])
    return _save(pdf, path)


def build_claim_like_pdf(path: Path) -> Path:
    """A scanned covering letter, then native narrative and table pages."""
    pdf = pikepdf.new()
    _add_scanned_letter_page(pdf, CLAIM_LETTER_PAGE_1)
    for number in CLAIM_NATIVE_PAGES:
        _add_composed_page(pdf, lines=CLAIM_NATIVE_PAGES_TEXT[number])
    return _save(pdf, path)


def build_hybrid_page_pdf(path: Path) -> Path:
    """One page: a native header above a large raster inset with its own text.

    The inset covers ~45% of the page - more than the classifier's
    mixed-content threshold, less than its scan threshold - and the header
    alone clears the 40-character native threshold.
    """
    pdf = pikepdf.new()
    box = (60.0, 160.0, 475.0, 475.0)
    size = _px(box[2], box[3])
    _add_composed_page(
        pdf,
        image=(_render_lines(size, HYBRID_INSET_LINES, font_px=40), size),
        image_box_pt=box,
        lines=HYBRID_NATIVE_LINES,
    )
    return _save(pdf, path)


def page_text_map(*pages: Dict[int, List[str]]) -> Dict[int, str]:
    """Merge page -> lines maps into page -> text, for stub OCR runners."""
    merged: Dict[int, str] = {}
    for mapping in pages:
        for number, lines in mapping.items():
            merged[number] = "\n".join(lines)
    return merged


#: What a perfect OCR engine would read from each rasterised page.
GLM_OCR_TRUTH: Dict[int, str] = page_text_map(
    {1: GLM_LETTER_PAGE_1, 2: GLM_LETTER_PAGE_2, 5: GLM_CAPTIONS[5]}
)
CLAIM_OCR_TRUTH: Dict[int, str] = page_text_map({1: CLAIM_LETTER_PAGE_1})
