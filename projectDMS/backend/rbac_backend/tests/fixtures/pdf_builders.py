"""Builders for deterministic PDF fixtures.

Uses pikepdf (already a dependency) rather than reportlab. Every builder is
byte-reproducible: pikepdf is given a fixed deterministic ID and no timestamps
are written, so two builds of the same fixture compare equal.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf

A4_PORTRAIT = (0, 0, 595, 842)
A4_LANDSCAPE = (0, 0, 842, 595)


def _text_stream(lines: list[str]) -> bytes:
    """Build a minimal PDF content stream drawing lines at 12pt Helvetica."""
    parts = [b"BT", b"/F1 12 Tf", b"72 770 Td", b"14 TL"]
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        parts.append(f"({escaped}) Tj".encode("latin-1", errors="replace"))
        parts.append(b"T*")
    parts.append(b"ET")
    return b"\n".join(parts)


def _add_page(
    pdf: pikepdf.Pdf,
    *,
    media_box: tuple[int, int, int, int],
    lines: list[str] | None,
) -> None:
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name.Helvetica,
        )
    )
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
