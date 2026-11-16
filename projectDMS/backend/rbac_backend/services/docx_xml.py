"""WordprocessingML text and tables that are always well-formed (plan 4B-29, C-16).

The claim-submission writer and the arbitration exporter's fallback writer both
build ``word/document.xml`` by hand. Both escaped ``&``, ``<`` and ``>`` but
passed through characters XML 1.0 forbids (a title with ``\\x0b`` from a pasted
spreadsheet cell), which makes a package Word will not open. The submission
writer's table had no ``w:tblGrid``, which Word requires; the arbitration
fallback writer flattened tables into tab-joined lines.

This module is the one place both writers get text and tables from.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, List, Sequence
from xml.sax.saxutils import escape

#: Characters XML 1.0 does not allow anywhere in a document: C0 controls other than
#: tab, newline and carriage return; lone surrogates; U+FFFE and U+FFFF.
_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")

#: Text width of an A4 page with one-inch margins, in twentieths of a point.
A4_TEXT_WIDTH_TWIPS = 11906 - 2 * 1440


def clean_text(value: Any) -> str:
    """The value as text with every XML-forbidden character removed (not escaped)."""
    return _XML_INVALID.sub("", "" if value is None else str(value))


def xml_text(value: Any) -> str:
    """Text safe inside ``<w:t>`` or an attribute: forbidden characters removed, markup escaped."""
    return escape(clean_text(value), {'"': "&quot;"})


def w_paragraph(text: Any, *, size: int | None = None, bold: bool = False, style: str | None = None,
                space_before: int | None = None) -> str:
    properties = ""
    if style or space_before is not None:
        inner = f'<w:pStyle w:val="{xml_text(style)}"/>' if style else ""
        if space_before is not None:
            inner += f'<w:spacing w:before="{int(space_before)}" w:after="120"/>'
        properties = f"<w:pPr>{inner}</w:pPr>"
    run_props = ""
    if bold or size is not None:
        bold_xml = "<w:b/>" if bold else ""
        size_xml = f'<w:sz w:val="{int(size)}"/>' if size is not None else ""
        run_props = f"<w:rPr>{bold_xml}{size_xml}</w:rPr>"
    return f'<w:p>{properties}<w:r>{run_props}<w:t xml:space="preserve">{xml_text(text)}</w:t></w:r></w:p>'


def w_table(rows: Sequence[Sequence[Any]], *, header: bool = True, size: int | None = 18,
            width_twips: int = A4_TEXT_WIDTH_TWIPS) -> str:
    """A bordered table with a ``w:tblGrid`` and the same number of cells in every row."""
    columns = max((len(row) for row in rows), default=0)
    if columns == 0:
        return ""
    column_width = max(1, width_twips // columns)
    border = "".join(
        f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
        for side in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    grid = "".join(f'<w:gridCol w:w="{column_width}"/>' for _ in range(columns))
    body: List[str] = []
    for index, row in enumerate(rows):
        padded = list(row) + [""] * (columns - len(row))
        cells = "".join(
            f'<w:tc><w:tcPr><w:tcW w:w="{column_width}" w:type="dxa"/></w:tcPr>'
            f"{w_paragraph(cell, size=size, bold=header and index == 0)}</w:tc>"
            for cell in padded
        )
        body.append(f"<w:tr>{cells}</w:tr>")
    return (
        f'<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>{border}</w:tblBorders></w:tblPr>'
        f"<w:tblGrid>{grid}</w:tblGrid>{''.join(body)}</w:tbl>"
    )


def clean_rows(rows: Iterable[Sequence[Any]]) -> List[List[str]]:
    """Rows as text with forbidden characters removed: for writers that build XML themselves (python-docx)."""
    return [[clean_text(cell) for cell in row] for row in rows]


__all__ = ["A4_TEXT_WIDTH_TWIPS", "clean_rows", "clean_text", "w_paragraph", "w_table", "xml_text"]
