"""ClamAV signature freshness and live antivirus readiness (R-A8Y).

R-A8Y measured production answering `ClamAV 1.4.5/28051/Sun Jul  5 06:24:23 2026`
on 2026-09-14: a daily database 70 days old, behind a daemon that was healthy,
reachable and fail-closed. Every existing control was green, because every
existing control asked whether clamd *answers*, never what it is answering with.
freshclam had failed 68 consecutive daily runs ("Could not resolve hostname")
since the container was created, because the service is attached only to the
internal `service-net`.

`scripts/check_clamav_signature_freshness.py` is the control that asks. These
tests pin its contract, not its implementation:

* the age comes from the database clamd has LOADED (its VERSION reply), never
  from a file mtime;
* every way the answer can be missing - daemon unreachable, a reply that does
  not parse, a reply with no database part, a timestamp in the future - is a
  FAIL, not a skip;
* a database older than the configured maximum is a FAIL, one older than the
  warning threshold is a WARN, and the boundaries are exact;
* a clean file must be accepted and EICAR must be rejected over the real
  INSTREAM framing, so a daemon that loads no signatures cannot pass by
  answering OK to everything;
* a partial run (no scans, or a supplied VERSION text) can never print the
  readiness verdict, and a simulated clock must be asked for explicitly.

Everything runs against a deterministic in-process fake clamd: no network, no
live daemon, no real EICAR file on disk (host antivirus would quarantine it).
"""

from __future__ import annotations

import importlib.util
import math
import os
import socket
import socketserver
import struct
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check_clamav_signature_freshness.py"

#: What production's clamd answered on 2026-09-14 (R-A8Y, read-only).
PRODUCTION_REPLY = "ClamAV 1.4.5/28051/Sun Jul  5 06:24:23 2026"
PRODUCTION_OBSERVED_AT = datetime(2026, 9, 14, 2, 45, 2, tzinfo=timezone.utc)
LOADED_AT = datetime(2026, 7, 5, 6, 24, 23, tzinfo=timezone.utc)

#: Built from parts so no file in this repository carries the EICAR string.
EICAR_PARTS = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$", "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")

NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("check_clamav_signature_freshness", SCRIPT)
    assert spec and spec.loader, f"cannot load {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker():
    assert SCRIPT.is_file(), f"{SCRIPT} is missing"
    return _load()


@pytest.fixture(scope="module")
def policy(checker):
    return checker.AgePolicy(warn_hours=24, max_age_hours=48)


def _reply_for(loaded_at: datetime) -> str:
    # ctime() layout, day space-padded; `%e` is not portable (Windows strftime).
    return f"ClamAV 1.4.6/28060/{loaded_at:%a %b} {loaded_at.day:2d} {loaded_at:%H:%M:%S %Y}"


# --------------------------------------------------------------------------- #
# Parsing the loaded database
# --------------------------------------------------------------------------- #


def test_the_production_reply_parses_to_the_loaded_database(checker) -> None:
    version = checker.parse_version(PRODUCTION_REPLY)
    assert version.engine == "1.4.5"
    assert version.database_version == 28051
    assert version.database_time == LOADED_AT


@pytest.mark.parametrize("framing", ["{}\0", "{}\n", "  {}\r\n", "{}\0\0"])
def test_protocol_framing_is_not_part_of_the_answer(checker, framing: str) -> None:
    assert checker.parse_version(framing.format(PRODUCTION_REPLY)).database_time == LOADED_AT


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "PONG",
        "ClamAV",
        "garbage/1/2",
        "ClamAV 1.4.5/abc/Sun Jul  5 06:24:23 2026",
        "ClamAV 1.4.5/28051/Someday",
        "ClamAV 1.4.5/28051/Sun Jul 45 06:24:23 2026",
        "UNKNOWN COMMAND",
    ],
)
def test_an_unparsable_reply_is_refused(checker, reply: str) -> None:
    with pytest.raises(checker.VersionUnparsable):
        checker.parse_version(reply)


def test_a_reply_without_a_database_part_is_its_own_refusal(checker) -> None:
    """clamd answers the bare engine version when no database is loaded."""
    with pytest.raises(checker.DatabaseTimestampUnavailable):
        checker.parse_version("ClamAV 1.4.6")
    assert issubclass(checker.DatabaseTimestampUnavailable, checker.ClamavCheckError)


