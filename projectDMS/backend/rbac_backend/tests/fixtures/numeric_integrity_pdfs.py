"""A native PDF that reproduces the Excel padding-glyph corruption (audit R2).

Excel right-aligns a number by drawing padding spaces *after* the digits in the
content stream but positioned to their *left*; the last padding space lands
inside the first digit's box. pdfplumber orders glyphs by x, so that space is
read between the first and second digit. Measured on the 2026-10-07 audit
claim (page 5): the space at x 472.69-474.46 lies wholly inside the '1' at
472.20-476.16, producing ``1 9,292,171``.

This builder draws the same geometry with invented values. Controls beside it
must survive unchanged: real word spaces, and a cell that genuinely holds two
numbers (``52 184,615`` - the shape `detect_split_digits` also refuses).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import pikepdf

from rbac_backend.tests.fixtures.pdf_builders import A4_PORTRAIT, _helvetica, _save

#: Helvetica advance widths at 10pt: digit 5.56, space 2.78.
_DIGIT = 5.56
_SPACE = 2.78

#: (correct value, what pdfplumber reads without the fix)
PADDED_VALUES: List[Tuple[str, str]] = [
    ("19,292,171", "1 9,292,171"),
    ("689,012", "6 89,012"),
    ("950,000", "9 50,000"),
]
HEADING = "Annexure 2 - Cost of idle plant and machinery"
#: Real spaces between words and between two numbers in one cell.
CONTROL_LINE = "Hydraulic piling rig idle 52 184,615 per day"


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _padded_cell(y: float, value: str, right_edge: float) -> List[str]:
    """Draw `value` right-aligned at `right_edge`, then Excel's padding.

    The padding spaces are drawn after the digits and walk left from the first
    digit; the last one starts 0.5pt inside the first digit's box.
    """
    width = sum(_SPACE if ch == " " else (2.78 if ch == "," else _DIGIT) for ch in value)
    x_digits = right_edge - width
    ops = [f"BT /F1 10 Tf 1 0 0 1 {x_digits:.2f} {y} Tm ({_escape(value)}) Tj ET"]
    for step in (4, 3, 2, 1):
        ops.append(f"BT /F1 10 Tf 1 0 0 1 {x_digits - step * _SPACE:.2f} {y} Tm ( ) Tj ET")
    ops.append(f"BT /F1 10 Tf 1 0 0 1 {x_digits + 0.5:.2f} {y} Tm ( ) Tj ET")
    return ops


def build_excel_padded_numbers_pdf(path: Path) -> Path:
    pdf = pikepdf.new()
    font = _helvetica(pdf)
    ops = [
        f"BT /F1 12 Tf 1 0 0 1 72 780 Tm ({_escape(HEADING)}) Tj ET",
        f"BT /F1 10 Tf 1 0 0 1 72 740 Tm ({_escape(CONTROL_LINE)}) Tj ET",
    ]
    y = 700.0
    for value, _ in PADDED_VALUES:
        ops += [f"BT /F1 10 Tf 1 0 0 1 72 {y} Tm (Amount) Tj ET"]
        ops += _padded_cell(y, value, right_edge=460.0)
        y -= 20
    page = pikepdf.Dictionary(
        Type=pikepdf.Name.Page,
        MediaBox=list(A4_PORTRAIT),
        Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
        Contents=pdf.make_stream("\n".join(ops).encode("latin-1")),
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    return _save(pdf, path)
