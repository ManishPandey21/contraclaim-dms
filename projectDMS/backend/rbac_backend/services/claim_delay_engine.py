"""Delay arithmetic for a head of claim. Pure functions; no I/O, no model.

This module computes facts from dated events and applies decisions people have
recorded. It decides nothing itself:

- Only an event a person has decided is **critical** is counted. An event whose
  criticality is unproven or undecided is listed and left out of every figure.
- Where a critical Employer-risk event and a critical Contractor-risk event are
  active on the same days, those days are an **overlap**. An overlap is never
  deducted automatically. Each one needs a recorded treatment; until it has
  one, the result is a range between counting all of its days and none.
- Days are calendar days, counted inclusively: an event from the 1st to the
  3rd is three days. An event with no end date runs to the ``as_of`` date.

The engine does not know the contract. Whether neutral events carry time, and
how each overlap is treated, are inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Set, Tuple

RISK_OWNERS = ("employer", "contractor", "neutral")
# Treatments under which the overlap's days count towards the extension in full.
FULL_TIME_TREATMENTS = {"eot_granted_no_cost", "eot_granted_with_cost", "not_true_concurrency"}
# Treatments under which the overlap's days are also compensable.
COMPENSABLE_TREATMENTS = {"eot_granted_with_cost", "not_true_concurrency"}
TREATMENTS = FULL_TIME_TREATMENTS | {"apportioned", "no_eot"}


@dataclass(frozen=True)
class EngineEvent:
    event_id: str
    start: date
    end: Optional[date]
    risk_owner: Optional[str]  # employer | contractor | neutral | None (undecided)
    criticality: Optional[str]  # critical | not_critical | unproven | None (undecided)


@dataclass(frozen=True)
class Overlap:
    overlap_id: str
    start: date
    end: date
    days: int
    employer_event_ids: Tuple[str, ...]
    contractor_event_ids: Tuple[str, ...]


@dataclass(frozen=True)
class Treatment:
    treatment: str
    days_to_eot: Optional[int] = None  # required for "apportioned"


@dataclass
class DelayFacts:
    as_of: Optional[date]
    counted_event_ids: List[str] = field(default_factory=list)
    uncounted_event_ids: List[str] = field(default_factory=list)
    open_ended_event_ids: List[str] = field(default_factory=list)
    invalid_event_ids: List[str] = field(default_factory=list)
    employer_critical_days: int = 0
    contractor_critical_days: int = 0
    neutral_critical_days: int = 0
    # Neutral critical days that are not also Employer critical days.
    neutral_only_days: int = 0
    # Contractor critical days with no Employer critical delay on the same day.
    contractor_only_days: int = 0
    overlaps: List[Overlap] = field(default_factory=list)
    overlap_days: int = 0
    employer_only_days: int = 0


@dataclass
class NetEot:
    low: int
    high: int
    undecided_overlap_ids: List[str]
    previously_granted: int
    neutral_days_included: int
    # Days per component, so every figure can be traced.
    employer_only_days: int
    overlap_days_to_eot: int

    @property
    def settled(self) -> bool:
        return not self.undecided_overlap_ids

    @property
    def days(self) -> Optional[int]:
        return self.low if self.settled else None


def _span(start: date, end: date) -> Set[date]:
    return {start + timedelta(days=offset) for offset in range((end - start).days + 1)}


def _runs(days: Iterable[date]) -> List[Tuple[date, date]]:
    """Consecutive runs in a set of days, as (first, last) pairs in date order."""
    ordered = sorted(days)
    runs: List[Tuple[date, date]] = []
    for day in ordered:
        if runs and (day - runs[-1][1]).days == 1:
            runs[-1] = (runs[-1][0], day)
        else:
            runs.append((day, day))
    return runs


def event_days(event: EngineEvent, as_of: Optional[date]) -> Optional[Set[date]]:
    """The days an event covers, or None when it cannot be dated."""
    end = event.end or as_of
    if end is None or end < event.start:
        return None
    return _span(event.start, end)


def compute_facts(events: Iterable[EngineEvent], as_of: Optional[date] = None) -> DelayFacts:
    facts = DelayFacts(as_of=as_of)
    by_owner: Dict[str, Set[date]] = {owner: set() for owner in RISK_OWNERS}
    spans: Dict[str, Set[date]] = {}
    owners: Dict[str, str] = {}
    for event in events:
        if event.end is None:
            facts.open_ended_event_ids.append(event.event_id)
        days = event_days(event, as_of)
        if days is None:
            facts.invalid_event_ids.append(event.event_id)
            continue
        if event.criticality != "critical" or event.risk_owner not in RISK_OWNERS:
            facts.uncounted_event_ids.append(event.event_id)
            continue
        facts.counted_event_ids.append(event.event_id)
        by_owner[event.risk_owner] |= days
        spans[event.event_id] = days
        owners[event.event_id] = event.risk_owner

    employer, contractor, neutral = by_owner["employer"], by_owner["contractor"], by_owner["neutral"]
    shared = employer & contractor
    facts.employer_critical_days = len(employer)
    facts.contractor_critical_days = len(contractor)
    facts.neutral_critical_days = len(neutral)
    facts.neutral_only_days = len(neutral - employer)
    facts.contractor_only_days = len(contractor - employer)
    facts.overlap_days = len(shared)
    facts.employer_only_days = len(employer - contractor)
    for first, last in _runs(shared):
        run = _span(first, last)
        facts.overlaps.append(
            Overlap(
                overlap_id=f"{first.isoformat()}_{last.isoformat()}",
                start=first,
                end=last,
                days=len(run),
                employer_event_ids=tuple(
                    sorted(eid for eid, days in spans.items() if owners[eid] == "employer" and days & run)
                ),
                contractor_event_ids=tuple(
                    sorted(eid for eid, days in spans.items() if owners[eid] == "contractor" and days & run)
                ),
            )
        )
    return facts


def treatment_days(overlap: Overlap, treatment: Treatment) -> int:
    """Days of an overlap that count towards the extension under a decided treatment."""
    if treatment.treatment in FULL_TIME_TREATMENTS:
        return overlap.days
    if treatment.treatment == "apportioned":
        return max(0, min(overlap.days, int(treatment.days_to_eot or 0)))
    return 0


def net_eot(
    facts: DelayFacts,
    treatments: Dict[str, Treatment],
    *,
    previously_granted: int = 0,
    include_neutral: bool = False,
) -> NetEot:
    """Extension of time on the facts and the treatments decided so far.

    An overlap with no treatment contributes nothing to ``low`` and all of its
    days to ``high``. Days already granted are taken off both, never below zero.
    """
    decided = 0
    undecided_days = 0
    undecided: List[str] = []
    for overlap in facts.overlaps:
        treatment = treatments.get(overlap.overlap_id)
        if treatment is None or treatment.treatment not in TREATMENTS:
            undecided.append(overlap.overlap_id)
            undecided_days += overlap.days
        else:
            decided += treatment_days(overlap, treatment)
    neutral = facts.neutral_only_days if include_neutral else 0
    base = facts.employer_only_days + decided + neutral
    granted = max(0, int(previously_granted))
    return NetEot(
        low=max(0, base - granted),
        high=max(0, base + undecided_days - granted),
        undecided_overlap_ids=undecided,
        previously_granted=granted,
        neutral_days_included=neutral,
        employer_only_days=facts.employer_only_days,
        overlap_days_to_eot=decided,
    )


def compensable_days(
    events: Iterable[EngineEvent], as_of: Optional[date], treatments: Dict[str, Treatment]
) -> Set[date]:
    """Days on which prolongation cost may be claimed, on the decisions recorded.

    Employer critical days with no Contractor critical delay, plus the days of
    any overlap decided as carrying cost. An undecided overlap is not
    compensable, and neither is an apportioned one: which of its days carry
    cost is not something a day count can tell.
    """
    employer: Set[date] = set()
    contractor: Set[date] = set()
    for event in events:
        days = event_days(event, as_of)
        if days is None or event.criticality != "critical":
            continue
        if event.risk_owner == "employer":
            employer |= days
        elif event.risk_owner == "contractor":
            contractor |= days
    result = employer - contractor
    for first, last in _runs(employer & contractor):
        treatment = treatments.get(f"{first.isoformat()}_{last.isoformat()}")
        if treatment is not None and treatment.treatment in COMPENSABLE_TREATMENTS:
            result |= _span(first, last)
    return result


def days_outside(period_start: date, period_end: date, allowed: Set[date]) -> Tuple[int, int]:
    """(days of the period not in ``allowed``, days in the period)."""
    if period_end < period_start:
        return 0, 0
    period = _span(period_start, period_end)
    return len(period - allowed), len(period)


__all__ = [
    "COMPENSABLE_TREATMENTS",
    "FULL_TIME_TREATMENTS",
    "TREATMENTS",
    "DelayFacts",
    "EngineEvent",
    "NetEot",
    "Overlap",
    "Treatment",
    "compensable_days",
    "compute_facts",
    "days_outside",
    "event_days",
    "net_eot",
    "treatment_days",
]
