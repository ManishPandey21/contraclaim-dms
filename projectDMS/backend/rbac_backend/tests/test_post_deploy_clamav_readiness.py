"""post_deploy_verify.sh's ClamAV readiness step, driven for real (R-A8Y).

The static canary tests prove the call is present. These prove what it DOES
with each outcome, by sourcing `scripts/lib/clamav_readiness.sh` under the
caller's own `set -euo pipefail` with `docker` replaced by a stub:

* checker exit 0 -> PASS, 3 -> WARN, and every other code -> FAIL, including
  the "could not even run" codes (2 usage, 126/127 exec failures);
* a FAIL is counted and verification CONTINUES - `set -e` must not turn a red
  check into an aborted script that reports nothing after it;
* the stub receives exactly `compose ... exec -T backend python - ...` and the
  checker's bytes on stdin, so the checker that runs is the deployed file;
* end to end, the real checker fed production's R-A8Y VERSION reply produces a
  FAIL line through the same pipe.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB = REPO_ROOT / "scripts" / "lib" / "clamav_readiness.sh"
CHECKER = REPO_ROOT / "scripts" / "check_clamav_signature_freshness.py"
POST_DEPLOY = REPO_ROOT / "scripts" / "post_deploy_verify.sh"


def _working_bash() -> str | None:
    """A bash that actually runs (on Windows `which bash` can be the WSL stub)."""
    for candidate in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"):
        if not candidate:
            continue
        try:
            if subprocess.run([candidate, "-c", "exit 0"], capture_output=True, timeout=30).returncode == 0:
                return candidate
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


BASH = _working_bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="no working bash")


def _posix(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def _run(tmp_path: Path, stub_body: str, max_age: str = "", warn_hours: str = "") -> subprocess.CompletedProcess:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "docker"
    stub.write_bytes(("#!/usr/bin/env bash\n" + stub_body).encode())
    stub.chmod(0o755)
    script = f"""
