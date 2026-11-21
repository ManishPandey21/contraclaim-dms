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

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Set, Tuple

RISK_OWNERS = ("employer", "contractor", "neutral")
# Treatments under which the overlap's days count towards the extension in full.
FULL_TIME_TREATMENTS = {"eot_granted_no_cost", "eot_granted_with_cost", "not_true_concurrency"}
# Treatments under which the overlap's days MAY also carry cost. They carry it only
# when the finding's cost basis is established (D-16, AQ-4): contractual basis,
# causation, linked cost evidence and an authorised review. Never automatically.
COMPENSABLE_TREATMENTS = {"eot_granted_with_cost", "not_true_concurrency"}
TREATMENTS = FULL_TIME_TREATMENTS | {"apportioned", "no_eot"}

#: The two concurrencies a person decides (D-16).
EMPLOYER_CONTRACTOR = "employer_contractor"
NEUTRAL_CONTRACTOR = "neutral_contractor"
_KIND_CODE = {EMPLOYER_CONTRACTOR: "ec", NEUTRAL_CONTRACTOR: "nc"}


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
    kind: str = EMPLOYER_CONTRACTOR
    neutral_event_ids: Tuple[str, ...] = ()

    @property
    def day_set(self) -> Set[date]:
        return _span(self.start, self.end)


@dataclass(frozen=True)
class Treatment:
    treatment: str
    days_to_eot: Optional[int] = None  # required for "apportioned"
    # True only when the finding's cost basis is complete and reviewed (D-16).
    cost_established: bool = False


@dataclass
class DelayFacts:
    as_of: Optional[date]
    counted_event_ids: List[str] = field(default_factory=list)
    uncounted_event_ids: List[str] = field(default_factory=list)
    open_ended_event_ids: List[str] = field(default_factory=list)
    # Events with no usable end: open-ended with no as-of date.
    invalid_event_ids: List[str] = field(default_factory=list)
    # Events whose end date is before their start date (a data error, not a duration).
    reversed_event_ids: List[str] = field(default_factory=list)
    employer_critical_days: int = 0
    contractor_critical_days: int = 0
    neutral_critical_days: int = 0
    # Neutral critical days with no Contractor critical delay on the same day.
    neutral_only_days: int = 0
    # Contractor critical days with no Employer critical delay on the same day.
    contractor_only_days: int = 0
    overlaps: List[Overlap] = field(default_factory=list)
    overlap_days: int = 0
    employer_only_days: int = 0
    # Day sets behind the counts, for the arithmetic in net_eot.
    employer_only: Set[date] = field(default_factory=set)
    neutral_only: Set[date] = field(default_factory=set)


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


def overlap_identity(kind: str, start: date, end: date, event_ids: Iterable[str]) -> str:
    """An overlap is its kind, its dates and the events in it (D-16, C-03).

    Two different pairs of events on the same dates are two overlaps, and a change
    to either the dates or the events gives a new id, which orphans the old finding.
    """
    digest = hashlib.sha256("|".join(sorted(event_ids)).encode("utf-8")).hexdigest()[:10]
    return f"{_KIND_CODE[kind]}:{start.isoformat()}_{end.isoformat()}:{digest}"


def _segments(
    days: Set[date], left: Dict[str, Set[date]], right: Dict[str, Set[date]]
) -> List[Tuple[date, date, Tuple[str, ...], Tuple[str, ...]]]:
    """Split ``days`` into consecutive runs over which the same events are active on each side.

    Adjacent events of one owner (Ca 1-5, Cb 6-10 against E1 1-10) give separate
    overlaps, each decided on its own; they are not merged automatically.
    """
    segments: List[Tuple[date, date, Tuple[str, ...], Tuple[str, ...]]] = []
    for day in sorted(days):
        active_left = tuple(sorted(eid for eid, span in left.items() if day in span))
        active_right = tuple(sorted(eid for eid, span in right.items() if day in span))
        if (
            segments
            and (day - segments[-1][1]).days == 1
            and segments[-1][2] == active_left
            and segments[-1][3] == active_right
        ):
            first, _last, a, b = segments[-1]
            segments[-1] = (first, day, a, b)
        else:
            segments.append((day, day, active_left, active_right))
    return segments


