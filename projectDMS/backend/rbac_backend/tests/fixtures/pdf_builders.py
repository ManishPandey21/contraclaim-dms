"""Builders for deterministic PDF fixtures.

Uses pikepdf (already a dependency) rather than reportlab. Every builder is
byte-reproducible: pikepdf is given a fixed deterministic ID and no timestamps
are written, so two builds of the same fixture compare equal.

Every text font names ``/WinAnsiEncoding``. PDF 32000-1 9.10.2 derives Unicode
for a simple font from a predefined encoding (or a ``/ToUnicode`` CMap); a
Standard-14 font with neither is left to the font's built-in encoding, which is
not a Unicode mapping. pdfminer guesses StandardEncoding for that shape, but
importing OCRmyPDF replaces ``PDFSimpleFont.__init__`` process-wide with one
that refuses to guess, and every glyph then extracts as ``(cid:N)``. The explicit
encoding makes these fixtures read the same in either interpreter;
``test_pdf_fixture_import_order`` pins that. ``build_unencoded_font_pdf``
deliberately keeps the ambiguous shape, because real documents carry it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pikepdf

A4_PORTRAIT = (0, 0, 595, 842)
A4_LANDSCAPE = (0, 0, 842, 595)


def _show_lines(lines: list[str]) -> list[bytes]:
    """Text-showing operators for `lines`, one line per ``T*``."""
    parts: list[bytes] = []
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        parts.append(f"({escaped}) Tj".encode("latin-1", errors="replace"))
        parts.append(b"T*")
    return parts


def _text_stream(lines: list[str]) -> bytes:
    """Build a minimal PDF content stream drawing lines at 12pt Helvetica."""
    parts = [b"BT", b"/F1 12 Tf", b"72 770 Td", b"14 TL", *_show_lines(lines), b"ET"]
    return b"\n".join(parts)


def _helvetica(pdf: pikepdf.Pdf, *, declare_encoding: bool = True) -> pikepdf.Object:
    font_entries: dict[str, Any] = {
        "/Type": pikepdf.Name.Font,
        "/Subtype": pikepdf.Name.Type1,
        "/BaseFont": pikepdf.Name.Helvetica,
    }
    if declare_encoding:
        # WinAnsiEncoding reads every printable ASCII byte as itself. The
        # implicit StandardEncoding pdfminer otherwise guesses does not: it
        # reads ' (0x27) and ` (0x60) as curly quotes. No fixture prints a byte
        # in 0x80-0x9F, the only range where WinAnsi and latin-1 differ.
        font_entries["/Encoding"] = pikepdf.Name.WinAnsiEncoding
    return pdf.make_indirect(pikepdf.Dictionary(font_entries))


def _add_page(
    pdf: pikepdf.Pdf,
    *,
    media_box: tuple[int, int, int, int],
    lines: list[str] | None,
    declare_encoding: bool = True,
) -> None:
    font = _helvetica(pdf, declare_encoding=declare_encoding)
    contents = pdf.make_stream(_text_stream(lines) if lines else b"")
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=list(media_box),
        Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
        Contents=contents,
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))


def _save(pdf: pikepdf.Pdf, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    pdf.save(str(path), deterministic_id=True)
    pdf.close()
    return path


def build_text_pdf(path: Path, *, pages: int = 1, text: str = "Sample text") -> Path:
    """A PDF whose every page carries a real text layer."""
    pdf = pikepdf.new()
    for number in range(1, pages + 1):
        _add_page(
            pdf,
            media_box=A4_PORTRAIT,
            lines=[f"{text}", f"Page {number} of {pages}", "Ref: CC/PLAN/0001"],
        )
    return _save(pdf, path)


def build_unencoded_font_pdf(path: Path, *, lines: list[str]) -> Path:
    """One page set in a Standard-14 font with no /Encoding and no /ToUnicode.

    The ambiguous shape, kept on purpose: some real generators emit it, and in
    an interpreter that has imported OCRmyPDF it extracts as ``(cid:N)``
    placeholders rather than text. Only for tests about that hazard - never use
    it as an ordinary text fixture.
    """
    pdf = pikepdf.new()
    _add_page(pdf, media_box=A4_PORTRAIT, lines=lines, declare_encoding=False)
    return _save(pdf, path)


def build_scanned_only_pdf(path: Path, *, pages: int = 2) -> Path:
    """A PDF with no text layer on any page - stands in for a pure scan."""
    pdf = pikepdf.new()
    for _ in range(pages):
        _add_page(pdf, media_box=A4_PORTRAIT, lines=None)
    return _save(pdf, path)


def build_mixed_pdf(path: Path) -> Path:
    """Nine pages mirroring the measured sample claim.

    Pages 1-2 carry no text layer. Pages 3-9 carry text. Page 5 is landscape.
    This is fixture #1 of the benchmark corpus.
    """
    pdf = pikepdf.new()

    _add_page(pdf, media_box=A4_PORTRAIT, lines=None)
    _add_page(pdf, media_box=A4_PORTRAIT, lines=None)

    for number in range(3, 10):
        media_box = A4_LANDSCAPE if number == 5 else A4_PORTRAIT
        _add_page(
            pdf,
            media_box=media_box,
            lines=[
                f"Claim summary page {number}",
                "Employer: Uttar Pradesh Metro Rail Corporation Ltd",
                "Contractor: Gulermak-Sam India Kanpur Metro JV",
                "S/N  Description            Qty   Rate    Amount",
                "1    Idling of rig machine   52  184615  9600000",
            ],
        )

    return _save(pdf, path)


CLEAN_NATIVE_PAGE_COUNT = 2
#: The table the clean fixture prints, and its exact arithmetic. Kept beside
#: the builder so the acceptance test asserts against declared values rather
#: than re-deriving them.
CLEAN_NATIVE_TABLE = {
    "headers": ["S/N", "Description", "Qty", "Rate", "Amount"],
    "rows": [
        ["1", "Excavation in ordinary soil", "120", "450", "54,000"],
        ["2", "Reinforced concrete in foundation", "80", "6,500", "520,000"],
    ],
    "stated_total": "574,000",
}


def build_clean_native_pdf(path: Path) -> Path:
    """Two pages of coherent native text with one exact, checkable table.

    No page needs OCR and every applicable deterministic check passes, so this
    fixture is the zero-fallback-cost baseline: any model call while processing
    it is a defect.
    """
    pdf = pikepdf.new()

    narrative = [
        "The Contractor hereby submits its monthly interim payment application",
        "in respect of the permanent works executed at the Nayaganj Station",
        "site during the period under review, prepared in accordance with the",
        "measurement provisions of the Contract and supported by the joint",
        "records signed by the Engineer's representative on site.",
    ]
    _add_page(pdf, media_box=A4_PORTRAIT, lines=narrative)

    table = CLEAN_NATIVE_TABLE
    lines = [
        "Summary of measured work for the current valuation period follows",
        "below, with quantities agreed jointly and rates taken from the",
        "priced bill of quantities forming part of the Contract documents.",
        "  ".join(table["headers"]),
    ]
    lines.extend("  ".join(row) for row in table["rows"])
    lines.append(f"        Total        {table['stated_total']}")
    _add_page(pdf, media_box=A4_PORTRAIT, lines=lines)

    return _save(pdf, path)


def build_corrupt_pdf(path: Path) -> Path:
    """A file with a PDF header whose body is unusable.

    Stands in for the truncated or damaged upload that must reach human review
    rather than being reported as an empty but successfully processed document.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"\x00" * 512)
    return path