set -euo pipefail
failures=0; warnings=0
pass() {{ printf 'PASS: %s\\n' "$1"; }}
warn() {{ printf 'WARN: %s\\n' "$1"; warnings=$((warnings + 1)); }}
fail() {{ printf 'FAIL: %s\\n' "$1"; failures=$((failures + 1)); }}
STUB_DIR='{_posix(stub_dir)}'
# `C:/...` would split on its own colon inside PATH (Git Bash on Windows).
if command -v cygpath >/dev/null 2>&1; then STUB_DIR=$(cygpath -u "$STUB_DIR"); fi
export PATH="$STUB_DIR:$PATH"
ROOT_DIR="{_posix(REPO_ROOT)}"; ENV_FILE="{_posix(tmp_path / '.env')}"; COMPOSE_FILES="-f docker-compose.prod.yml"
. "$ROOT_DIR/scripts/lib/clamav_readiness.sh"
clamav_readiness_check "{max_age}" "{warn_hours}"
printf 'CONTINUED failures=%s warnings=%s\\n' "$failures" "$warnings"
"""
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=120, cwd=tmp_path)


@needs_bash
@pytest.mark.parametrize(
    "rc,verdict,failures,warnings",
    [(0, "PASS", 0, 0), (3, "WARN", 0, 1), (1, "FAIL", 1, 0), (2, "FAIL", 1, 0), (126, "FAIL", 1, 0), (127, "FAIL", 1, 0)],
)
def test_exit_code_mapping_and_continuation(tmp_path: Path, rc: int, verdict: str, failures: int, warnings: int) -> None:
    marker = tmp_path / "stub-ran"
    result = _run(tmp_path, f"touch '{_posix(marker)}'\ncat >/dev/null\necho 'CLAMAV_READINESS=STUB age_hours=1.00'\nexit {rc}\n")
    assert result.returncode == 0, result.stderr
    # Anti-vacuity: a stub that is never found yields 127 and a FAIL for free.
    assert marker.exists(), f"the docker stub never ran: {result.stdout} {result.stderr}"
    lines = result.stdout.splitlines()
    assert any(line.startswith(f"{verdict}:") for line in lines), result.stdout
    assert f"CONTINUED failures={failures} warnings={warnings}" in result.stdout


@needs_bash
def test_the_checker_runs_inside_the_backend_container_from_stdin(tmp_path: Path) -> None:
    args_file, stdin_file = tmp_path / "args.txt", tmp_path / "stdin.py"
    stub = f'printf "%s\\n" "$@" > "{_posix(args_file)}"\ncat > "{_posix(stdin_file)}"\necho CLAMAV_READINESS=OK\nexit 0\n'
    result = _run(tmp_path, stub, max_age="36", warn_hours="12")
    assert "PASS:" in result.stdout, result.stdout + result.stderr
    args = args_file.read_text().split()
    joined = " ".join(args)
    assert re.search(r"^compose --env-file \S+ -f docker-compose\.prod\.yml exec -T backend python -", joined), joined
    assert "--max-age-hours 36" in joined and "--warn-hours 12" in joined
    assert "up" not in args and "restart" not in args
    # A release run is always the full check against the live daemon.
    for partial in ("--no-scan", "--version-text", "--now", "--allow-simulated-clock"):
        assert partial not in args, f"post_deploy_verify must not pass {partial}"
    assert stdin_file.read_bytes() == CHECKER.read_bytes()


@needs_bash
def test_empty_policy_falls_back_to_48_and_24(tmp_path: Path) -> None:
    args_file = tmp_path / "args.txt"
    result = _run(tmp_path, f'printf "%s\\n" "$@" > "{_posix(args_file)}"\ncat >/dev/null\nexit 0\n')
    assert "PASS:" in result.stdout
    joined = " ".join(args_file.read_text().split())
    assert "--max-age-hours 48" in joined and "--warn-hours 24" in joined


@needs_bash
@pytest.mark.parametrize(
    "reply,verdict",
    [
        ("ClamAV 1.4.5/28051/Sun Jul  5 06:24:23 2026", "FAIL"),  # production, R-A8Y
        ("ClamAV 1.4.6/28123/Sun Sep 13 20:00:00 2026", "PASS"),
        ("ClamAV 1.4.6/28123/Sun Sep 13 04:00:00 2026", "WARN"),
        ("ClamAV 1.4.6", "FAIL"),
    ],
)
def test_the_real_checker_through_the_real_pipe(tmp_path: Path, reply: str, verdict: str) -> None:
    python = _posix(sys.executable)
    stub = (
        'while [[ $# -gt 0 && "$1" != "python" ]]; do shift; done; shift; shift\n'
        f'exec "{python}" - "$@" --version-text "{reply}" --now 2026-09-14T06:00:00Z --allow-simulated-clock\n'
    )
    result = _run(tmp_path, stub)
    assert any(line.startswith(f"{verdict}:") for line in result.stdout.splitlines()), result.stdout + result.stderr
    assert "CONTINUED" in result.stdout


@needs_bash
@pytest.mark.parametrize(
    "value,disabled",
    [
        ("false", True),
        ("False", True),
        ("FALSE", True),
        ("0", True),
        ("off", True),
        ("OFF", True),
        ("no", True),
        ("No", True),
        ("n", True),
        ("f", True),
        (" false ", True),
        ("true", False),
        ("1", False),
        ("yes", False),
        ("on", False),
        ("", False),
    ],
)
def test_antivirus_disabled_matches_what_the_backend_parses_as_false(value: str, disabled: bool) -> None:
    script = f'. "{_posix(LIB)}"; if clamav_antivirus_disabled "{value}"; then echo DISABLED; else echo ENABLED; fi'
    result = subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=60)
    assert result.stdout.strip() == ("DISABLED" if disabled else "ENABLED"), result.stderr


def test_post_deploy_verify_sources_the_library_and_fails_without_a_backend() -> None:
    text = POST_DEPLOY.read_text(encoding="utf-8")
    assert '. "$ROOT_DIR/scripts/lib/clamav_readiness.sh"' in text
    assert re.search(r'fail "No running backend container; ClamAV readiness', text)
    assert re.search(r'fail "ANTIVIRUS_ENABLED is false on the running backend', text)
    assert "clamav_antivirus_disabled " in text, "the disabled check must use the pydantic-equivalent parser"


MUTATING = (
    r"\bup\b",
    r"\bdown\b",
    r"\brestart\b",
    r"\brm\b",
    r"\bstop\b",
    r"exec[^\n]*\bclamav\b",  # the check runs from the backend, never inside clamav
    r"(^|[;&|(]\s*)freshclam\b",
    r"/var/lib/clamav",
)


@pytest.mark.parametrize("pattern", MUTATING)
def test_the_library_never_mutates(pattern: str) -> None:
    code = "\n".join(line for line in LIB.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(pattern, code), f"clamav_readiness.sh contains {pattern}"