def compute_facts(
    events: Iterable[EngineEvent], as_of: Optional[date] = None, *, include_neutral: bool = False
) -> DelayFacts:
    """Facts on the events as decided. Neutral x Contractor overlaps are surfaced only when the
    head records that the contract gives time for neutral events (``include_neutral``)."""
    facts = DelayFacts(as_of=as_of)
    spans: Dict[str, Dict[str, Set[date]]] = {owner: {} for owner in RISK_OWNERS}
    for event in events:
        if event.end is None:
            facts.open_ended_event_ids.append(event.event_id)
        if event.end is not None and event.end < event.start:
            facts.reversed_event_ids.append(event.event_id)
            continue
        days = event_days(event, as_of)
        if days is None:
            facts.invalid_event_ids.append(event.event_id)
            continue
        if event.criticality != "critical" or event.risk_owner not in RISK_OWNERS:
            facts.uncounted_event_ids.append(event.event_id)
            continue
        facts.counted_event_ids.append(event.event_id)
        spans[event.risk_owner][event.event_id] = days

    def union(owner: str) -> Set[date]:
        result: Set[date] = set()
        for span in spans[owner].values():
            result |= span
        return result

    employer, contractor, neutral = union("employer"), union("contractor"), union("neutral")
    facts.employer_critical_days = len(employer)
    facts.contractor_critical_days = len(contractor)
    facts.neutral_critical_days = len(neutral)
    facts.employer_only = employer - contractor
    facts.neutral_only = neutral - contractor
    facts.neutral_only_days = len(facts.neutral_only)
    facts.contractor_only_days = len(contractor - employer)
    facts.employer_only_days = len(facts.employer_only)
    shared = employer & contractor
    facts.overlap_days = len(shared)

    for first, last, emp_ids, con_ids in _segments(shared, spans["employer"], spans["contractor"]):
        facts.overlaps.append(
            Overlap(
                overlap_id=overlap_identity(EMPLOYER_CONTRACTOR, first, last, emp_ids + con_ids),
                start=first,
                end=last,
                days=(last - first).days + 1,
                employer_event_ids=emp_ids,
                contractor_event_ids=con_ids,
                kind=EMPLOYER_CONTRACTOR,
            )
        )
    if include_neutral:
        # Every day neutral and contractor delay run together is a decided fact, whether or
        # not an Employer event also covers it: the neutral entitlement is its own question.
        for first, last, neu_ids, con_ids in _segments(neutral & contractor, spans["neutral"], spans["contractor"]):
            facts.overlaps.append(
                Overlap(
                    overlap_id=overlap_identity(NEUTRAL_CONTRACTOR, first, last, neu_ids + con_ids),
                    start=first,
                    end=last,
                    days=(last - first).days + 1,
                    employer_event_ids=(),
                    contractor_event_ids=con_ids,
                    kind=NEUTRAL_CONTRACTOR,
                    neutral_event_ids=neu_ids,
                )
            )
    facts.overlaps.sort(key=lambda overlap: (overlap.start, overlap.kind, overlap.overlap_id))
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

    Days are counted as a set, so a day is never counted twice and never lost because
    another rule also covers it (D-16, C-02): Employer days with no Contractor delay;
    neutral days with no Contractor delay, where the contract gives time for them; and
    the days of every overlap decided as carrying time. An apportioned overlap adds its
    number of days, capped at the days of it not already counted. An overlap with no
    treatment adds nothing to ``low`` and its uncounted days to ``high``. Days already
    granted come off both, never below zero.
    """
    counted: Set[date] = set(facts.employer_only)
    neutral_days = set(facts.neutral_only) if include_neutral else set()
    counted |= neutral_days
    relevant = [o for o in facts.overlaps if o.kind == EMPLOYER_CONTRACTOR or include_neutral]
    undecided: List[Overlap] = []
    apportioned: List[Tuple[Overlap, int]] = []
    for overlap in relevant:
        treatment = treatments.get(overlap.overlap_id)
        if treatment is None or treatment.treatment not in TREATMENTS:
            undecided.append(overlap)
        elif treatment.treatment in FULL_TIME_TREATMENTS:
            counted |= overlap.day_set
        elif treatment.treatment == "apportioned":
            apportioned.append((overlap, treatment_days(overlap, treatment)))
    def apportioned_extra(covered: Set[date]) -> int:
        # Each apportioned overlap adds at most its own uncounted days, and all of them
        # together at most the uncounted days of their union: no day is counted twice.
        per_overlap = sum(min(days, len(overlap.day_set - covered)) for overlap, days in apportioned)
        union: Set[date] = set()
        for overlap, _days in apportioned:
            union |= overlap.day_set
        return min(per_overlap, len(union - covered))

    low_days = len(counted) + apportioned_extra(counted)
    high_set = set(counted)
    for overlap in undecided:
        high_set |= overlap.day_set
    high_days = max(low_days, len(high_set) + apportioned_extra(high_set))
    granted = max(0, int(previously_granted))
    overlap_to_eot = low_days - len(facts.employer_only | neutral_days)
    return NetEot(
        low=max(0, low_days - granted),
        high=max(0, high_days - granted),
        undecided_overlap_ids=[overlap.overlap_id for overlap in undecided],
        previously_granted=granted,
        neutral_days_included=len(neutral_days),
        employer_only_days=facts.employer_only_days,
        overlap_days_to_eot=max(0, overlap_to_eot),
    )


@dataclass
class NetEotRow:
    """One line of the net-EOT table (plan 4B-25). ``days`` adds to the lower (settled)
    figure; ``up_to`` adds only to the higher one, while an overlap is undecided."""

    key: str
    label: str
    days: int = 0
    up_to: int = 0
    overlap_id: Optional[str] = None
    treatment: Optional[str] = None


def net_eot_table(
    facts: DelayFacts,
    treatments: Dict[str, Treatment],
    *,
    previously_granted: int = 0,
    include_neutral: bool = False,
) -> Tuple[List[NetEotRow], NetEot]:
    """The net-EOT figure as a table whose rows add up to it exactly.

    Built from the same day sets as ``net_eot``, in the same order, so each row is the
    days it adds that no earlier row counted: the sum of ``days`` is ``net.low`` and
    adding ``up_to`` gives ``net.high``. Returns the rows and the engine's own figure;
    a mismatch raises, so the table can never disagree with the figure it explains.
    """
    net = net_eot(facts, treatments, previously_granted=previously_granted, include_neutral=include_neutral)
    rows: List[NetEotRow] = []
    counted: Set[date] = set(facts.employer_only)
    rows.append(NetEotRow("employer_only", "Employer delay with no Contractor delay on the same day", len(counted)))
    if include_neutral:
        neutral_new = set(facts.neutral_only) - counted
        rows.append(
            NetEotRow("neutral_only", "Neutral delay with no Contractor delay, where the contract gives time", len(neutral_new))
        )
        counted |= neutral_new
    relevant = [o for o in facts.overlaps if o.kind == EMPLOYER_CONTRACTOR or include_neutral]
    apportioned: List[Tuple[Overlap, int]] = []
    for overlap in relevant:
        treatment = treatments.get(overlap.overlap_id)
        name = treatment.treatment if treatment is not None else None
        if name in FULL_TIME_TREATMENTS:
            new = overlap.day_set - counted
            rows.append(NetEotRow("overlap_time", "Overlap decided as carrying time", len(new), overlap_id=overlap.overlap_id, treatment=name))
            counted |= new
        elif name == "apportioned":
            assert treatment is not None
            apportioned.append((overlap, treatment_days(overlap, treatment)))
        elif name == "no_eot":
            rows.append(NetEotRow("overlap_none", "Overlap decided as carrying no time", 0, overlap_id=overlap.overlap_id, treatment=name))
    union: Set[date] = set()
    per_overlap = 0
    for overlap, days in apportioned:
        share = min(days, len(overlap.day_set - counted))
        per_overlap += share
        union |= overlap.day_set
        rows.append(NetEotRow("overlap_apportioned", "Overlap apportioned: the days decided as time", share,
                              overlap_id=overlap.overlap_id, treatment="apportioned"))
    cap = min(per_overlap, len(union - counted))
    if cap < per_overlap:
        rows.append(NetEotRow("apportioned_shared", "Days that more than one apportioned overlap would count", cap - per_overlap))
    granted = max(0, int(previously_granted))
    if granted:
        rows.append(NetEotRow("previously_granted", "Already granted", -granted))
    subtotal = sum(row.days for row in rows)
    if subtotal < 0:
        rows.append(NetEotRow("floor", "An extension is never less than none", -subtotal))
    if not net.settled:
        rows.append(NetEotRow(
            "undecided", "Overlaps with no decided treatment: counted only in the higher figure",
            0, up_to=net.high - net.low,
        ))
    low = sum(row.days for row in rows)
    high = low + sum(row.up_to for row in rows)
    if (low, high) != (net.low, net.high):  # pragma: no cover - the invariant the tests pin
        raise AssertionError(f"net-EOT table ({low}, {high}) disagrees with the engine ({net.low}, {net.high})")
    return rows, net


def compensable_days(
    events: Iterable[EngineEvent],
    as_of: Optional[date],
    treatments: Dict[str, Treatment],
    *,
    include_neutral: bool = False,
) -> Set[date]:
    """Days on which prolongation cost may be claimed, on the decisions recorded.

    Employer critical days with no Contractor critical delay, plus the days of an
    Employer x Contractor overlap decided as carrying cost **and** whose cost basis is
    established (D-16, AQ-4: no automatic money, not even for not_true_concurrency).
    Undecided and apportioned overlaps are not compensable, nor is neutral delay.
    """
    facts = compute_facts(events, as_of, include_neutral=include_neutral)
    result = set(facts.employer_only)
    for overlap in facts.overlaps:
        if overlap.kind != EMPLOYER_CONTRACTOR:
            continue
        treatment = treatments.get(overlap.overlap_id)
        if treatment is not None and treatment.treatment in COMPENSABLE_TREATMENTS and treatment.cost_established:
            result |= overlap.day_set
    return result


def days_outside(period_start: date, period_end: date, allowed: Set[date]) -> Tuple[int, int]:
    """(days of the period not in ``allowed``, days in the period)."""
    if period_end < period_start:
        return 0, 0
    period = _span(period_start, period_end)
    return len(period - allowed), len(period)


__all__ = [
    "NetEotRow",
    "net_eot_table",
    "COMPENSABLE_TREATMENTS",
    "EMPLOYER_CONTRACTOR",
    "NEUTRAL_CONTRACTOR",
    "overlap_identity",
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
