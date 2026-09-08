"""The production stop must be gated on remaining window time, not on a verdict.

R-A8P computed a 24/24 GREEN PRE-OUTAGE GO at 08:15Z, sat idle for two and a
half hours, and executed the production stop at 10:42:04Z - seven minutes after
the 90-minute recovery reserve had already begun. Not one of those 24 items was
time-valued, so the verdict that was true at 08:15Z was still "true" at 10:42Z.

These tests pin the gate that makes that impossible: a decision that is a
function of the clock, recomputed immediately before the stop, and an
authorization that expires rather than one that is ticked.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from rbac_backend.services.maintenance_window import (
    MaintenanceWindowError,
    check_maintenance_time_budget,
    confirm_authorization,
    issue_authorization,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check_maintenance_time_budget.py"

WINDOW_END = datetime(2026, 9, 8, 12, 5, 7, tzinfo=timezone.utc)
RESERVE = 90
BUDGET = 120


def _check(now, **overrides):
    kwargs = {
        "window_end": WINDOW_END,
        "recovery_reserve_minutes": RESERVE,
        "expected_execution_budget_minutes": BUDGET,
        "now": now,
    }
    kwargs.update(overrides)
    return check_maintenance_time_budget(**kwargs)


# ---------------------------------------------------------------- 1. inside


def test_comfortably_inside_the_budget_is_a_go() -> None:
    verdict = _check(WINDOW_END - timedelta(minutes=300))

    assert verdict.go is True
    assert verdict.latest_safe_stop == WINDOW_END - timedelta(minutes=RESERVE + BUDGET)
    assert verdict.hard_recovery_start == WINDOW_END - timedelta(minutes=RESERVE)
    assert verdict.expected_execution_budget == timedelta(minutes=BUDGET)
    assert verdict.recovery_reserve == timedelta(minutes=RESERVE)
    assert verdict.time_remaining_to_latest_safe_stop == timedelta(minutes=90)


# ------------------------------------------------------- 2. exact boundary


def test_exactly_on_the_latest_safe_stop_is_a_go_and_says_so() -> None:
    """Defined deterministic behaviour: the boundary is inclusive.

    now + budget + reserve == window_end leaves exactly enough, and the
    comparison is <=. One second later (below) it is not.
    """
    latest = WINDOW_END - timedelta(minutes=RESERVE + BUDGET)

    verdict = _check(latest)

    assert verdict.go is True
    assert verdict.time_remaining_to_latest_safe_stop == timedelta(0)


# ------------------------------------------------------- 3. one second past


def test_one_second_beyond_the_latest_safe_stop_is_refused() -> None:
    latest = WINDOW_END - timedelta(minutes=RESERVE + BUDGET)

    verdict = _check(latest + timedelta(seconds=1))

    assert verdict.go is False
    assert "does not fit" in verdict.reason


def test_inside_the_recovery_reserve_is_refused_even_with_a_short_budget() -> None:
    """The second half of the contract: NOW < HARD_RECOVERY_START.

    A one-minute budget satisfies now + budget + reserve <= window_end only
    before the reserve starts, but the reserve boundary is checked in its own
    right so a shrinking budget can never buy time inside it.
    """
    inside = WINDOW_END - timedelta(minutes=RESERVE) + timedelta(seconds=1)

    verdict = _check(inside, expected_execution_budget_minutes=1)

    assert verdict.go is False
    assert "recovery reserve" in verdict.reason


# ------------------------------------------ 4 & 5. a GO that stops being one


def test_a_go_issued_at_t0_is_refused_once_its_authorization_has_expired() -> None:
    t0 = WINDOW_END - timedelta(minutes=300)
    authorization = issue_authorization(
        window_end=WINDOW_END,
        recovery_reserve_minutes=RESERVE,
        expected_execution_budget_minutes=BUDGET,
        now=t0,
        max_age_seconds=300,
    )
    assert authorization.verdict.go is True

    confirmed = confirm_authorization(authorization, now=t0 + timedelta(seconds=301))

    assert confirmed.go is False
    assert "expired" in confirmed.reason


def test_a_go_still_inside_its_expiry_confirms() -> None:
    t0 = WINDOW_END - timedelta(minutes=300)
    authorization = issue_authorization(
        window_end=WINDOW_END,
        recovery_reserve_minutes=RESERVE,
        expected_execution_budget_minutes=BUDGET,
        now=t0,
        max_age_seconds=300,
    )

    confirmed = confirm_authorization(authorization, now=t0 + timedelta(seconds=299))

    assert confirmed.go is True


def test_wall_clock_advancing_past_the_window_refuses_even_a_fresh_authorization() -> None:
    """R-A8P's shape exactly: the verdict is re-derived from the clock.

    Even with the age check disabled entirely, an authorization confirmed after
    the budget no longer fits is refused, because confirmation recomputes the
    budget rather than replaying the stored answer.
    """
    t0 = WINDOW_END - timedelta(minutes=300)
    authorization = issue_authorization(
        window_end=WINDOW_END,
        recovery_reserve_minutes=RESERVE,
        expected_execution_budget_minutes=BUDGET,
        now=t0,
        max_age_seconds=10**9,
    )

    confirmed = confirm_authorization(authorization, now=WINDOW_END - timedelta(minutes=100))

    assert confirmed.go is False


# ------------------------------------------------------------ 6-8. fail closed


@pytest.mark.parametrize(
    "value",
    ["", "not-a-timestamp", "2026-09-08", "2026-09-08T12:05:07", "12:05:07Z"],
)
def test_a_malformed_or_zoneless_window_end_fails_closed(value: str) -> None:
    with pytest.raises(MaintenanceWindowError):
        check_maintenance_time_budget(
            window_end=value,
            recovery_reserve_minutes=RESERVE,
            expected_execution_budget_minutes=BUDGET,
            now=WINDOW_END - timedelta(minutes=300),
        )


@pytest.mark.parametrize("reserve", [0, -1, -90])
def test_a_zero_or_negative_recovery_reserve_fails_closed(reserve: int) -> None:
    with pytest.raises(MaintenanceWindowError):
        _check(WINDOW_END - timedelta(minutes=300), recovery_reserve_minutes=reserve)


@pytest.mark.parametrize("budget", [None, 0, -1])
def test_an_absent_or_non_positive_execution_budget_fails_closed(budget) -> None:
    with pytest.raises(MaintenanceWindowError):
        _check(WINDOW_END - timedelta(minutes=300), expected_execution_budget_minutes=budget)


# ------------------------------------------------------------- 9. timezones


def test_the_same_instant_in_another_zone_reaches_the_same_verdict() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    now = WINDOW_END - timedelta(minutes=300)

    utc_verdict = _check(now)
    ist_verdict = check_maintenance_time_budget(
        window_end=WINDOW_END.astimezone(ist).isoformat(),
        recovery_reserve_minutes=RESERVE,
        expected_execution_budget_minutes=BUDGET,
        now=now.astimezone(ist).isoformat(),
    )

    assert ist_verdict.go is True
    assert utc_verdict.go is True
    assert ist_verdict.latest_safe_stop == utc_verdict.latest_safe_stop
    assert ist_verdict.hard_recovery_start == utc_verdict.hard_recovery_start


def test_a_zone_offset_that_pushes_the_stop_past_the_reserve_is_refused() -> None:
    """The decision follows the instant, not the printed local time."""
    ist = timezone(timedelta(hours=5, minutes=30))
    refused = check_maintenance_time_budget(
        window_end=WINDOW_END.isoformat(),
        recovery_reserve_minutes=RESERVE,
        expected_execution_budget_minutes=BUDGET,
        now=(WINDOW_END - timedelta(minutes=60)).astimezone(ist).isoformat(),
    )

    assert refused.go is False


# ------------------------------------------------- 10. the R-A8P regression


def test_the_r_a8p_stop_is_refused_against_its_own_window() -> None:
    """The exact numbers from the aborted run.

    The window opened 2026-09-08T08:05:07Z for four hours, so it ended at
    12:05:07Z and the 90-minute reserve began at 10:35:07Z. The stop landed at
    10:42:04Z. Any gate worth having refuses that.
    """
    window_end = datetime(2026, 9, 8, 12, 5, 7, tzinfo=timezone.utc)
    stop_attempted_at = datetime(2026, 9, 8, 10, 42, 4, tzinfo=timezone.utc)

    verdict = check_maintenance_time_budget(
        window_end=window_end,
        recovery_reserve_minutes=90,
        expected_execution_budget_minutes=120,
        now=stop_attempted_at,
    )

    assert verdict.go is False
    assert verdict.hard_recovery_start == datetime(2026, 9, 8, 10, 35, 7, tzinfo=timezone.utc)
    assert verdict.now > verdict.hard_recovery_start
    assert "recovery reserve" in verdict.reason


def test_the_r_a8p_pre_outage_go_would_not_have_survived_the_idle_period() -> None:
    """08:15Z GO, 10:42Z stop: the authorization is what expires."""
    window_end = datetime(2026, 9, 8, 12, 5, 7, tzinfo=timezone.utc)
    issued_at = datetime(2026, 9, 8, 8, 15, 0, tzinfo=timezone.utc)
    stop_attempted_at = datetime(2026, 9, 8, 10, 42, 4, tzinfo=timezone.utc)

    authorization = issue_authorization(
        window_end=window_end,
        recovery_reserve_minutes=90,
        expected_execution_budget_minutes=120,
        now=issued_at,
        max_age_seconds=300,
    )
    assert authorization.verdict.go is True

    confirmed = confirm_authorization(authorization, now=stop_attempted_at)

    assert confirmed.go is False


# --------------------------------------------------------------- the CLI


def _run(*args: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def test_the_cli_reports_go_with_every_required_field() -> None:
    result = _run(
        "--window-end",
        WINDOW_END.isoformat(),
        "--recovery-reserve-minutes",
        str(RESERVE),
        "--execution-budget-minutes",
        str(BUDGET),
        "--now",
        (WINDOW_END - timedelta(minutes=300)).isoformat(),
    )

    assert result.returncode == 0, result.stderr
    assert "GO" in result.stdout
    for field in (
        "latest permissible stop",
        "hard recovery start",
        "execution budget",
        "recovery reserve",
        "time remaining",
    ):
        assert field in result.stdout.lower(), f"the CLI must report {field}"


def test_the_cli_refuses_the_r_a8p_stop_with_a_distinct_exit_code() -> None:
    result = _run(
        "--window-end",
        "2026-09-08T12:05:07+00:00",
        "--recovery-reserve-minutes",
        "90",
        "--execution-budget-minutes",
        "120",
        "--now",
        "2026-09-08T10:42:04+00:00",
    )

    assert result.returncode == 2
    assert "NO-GO" in result.stdout + result.stderr


def test_the_cli_fails_closed_on_a_malformed_window_end() -> None:
    result = _run(
        "--window-end",
        "sometime tonight",
        "--recovery-reserve-minutes",
        "90",
        "--execution-budget-minutes",
        "120",
    )

    assert result.returncode == 2


def test_the_cli_round_trips_an_authorization_and_refuses_it_once_stale(
    tmp_path: Path,
) -> None:
    token = tmp_path / "authorization.json"
    issued = _run(
        "--window-end",
        WINDOW_END.isoformat(),
        "--recovery-reserve-minutes",
        str(RESERVE),
        "--execution-budget-minutes",
        str(BUDGET),
        "--now",
        (WINDOW_END - timedelta(minutes=300)).isoformat(),
        "--max-authorization-age-seconds",
        "300",
        "--emit-authorization",
        str(token),
    )
    assert issued.returncode == 0, issued.stderr
    stored = json.loads(token.read_text(encoding="utf-8"))
    assert stored["window_end"] == WINDOW_END.isoformat()

    fresh = _run(
        "--confirm",
        str(token),
        "--now",
        (WINDOW_END - timedelta(minutes=299)).isoformat(),
    )
    assert fresh.returncode == 0, fresh.stderr

    stale = _run(
        "--confirm",
        str(token),
        "--now",
        (WINDOW_END - timedelta(minutes=294)).isoformat(),
    )
    assert stale.returncode == 2
    assert "NO-GO" in stale.stdout + stale.stderr


def test_the_authorization_carries_no_secret_material(tmp_path: Path) -> None:
    """It is written into evidence directories; it may hold clock data only."""
    token = tmp_path / "authorization.json"
    _run(
        "--window-end",
        WINDOW_END.isoformat(),
        "--recovery-reserve-minutes",
        str(RESERVE),
        "--execution-budget-minutes",
        str(BUDGET),
        "--now",
        (WINDOW_END - timedelta(minutes=300)).isoformat(),
        "--emit-authorization",
        str(token),
    )

    stored = json.loads(token.read_text(encoding="utf-8"))

    assert set(stored) == {
        "window_end",
        "recovery_reserve_minutes",
        "expected_execution_budget_minutes",
        "issued_at",
        "expires_at",
        "max_age_seconds",
        "verdict",
    }
