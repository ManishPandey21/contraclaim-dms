"""Keep amounts whole through extraction (audit R2).

Two readers, two mechanisms, one module:

* **Native text - phantom space glyphs.** Excel right-aligns a number by
  drawing padding spaces after the digits in the content stream but positioned
  to their left; the last one lands inside the first digit's box. pdfplumber
  orders glyphs by x, so it reads ``19,292,171`` as ``1 9,292,171``. A space
  whose box lies mostly inside an inked glyph on the same line cannot be a word
  boundary - a real space sits in a gap - so it is dropped before the text is
  read. Geometry decides, not the digits: the rule needs no knowledge of
  numbers and cannot merge two values that are genuinely apart.
* **OCR - a grouped number split at a word gap.** Tesseract can break a word
  inside ``3,40,11,265`` and emit ``3,40, 11,265``. The two pieces are joined
  only when the left piece is not a valid number on its own and the joined
  literal is a valid Western or Indian grouping - the pairing that proves a
  fragment. A list such as ``1,200, 3,400`` fails it and is left alone.

Every pdfplumber page read in the extraction readers goes through
`readable_page`; `test_numeric_integrity.py` walks their AST to keep it so.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

#: Share of a space glyph's width that must lie inside one inked glyph for the
#: space to be phantom. Measured overlaps are 100%; real spaces measure ~0%.
PHANTOM_OVERLAP = 0.5
#: Share of the shorter glyph's height two glyphs must share to be one line.
SAME_LINE_OVERLAP = 0.5

_CACHE_ATTR = "_cc_phantom_space_keys"


def _key(obj: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        round(float(obj.get("x0", 0.0)), 3),
        round(float(obj.get("x1", 0.0)), 3),
        round(float(obj.get("top", 0.0)), 3),
        obj.get("text"),
        obj.get("fontname"),
    )


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _phantom_space_keys(page: Any) -> frozenset:
    cached = getattr(page, _CACHE_ATTR, None)
    if cached is not None:
        return cached

    try:
        chars = list(page.chars or [])
    except Exception:
        chars = []

    inked: Dict[int, List[Dict[str, Any]]] = {}
    for char in chars:
        if (char.get("text") or "").strip():
            inked.setdefault(int(float(char.get("top", 0.0))), []).append(char)

    phantoms = set()
    for space in chars:
        text = space.get("text")
        if text is None or text.strip() or text == "":
            continue
        x0, x1 = float(space.get("x0", 0.0)), float(space.get("x1", 0.0))
        top, bottom = float(space.get("top", 0.0)), float(space.get("bottom", 0.0))
        width = x1 - x0
        if width <= 0:
            continue
        bucket = int(top)
        for candidate in inked.get(bucket - 1, []) + inked.get(bucket, []) + inked.get(bucket + 1, []):
            c_top, c_bottom = float(candidate.get("top", 0.0)), float(candidate.get("bottom", 0.0))
            height = min(bottom - top, c_bottom - c_top)
            if height <= 0 or _overlap(top, bottom, c_top, c_bottom) < SAME_LINE_OVERLAP * height:
                continue
            covered = _overlap(x0, x1, float(candidate.get("x0", 0.0)), float(candidate.get("x1", 0.0)))
            if covered >= PHANTOM_OVERLAP * width:
                phantoms.add(_key(space))
                break

    result = frozenset(phantoms)
    try:
        setattr(page, _CACHE_ATTR, result)
    except Exception:
        pass
    return result


def phantom_space_count(page: Any) -> int:
    """How many phantom space glyphs `page` carries."""
    return len(_phantom_space_keys(page))


def readable_page(page: Any, *, keep_phantoms: bool = False) -> Any:
    """`page` without phantom space glyphs, for every text or table read.

    Returns `page` itself when it has none, so a clean page reads exactly as
    before. `keep_phantoms=True` returns the unaided page on purpose - for
    recording what a naive read produced as evidence, never for publishing.
    """
    if keep_phantoms:
        return page
    phantoms = _phantom_space_keys(page)
    if not phantoms:
        return page
    return page.filter(
        lambda obj: obj.get("object_type") != "char" or _key(obj) not in phantoms
    )


# --- OCR: grouped numbers split at a word gap -------------------------------------

_WESTERN = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_INDIAN = re.compile(r"^\d{1,2}(?:,\d{2})*,\d{3}$")
#: left fragment (with at least one internal group), its trailing comma, ONE
#: space, then the remainder. Anchored so neither side is part of a longer
#: number, a date, or a clause reference.
_SPLIT = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:,\d{2,3})+),[ ](\d{2,3}(?:,\d{2,3})*)(?![\d]|,\d)"
)


def is_valid_grouping(literal: str) -> bool:
    """True for a thousands-grouped integer in Western or Indian style."""
    return bool(_WESTERN.match(literal) or _INDIAN.match(literal))


def repair_split_grouped_numbers(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Join OCR fragments of one grouped number; return text and provenance."""
    if not text or ", " not in text:
        return text, []

    repairs: List[Dict[str, Any]] = []

    def _join(match: "re.Match[str]") -> str:
        left, right = match.group(1), match.group(2)
        joined = f"{left},{right}"
        if is_valid_grouping(left) or not is_valid_grouping(joined):
            return match.group(0)
        repairs.append(
            {
                "method": "ocr_split_grouped_number",
                "before": match.group(0),
                "after": joined,
                "applied": True,
                "scope": "text",
                "reason": (
                    "left fragment is not a valid number on its own and the "
                    "joined literal is a valid grouping"
                ),
            }
        )
        return joined

    repaired = _SPLIT.sub(_join, text)
    return repaired, repairs


def phantom_space_repair_record(count: int) -> Dict[str, Any]:
    return {
        "method": "phantom_space_glyph",
        "count": count,
        "applied": True,
        "scope": "text+tables",
        "reason": (
            "space glyph(s) drawn inside an inked glyph's box were dropped "
            "before reading; the unaided reading is kept as raw_text"
        ),
    }
