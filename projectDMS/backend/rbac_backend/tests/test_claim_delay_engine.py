"""Delay arithmetic: facts are computed, overlaps are never deducted on their own."""

from __future__ import annotations

from datetime import date
from typing import Optional

import pytest

from rbac_backend.services.claim_delay_engine import (
    EMPLOYER_CONTRACTOR,
    NEUTRAL_CONTRACTOR,
    EngineEvent,
    Treatment,
    compensable_days,
    compute_facts,
    days_outside,
    net_eot,
    overlap_identity,
)


def ev(
    event_id: str, start: str, end: Optional[str], owner: Optional[str] = "employer", criticality: Optional[str] = "critical"
) -> EngineEvent:
    return EngineEvent(event_id, date.fromisoformat(start), date.fromisoformat(end) if end else None, owner, criticality)


E1 = ev("E1", "2026-03-01", "2026-03-31")  # 31 days, Employer
C1 = ev("C1", "2026-03-20", "2026-04-10", "contractor")  # 22 days, Contractor
# 4B-22: an overlap is identified by its kind, dates and events, not by its dates alone.
OVERLAP_ID = overlap_identity(EMPLOYER_CONTRACTOR, date(2026, 3, 20), date(2026, 3, 31), ["E1", "C1"])  # 12 days


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
    first = overlap_identity(EMPLOYER_CONTRACTOR, date(2026, 3, 5), date(2026, 3, 6), ["E1", "C1"])
    second = overlap_identity(EMPLOYER_CONTRACTOR, date(2026, 3, 20), date(2026, 3, 22), ["E1", "C2"])
    assert [(o.overlap_id, o.days) for o in facts.overlaps] == [(first, 2), (second, 3)]
    partly = net_eot(facts, {first: Treatment("no_eot")})
    assert (partly.low, partly.high) == (26, 29) and partly.undecided_overlap_ids == [second]


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


def test_an_event_that_ends_before_it_starts_is_reported_as_reversed_not_negative() -> None:
    """C-06: reported apart from "no end date", so the screen can say which is wrong."""
    facts = compute_facts([ev("E", "2026-03-10", "2026-03-01")], as_of=date(2026, 4, 1))
    assert facts.reversed_event_ids == ["E"] and facts.invalid_event_ids == [] and facts.employer_critical_days == 0


def test_neutral_days_count_only_when_the_contract_is_said_to_give_time_for_them() -> None:
    events = [E1, ev("N", "2026-03-25", "2026-04-05", "neutral")]
    facts = compute_facts(events)
    # Neutral days with no Contractor delay: all 12, including the 7 an Employer event also covers.
    assert facts.neutral_critical_days == 12 and facts.neutral_only_days == 12
    assert net_eot(facts, {}).days == 31
    included = net_eot(facts, {}, include_neutral=True)
    assert included.days == 36 and included.neutral_days_included == 12  # counted once: 31 + 1-5 April


def test_neutral_and_contractor_overlap_is_surfaced_only_with_a_neutral_entitlement() -> None:
    """C-02 / D-16: neutral x Contractor concurrency is a decided fact once the contract is said to give time."""
    events = [ev("N", "2026-03-01", "2026-03-10", "neutral"), ev("C", "2026-03-05", "2026-03-12", "contractor")]
    assert compute_facts(events).overlaps == [] and compute_facts(events).contractor_only_days == 8
    (overlap,) = compute_facts(events, include_neutral=True).overlaps
    assert overlap.kind == NEUTRAL_CONTRACTOR and overlap.days == 6
    assert overlap.neutral_event_ids == ("N",) and overlap.contractor_event_ids == ("C",)


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
    # D-16 / AQ-4: no automatic money. Cost needs an established cost basis.
    assert count({OVERLAP_ID: Treatment("eot_granted_with_cost")}) == 19
    assert count({OVERLAP_ID: Treatment("not_true_concurrency")}) == 19
    assert count({OVERLAP_ID: Treatment("eot_granted_with_cost", cost_established=True)}) == 31
    assert count({OVERLAP_ID: Treatment("not_true_concurrency", cost_established=True)}) == 31
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


# --------------------------------------------------------------------------- #
# 4B-22 (D-16): C-02, C-03, C-04, and properties
# --------------------------------------------------------------------------- #
def _ids(facts, kind):
    return [o.overlap_id for o in facts.overlaps if o.kind == kind]


