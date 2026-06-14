from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def now_utc() -> datetime:
    """
    Return a timezone-aware datetime in UTC.
    """
    return datetime.now(timezone.utc)


def to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Normalize a datetime to timezone-aware UTC.

    - If dt is None: return None
    - If dt is naive: assume it is UTC and attach tzinfo=UTC
    - If dt is aware: convert to UTC
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def isoformat_z(dt: datetime) -> str:
    """
    RFC 3339 / ISO 8601 string with 'Z' suffix for UTC.

    - If dt is aware: convert to UTC, format, and replace +00:00 with Z
    - If dt is naive: assume UTC and format with Z
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
