#!/usr/bin/env python3
"""Refuse a production stop that cannot finish before the recovery reserve.

Two modes, and the second is the one that matters:

    # final pre-outage check - computes the verdict and stamps an expiry
    python3 scripts/check_maintenance_time_budget.py \
        --window-end 2026-09-08T12:05:07+00:00 \
        --recovery-reserve-minutes 90 \
        --execution-budget-minutes 120 \
        --emit-authorization /var/backups/.../time-gate.json

    # immediately before the stop command - re-reads the clock
    python3 scripts/check_maintenance_time_budget.py \
        --confirm /var/backups/.../time-gate.json

Exit codes: 0 GO, 2 NO-GO, 2 for any input the gate cannot decide on. There is
no exit code that means "probably fine".

The document this writes holds clock data only and is safe to seal into an
evidence directory.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from rbac_backend.services.maintenance_window import (  # noqa: E402
    DEFAULT_MAX_AUTHORIZATION_AGE_SECONDS,
    MaintenanceWindowError,
    TimeBudgetVerdict,
    confirm_authorization,
    issue_authorization,
    load_authorization,
)

GO = 0
NO_GO = 2


def _report(verdict: TimeBudgetVerdict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(verdict.as_dict(), indent=2, sort_keys=True))
        return

    print("GO" if verdict.go else "NO-GO")
    print(f"  now                     : {verdict.now.isoformat()}")
    print(f"  window end              : {verdict.window_end.isoformat()}")
    print(f"  recovery reserve        : {int(verdict.recovery_reserve.total_seconds() // 60)} min")
    print(f"  execution budget        : {int(verdict.expected_execution_budget.total_seconds() // 60)} min")
    print(f"  hard recovery start     : {verdict.hard_recovery_start.isoformat()}")
    print(f"  latest permissible stop : {verdict.latest_safe_stop.isoformat()}")
    print(f"  time remaining to stop  : {int(verdict.time_remaining_to_latest_safe_stop.total_seconds())} s")
    print(f"  time remaining in window: {int(verdict.time_remaining_to_window_end.total_seconds())} s")
    print(f"  {verdict.reason}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--window-end", help="ISO-8601 window end, with an explicit UTC offset.")
    parser.add_argument("--recovery-reserve-minutes", type=int, help="Minutes reserved for production recovery.")
    parser.add_argument(
        "--execution-budget-minutes",
        type=int,
        help="Minutes the remaining work is budgeted at, excluding the recovery reserve.",
    )
    parser.add_argument("--now", help="Override the clock. Testing and replay only.")
    parser.add_argument(
        "--allow-simulated-clock",
        action="store_true",
        help=(
            "Permit --now in --confirm mode. Required, because --confirm is the "
            "gate that runs immediately before a production stop."
        ),
    )
    parser.add_argument(
        "--max-authorization-age-seconds",
        type=int,
        default=DEFAULT_MAX_AUTHORIZATION_AGE_SECONDS,
        help="How long an issued GO stays usable before it must be recomputed.",
    )
    parser.add_argument("--emit-authorization", help="Write the stamped authorization to this path.")
    parser.add_argument("--confirm", help="Re-decide from an authorization document written earlier.")
    parser.add_argument("--json", action="store_true", help="Machine-readable verdict.")
    args = parser.parse_args(argv)

    # F-A8T2-10. `--confirm` is the last gate before the stop command: it exists
    # to re-read the clock at the moment of the decision. `--now` in that mode
    # turns a NO-GO into a GO with one flag, and while the fabricated instant is
    # written into the emitted evidence - so it is auditable after the fact - an
    # audit trail is not a gate. Simulating the clock here is now a deliberate
    # act that has to be spelled out, and the issuing mode (which stamps an
    # expiry that `--confirm` will re-check anyway) is untouched, so replaying a
    # window in a test costs nothing extra.
    if args.confirm and args.now and not args.allow_simulated_clock:
        print("NO-GO", file=sys.stderr)
        print(
            "  --now was supplied in --confirm mode without --allow-simulated-clock. "
            "The confirmation gate re-reads the clock immediately before a "
            "production stop; a fabricated instant there is a GO nobody measured.",
            file=sys.stderr,
        )
        return NO_GO

    try:
        if args.confirm:
            document = pathlib.Path(args.confirm).read_text(encoding="utf-8")
            verdict = confirm_authorization(load_authorization(document), now=args.now)
        else:
            authorization = issue_authorization(
                window_end=args.window_end,
                recovery_reserve_minutes=args.recovery_reserve_minutes,
                expected_execution_budget_minutes=args.execution_budget_minutes,
                now=args.now,
                max_age_seconds=args.max_authorization_age_seconds,
            )
            verdict = authorization.verdict
            if args.emit_authorization:
                target = pathlib.Path(args.emit_authorization)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(authorization.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
    except MaintenanceWindowError as exc:
        print("NO-GO", file=sys.stderr)
        print(f"  the time-budget gate cannot decide: {exc}", file=sys.stderr)
        return NO_GO
    except OSError as exc:
        print("NO-GO", file=sys.stderr)
        print(f"  the authorization document could not be read or written: {exc}", file=sys.stderr)
        return NO_GO

    _report(verdict, as_json=args.json)
    return GO if verdict.go else NO_GO


if __name__ == "__main__":
    raise SystemExit(main())