def test_an_employer_event_no_longer_takes_neutral_days_away() -> None:
    """C-02 exactly: C1 and N1 critical 1-10 Jan with the neutral entitlement recorded, then E1 on the
    same days with the Employer x Contractor overlap decided no_eot. Before 4B-22: 10 days, then 0."""
    c1 = ev("C1", "2026-01-01", "2026-01-10", "contractor")
    n1 = ev("N1", "2026-01-01", "2026-01-10", "neutral")
    e1 = ev("E1", "2026-01-01", "2026-01-10", "employer")
    before = compute_facts([c1, n1], include_neutral=True)
    (nc,) = _ids(before, NEUTRAL_CONTRACTOR)
    entitlement = {nc: Treatment("eot_granted_no_cost")}  # the contract's neutral entitlement, recorded
    assert net_eot(before, entitlement, include_neutral=True).days == 10

    after = compute_facts([c1, n1, e1], include_neutral=True)
    (ec,) = _ids(after, EMPLOYER_CONTRACTOR)
    assert _ids(after, NEUTRAL_CONTRACTOR) == [nc]  # the neutral finding is not orphaned by E1
    result = net_eot(after, {**entitlement, ec: Treatment("no_eot")}, include_neutral=True)
    assert result.days == 10 and result.neutral_days_included == 0  # counted through the N x C finding


def test_neutral_days_count_only_where_the_entitlement_is_recorded() -> None:
    events = [ev("N1", "2026-01-01", "2026-01-10", "neutral")]
    facts = compute_facts(events, include_neutral=True)
    assert net_eot(facts, {}).days == 0  # no entitlement recorded on the head
    assert net_eot(facts, {}, include_neutral=True).days == 10


def test_two_pairs_of_events_on_the_same_dates_are_two_findings() -> None:
    """C-03: {E1,C1} and {E2,C2} on the same dates were keyed by the dates and shared one finding."""
    a = compute_facts([ev("E1", "2026-03-01", "2026-03-10"), ev("C1", "2026-03-01", "2026-03-10", "contractor")])
    b = compute_facts([ev("E2", "2026-03-01", "2026-03-10"), ev("C2", "2026-03-01", "2026-03-10", "contractor")])
    assert a.overlaps[0].overlap_id != b.overlaps[0].overlap_id


def test_swapping_an_event_orphans_the_finding() -> None:
    old = compute_facts([E1, C1])
    swapped = compute_facts([E1, ev("C9", "2026-03-20", "2026-04-10", "contractor")])
    assert old.overlaps[0].overlap_id not in {o.overlap_id for o in swapped.overlaps}
    result = net_eot(swapped, {OVERLAP_ID: Treatment("eot_granted_no_cost")})
    assert not result.settled  # the old decision does not carry over to different events


def test_adjacent_events_of_one_owner_give_separate_overlaps() -> None:
    """C-03: Ca 1-5 and Cb 6-10 against E1 1-10 gave one overlap and one treatment."""
    facts = compute_facts(
        [ev("E1", "2026-03-01", "2026-03-10"), ev("Ca", "2026-03-01", "2026-03-05", "contractor"),
         ev("Cb", "2026-03-06", "2026-03-10", "contractor")]
    )
    assert [(o.contractor_event_ids, o.days) for o in facts.overlaps] == [(("Ca",), 5), (("Cb",), 5)]
    first, second = facts.overlaps
    mixed = net_eot(facts, {first.overlap_id: Treatment("eot_granted_no_cost"), second.overlap_id: Treatment("no_eot")})
    assert mixed.days == 5


def test_not_true_concurrency_carries_time_but_no_money_without_a_cost_basis() -> None:
    """C-04: compensable_days with not_true_concurrency returned 10 with no cost basis."""
    events = [ev("E1", "2026-03-01", "2026-03-10"), ev("C1", "2026-03-01", "2026-03-10", "contractor")]
    (overlap,) = compute_facts(events).overlaps
    plain = {overlap.overlap_id: Treatment("not_true_concurrency")}
    assert net_eot(compute_facts(events), plain).days == 10  # time
    assert compensable_days(events, None, plain) == set()  # no money
    established = {overlap.overlap_id: Treatment("not_true_concurrency", cost_established=True)}
    assert len(compensable_days(events, None, established)) == 10


