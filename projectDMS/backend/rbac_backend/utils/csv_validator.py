from __future__ import annotations

from typing import List, Dict, Any, Tuple


REQUIRED_COLUMNS = ["filename", "upload_type", "letter_no", "date", "subject"]


async def validate_csv_structure(columns: List[str]) -> Tuple[bool, List[str]]:
    """
    Validate that the CSV has at least the required columns.
    Returns (is_valid, missing_columns).
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    return (len(missing) == 0, missing)


async def parse_csv_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Stub parser that returns the row unchanged.
    Documents router performs detailed validation itself.
    """
    return dict(row or {})
