"""Delay arithmetic: facts are computed, overlaps are never deducted on their own."""

from __future__ import annotations

from datetime import date
from typing import Optional

import pytest

from rbac_backend.services.claim_delay_engine import (
    EngineEvent,
    Treatment,
    compensable_days,
    compute_facts,
    days_outside,
    net_eot,
)


def ev(
    event_id: str, start: str, end: Optional[str], owner: Optional[str] = "employer", criticality: Optional[str] = "critical"
) -> EngineEvent:
    return EngineEvent(event_id, date.fromisoformat(start), date.fromisoformat(end) if end else None, owner, criticality)


E1 = ev("E1", "2026-03-01", "2026-03-31")  # 31 days, Employer
C1 = ev("C1", "2026-03-20", "2026-04-10", "contractor")  # 22 days, Contractor
OVERLAP_ID = "2026-03-20_2026-03-31"  # 12 days


def test_days_are_calendar_days_counted_inclusively() -> None:
    facts = compute_facts([ev("E", "2026-03-01", "2026-03-03")])
    assert facts.employer_critical_days == 3 and facts.overlaps == [] and facts.employer_only_days == 3
    assert compute_facts([ev("E", "2026-03-01", "2026-03-01")]).employer_critical_days == 1


def test_overlapping_events_of_one_party_are_not_double_counted() -> None:
    facts = compute_facts([ev("A", "2026-03-01", "2026-03-10"), ev("B", "2026-03-05", "2026-03-20")])
    assert facts.employer_critical_days == 20


def test_an_overlap_is_found_and_described_but_not_deducted() -> None:
    facts = compute_facts([E1, C1])
    (overlap,) = facts.overlaps
    assert overlap.overlap_id == OVERLAP_ID and overlap.days == 12
    assert overlap.employer_event_ids == ("E1",) and overlap.contractor_event_ids == ("C1",)
    assert facts.employer_critical_days == 31  # the fact is unchanged by the overlap
    assert facts.employer_only_days == 19 and facts.overlap_days == 12 and facts.contractor_only_days == 10
    result = net_eot(facts, {})
    assert (result.low, result.high) == (19, 31) and result.days is None and not result.settled
    assert result.undecided_overlap_ids == [OVERLAP_ID]


@pytest.mark.parametrize(
    "treatment, expected",
    [
        (Treatment("eot_granted_no_cost"), 31),
        (Treatment("eot_granted_with_cost"), 31),
        (Treatment("not_true_concurrency"), 31),
        (Treatment("no_eot"), 19),
        (Treatment("apportioned", 5), 24),
        (Treatment("apportioned", 99), 31),  # never more than the overlap holds
        (Treatment("apportioned", None), 19),
    ],
)
def test_a_decided_treatment_settles_the_figure(treatment: Treatment, expected: int) -> None:
    result = net_eot(compute_facts([E1, C1]), {OVERLAP_ID: treatment})
    assert result.settled and result.days == expected and (result.low, result.high) == (expected, expected)


def test_an_unknown_treatment_is_not_a_decision() -> None:
    result = net_eot(compute_facts([E1, C1]), {OVERLAP_ID: Treatment("whatever")})
    assert not result.settled and (result.low, result.high) == (19, 31)


def test_separate_overlaps_are_decided_separately() -> None:
    events = [
        ev("E1", "2026-03-01", "2026-03-31"),
        ev("C1", "2026-03-05", "2026-03-06", "contractor"),
        ev("C2", "2026-03-20", "2026-03-22", "contractor"),
    ]
    facts = compute_facts(events)
    assert [(o.overlap_id, o.days) for o in facts.overlaps] == [
        ("2026-03-05_2026-03-06", 2),
        ("2026-03-20_2026-03-22", 3),
    ]
    partly = net_eot(facts, {"2026-03-05_2026-03-06": Treatment("no_eot")})
    assert (partly.low, partly.high) == (26, 29) and partly.undecided_overlap_ids == ["2026-03-20_2026-03-22"]


