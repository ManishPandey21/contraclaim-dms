"""Is there enough maintenance window left to stop production and still recover?

R-A8P answered that question once, at the start of a four-hour window, and then
acted on the answer two and a half hours later. The stop landed seven minutes
inside the 90-minute recovery reserve. Every item in that 24-point PRE-OUTAGE
GO was true when it was measured and still "true" when it was used, because not
one of them was a function of the clock.

The contract here is deliberately narrow:

    NOW + EXPECTED_EXECUTION_BUDGET + RECOVERY_RESERVE <= WINDOW_END
    NOW < HARD_RECOVERY_START

Both halves are re-derived from the clock on every call. An authorization can
be issued and carried to the stop command, but confirming it recomputes the
decision and additionally refuses once the authorization is older than its
declared lifetime - so "the GO was green earlier" is never evidence.

Nothing here reads or accepts secret material. The authorization document is
clock data only, so it is safe to seal into an evidence directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

#: How long an issued GO stays usable. It exists to cover the handful of
#: commands between the final time gate and the audited stop, not an interval
#: an operator can pause inside. R-A8P's gap was 2 h 27 m.
DEFAULT_MAX_AUTHORIZATION_AGE_SECONDS = 300


class MaintenanceWindowError(ValueError):
    """An input the gate cannot decide on. Always fatal, never a warning.

    A window end that does not parse, a reserve that is not positive, or an
    absent execution budget each mean the safety contract cannot be evaluated.
    The only safe answer to "I cannot tell" is to refuse the stop.
    """


@dataclass(frozen=True)
class TimeBudgetVerdict:
    """Everything an operator needs to see before stopping production."""

    go: bool
    reason: str
    now: datetime
    window_end: datetime
    hard_recovery_start: datetime
    latest_safe_stop: datetime
    recovery_reserve: timedelta
    expected_execution_budget: timedelta

    @property
    def time_remaining_to_window_end(self) -> timedelta:
        return self.window_end - self.now

    @property
    def time_remaining_to_hard_recovery_start(self) -> timedelta:
        return self.hard_recovery_start - self.now

    @property
    def time_remaining_to_latest_safe_stop(self) -> timedelta:
        return self.latest_safe_stop - self.now

    def as_dict(self) -> dict[str, Any]:
        return {
            "go": self.go,
            "reason": self.reason,
            "now": self.now.isoformat(),
            "window_end": self.window_end.isoformat(),
            "hard_recovery_start": self.hard_recovery_start.isoformat(),
            "latest_safe_stop": self.latest_safe_stop.isoformat(),
            "recovery_reserve_minutes": self.recovery_reserve.total_seconds() / 60,
            "expected_execution_budget_minutes": self.expected_execution_budget.total_seconds() / 60,
            "time_remaining_to_window_end_seconds": self.time_remaining_to_window_end.total_seconds(),
            "time_remaining_to_hard_recovery_start_seconds": (
                self.time_remaining_to_hard_recovery_start.total_seconds()
            ),
            "time_remaining_to_latest_safe_stop_seconds": (
                self.time_remaining_to_latest_safe_stop.total_seconds()
            ),
        }


@dataclass(frozen=True)
class MaintenanceAuthorization:
    """A GO with an expiry, and the inputs needed to re-derive it."""

    window_end: datetime
    recovery_reserve_minutes: int
    expected_execution_budget_minutes: int
    issued_at: datetime
    max_age_seconds: int
    verdict: TimeBudgetVerdict

    @property
    def expires_at(self) -> datetime:
        return self.issued_at + timedelta(seconds=self.max_age_seconds)

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_end": self.window_end.isoformat(),
            "recovery_reserve_minutes": self.recovery_reserve_minutes,
            "expected_execution_budget_minutes": self.expected_execution_budget_minutes,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "max_age_seconds": self.max_age_seconds,
            "verdict": self.verdict.as_dict(),
        }


def _parse_instant(value: Any, *, field: str) -> datetime:
    """A timestamp is only usable if it names an instant, not a wall-clock time.

    A naive datetime is refused rather than assumed to be UTC: the window is
    stated in IST and executed on a UTC host, and guessing which one a bare
    `2026-09-08T12:05:07` meant is exactly the class of error this module
    exists to remove.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise MaintenanceWindowError(f"{field} is empty")
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise MaintenanceWindowError(f"{field} is not an ISO-8601 timestamp: {value!r}") from exc
    else:
        raise MaintenanceWindowError(f"{field} must be a timestamp, got {type(value).__name__}")

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MaintenanceWindowError(
            f"{field} carries no UTC offset: {value!r}. State the zone explicitly - "
            f"a window declared in IST and executed on a UTC host cannot be guessed."
        )
    return parsed.astimezone(timezone.utc)