def build_page_text_map_pdf(
    path: Path, *, page_lines: list[list[str] | None]
) -> Path:
    """A PDF whose per-page text layer is declared page by page.

    ``None`` means a page with no text layer at all (a scan); a list of lines
    means a native text page. Retry tests need an exact, per-page mix of the
    two, which the fixed-shape builders above cannot express.
    """
    pdf = pikepdf.new()
    for lines in page_lines:
        _add_page(pdf, media_box=A4_PORTRAIT, lines=lines)
    return _save(pdf, path)


def _to_unicode_cmap(characters: set[str]) -> bytes:
    """A ToUnicode CMap mapping each 2-byte code (its code point) to itself."""
    codes = sorted({ord(character) for character in characters})
    lines = [
        b"/CIDInit /ProcSet findresource begin",
        b"12 dict begin",
        b"begincmap",
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        b"/CMapName /Adobe-Identity-UCS def",
        b"/CMapType 2 def",
        b"1 begincodespacerange",
        b"<0000> <FFFF>",
        b"endcodespacerange",
    ]
    for start in range(0, len(codes), 100):
        chunk = codes[start : start + 100]
        lines.append(f"{len(chunk)} beginbfchar".encode("ascii"))
        lines.extend(f"<{code:04X}> <{code:04X}>".encode("ascii") for code in chunk)
        lines.append(b"endbfchar")
    lines += [b"endcmap", b"CMapName currentdict /CMap defineresource pop", b"end", b"end"]
    return b"\n".join(lines)