# --------------------------------------------------------------------------- #
# The age policy
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "age_hours,expected",
    [
        (0.0, "OK"),
        (23.99, "OK"),
        (24.0, "OK"),  # "WARN after 24 hours": the boundary itself is not after
        (24.01, "WARN"),
        (47.99, "WARN"),
        (48.0, "WARN"),
        (48.01, "FAIL"),
        (1000.0, "FAIL"),
    ],
)
def test_the_age_boundaries_are_exact(checker, policy, age_hours: float, expected: str) -> None:
    version = checker.ClamdVersion("1.4.6", 28060, NOW - timedelta(hours=age_hours))
    verdict = checker.evaluate_age(version, now=NOW, policy=policy)
    assert verdict.status == expected
    assert verdict.age_hours == pytest.approx(age_hours, abs=0.01)


def test_the_production_database_measured_in_r_a8y_is_red(checker, policy) -> None:
    version = checker.parse_version(PRODUCTION_REPLY)
    verdict = checker.evaluate_age(version, now=PRODUCTION_OBSERVED_AT, policy=policy)
    assert verdict.status == "FAIL"
    assert verdict.age_hours == pytest.approx(1700.34, abs=0.01)


def test_a_database_from_the_future_fails_closed(checker, policy) -> None:
    """A clock or timezone skew would otherwise read as a perfectly fresh DB."""
    version = checker.ClamdVersion("1.4.6", 28060, NOW + timedelta(hours=3))
    assert checker.evaluate_age(version, now=NOW, policy=policy).status == "FAIL"


def test_small_forward_skew_is_tolerated(checker, policy) -> None:
    version = checker.ClamdVersion("1.4.6", 28060, NOW + timedelta(minutes=30))
    assert checker.evaluate_age(version, now=NOW, policy=policy).status == "OK"


@pytest.mark.parametrize(
    "warn,maximum",
    [(49, 48), (0, 48), (24, 0), (-1, 48), (24, math.inf), (math.nan, 48), (24, math.nan)],
)
def test_an_incoherent_policy_is_refused(checker, warn: float, maximum: float) -> None:
    with pytest.raises(ValueError):
        checker.AgePolicy(warn_hours=warn, max_age_hours=maximum)


def test_the_default_policy_is_24_and_48(checker) -> None:
    assert checker.AgePolicy() == checker.AgePolicy(warn_hours=24, max_age_hours=48)


def test_the_maximum_is_configurable(checker, policy) -> None:
    version = checker.ClamdVersion("1.4.6", 28060, NOW - timedelta(hours=60))
    assert checker.evaluate_age(version, now=NOW, policy=policy).status == "FAIL"
    relaxed = checker.AgePolicy(warn_hours=24, max_age_hours=72)
    assert checker.evaluate_age(version, now=NOW, policy=relaxed).status == "WARN"


# --------------------------------------------------------------------------- #
# A fake clamd that speaks the real protocol
# --------------------------------------------------------------------------- #


class _FakeClamd(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, version_reply: str, detects: bool, clean_verdict: str = "OK") -> None:
        self.version_reply = version_reply
        self.detects = detects
        self.clean_verdict = clean_verdict
        self.streams: list[bytes] = []
        super().__init__(("127.0.0.1", 0), _FakeClamdHandler)


class _FakeClamdHandler(socketserver.BaseRequestHandler):
    def _read_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.request.recv(n - len(data))
            if not chunk:
                raise ConnectionError("client closed mid-frame")
            data += chunk
        return data

    def handle(self) -> None:
        server: _FakeClamd = self.server  # type: ignore[assignment]
        command = b""
        while not command.endswith(b"\0") and not command.endswith(b"\n"):
            chunk = self.request.recv(1)
            if not chunk:
                return
            command += chunk
        name = command.strip(b"\0\n").lstrip(b"zn")
        if name == b"VERSION":
            self.request.sendall(server.version_reply.encode() + b"\0")
        elif name == b"PING":
            self.request.sendall(b"PONG\0")
        elif name == b"INSTREAM":
            payload = b""
            while True:
                (length,) = struct.unpack(">I", self._read_exact(4))
                if length == 0:
                    break
                payload += self._read_exact(length)
            server.streams.append(payload)
            signature = "".join(EICAR_PARTS).encode()
            if server.detects and signature in payload:
                self.request.sendall(b"stream: Eicar-Test-Signature FOUND\0")
            elif signature in payload:
                self.request.sendall(b"stream: OK\0")
            else:
                self.request.sendall(f"stream: {server.clean_verdict}\0".encode())
        else:
            self.request.sendall(b"UNKNOWN COMMAND\0")


@contextmanager
def fake_clamd(version_reply: str, detects: bool = True, clean_verdict: str = "OK") -> Iterator[_FakeClamd]:
    server = _FakeClamd(version_reply, detects, clean_verdict)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _statuses(report) -> dict[str, str]:
    return {check.name: check.status for check in report.checks}


def _run(checker, policy, server, now=NOW, **kwargs):
    return checker.run_checks("127.0.0.1", server.server_address[1], now=now, policy=policy, **kwargs)