def _parse_positive_duration(value: Any, *, field: str) -> int:
    """A duration that is absent, zero or negative cannot bound anything.

    A zero reserve says the window may be used to its last second, and a zero
    budget says the remaining work takes no time. Both make the arithmetic
    succeed and the gate meaningless, so both are refused rather than defaulted.
    """
    if value is None:
        raise MaintenanceWindowError(f"{field} was not supplied")
    if isinstance(value, bool):
        raise MaintenanceWindowError(f"{field} must be a number")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise MaintenanceWindowError(f"{field} is not a number: {value!r}") from exc
    if parsed <= 0:
        raise MaintenanceWindowError(f"{field} must be greater than zero, got {parsed}")
    return parsed


def check_maintenance_time_budget(
    *,
    window_end: Any,
    recovery_reserve_minutes: Any,
    expected_execution_budget_minutes: Any,
    now: Any = None,
) -> TimeBudgetVerdict:
    """Decide whether production may be stopped right now.

    `now` defaults to the real clock precisely because a caller that wants a
    decision about a different moment has to say so.
    """

    end = _parse_instant(window_end, field="window_end")
    reserve = timedelta(minutes=_parse_positive_duration(recovery_reserve_minutes, field="recovery_reserve_minutes"))
    budget = timedelta(
        minutes=_parse_positive_duration(
            expected_execution_budget_minutes, field="expected_execution_budget_minutes"
        )
    )
    current = _parse_instant(now if now is not None else datetime.now(timezone.utc), field="now")

    hard_recovery_start = end - reserve
    latest_safe_stop = hard_recovery_start - budget

    if current >= hard_recovery_start:
        reason = (
            f"NO-GO: the recovery reserve began at {hard_recovery_start.isoformat()} and it is now "
            f"{current.isoformat()}. Nothing new may start inside the reserve."
        )
        go = False
    elif current + budget + reserve > end:
        overrun = (current + budget + reserve) - end
        reason = (
            f"NO-GO: the execution budget does not fit before the reserve. The latest permissible "
            f"stop was {latest_safe_stop.isoformat()}; it is now {current.isoformat()}, "
            f"{_humanise(overrun)} too late."
        )
        go = False
    else:
        reason = (
            f"GO: {_humanise(latest_safe_stop - current)} of slack remains before the latest "
            f"permissible stop at {latest_safe_stop.isoformat()}."
        )
        go = True

    return TimeBudgetVerdict(
        go=go,
        reason=reason,
        now=current,
        window_end=end,
        hard_recovery_start=hard_recovery_start,
        latest_safe_stop=latest_safe_stop,
        recovery_reserve=reserve,
        expected_execution_budget=budget,
    )


