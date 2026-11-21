"""Deterministic notice-period assessment for a claim.

Dates in, dates out: no model call. The period comes, in order of preference,
from a notice requirement recorded for the project from its own contract, from
a figure the organisation configured (``sla_rules``), or from a typical figure
for the claim type. The result says which. Only a requirement a person has
verified against the quoted clause is reported as ``contract_verified``;
callers must not present any other source as the contract's requirement.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Optional, Union

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
    requirement: Optional[Any] = None,
) -> Optional[NoticeAssessment]:
    """Assess ``claim`` against its notice period, or ``None`` when there is no date to run from.

    ``organisation_windows`` holds only the periods the organisation has set
    itself, keyed by claim type. A type absent from it uses the typical figure
    and is reported as ``assumed_default``. ``requirement`` is the project's
    recorded notice requirement for this claim type, when there is one; it
    takes precedence, and its own trigger decides which date the period runs from.
    """
    awareness = _as_datetime(claim.get("awareness_date"))
    event = _as_datetime(claim.get("event_date"))
    runs_from_event = requirement is not None and getattr(requirement, "trigger", None) == "event"
    # A period the contract counts from the event runs from the event even when
    # a later awareness date is recorded; otherwise awareness comes first.
    basis_date = (event or awareness) if runs_from_event else (awareness or event)
    if basis_date is None:
        return None
    served = _as_datetime(claim.get("notice_date"))
    if served is None and str(claim.get("status") or "").lower() in TERMINAL_STATUSES:
        return None  # settled without a recorded notice: nothing left to chase

    claim_type = str(getattr(claim.get("type"), "value", claim.get("type")) or "other")
    configured = (organisation_windows or {}).get(claim_type)
    source: Union[str, None]
    # A provisional claim type (the three-question start, a defaulted "other") cannot
    # make a requirement recorded for that type the contract's period for this claim.
    type_confirmed = claim.get("type_confirmed") is not False
    if requirement is not None:
        period_days = int(requirement.period_days)
        source = (
            "contract_verified"
            if requirement.verification == "verified" and type_confirmed
            else "contract_unverified"
        )
    elif isinstance(configured, int) and configured > 0:
        period_days, source = configured, "organisation_rule"
    else:
        period_days = DEFAULT_NOTICE_WINDOWS.get(claim_type, DEFAULT_NOTICE_WINDOWS["other"])
        source = "assumed_default"

    due_day = _day(basis_date) + timedelta(days=period_days)
    result = NoticeAssessment(
        basis="awareness" if basis_date is awareness and awareness is not None else "event",
        basis_date=_midnight(_day(basis_date)),
        period_days=period_days,
        period_source=source,  # type: ignore[arg-type]
        period_verified=source == "contract_verified",
        claim_type_confirmed=type_confirmed,
        clause_no=getattr(requirement, "clause_no", None),
        requirement_id=getattr(requirement, "id", None),
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