def test_neutral_delay_is_never_compensable() -> None:
    events = [ev("N1", "2026-03-01", "2026-03-10", "neutral"), ev("C1", "2026-03-01", "2026-03-10", "contractor")]
    (overlap,) = compute_facts(events, include_neutral=True).overlaps
    treatments = {overlap.overlap_id: Treatment("eot_granted_with_cost", cost_established=True)}
    assert compensable_days(events, None, treatments, include_neutral=True) == set()


def test_an_apportioned_overlap_never_counts_days_already_counted() -> None:
    events = [
        ev("E1", "2026-01-01", "2026-01-10"), ev("N1", "2026-01-01", "2026-01-10", "neutral"),
        ev("C1", "2026-01-01", "2026-01-10", "contractor"),
    ]
    facts = compute_facts(events, include_neutral=True)
    (ec,) = _ids(facts, EMPLOYER_CONTRACTOR)
    (nc,) = _ids(facts, NEUTRAL_CONTRACTOR)
    result = net_eot(facts, {nc: Treatment("eot_granted_no_cost"), ec: Treatment("apportioned", 6)}, include_neutral=True)
    assert result.days == 10  # not 16: the same ten days


# Properties: a seeded generator over many event sets (no new test dependency).
import random  # noqa: E402

_OWNERS = ("employer", "contractor", "neutral")
_POLICIES = [None, Treatment("no_eot"), Treatment("eot_granted_no_cost"), Treatment("not_true_concurrency"),
             Treatment("apportioned", 3), Treatment("eot_granted_with_cost", cost_established=True)]


def _random_events(rng: random.Random, count: int, prefix: str = "X"):
    events = []
    for index in range(count):
        start = date(2026, 1, 1) + __import__("datetime").timedelta(days=rng.randint(0, 40))
        length = rng.randint(0, 15)
        events.append(EngineEvent(f"{prefix}{index}", start, start + __import__("datetime").timedelta(days=length),
                                  rng.choice(_OWNERS), "critical"))
    return events


def _under_policy(facts, policy):
    return {} if policy is None else {o.overlap_id: policy for o in facts.overlaps}


@pytest.mark.parametrize("seed", range(40))
def test_property_adding_an_employer_event_never_lowers_the_low_end(seed: int) -> None:
    """D-16: under any one treatment policy applied to every overlap before and after, an added
    Employer event never lowers the extension's low end (nothing is deducted automatically).
    A finding recorded on specific events is orphaned when its events change, by design (C-03):
    the policy is re-applied after the change, as a person would re-decide it."""
    rng = random.Random(seed)
    for _ in range(25):
        events = _random_events(rng, rng.randint(0, 6))
        added = EngineEvent("E-added", date(2026, 1, 1) + __import__("datetime").timedelta(days=rng.randint(0, 40)),
                            date(2026, 1, 1) + __import__("datetime").timedelta(days=rng.randint(41, 60)), "employer", "critical")
        include = rng.random() < 0.5
        policy = rng.choice(_POLICIES)
        before_facts = compute_facts(events, include_neutral=include)
        after_facts = compute_facts(events + [added], include_neutral=include)
        before = net_eot(before_facts, _under_policy(before_facts, policy), include_neutral=include)
        after = net_eot(after_facts, _under_policy(after_facts, policy), include_neutral=include)
        assert after.low >= before.low, (seed, events, added, include, policy)
        assert after.high >= before.high


@pytest.mark.parametrize("seed", range(40))
def test_property_no_treatment_set_deducts_automatically(seed: int) -> None:
    """D-16: whatever is decided, the extension is never below the Employer days with no Contractor
    delay, never above every day any counted event covers, and cost never exceeds time."""
    rng = random.Random(1000 + seed)
    for _ in range(25):
        events = _random_events(rng, rng.randint(0, 7))
        include = rng.random() < 0.5
        facts = compute_facts(events, include_neutral=include)
        treatments = {o.overlap_id: rng.choice(_POLICIES[1:]) for o in facts.overlaps if rng.random() < 0.8}
        result = net_eot(facts, treatments, include_neutral=include)
        floor = len(facts.employer_only | (facts.neutral_only if include else set()))
        covered = set()
        for event in events:
            if event.risk_owner == "employer" or (include and event.risk_owner == "neutral"):
                covered |= __import__("rbac_backend.services.claim_delay_engine", fromlist=["_span"])._span(event.start, event.end)
        assert floor <= result.low <= result.high <= max(floor, len(covered)), (seed, events, treatments)
        money = compensable_days(events, None, treatments, include_neutral=include)
        assert money <= (facts.employer_only | covered)