def issue_authorization(
    *,
    window_end: Any,
    recovery_reserve_minutes: Any,
    expected_execution_budget_minutes: Any,
    now: Any = None,
    max_age_seconds: int = DEFAULT_MAX_AUTHORIZATION_AGE_SECONDS,
) -> MaintenanceAuthorization:
    """Compute a verdict and stamp it with the moment it stops being usable."""

    verdict = check_maintenance_time_budget(
        window_end=window_end,
        recovery_reserve_minutes=recovery_reserve_minutes,
        expected_execution_budget_minutes=expected_execution_budget_minutes,
        now=now,
    )
    age = _parse_positive_duration(max_age_seconds, field="max_age_seconds")
    return MaintenanceAuthorization(
        window_end=verdict.window_end,
        recovery_reserve_minutes=int(verdict.recovery_reserve.total_seconds() // 60),
        expected_execution_budget_minutes=int(verdict.expected_execution_budget.total_seconds() // 60),
        issued_at=verdict.now,
        max_age_seconds=age,
        verdict=verdict,
    )


def confirm_authorization(
    authorization: MaintenanceAuthorization,
    *,
    now: Any = None,
) -> TimeBudgetVerdict:
    """Re-decide at the stop command, from the clock and never from the stamp.

    Two independent refusals, in this order:

    1. the authorization is older than its declared lifetime - the operator
       paused, and a paused GO is not a GO;
    2. the budget no longer fits, recomputed from scratch.

    The stored verdict is never replayed. It is kept only so the evidence shows
    what was decided and when.
    """

    current = _parse_instant(now if now is not None else datetime.now(timezone.utc), field="now")
    recomputed = check_maintenance_time_budget(
        window_end=authorization.window_end,
        recovery_reserve_minutes=authorization.recovery_reserve_minutes,
        expected_execution_budget_minutes=authorization.expected_execution_budget_minutes,
        now=current,
    )

    if current > authorization.expires_at:
        age = current - authorization.issued_at
        return TimeBudgetVerdict(
            go=False,
            reason=(
                f"NO-GO: the authorization issued at {authorization.issued_at.isoformat()} expired at "
                f"{authorization.expires_at.isoformat()} and is now {_humanise(age)} old. "
                f"Re-run the time-budget gate; a GO that was green earlier is not evidence."
            ),
            now=current,
            window_end=recomputed.window_end,
            hard_recovery_start=recomputed.hard_recovery_start,
            latest_safe_stop=recomputed.latest_safe_stop,
            recovery_reserve=recomputed.recovery_reserve,
            expected_execution_budget=recomputed.expected_execution_budget,
        )

    if not authorization.verdict.go:
        return TimeBudgetVerdict(
            go=False,
            reason="NO-GO: the authorization being confirmed was itself a refusal.",
            now=current,
            window_end=recomputed.window_end,
            hard_recovery_start=recomputed.hard_recovery_start,
            latest_safe_stop=recomputed.latest_safe_stop,
            recovery_reserve=recomputed.recovery_reserve,
            expected_execution_budget=recomputed.expected_execution_budget,
        )

    return recomputed


def load_authorization(document: Any) -> MaintenanceAuthorization:
    """Rebuild an authorization from the document `issue_authorization` wrote.

    The stored verdict is re-derived from the stored inputs at the stored issue
    time rather than trusted from the file, so editing `verdict.go` to `true`
    buys nothing.

    It is not a tamper-proof document and is not meant to be one: `window_end`,
    `issued_at` and `max_age_seconds` are read as given, and editing any of them
    does move the decision. It is an operator's own note about an owner-supplied
    window. Its job is to stop a stale GO being reused by accident, not to
    survive someone determined to defeat it; the window comes from the owner,
    and the receipt records which values were used.
    """

    if isinstance(document, (str, bytes)):
        try:
            payload = json.loads(document)
        except ValueError as exc:
            raise MaintenanceWindowError("the authorization document is not valid JSON") from exc
    elif isinstance(document, dict):
        payload = document
    else:
        raise MaintenanceWindowError(
            f"the authorization document must be JSON or a mapping, got {type(document).__name__}"
        )

    for field in ("window_end", "recovery_reserve_minutes", "expected_execution_budget_minutes", "issued_at"):
        if field not in payload:
            raise MaintenanceWindowError(f"the authorization document has no {field}")

    return issue_authorization(
        window_end=payload["window_end"],
        recovery_reserve_minutes=payload["recovery_reserve_minutes"],
        expected_execution_budget_minutes=payload["expected_execution_budget_minutes"],
        now=payload["issued_at"],
        max_age_seconds=payload.get("max_age_seconds", DEFAULT_MAX_AUTHORIZATION_AGE_SECONDS),
    )


def _humanise(delta: timedelta) -> str:
    total = int(abs(delta).total_seconds())
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"