def test_a_fresh_detecting_daemon_is_green_on_every_gate5_requirement(checker, policy) -> None:
    with fake_clamd(_reply_for(NOW - timedelta(hours=3))) as server:
        report = _run(checker, policy, server)
    assert report.status == "OK", report.render()
    assert _statuses(report) == {
        "clamd_reachable": "OK",
        "signature_database": "OK",
        "signature_age": "OK",
        "clean_file_accepted": "OK",
        "eicar_rejected": "OK",
    }
    assert "CLAMAV_READINESS=OK scope=full" in report.render()
    # The scans really went over INSTREAM: one clean payload, one EICAR payload.
    assert len(server.streams) == 2
    assert "".join(EICAR_PARTS).encode() in server.streams[1]
    assert "".join(EICAR_PARTS).encode() not in server.streams[0]


def test_eicar_not_detected_fails_gate5(checker, policy) -> None:
    """Mutation control: a daemon that answers OK to everything must not pass."""
    with fake_clamd(_reply_for(NOW - timedelta(hours=3)), detects=False) as server:
        report = _run(checker, policy, server)
    assert report.status == "FAIL"
    assert _statuses(report)["eicar_rejected"] == "FAIL"
    assert _statuses(report)["clean_file_accepted"] == "OK"


def test_a_clean_file_flagged_as_infected_fails_gate5(checker, policy) -> None:
    with fake_clamd(_reply_for(NOW - timedelta(hours=3)), clean_verdict="Suspicious FOUND") as server:
        report = _run(checker, policy, server)
    assert report.status == "FAIL"
    assert _statuses(report)["clean_file_accepted"] == "FAIL"


def test_a_scan_error_is_not_a_clean_verdict(checker, policy) -> None:
    with fake_clamd(_reply_for(NOW - timedelta(hours=3)), clean_verdict="ExceededSizeLimit ERROR") as server:
        report = _run(checker, policy, server)
    assert _statuses(report)["clean_file_accepted"] == "FAIL"


def test_an_unreachable_daemon_fails_every_requirement(checker, policy) -> None:
    report = checker.run_checks("127.0.0.1", _unused_port(), now=NOW, policy=policy, timeout=2)
    assert report.status == "FAIL"
    assert set(_statuses(report)) == {
        "clamd_reachable",
        "signature_database",
        "signature_age",
        "clean_file_accepted",
        "eicar_rejected",
    }
    assert set(_statuses(report).values()) == {"FAIL"}


def test_a_stale_database_fails_even_when_scanning_works(checker, policy) -> None:
    """Mutation control: stale signatures still detect EICAR - which is exactly
    why a scan alone was never evidence of protection."""
    with fake_clamd(PRODUCTION_REPLY) as server:
        report = _run(checker, policy, server, now=PRODUCTION_OBSERVED_AT)
    assert report.status == "FAIL"
    assert _statuses(report)["signature_age"] == "FAIL"
    assert _statuses(report)["eicar_rejected"] == "OK"


def test_an_unparsable_version_fails_closed(checker, policy) -> None:
    with fake_clamd("ClamAV") as server:
        report = _run(checker, policy, server)
    assert report.status == "FAIL"
    assert _statuses(report)["signature_database"] == "FAIL"
    assert _statuses(report)["signature_age"] == "FAIL"


def test_an_aging_database_warns_without_failing(checker, policy) -> None:
    with fake_clamd(_reply_for(NOW - timedelta(hours=30))) as server:
        report = _run(checker, policy, server)
    assert report.status == "WARN"


def test_a_missing_required_check_is_not_a_pass(checker, policy) -> None:
    """A report is judged against what its scope requires, not what happened to run."""
    report = checker.Report(policy=policy, scope="full")
    report.checks.append(checker.Check("clamd_reachable", "OK", "answered"))
    assert report.status == "FAIL"


def test_a_partial_run_never_prints_the_readiness_verdict(checker, policy) -> None:
    with fake_clamd(_reply_for(NOW - timedelta(hours=1))) as server:
        report = _run(checker, policy, server, scan=False)
    assert report.status == "OK"
    rendered = report.render()
    assert "CLAMAV_READINESS=" not in rendered
    assert "CLAMAV_FRESHNESS=OK scope=no-scan" in rendered
    assert not server.streams


def test_the_checker_never_carries_the_eicar_string_literally(checker) -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "".join(EICAR_PARTS) not in text
    assert checker.eicar_bytes() == "".join(EICAR_PARTS).encode()


# --------------------------------------------------------------------------- #
# The command line: exit codes are the integration contract
# --------------------------------------------------------------------------- #

#: Pinned here rather than imported: the numbers are what callers depend on.
EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_WARN = 0, 1, 2, 3
SIMULATED = "--allow-simulated-clock"