def build_composite_font_pdf(
    path: Path,
    *,
    composite_lines: list[str],
    text_lines: list[str] | None = None,
    to_unicode: bool = False,
) -> Path:
    """One page whose `composite_lines` are set in an Identity-H Type0 font.

    Each character is written as a 2-byte code equal to its code point. With
    ``to_unicode=False`` the font carries no Unicode mapping at all - the
    shape a subsetted CID font takes when its producer omits ``/ToUnicode`` -
    so every glyph extracts as ``(cid:N)`` in any interpreter, with or without
    OCRmyPDF. With ``to_unicode=True`` the same glyphs extract as real text,
    non-Latin characters included. `text_lines`, if given, are drawn first in
    the ordinary WinAnsi Helvetica, so one page can mix readable and
    unreadable text.
    """
    if any(ord(character) > 0xFFFF for character in "".join(composite_lines)):
        raise ValueError("composite_lines must stay within the 2-byte code space")
    pdf = pikepdf.new()
    helvetica = _helvetica(pdf)
    descendant = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.CIDFontType2,
            BaseFont=pikepdf.Name("/FixtureCID"),
            CIDSystemInfo=pikepdf.Dictionary(
                Registry=pikepdf.String("Adobe"),
                Ordering=pikepdf.String("Identity"),
                Supplement=0,
            ),
            # Indirect, as PDF 32000-1 Table 117 requires of a FontDescriptor.
            FontDescriptor=pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.FontDescriptor,
                    FontName=pikepdf.Name("/FixtureCID"),
                    Flags=4,
                    FontBBox=[0, -200, 1000, 900],
                    ItalicAngle=0,
                    Ascent=900,
                    Descent=-200,
                    CapHeight=700,
                    StemV=80,
                )
            ),
            DW=500,
        )
    )
    composite_entries: dict[str, Any] = {
        "/Type": pikepdf.Name.Font,
        "/Subtype": pikepdf.Name.Type0,
        "/BaseFont": pikepdf.Name("/FixtureCID"),
        "/Encoding": pikepdf.Name("/Identity-H"),
        "/DescendantFonts": pikepdf.Array([descendant]),
    }
    if to_unicode:
        composite_entries["/ToUnicode"] = pdf.make_stream(
            _to_unicode_cmap(set("".join(composite_lines)))
        )
    composite = pdf.make_indirect(pikepdf.Dictionary(composite_entries))

    parts = [b"BT", b"72 770 Td", b"14 TL"]
    if text_lines:
        parts.append(b"/F1 12 Tf")
        parts.extend(_show_lines(text_lines))
    parts.append(b"/F2 12 Tf")
    for line in composite_lines:
        hex_codes = "".join(f"{ord(character):04X}" for character in line)
        parts.append(f"<{hex_codes}> Tj".encode("ascii"))
        parts.append(b"T*")
    parts.append(b"ET")

    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=list(A4_PORTRAIT),
        Resources=pikepdf.Dictionary(
            Font=pikepdf.Dictionary(F1=helvetica, F2=composite)
        ),
        Contents=pdf.make_stream(b"\n".join(parts)),
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    return _save(pdf, path)


def build_image_page_pdf(path: Path) -> Path:
    """One page that is nothing but a full-page raster - the shape of a real scan.

    ``build_scanned_only_pdf`` draws no image at all, so the classifier reads
    its pages as BLANK. A test that needs a SCANNED_IMAGE page - one whose
    empty OCR read means content was lost, not that the page was empty - uses
    this instead.
    """
    pdf = pikepdf.new()
    width, height = 8, 8
    image = pdf.make_stream(
        bytes([0x80] * (width * height)),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=width,
        Height=height,
        ColorSpace=pikepdf.Name.DeviceGray,
        BitsPerComponent=8,
    )
    x0, y0, x1, y1 = A4_PORTRAIT
    contents = f"q {x1 - x0} 0 0 {y1 - y0} {x0} {y0} cm /Im1 Do Q".encode("ascii")
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=list(A4_PORTRAIT),
        Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im1=image)),
        Contents=pdf.make_stream(contents),
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    return _save(pdf, path)
