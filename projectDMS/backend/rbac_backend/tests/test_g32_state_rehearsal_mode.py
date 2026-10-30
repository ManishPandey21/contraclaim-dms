"""The G32-STATE rehearsal invocation cannot be green while proving nothing.

Third instance of the trap `GRAPH-GATES.md` U5 describes, and the one where it
bites hardest: every claim the migration rehearsal makes - dry-run purity,
idempotency, interruption recovery, peer-owner isolation - is a claim about what
a real FalkorDB did. With the engine unreachable the suite skips, the run is
GREEN, and it carries none of that evidence.

`G32_STATE_REHEARSAL=1` plus the hook in `backend/rbac_backend/tests/conftest.py`
turns a skip inside a required G32-STATE suite into a failure. It is a THIRD
switch, not a widening of `G31_WRITER_CERTIFICATION` or
`G30_READER_CERTIFICATION`: a run offered as migration-readiness evidence must
not silently demand the writer and reader sets, and a writer run must not
silently claim migration coverage.

The exit code is the assertion that matters. A conversion that reports a failure
and still exits zero closes nothing - an earlier revision of this hook did
exactly that by setting `wasxfail`.

This file guards the mechanism, not an authority behaviour, so it is
deliberately NOT in the Authority Band - the same call the G30 and G31
certification suites made.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from rbac_backend.tests.authority_band_graph import (
    G30_CERTIFICATION_ENV,
    G31_CERTIFICATION_ENV,
    G32_STATE_REHEARSAL_ENV,
    G32_STATE_REQUIRED_FILES,
    g32_state_rehearsal_mode,
    g32_state_skip_is_rehearsal_failure,
    is_g32_state_required_file,
)

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[2]
MANIFEST = TESTS_DIR / "AUTHORITY_BAND.txt"

#: Nothing listens here. The required suite then takes its OWN skip path.
UNREACHABLE_FALKOR_PORT = "6399"

REQUIRED_SUITE = (
    "backend/rbac_backend/tests/integration/test_graph_letter_state_migration_falkor.py"
)
UNREQUIRED_SUITE = "backend/rbac_backend/tests/integration/test_graph_invalidation_falkor.py"


def _manifest_entries() -> list[str]:
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    return [
        stripped
        for stripped in (line.strip() for line in lines)
        if stripped and not stripped.startswith("#")
    ]


def test_the_required_state_set_is_not_empty_and_has_no_duplicates() -> None:
    assert G32_STATE_REQUIRED_FILES, "G32-STATE has no required suites"
    duplicates = sorted(
        {
            name
            for name in G32_STATE_REQUIRED_FILES
            if G32_STATE_REQUIRED_FILES.count(name) > 1
        }
    )
    assert not duplicates, f"duplicate entries in the G32-STATE required set: {duplicates}"


def test_every_required_state_suite_exists() -> None:
    missing = [name for name in G32_STATE_REQUIRED_FILES if not (REPO_ROOT / name).is_file()]
    assert not missing, f"G32-STATE requires suites that do not exist: {missing}"


def test_every_required_state_suite_is_an_accepted_authority_band_regression() -> None:
    """A required suite outside the band can be deleted without anything failing."""
    registered = {Path(entry).name for entry in _manifest_entries()}
    unregistered = [
        name for name in G32_STATE_REQUIRED_FILES if Path(name).name not in registered
    ]
    assert not unregistered, (
        "G32-STATE requires suites that are not accepted Authority Band "
        f"regressions: {unregistered}"
    )


def test_the_classifier_recognises_every_required_suite() -> None:
    for name in G32_STATE_REQUIRED_FILES:
        assert is_g32_state_required_file(name)
        assert is_g32_state_required_file(str(REPO_ROOT / name))
        assert is_g32_state_required_file(name.replace("/", "\\"))


def test_the_classifier_rejects_a_file_outside_the_required_set() -> None:
    assert not is_g32_state_required_file(UNREQUIRED_SUITE)
    # A same-named file in another tree must not satisfy the requirement.
    assert not is_g32_state_required_file(
        "somewhere/else/test_graph_letter_state_migration_falkor.py"
    )


def test_the_three_switches_are_independent(monkeypatch) -> None:
    """One flag over three sets would make every claim as wide as the widest run."""
    for env_var in (G30_CERTIFICATION_ENV, G31_CERTIFICATION_ENV, G32_STATE_REHEARSAL_ENV):
        monkeypatch.delenv(env_var, raising=False)
    assert not g32_state_rehearsal_mode()

    monkeypatch.setenv(G31_CERTIFICATION_ENV, "1")
    assert not g32_state_rehearsal_mode(), (
        "the writer certification flag switched on the migration rehearsal too"
    )

    monkeypatch.setenv(G32_STATE_REHEARSAL_ENV, "1")
    assert g32_state_rehearsal_mode()


def test_a_skip_is_only_a_failure_in_rehearsal_mode(monkeypatch) -> None:
    monkeypatch.delenv(G32_STATE_REHEARSAL_ENV, raising=False)
    assert not g32_state_skip_is_rehearsal_failure(REQUIRED_SUITE, "skipped")

    monkeypatch.setenv(G32_STATE_REHEARSAL_ENV, "1")
    assert g32_state_skip_is_rehearsal_failure(REQUIRED_SUITE, "skipped")
    assert not g32_state_skip_is_rehearsal_failure(REQUIRED_SUITE, "passed")
    assert not g32_state_skip_is_rehearsal_failure(UNREQUIRED_SUITE, "skipped")


def test_the_hook_applies_the_g32_state_decision() -> None:
    conftest = TESTS_DIR / "conftest.py"
    source = conftest.read_text(encoding="utf-8")
    assert "g32_state_skip_is_rehearsal_failure" in source
    assert "pytest_runtest_makereport" in source


def _run_pytest(target: str, env_extra: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for env_var in (G30_CERTIFICATION_ENV, G31_CERTIFICATION_ENV, G32_STATE_REHEARSAL_ENV):
        env.pop(env_var, None)
    env["FALKOR_TEST_HOST"] = "localhost"
    env["FALKOR_TEST_PORT"] = UNREACHABLE_FALKOR_PORT
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_rehearsal_mode_turns_a_required_suite_skip_into_a_nonzero_exit() -> None:
    """The harness proof, in a real subprocess. Everything else asserts intent."""
    permissive = _run_pytest(REQUIRED_SUITE, {})
    assert permissive.returncode == 0, (
        "an ordinary run must still skip when Falkor is absent:\n" + permissive.stdout
    )
    assert "skipped" in permissive.stdout
    assert G32_STATE_REHEARSAL_ENV not in permissive.stdout

    strict = _run_pytest(REQUIRED_SUITE, {G32_STATE_REHEARSAL_ENV: "1"})
    assert strict.returncode != 0, (
        "rehearsal mode allowed the required migration suite to skip, so a "
        "G32-STATE run can still be green while proving nothing:\n" + strict.stdout
    )
    assert "failed" in strict.stdout or "error" in strict.stdout.lower()
    assert G32_STATE_REHEARSAL_ENV in strict.stdout


def test_rehearsal_mode_does_not_fail_a_skip_outside_the_required_set() -> None:
    result = _run_pytest(UNREQUIRED_SUITE, {G32_STATE_REHEARSAL_ENV: "1"})
    assert result.returncode == 0, (
        "rehearsal mode failed a skip outside the G32-STATE required set:\n" + result.stdout
    )
    assert "skipped" in result.stdout
    assert G32_STATE_REHEARSAL_ENV not in result.stdout
