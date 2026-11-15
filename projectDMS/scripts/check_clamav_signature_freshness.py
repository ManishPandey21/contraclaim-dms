#!/usr/bin/env python3
"""Live antivirus readiness: is clamd reachable, is the database it has LOADED
fresh, does it accept a clean file and reject EICAR?

Why this exists (R-A8Y, 2026-09-14): production's clamd answered
`ClamAV 1.4.5/28051/Sun Jul  5 06:24:23 2026` - a 70-day-old daily database -
while its healthcheck, the backend's readiness probe and every Gate 5 control
were green. They all asked whether clamd answers. None asked what it answers
with, and a stale daemon still detects EICAR, so a scan test alone proves
nothing about protection either.

The age is taken from clamd's own VERSION reply, i.e. the database the daemon
actually loaded, never from a file mtime: the R-A8Y drill caught clamd serving
daily 28115 while `daily.cld` 28122 sat on disk.

Stdlib only, so it runs anywhere Python 3 does - including inside the backend
container, where post_deploy_verify.sh feeds it on stdin
(`python - [args] < this-file`) to exercise the same network path uploads use.

Only a full run (the default: VERSION plus both scans against a live clamd)
prints `CLAMAV_READINESS=`. `--no-scan` and `--version-text` evaluate freshness
alone and print `CLAMAV_FRESHNESS=`, so a partial run can never be read as
readiness. `--now` requires `--allow-simulated-clock`: a fabricated instant is
a verdict nobody measured.

Exit codes are the integration contract:
    0  OK    every evaluated requirement met
    3  WARN  database older than the warning threshold, within the maximum
    1  FAIL  anything else: unreachable, unparsable, no timestamp, too old,
             clean file refused, EICAR accepted
    2        invalid invocation or incoherent policy (argparse convention)
Callers must treat every code other than 0 and 3 as a failure.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import struct
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_WARN = 0, 1, 2, 3
OK, WARN, FAIL = "OK", "WARN", "FAIL"

DEFAULT_WARN_HOURS = 24.0
DEFAULT_MAX_AGE_HOURS = 48.0
#: A database timestamp this far ahead of our clock is a clock or timezone
#: fault, and a fault must not read as "perfectly fresh".
FUTURE_SKEW_HOURS = 1.0

FULL, NO_SCAN, VERSION_TEXT = "full", "no-scan", "version-text"
CHECKS_BY_SCOPE = {
    FULL: ("clamd_reachable", "signature_database", "signature_age", "clean_file_accepted", "eicar_rejected"),
    NO_SCAN: ("clamd_reachable", "signature_database", "signature_age"),
    VERSION_TEXT: ("signature_database", "signature_age"),
}

_VERSION_RE = re.compile(r"^ClamAV (?P<engine>\d+\.\d+(?:\.\d+)?[0-9A-Za-z.~+-]*)(?:/(?P<rest>.*))?$")
_DB_RE = re.compile(r"^(?P<db>\d+)/(?P<when>.+)$")


class ClamavCheckError(Exception):
    """A requirement could not be evaluated. Always a FAIL, never a skip."""


class ClamdUnreachable(ClamavCheckError):
    pass


class VersionUnparsable(ClamavCheckError):
    pass


class DatabaseTimestampUnavailable(ClamavCheckError):
    pass


@dataclass(frozen=True)
class AgePolicy:
    """WARN when age > warn_hours, FAIL when age > max_age_hours."""

    warn_hours: float = DEFAULT_WARN_HOURS
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS

    def __post_init__(self) -> None:
        for name, value in (("warn_hours", self.warn_hours), ("max_age_hours", self.max_age_hours)):
            # `inf` would switch the maximum off without anyone saying so.
            if not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be a positive finite number of hours, got {value!r}")
        if self.warn_hours > self.max_age_hours:
            raise ValueError(f"warn threshold {self.warn_hours}h exceeds the maximum {self.max_age_hours}h")


@dataclass(frozen=True)
class ClamdVersion:
    engine: str
    database_version: int
    database_time: datetime


@dataclass(frozen=True)
class AgeVerdict:
    status: str
    age_hours: float
    detail: str


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


@dataclass
class Report:
    policy: AgePolicy
    scope: str = FULL
    checks: list[Check] = field(default_factory=list)
    age_hours: float | None = None

    @property
    def status(self) -> str:
        present = {check.name for check in self.checks}
        # A required check that never produced a result is not a pass.
        if set(CHECKS_BY_SCOPE[self.scope]) - present:
            return FAIL
        statuses = {check.status for check in self.checks}
        if statuses - {OK, WARN}:
            return FAIL
        return WARN if WARN in statuses else OK

    def render(self) -> str:
        lines = [f"CLAMAV {c.name} {c.status}: {c.detail}" for c in self.checks]
        label = "CLAMAV_READINESS" if self.scope == FULL else "CLAMAV_FRESHNESS"
        age = "unknown" if self.age_hours is None else f"{self.age_hours:.2f}"
        lines.append(
            f"{label}={self.status} scope={self.scope} age_hours={age} "
            f"warn_hours={self.policy.warn_hours:g} max_age_hours={self.policy.max_age_hours:g}"
        )
        return "\n".join(lines)


def eicar_bytes() -> bytes:
    """The EICAR test string, assembled at runtime.

    Kept out of the file on purpose: host antivirus quarantines any file that
    carries it verbatim, which would silently delete this checker.
    """
    head = "X5O!P%@AP[4\\PZX54(P^)7CC)7}$"
    tail = "-".join(("EICAR", "STANDARD", "ANTIVIRUS", "TEST", "FILE!$H+H*"))
    return (head + tail).encode("ascii")


CLEAN_PAYLOAD = b"ContraClaim antivirus readiness probe: an ordinary clean text file.\n"


def parse_version(reply: str) -> ClamdVersion:
    text = reply.replace("\0", "").strip()
    match = _VERSION_RE.match(text)
    if not match:
        raise VersionUnparsable(f"unparsable clamd VERSION reply: {text[:80]!r}")
    rest = match.group("rest")
    if rest is None:
        raise DatabaseTimestampUnavailable(f"clamd reports no loaded database: {text[:80]!r}")
    db = _DB_RE.match(rest)
    if not db:
        raise VersionUnparsable(f"unparsable database part in clamd VERSION reply: {text[:80]!r}")
    try:
        loaded = datetime.strptime(" ".join(db.group("when").split()), "%a %b %d %H:%M:%S %Y")
    except ValueError as exc:
        raise VersionUnparsable(f"unparsable database timestamp {db.group('when')!r}: {exc}") from exc
    # clamd renders the build time with ctime() in the container's local time;
    # the official image runs in UTC (verified R-A8Y: TZ=Etc/UTC). A container
    # started with another TZ would skew the age - a future timestamp fails
    # closed below, a past one would over-report age (the safe direction).
    return ClamdVersion(match.group("engine"), int(db.group("db")), loaded.replace(tzinfo=timezone.utc))


def evaluate_age(version: ClamdVersion, *, now: datetime, policy: AgePolicy) -> AgeVerdict:
    age = (now - version.database_time).total_seconds() / 3600.0
    when = version.database_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    if age < -FUTURE_SKEW_HOURS:
        return AgeVerdict(FAIL, age, f"database {when} is {-age:.2f} h in the future: clock or timezone fault")
    if age > policy.max_age_hours:
        return AgeVerdict(FAIL, age, f"database {when} is {age:.2f} h old, maximum {policy.max_age_hours:g} h")
    if age > policy.warn_hours:
        return AgeVerdict(WARN, age, f"database {when} is {age:.2f} h old, warning after {policy.warn_hours:g} h")
    return AgeVerdict(OK, age, f"database {when} is {max(age, 0.0):.2f} h old, maximum {policy.max_age_hours:g} h")


def _read_reply(sock: socket.socket) -> str:
    data = b""
    while not data.endswith(b"\0"):
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 65536:
            break
    return data.replace(b"\0", b"").decode("utf-8", errors="replace").strip()


def _connect(host: str, port: int, timeout: float) -> socket.socket:
    try:
        return socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise ClamdUnreachable(f"cannot connect to clamd at {host}:{port}: {exc}") from exc


def query_version(host: str, port: int, timeout: float) -> str:
    with _connect(host, port, timeout) as sock:
        try:
            sock.sendall(b"zVERSION\0")
            return _read_reply(sock)
        except OSError as exc:
            raise ClamdUnreachable(f"clamd at {host}:{port} did not answer VERSION: {exc}") from exc


def instream(host: str, port: int, payload: bytes, timeout: float) -> str:
    """Scan `payload` with the same INSTREAM framing AntivirusService uses."""
    with _connect(host, port, timeout) as sock:
        try:
            sock.sendall(b"zINSTREAM\0")
            sock.sendall(struct.pack(">I", len(payload)) + payload)
            sock.sendall(struct.pack(">I", 0))
            return _read_reply(sock)
        except OSError as exc:
            raise ClamdUnreachable(f"clamd at {host}:{port} failed an INSTREAM scan: {exc}") from exc


def report_version(report: Report, reply: str, *, now: datetime) -> None:
    add = report.checks.append
    try:
        version = parse_version(reply)
    except ClamavCheckError as exc:
        add(Check("signature_database", FAIL, str(exc)))
        add(Check("signature_age", FAIL, "not evaluated: no loaded database timestamp"))
        return
    add(
        Check(
            "signature_database",
            OK,
            f"engine {version.engine}, daily {version.database_version}, "
            f"loaded build {version.database_time.strftime('%Y-%m-%dT%H:%M:%SZ')}",
        )
    )
    verdict = evaluate_age(version, now=now, policy=report.policy)
    report.age_hours = round(verdict.age_hours, 2)
    add(Check("signature_age", verdict.status, verdict.detail))


def _scan_check(name: str, host: str, port: int, payload: bytes, timeout: float, accept) -> Check:
    try:
        reply = instream(host, port, payload, timeout)
    except ClamavCheckError as exc:
        return Check(name, FAIL, str(exc))
    return Check(name, OK if accept(reply) else FAIL, f"clamd answered {reply[:120]!r}")


def run_checks(
    host: str,
    port: int,
    *,
    now: datetime,
    policy: AgePolicy,
    timeout: float = 15.0,
    scan: bool = True,
) -> Report:
    report = Report(policy=policy, scope=FULL if scan else NO_SCAN)
    add = report.checks.append

    try:
        reply = query_version(host, port, timeout)
    except ClamavCheckError as exc:
        add(Check("clamd_reachable", FAIL, str(exc)))
        for name in CHECKS_BY_SCOPE[report.scope][1:]:
            add(Check(name, FAIL, "not evaluated: clamd unreachable"))
        return report
    add(Check("clamd_reachable", OK, f"{host}:{port} answered VERSION"))

    report_version(report, reply, now=now)

    if scan:
        add(
            _scan_check(
                "clean_file_accepted",
                host,
                port,
                CLEAN_PAYLOAD,
                timeout,
                lambda r: r.endswith("OK") and "FOUND" not in r and "ERROR" not in r,
            )
        )
        add(_scan_check("eicar_rejected", host, port, eicar_bytes(), timeout, lambda r: r.endswith("FOUND")))
    return report


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def _parse_now(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"--now {value!r} carries no UTC offset")
    return parsed.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default=os.environ.get("CLAMAV_HOST") or "clamav")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--warn-hours", type=float, default=None)
    parser.add_argument("--max-age-hours", type=float, default=None)
    parser.add_argument("--no-scan", action="store_true", help="freshness only: skip the clean/EICAR scans")
    parser.add_argument(
        "--version-text",
        help="freshness only: evaluate this VERSION reply instead of querying clamd",
    )
    parser.add_argument("--now", help="ISO-8601 instant with offset (simulation and tests only)")
    parser.add_argument("--allow-simulated-clock", action="store_true", help="required with --now")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.now and not args.allow_simulated_clock:
            raise ValueError("--now was supplied without --allow-simulated-clock; a fabricated instant is a verdict nobody measured")
        port = args.port if args.port is not None else int(os.environ.get("CLAMAV_PORT") or 3310)
        policy = AgePolicy(
            warn_hours=args.warn_hours
            if args.warn_hours is not None
            else _env_float("CLAMAV_SIGNATURE_WARN_HOURS", DEFAULT_WARN_HOURS),
            max_age_hours=args.max_age_hours
            if args.max_age_hours is not None
            else _env_float("CLAMAV_SIGNATURE_MAX_AGE_HOURS", DEFAULT_MAX_AGE_HOURS),
        )
        now = _parse_now(args.now) if args.now else datetime.now(timezone.utc)
    except ValueError as exc:
        print(f"CLAMAV_READINESS=FAIL invalid policy or invocation: {exc}")
        return EXIT_USAGE

    if args.version_text is not None:
        report = Report(policy=policy, scope=VERSION_TEXT)
        report_version(report, args.version_text, now=now)
    else:
        report = run_checks(args.host, port, now=now, policy=policy, timeout=args.timeout, scan=not args.no_scan)

    if args.json:
        print(json.dumps({"status": report.status, **asdict(report)}, default=str, indent=2))
    else:
        print(report.render())
    return {OK: EXIT_OK, WARN: EXIT_WARN}.get(report.status, EXIT_FAIL)


if __name__ == "__main__":
    sys.exit(main())
