"""4B-25: the net-EOT table's rows add up to the engine's figure, exactly.

The table is built from the engine's own day sets, so it explains the figure the
claim relies on rather than recomputing it. Checked on worked cases and on 3,000
generated ones (events, overlaps, treatments of every kind, neutral delay in or
out, days already granted), against ``net_eot`` itself.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from rbac_backend.services.claim_delay_engine import (
    TREATMENTS,
    EngineEvent,
    Treatment,
    compute_facts,
    net_eot,
    net_eot_table,
)

D = date(2026, 3, 1)


def _event(event_id: str, owner: str, start: int, end: int) -> EngineEvent:
    return EngineEvent(event_id, D + timedelta(days=start), D + timedelta(days=end), owner, "critical")


def _sums(rows):
    low = sum(row.days for row in rows)
    return low, low + sum(row.up_to for row in rows)


def test_a_worked_table() -> None:
    # Employer 1-20 March, Contractor 11-15 March: 15 Employer-only days and a 5-day overlap.
    facts = compute_facts([_event("E1", "employer", 0, 19), _event("C1", "contractor", 10, 14)])
    overlap = facts.overlaps[0].overlap_id

    rows, net = net_eot_table(facts, {})
    assert [(row.key, row.days, row.up_to) for row in rows] == [("employer_only", 15, 0), ("undecided", 0, 5)]
    assert (net.low, net.high) == (15, 20) and _sums(rows) == (15, 20)

    rows, net = net_eot_table(facts, {overlap: Treatment("apportioned", days_to_eot=2)}, previously_granted=4)
    assert [(row.key, row.days) for row in rows] == [("employer_only", 15), ("overlap_apportioned", 2), ("previously_granted", -4)]
    assert net.days == 13 and _sums(rows) == (13, 13)

    rows, net = net_eot_table(facts, {overlap: Treatment("eot_granted_no_cost")}, previously_granted=30)
    assert [(row.key, row.days) for row in rows] == [
        ("employer_only", 15), ("overlap_time", 5), ("previously_granted", -30), ("floor", 10),
    ]
    assert net.days == 0 and _sums(rows) == (0, 0)
    assert rows[1].overlap_id == overlap and rows[1].treatment == "eot_granted_no_cost"


@pytest.mark.parametrize("seed", range(3))
def test_the_table_always_adds_up_to_the_engine(seed: int) -> None:
    rng = random.Random(20261117 + seed)
    owners = ["employer", "contractor", "neutral"]
    for _ in range(1000):
        events = []
        for index in range(rng.randint(1, 7)):
            start = rng.randint(0, 40)
            events.append(_event(f"X{index}", rng.choice(owners), start, start + rng.randint(0, 15)))
        include_neutral = rng.random() < 0.5
        facts = compute_facts(events, include_neutral=include_neutral)
        treatments = {}
        for overlap in facts.overlaps:
            choice = rng.choice(sorted(TREATMENTS) + [None])
            if choice is None:
                continue
            days = rng.randint(0, overlap.days + 3) if choice == "apportioned" else None
            treatments[overlap.overlap_id] = Treatment(choice, days_to_eot=days)
        granted = rng.choice([0, 0, rng.randint(0, 60)])
        rows, net = net_eot_table(facts, treatments, previously_granted=granted, include_neutral=include_neutral)
        engine = net_eot(facts, treatments, previously_granted=granted, include_neutral=include_neutral)
        assert (net.low, net.high) == (engine.low, engine.high)
        assert _sums(rows) == (engine.low, engine.high), (events, treatments, granted, include_neutral, rows)
        assert all(row.up_to == 0 for row in rows if row.key != "undecided")
