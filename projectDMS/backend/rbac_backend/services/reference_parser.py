"""Reference parsing helpers shared by document metadata and reference views."""

from __future__ import annotations

import re
from typing import Dict, Optional

from ..utils.date_parser import format_date_ddmmyyyy


DATE_MARKER_PATTERN = re.compile(
    r"(?is)(?P<prefix>.*?)\s*(?:[-\u2013\u2014]?\s*\b(?:dated|dtd|dt)\.?(?!\w))\s*[:\-]?\s*"
    r"(?P<date>\d{1,2}[.\/-]\d{1,2}[.\/-]\d{2,4})"
)

REFERENCE_LABEL_PATTERN = re.compile(
    r"(?i)\b(?:LOA|letter|ltr|reference|ref)\s*(?:no|number|#)\.?\s*[:\-]?"
)


def _clean_letter_number(prefix: str) -> str:
    """Remove prose labels while preserving the actual reference code."""

    candidate = (prefix or "").strip()
    matches = list(REFERENCE_LABEL_PATTERN.finditer(candidate))
    if matches:
        candidate = candidate[matches[-1].end() :]

    candidate = re.sub(r"\s+", " ", candidate).strip()
    candidate = candidate.strip(" \t\r\n:;,.")
    candidate = re.sub(r"^[\s:;,.()\[\]#\-\u2013\u2014]+", "", candidate).strip()
    candidate = re.sub(r"[\s:;,.#\-\u2013\u2014]+$", "", candidate).strip()
    return candidate


def parse_legacy_reference_text(value: str) -> Optional[Dict[str, str]]:
    """Parse legacy free-text references into structured letter/date fields."""

    raw = (value or "").strip()
    if not raw:
        return None

    match = DATE_MARKER_PATTERN.search(raw)
    if not match:
        return None

    letter_no = _clean_letter_number(match.group("prefix"))
    if not letter_no:
        return None

    raw_date = match.group("date").strip()
    formatted_date = format_date_ddmmyyyy(raw_date) or raw_date

    return {
        "letterNo": letter_no,
        "letter_no": letter_no,
        "date": formatted_date,
        "raw": raw,
    }
