"""Detect and structure Bill of Quantities (BOQ) tables from cleaned page text.

Unlike caption-based table detection, a BOQ table is recognised by its columnar
structure — an Item / Description / Unit / Quantity / Rate / Amount layout — and
parsed into structured rows so it is stored as data, not a blind blob (req 15).
Detection is heuristic (plain OCR text loses column geometry); rows must match
the unit + three-number shape to count, keeping false positives low.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Column-header keywords: a BOQ header line carries several of these.
_HEADER_KEYWORDS = [
    r"description", r"particulars", r"\bitem\b", r"\bunit\b", r"\bqty\b",
    r"quantity", r"\brate\b", r"\bamount\b", r"sl\.?\s*no", r"sr\.?\s*no",
    r"s\.?\s*no",
]
_HEADER_RE = re.compile("|".join(_HEADER_KEYWORDS), re.IGNORECASE)

# Recognised units of measure (dots optional).
_UNITS = [
    "sqm", "sq.m", "cum", "cu.m", "rmt", "rm", "nos", "no", "mt", "kg", "ton",
    "tonne", "ls", "lot", "each", "ea", "ltr", "pair", "set", "job", "day",
    "month", "km", "mm", "cm", "m3", "m2", "m", "%",
]
# Longest-first so "m2"/"m3" win over "m".
_UNIT_ALT = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
_NUM = r"\d[\d,]*(?:\.\d+)?"

# item?  description  unit  qty  rate  amount
_ROW_RE = re.compile(
    r"^\s*(?:(?P<item>\d+(?:\.\d+)*|[A-Za-z](?:\.\d+)*)\s+)?"
    r"(?P<desc>.+?)\s+"
    r"(?P<unit>" + _UNIT_ALT + r")\.?\s+"
    r"(?P<qty>" + _NUM + r")\s+"
    r"(?P<rate>" + _NUM + r")\s+"
    r"(?P<amount>" + _NUM + r")\s*$",
    re.IGNORECASE,
)


@dataclass
class BOQRow:
    item_no: Optional[str]
    description: str
    unit: str
    quantity: Optional[float]
    rate: Optional[float]
    amount: Optional[float]
    raw_line: str


@dataclass
class BOQTable:
    page_no: int
    header: Optional[str]
    columns: List[str]
    rows: List[BOQRow] = field(default_factory=list)
    text: str = ""


def _to_number(token: str) -> Optional[float]:
    try:
        return float(token.replace(",", ""))
    except (TypeError, ValueError):
        return None


def is_boq_header(line: str) -> bool:
    """A header carries at least two BOQ column keywords."""
    hits = {m.group(0).lower().replace(".", "").replace(" ", "") for m in _HEADER_RE.finditer(line or "")}
    return len(hits) >= 2


def parse_boq_row(line: str) -> Optional[BOQRow]:
    """Parse one BOQ data line (item? desc unit qty rate amount) or None."""
    match = _ROW_RE.match(line or "")
    if not match:
        return None
    desc = (match.group("desc") or "").strip()
    if not desc:
        return None
    return BOQRow(
        item_no=(match.group("item") or None),
        description=desc,
        unit=match.group("unit"),
        quantity=_to_number(match.group("qty")),
        rate=_to_number(match.group("rate")),
        amount=_to_number(match.group("amount")),
        raw_line=line.strip(),
    )


def _columns_from_header(header: str) -> List[str]:
    cols = [m.group(0).strip() for m in _HEADER_RE.finditer(header or "")]
    # de-dupe preserving order
    seen, out = set(), []
    for c in cols:
        key = c.lower()
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def detect_boq_tables(
    pages: List[Dict], *, min_rows: int = 2, max_gap: int = 2
) -> List[BOQTable]:
    """Find BOQ tables across pages.

    A table is a header line followed by BOQ rows, or (header-less, e.g. a table
    continuing across pages) a run of at least ``min_rows`` consecutive rows.
    """
    tables: List[BOQTable] = []
    for page in pages:
        page_no = int(page.get("page_no") or page.get("page_number") or 0)
        text = page.get("cleaned_text") or page.get("text") or ""
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            header = lines[i] if is_boq_header(lines[i]) else None
            start = i + 1 if header else i
            rows: List[BOQRow] = []
            j = start
            gap = 0
            while j < len(lines) and gap <= max_gap:
                row = parse_boq_row(lines[j])
                if row:
                    rows.append(row)
                    gap = 0
                elif not lines[j].strip():
                    pass
                else:
                    if not rows:
                        break  # header-less: require rows to start immediately
                    gap += 1
                j += 1
            if len(rows) >= min_rows:
                block = "\n".join(lines[i:j]).strip()
                tables.append(
                    BOQTable(
                        page_no=page_no,
                        header=header,
                        columns=_columns_from_header(header) if header else [],
                        rows=rows,
                        text=block,
                    )
                )
                i = j
            else:
                i += 1
    return tables


__all__ = ["BOQRow", "BOQTable", "detect_boq_tables", "parse_boq_row", "is_boq_header"]