def _cli(*args: str, env: dict[str, str] | None = None, via_stdin: bool = False) -> subprocess.CompletedProcess:
    run_env = {k: v for k, v in os.environ.items() if not k.startswith("CLAMAV_")}
    run_env.update(env or {})
    if via_stdin:
        # Exactly how post_deploy_verify.sh runs it inside the backend container.
        with SCRIPT.open("rb") as handle:
            return subprocess.run(
                [sys.executable, "-", *args], stdin=handle, capture_output=True, text=True, env=run_env, timeout=60
            )
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=run_env, timeout=60)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("via_stdin", [False, True], ids=["file", "stdin"])
def test_cli_exit_codes(via_stdin: bool) -> None:
    fresh = _reply_for(NOW - timedelta(hours=2))
    aging = _reply_for(NOW - timedelta(hours=30))
    stale = _reply_for(NOW - timedelta(hours=49))
    for reply, expected in ((fresh, EXIT_OK), (aging, EXIT_WARN), (stale, EXIT_FAIL), ("garbage", EXIT_FAIL)):
        result = _cli("--version-text", reply, "--now", _iso(NOW), SIMULATED, via_stdin=via_stdin)
        assert result.returncode == expected, (reply, result.stdout, result.stderr)
        assert "CLAMAV_FRESHNESS=" in result.stdout and "CLAMAV_READINESS=" not in result.stdout


def test_cli_reads_the_maximum_from_the_environment() -> None:
    reply = _reply_for(NOW - timedelta(hours=60))
    assert _cli("--version-text", reply, "--now", _iso(NOW), SIMULATED).returncode == EXIT_FAIL
    relaxed = _cli("--version-text", reply, "--now", _iso(NOW), SIMULATED, env={"CLAMAV_SIGNATURE_MAX_AGE_HOURS": "72"})
    assert relaxed.returncode == EXIT_WARN, relaxed.stdout


@pytest.mark.parametrize(
    "extra",
    [
        ("--warn-hours", "72", "--max-age-hours", "48"),
        ("--max-age-hours", "inf"),
        ("--warn-hours", "nan"),
    ],
)
def test_cli_refuses_an_incoherent_policy(extra: tuple[str, ...]) -> None:
    result = _cli("--version-text", _reply_for(NOW), "--now", _iso(NOW), SIMULATED, *extra)
    assert result.returncode == EXIT_USAGE, result.stdout


def test_cli_refuses_a_non_finite_maximum_from_the_environment() -> None:
    result = _cli("--version-text", _reply_for(NOW), "--now", _iso(NOW), SIMULATED, env={"CLAMAV_SIGNATURE_MAX_AGE_HOURS": "inf"})
    assert result.returncode == EXIT_USAGE, result.stdout


def test_cli_refuses_a_simulated_clock_nobody_asked_for() -> None:
    result = _cli("--version-text", _reply_for(NOW), "--now", _iso(NOW))
    assert result.returncode == EXIT_USAGE
    assert "--allow-simulated-clock" in result.stdout


def test_cli_refuses_a_naive_simulated_clock() -> None:
    result = _cli("--version-text", _reply_for(NOW), "--now", "2026-09-14T12:00:00", SIMULATED)
    assert result.returncode == EXIT_USAGE
    assert "offset" in result.stdout


def test_cli_against_a_live_fake_daemon() -> None:
    with fake_clamd(_reply_for(NOW - timedelta(hours=1))) as server:
        port = str(server.server_address[1])
        green = _cli("--host", "127.0.0.1", "--port", port, "--now", _iso(NOW), SIMULATED)
        simulated_stale = _cli("--host", "127.0.0.1", "--port", port, "--now", _iso(NOW + timedelta(hours=49)), SIMULATED)
        freshness_only = _cli("--host", "127.0.0.1", "--port", port, "--now", _iso(NOW), SIMULATED, "--no-scan")
    assert green.returncode == EXIT_OK, green.stdout + green.stderr
    assert "eicar_rejected" in green.stdout and "CLAMAV_READINESS=OK scope=full" in green.stdout
    assert simulated_stale.returncode == EXIT_FAIL, simulated_stale.stdout
    assert freshness_only.returncode == EXIT_OK and "CLAMAV_READINESS=" not in freshness_only.stdout


def test_cli_defaults_to_the_backend_clamav_settings() -> None:
    with fake_clamd(_reply_for(datetime.now(timezone.utc) - timedelta(hours=1))) as server:
        result = _cli(env={"CLAMAV_HOST": "127.0.0.1", "CLAMAV_PORT": str(server.server_address[1])})
    assert result.returncode == EXIT_OK, result.stdout + result.stderr