@pytest.mark.parametrize("criticality", ["unproven", "not_critical", None])
def test_an_event_not_decided_critical_is_listed_and_left_out(criticality) -> None:
    facts = compute_facts([E1, ev("C1", "2026-03-20", "2026-04-10", "contractor", criticality)])
    assert facts.counted_event_ids == ["E1"] and facts.uncounted_event_ids == ["C1"]
    assert facts.overlaps == [] and net_eot(facts, {}).days == 31


def test_an_event_with_no_decided_owner_is_left_out() -> None:
    facts = compute_facts([ev("X", "2026-03-01", "2026-03-10", None)])
    assert facts.employer_critical_days == 0 and facts.uncounted_event_ids == ["X"]


def test_an_open_ended_event_runs_to_the_as_of_date_and_cannot_be_counted_without_one() -> None:
    open_event = ev("E", "2026-03-01", None)
    undated = compute_facts([open_event])
    assert undated.invalid_event_ids == ["E"] and undated.open_ended_event_ids == ["E"]
    assert undated.employer_critical_days == 0
    dated = compute_facts([open_event], as_of=date(2026, 3, 10))
    assert dated.employer_critical_days == 10 and dated.invalid_event_ids == []


def test_an_event_that_ends_before_it_starts_is_invalid_not_negative() -> None:
    facts = compute_facts([ev("E", "2026-03-10", "2026-03-01")])
    assert facts.invalid_event_ids == ["E"] and facts.employer_critical_days == 0


def test_neutral_days_count_only_when_the_contract_is_said_to_give_time_for_them() -> None:
    events = [E1, ev("N", "2026-03-25", "2026-04-05", "neutral")]
    facts = compute_facts(events)
    assert facts.neutral_critical_days == 12 and facts.neutral_only_days == 5  # 1-5 April
    assert net_eot(facts, {}).days == 31
    included = net_eot(facts, {}, include_neutral=True)
    assert included.days == 36 and included.neutral_days_included == 5


def test_neutral_and_contractor_overlap_is_not_a_concurrency_overlap() -> None:
    facts = compute_facts([ev("N", "2026-03-01", "2026-03-10", "neutral"), ev("C", "2026-03-05", "2026-03-12", "contractor")])
    assert facts.overlaps == [] and facts.contractor_only_days == 8


def test_days_already_granted_come_off_and_never_go_below_zero() -> None:
    facts = compute_facts([E1, C1])
    result = net_eot(facts, {}, previously_granted=25)
    assert (result.low, result.high) == (0, 6) and result.previously_granted == 25
    assert net_eot(facts, {OVERLAP_ID: Treatment("eot_granted_no_cost")}, previously_granted=10).days == 21
    assert net_eot(facts, {}, previously_granted=-5).previously_granted == 0


def test_no_events_gives_zero_not_an_error() -> None:
    result = net_eot(compute_facts([]), {})
    assert result.settled and result.days == 0


def test_compensable_days_follow_the_decisions() -> None:
    def count(treatments: dict) -> int:
        return len(compensable_days([E1, C1], None, treatments))

    assert count({}) == 19  # an undecided overlap is not compensable
    assert count({OVERLAP_ID: Treatment("eot_granted_no_cost")}) == 19  # time, no cost
    assert count({OVERLAP_ID: Treatment("eot_granted_with_cost")}) == 31
    assert count({OVERLAP_ID: Treatment("not_true_concurrency")}) == 31
    assert count({OVERLAP_ID: Treatment("apportioned", 6)}) == 19
    assert count({OVERLAP_ID: Treatment("no_eot")}) == 19


def test_only_critical_employer_days_are_ever_compensable() -> None:
    events = [ev("N", "2026-03-01", "2026-03-10", "neutral"), ev("E", "2026-04-01", "2026-04-05", "employer", "unproven")]
    assert compensable_days(events, None, {}) == set()


def test_days_outside_counts_the_part_of_a_period_not_covered() -> None:
    allowed = compensable_days([E1, C1], None, {})  # 1-19 March
    assert days_outside(date(2026, 3, 1), date(2026, 3, 19), allowed) == (0, 19)
    assert days_outside(date(2026, 3, 15), date(2026, 3, 25), allowed) == (6, 11)
    assert days_outside(date(2026, 5, 1), date(2026, 5, 10), allowed) == (10, 10)
    assert days_outside(date(2026, 3, 10), date(2026, 3, 1), allowed) == (0, 0)
