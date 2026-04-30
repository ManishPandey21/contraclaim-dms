"""
Helper utilities shared across LangGraph-inspired workflows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional, Sequence


def pick_non_empty(values: Sequence[Optional[str]]) -> Optional[str]:
    """Return the first non-empty string from the provided collection."""
    for value in values:
        if value and isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return None


def to_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    """Convert datetimes to ISO strings with UTC indicator where possible."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.astimezone().isoformat()


def coerce_bullets(items: Iterable[str]) -> List[str]:
    """Normalise text into de-duplicated bullet lines."""
    output: List[str] = []
    seen: set[str] = set()
    for raw in items:
        cleaned = (raw or "").strip()
        if not cleaned:
            continue
        if cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        output.append(cleaned)
    return output


def summarise_points(subject: Optional[str], keywords: Iterable[str], context: Iterable[str]) -> List[str]:
    """
    Build a small summary list used by the planning node.

    Args:
        subject: Optional subject string.
        keywords: Sequence of keywords.
        context: Additional context strings.
    """
    bullets: List[str] = []
    if subject:
        bullets.append(f"Reconfirm subject focus: {subject.strip()}")
    for kw in keywords:
        cleaned = (kw or "").strip()
        if cleaned:
            bullets.append(f"Highlight keyword theme: {cleaned}")
    for line in context:
        cleaned = (line or "").strip()
        if cleaned:
            bullets.append(cleaned)
    return coerce_bullets(bullets)
