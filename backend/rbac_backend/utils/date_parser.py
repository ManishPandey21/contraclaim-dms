from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union


# Common date formats we expect from CSV/UI
DATE_FORMATS = [
    "%Y-%m-%d",              # 2024-01-15
    "%d-%m-%Y",              # 15-01-2024
    "%d/%m/%Y",              # 15/01/2024
    "%m/%d/%Y",              # 01/15/2024
    "%Y/%m/%d",              # 2024/01/15
    "%d-%b-%Y",              # 15-Jan-2024
    "%d %b %Y",              # 15 Jan 2024
    "%b %d, %Y",             # Jan 15, 2024
    "%dth %b %Y",            # 15th Jan 2024
    "%dst %b %Y",            # 1st Jan 2024
    "%dnd %b %Y",            # 2nd Jan 2024
    "%drd %b %Y",            # 3rd Jan 2024
    "%dth %B %Y",            # 15th January 2024
    "%dst %B %Y",            # 1st January 2024
    "%dnd %B %Y",            # 2nd January 2024
    "%drd %B %Y",            # 3rd January 2024
    "%dth %b, %Y",           # 15th Jan, 2024
    "%dst %b, %Y",           # 1st Jan, 2024
    "%dnd %b, %Y",           # 2nd Jan, 2024
    "%drd %b, %Y",           # 3rd Jan, 2024
    "%B %dth, %Y",           # January 15th, 2024
    "%B %dst, %Y",           # January 1st, 2024
    "%B %dnd, %Y",           # January 2nd, 2024
    "%B %drd, %Y",           # January 3rd, 2024
    "%b %dth, %Y",           # Jan 15th, 2024
    "%b %dst, %Y",           # Jan 1st, 2024
    "%b %dnd, %Y",           # Jan 2nd, 2024
    "%b %drd, %Y",           # Jan 3rd, 2024
    "%Y-%m-%dT%H:%M:%S",     # 2024-01-15T13:45:00
    "%Y-%m-%d %H:%M:%S",     # 2024-01-15 13:45:00
    "%Y-%m-%dT%H:%M:%S.%f",  # 2024-01-15T13:45:00.123456
    "%Y-%m-%d %H:%M:%S.%f",  # 2024-01-15 13:45:00.123456
    "%d.%m.%Y",              # 15.01.2024 (common in your documents)
    "%dth %b. %Y",           # 15th Jan. 2024
    "%dst %b. %Y",           # 1st Jan. 2024
    "%dnd %b. %Y",           # 2nd Jan. 2024
    "%drd %b. %Y",           # 3rd Jan. 2024
]


def parse_date_safely(value: Union[str, datetime, None]) -> datetime:
    """
    Parse a date string into a timezone-aware UTC datetime.
    - If a datetime is provided, normalize to UTC.
    - If a string is provided, try multiple common formats.
    - If parsing fails or value is falsy, raise ValueError.

    Returns:
        datetime with tzinfo=UTC
    """
    if value is None or value == "":
        raise ValueError("Date value is required")

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    s = str(value).strip()
    if not s:
        raise ValueError("Date value is required")

    # Try ISO 8601 first
    try:
        # fromisoformat handles many ISO variants but not 'Z'
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass

    # Try common explicit formats
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            # Assume naive parsed values are UTC
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue

    # Last resort: try parsing only date part
    try:
        # Accept YYYY-MM-DD like substrings
        part = s.split()[0]
        dt = datetime.strptime(part, "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass

    raise ValueError(f"Unsupported date format: {value!r}")


def format_date_ddmmyyyy(value: Union[str, datetime, None]) -> Optional[str]:
    """
    Convert the supplied date to dd-mm-yyyy string.
    Returns None when value is falsy.
    Falls back to trimmed original string if parsing fails.
    """
    if value in (None, ""):
        return None

    try:
        dt = parse_date_safely(value)
    except Exception:
        s = str(value).strip()
        return s or None

    return dt.astimezone(timezone.utc).strftime("%d-%m-%Y")
