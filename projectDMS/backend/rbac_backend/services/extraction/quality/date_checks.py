"""Detect mixed or ambiguous date conventions within a single column.

Companion 3.4 measured a 52-row idle register mixing `M/D/YYYY` and `D/M/YYYY`
in one column - the signature of an Excel export where days <= 12 were
re-interpreted as months. Those 52 dates establish the claim period behind
9,600,000 of idling charges.

Detecting it needs more than per-value validity. In the measured column the
day-first entries (1/3, 2/3, 12/3 = 1, 2, 12 March) are each *individually*
valid both ways; only their position in an otherwise ordered sequence reveals
them. So this module uses ordered-sequence evidence: apply the proven
convention uniformly, and if that produces a wildly out-of-order register while
a mixed reading produces an ordered one, the column is mixed.

The repo parses day-first `DD-MM-YYYY` by default (CLAUDE.md). That default is
deliberately *not* applied here: a day-first read of `2/26/2023` yields month
26 and fails, while silently falling back to month-first mis-dates the March
entries. Where ambiguity survives, it is reported as ambiguity and never
promoted to an authoritative chronology entry.
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import List, Optional, Sequence, Tuple

from .models import CheckResult, Verdict


class DateConvention(str, Enum):
    DAY_FIRST = "day_first"
    MONTH_FIRST = "month_first"
    #: Both conventions are in use in the same column - the measured
    #: corruption.
    MIXED = "mixed"
    #: Every value reads validly both ways; nothing decides it.
    AMBIGUOUS = "ambiguous"
    #: No parseable dates at all.
    UNKNOWN = "unknown"


class _Kind(str, Enum):
    ISO = "iso"
    SLASHED = "slashed"


_SLASHED = re.compile(r"^\s*(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\s*$")
_ISO = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")


class _Parsed:
    __slots__ = ("kind", "day_first", "month_first")

    def __init__(
        self,
        kind: _Kind,
        day_first: Optional[date],
        month_first: Optional[date],
    ) -> None:
        self.kind = kind
        self.day_first = day_first
        self.month_first = month_first

    @property
    def proves_day_first(self) -> bool:
        return self.day_first is not None and self.month_first is None

    @property
    def proves_month_first(self) -> bool:
        return self.month_first is not None and self.day_first is None


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _normalize_year(raw: int) -> int:
    return raw + 2000 if raw < 100 else raw


def _parse(value: Optional[str]) -> Optional[_Parsed]:
    if value is None:
        return None
    text = str(value)

    iso = _ISO.match(text)
    if iso:
        parsed = _safe_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        if parsed is None:
            return None
        # ISO is unambiguous: both readings are the same date.
        return _Parsed(_Kind.ISO, parsed, parsed)

    match = _SLASHED.match(text)
    if not match:
        return None

    first, second = int(match.group(1)), int(match.group(2))
    year = _normalize_year(int(match.group(3)))

    day_first = _safe_date(year, second, first)
    month_first = _safe_date(year, first, second)
    if day_first is None and month_first is None:
        # Neither reading works (13/13). It proves nothing either way.
        return None
    return _Parsed(_Kind.SLASHED, day_first, month_first)


def _is_ordered(dates: Sequence[Optional[date]]) -> bool:
    known = [value for value in dates if value is not None]
    return all(a <= b for a, b in zip(known, known[1:]))


def _uniform(parsed: Sequence[_Parsed], convention: DateConvention) -> List[Optional[date]]:
    if convention is DateConvention.DAY_FIRST:
        return [item.day_first for item in parsed]
    return [item.month_first for item in parsed]


def _ordered_mixed_reading_exists(parsed: Sequence[_Parsed]) -> bool:
    """True when a monotonic reading exists that needs BOTH conventions.

    Greedy: walk the column and take the earliest valid reading that does not
    move backwards. If that succeeds and used both conventions, the column
    genuinely mixes them.
    """
    previous: Optional[date] = None
    used_day_first = False
    used_month_first = False

    for item in parsed:
        options: List[Tuple[date, bool]] = []
        if item.day_first is not None:
            options.append((item.day_first, True))
        if item.month_first is not None:
            options.append((item.month_first, False))
        forward = [
            option for option in options if previous is None or option[0] >= previous
        ]
        if not forward:
            return False
        chosen, is_day_first = min(forward, key=lambda option: option[0])
        previous = chosen
        if item.kind is _Kind.SLASHED and item.day_first != item.month_first:
            used_day_first = used_day_first or is_day_first
            used_month_first = used_month_first or not is_day_first

    return used_day_first and used_month_first


def detect_convention(values: Sequence[Optional[str]]) -> DateConvention:
    """Decide the column's convention from validity and ordering evidence."""
    parsed = [item for item in (_parse(value) for value in values) if item is not None]
    if not parsed:
        return DateConvention.UNKNOWN

    slashed = [item for item in parsed if item.kind is _Kind.SLASHED]
    if not slashed:
        # Every value is ISO: unambiguous, and neither convention applies.
        return DateConvention.DAY_FIRST

    proves_day_first = any(item.proves_day_first for item in slashed)
    proves_month_first = any(item.proves_month_first for item in slashed)

    if proves_day_first and proves_month_first:
        return DateConvention.MIXED

    if proves_day_first or proves_month_first:
        convention = (
            DateConvention.DAY_FIRST if proves_day_first else DateConvention.MONTH_FIRST
        )
        # Ordered-sequence evidence: if the proven convention applied uniformly
        # scrambles the register but a mixed reading orders it, the column
        # mixes conventions even though no single value proves the other one.
        if not _is_ordered(_uniform(parsed, convention)) and _ordered_mixed_reading_exists(
            parsed
        ):
            return DateConvention.MIXED
        return convention

    return DateConvention.AMBIGUOUS


def check_date_column(values: Sequence[Optional[str]]) -> CheckResult:
    """Verdict for one date column."""
    name = "date_convention"
    convention = detect_convention(values)

    if convention is DateConvention.UNKNOWN:
        return CheckResult(
            name=name,
            verdict=Verdict.NOT_CHECKABLE,
            detail="no parseable dates in this column",
        )

    if convention is DateConvention.MIXED:
        return CheckResult(
            name=name,
            verdict=Verdict.FAIL,
            detail=(
                "mixed date conventions in one column: some values are only "
                "valid day-first and others only month-first"
            ),
            evidence_refs=["column.dates"],
        )

    if convention is DateConvention.AMBIGUOUS:
        return CheckResult(
            name=name,
            verdict=Verdict.INDETERMINATE,
            detail=(
                "every value reads validly both day-first and month-first; "
                "the convention cannot be established from this column"
            ),
            evidence_refs=["column.dates"],
        )

    return CheckResult(
        name=name,
        verdict=Verdict.PASS,
        detail=f"consistent {convention.value} dates",
        evidence_refs=["column.dates"],
    )
