"""Deterministic notice-period assessment for a claim.

Dates in, dates out: no model call. The period used here is NOT read from the
executed contract. It is either a figure the organisation configured
(``sla_rules``) or a typical figure for the claim type, and the result says
which. Callers must not present either as a verified contractual requirement.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Optional

from ..models.claim import NoticeAssessment
from .sla_service import DEFAULT_NOTICE_WINDOWS, TERMINAL_STATUSES, _as_datetime

DUE_SOON_DAYS = 7


def _day(value: datetime) -> date:
    return value.date()


def _midnight(value: date) -> datetime:
    return datetime.combine(value, time.min)


def assess_notice(
    claim: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    organisation_windows: Optional[Dict[str, int]] = None,
) -> Optional[NoticeAssessment]:
    """Assess ``claim`` against its notice period, or ``None`` when there is no date to run from.

    ``organisation_windows`` holds only the periods the organisation has set
    itself, keyed by claim type. A type absent from it uses the typical figure
    and is reported as ``assumed_default``.
    """
    awareness = _as_datetime(claim.get("awareness_date"))
    event = _as_datetime(claim.get("event_date"))
    basis_date = awareness or event
    if basis_date is None:
        return None
    served = _as_datetime(claim.get("notice_date"))
    if served is None and str(claim.get("status") or "").lower() in TERMINAL_STATUSES:
        return None  # settled without a recorded notice: nothing left to chase

    claim_type = str(getattr(claim.get("type"), "value", claim.get("type")) or "other")
    configured = (organisation_windows or {}).get(claim_type)
    if isinstance(configured, int) and configured > 0:
        period_days, source = configured, "organisation_rule"
    else:
        period_days = DEFAULT_NOTICE_WINDOWS.get(claim_type, DEFAULT_NOTICE_WINDOWS["other"])
        source = "assumed_default"

    due_day = _day(basis_date) + timedelta(days=period_days)
    result = NoticeAssessment(
        basis="awareness" if awareness else "event",
        basis_date=_midnight(_day(basis_date)),
        period_days=period_days,
        period_source=source,  # type: ignore[arg-type]
        due_date=_midnight(due_day),
        state="open",
    )
    if served is not None:
        late_by = (_day(served) - due_day).days
        result.served_on = _midnight(_day(served))
        result.served_late_by_days = late_by if late_by > 0 else None
        result.state = "served_late" if late_by > 0 else "served_in_time"
        return result
    today = _day(now or datetime.utcnow())
    remaining = (due_day - today).days
    result.days_remaining = remaining
    result.state = "overdue" if remaining < 0 else "due_soon" if remaining <= DUE_SOON_DAYS else "open"
    return result


__all__ = ["DUE_SOON_DAYS", "assess_notice"]
