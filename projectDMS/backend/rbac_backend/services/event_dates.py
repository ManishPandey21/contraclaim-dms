"""Event-date semantics shared by every writer of a timeline event (A0b / D1).

A missing event date stays missing. ``created_at``, upload timestamps and the
current time are never an event date, and neither is the first date found in a
body. What a stored date *means* is recorded beside it, in the event's existing
``metadata`` dict, rather than in a new schema:

* ``metadata.date_role`` - which date it is (``document_date`` for the one
  deterministic "letter / document issued" event, ``event_date`` for an
  explicitly entered occurrence date, ...);
* ``metadata.date_flags`` - why it is qualified (``ambiguous_dm``,
  ``source_missing``, ...).

Day-first is the project default (owner OD4), so a numeric date is read
day-first; one whose day and month could be swapped is still flagged
``ambiguous_dm`` and is never an unqualified exact date.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

DATE_ROLE_EVENT = "event_date"
DATE_ROLE_DOCUMENT = "document_date"
DATE_ROLE_PERIOD_START = "period_start"
DATE_ROLES = frozenset(
    {
        DATE_ROLE_EVENT,
        DATE_ROLE_DOCUMENT,
        "reference_document_date",
        "notice_date",
        "submission_date",
        "instruction_date",
        "deadline",
        "period_start",
        "period_end",
        "unspecified",
    }
)

FLAG_AMBIGUOUS_DM = "ambiguous_dm"
FLAG_SOURCE_MISSING = "source_missing"
FLAG_INFERRED = "inferred"

_NUMERIC_DMY = re.compile(r"^\s*(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})\s*$")


def parse_day_first(value: Any) -> Tuple[Optional[datetime], List[str]]:
    """Parse ``value`` day-first; return the date and its qualifying flags.

    A ``datetime`` is taken as stored (it was already resolved upstream). A
    numeric ``d/m/y`` string whose day and month are both <= 12 and differ is
    returned day-first *and* flagged ``ambiguous_dm``. Anything unparseable is
    ``(None, [])``; no fallback is ever invented here.
    """
    if isinstance(value, datetime):
        return value, []
    if not value:
        return None, []
    raw = str(value).strip()
    match = _NUMERIC_DMY.match(raw)
    if match:
        day, month, year = (int(part) for part in match.groups())
        if year < 100:
            year += 2000
        try:
            parsed = datetime(year, month, day)
        except ValueError:
            return None, []
        flags = [FLAG_AMBIGUOUS_DM] if day <= 12 and month <= 12 and day != month else []
        return parsed, flags
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d"), []
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None), []
    except ValueError:
        return None, []


def date_metadata(event_date: Optional[datetime], role: Optional[str], flags: Optional[List[str]] = None) -> Dict[str, Any]:
    """The ``metadata`` keys describing ``event_date``.

    An undated event carries ``source_missing`` and no role, so a reader can
    tell "not established" apart from a row written before A0b.
    """
    out_flags = [flag for flag in (flags or []) if flag]
    if event_date is None:
        if FLAG_SOURCE_MISSING not in out_flags:
            out_flags.append(FLAG_SOURCE_MISSING)
        return {"date_role": None, "date_flags": out_flags}
    return {"date_role": role, "date_flags": out_flags}
