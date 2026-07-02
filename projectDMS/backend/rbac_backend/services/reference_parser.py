"""Reference parsing helpers shared by document metadata and reference views."""

from __future__ import annotations

import re
from typing import Dict, Optional

from ..utils.date_parser import format_date_ddmmyyyy


LETTER_REFERENCE_PATTERN = re.compile(
    r"(?i)(?:LOA\s*no\.?|letter\s*no\.?)\s*([A-Z0-9\/\-.]+)\s*"
    r"(?:dated|dtd\.?|dt\.?)\s*(\d{2}[.\/-]\d{2}[.\/-]\d{4})"
)


def parse_legacy_reference_text(value: str) -> Optional[Dict[str, str]]:
    """Parse legacy free-text references into structured letter/date fields."""

    raw = (value or "").strip()
    if not raw:
        return None

    match = LETTER_REFERENCE_PATTERN.search(raw)
    if not match:
        return None

    letter_no = match.group(1).strip().rstrip(".,;")
    raw_date = match.group(2).strip()
    formatted_date = format_date_ddmmyyyy(raw_date) or raw_date

    return {
        "letterNo": letter_no,
        "letter_no": letter_no,
        "date": formatted_date,
        "raw": raw,
    }
