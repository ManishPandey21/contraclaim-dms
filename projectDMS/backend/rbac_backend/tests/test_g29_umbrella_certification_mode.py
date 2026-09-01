"""The G29 umbrella invocation cannot be green while proving nothing.

Fourth instance of the trap `GRAPH-GATES.md` U5 describes, and the one where the
consequence is largest: G29's field 12 names "a skipped integration test counted
as a pass" as invalid closure evidence in as many words, and EVERY suite in the
umbrella's required set is a real-engine suite that skips itself when FalkorDB is
unreachable. An unreachable engine therefore produces a green umbrella run
carrying zero end-to-end evidence - the exact shape of claim this gate exists to
refuse.

`G29_UMBRELLA_CERTIFICATION=1` plus the hook in
`backend/rbac_backend/tests/conftest.py` turns a skip inside a required umbrella
suite into a failure. It is a FOURTH switch, not a union of the other three: the
umbrella's set is wider than any predecessor's, and one flag over four sets would
let a writer run claim umbrella coverage it never executed, or an umbrella run
silently demand the reader set.

The exit code is the assertion that matters. A conversion that reports a failure
and still exits zero closes nothing - an earlier revision of this hook did
exactly that by setting `wasxfail`, and the conftest records it.

This file guards the mechanism, not an authority behaviour, so it is
deliberately NOT in the Authority Band - the same call the G30, G31 and G32
certification suites made.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from rbac_backend.tests.authority_band_graph import (
    G29_UMBRELLA_CERTIFICATION_ENV,
    G29_UMBRELLA_REQUIRED_FILES,
    G30_CERTIFICATION_ENV,
    G31_CERTIFICATION_ENV,
    G32_STATE_REHEARSAL_ENV,
    g29_umbrella_certification_mode,
    g29_umbrella_skip_is_certification_failure,
    is_g29_umbrella_required_file,
)

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[2]
MANIFEST = TESTS_DIR / "AUTHORITY_BAND.txt"

#: Nothing listens here. The required suite then takes its OWN skip path.
UNREACHABLE_FALKOR_PORT = "6399"

REQUIRED_SUITE = (
    "backend/rbac_backend/tests/integration/test_g29_umbrella_material_influence_falkor.py"
)
UNREQUIRED_SUITE = "backend/rbac_backend/tests/test_graph_consumer_authority.py"

_ALL_GATE_FLAGS = (
    G29_UMBRELLA_CERTIFICATION_ENV,
    G30_CERTIFICATION_ENV,
    G31_CERTIFICATION_ENV,
    G32_STATE_REHEARSAL_ENV,
)


def _manifest_entries() -> list[str]:
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    return [
        stripped
        for stripped in (line.strip() for line in lines)
        if stripped and not stripped.startswith("#")
    ]


def test_the_required_umbrella_set_is_not_empty_and_has_no_duplicates() -> None:
    assert G29_UMBRELLA_REQUIRED_FILES, "G29 has no required suites"
    duplicates = sorted(
        {
            name
            for name in G29_UMBRELLA_REQUIRED_FILES
            if G29_UMBRELLA_REQUIRED_FILES.count(name) > 1
        }
    )
    assert not duplicates, f"duplicate entries in the G29 required set: {duplicates}"


def test_every_required_umbrella_suite_exists() -> None:
    missing = [name for name in G29_UMBRELLA_REQUIRED_FILES if not (REPO_ROOT / name).is_file()]
    assert not missing, f"G29 requires suites that do not exist: {missing}"


def test_every_required_umbrella_suite_is_an_accepted_authority_band_regression() -> None:
    """A required suite outside the band can be deleted without anything failing."""
    registered = {Path(entry).name for entry in _manifest_entries()}
    unregistered = [
        name for name in G29_UMBRELLA_REQUIRED_FILES if Path(name).name not in registered
    ]
    assert not unregistered, (
        "G29 requires suites that are not accepted Authority Band regressions: "
        f"{unregistered}"
    )


def test_every_required_umbrella_suite_is_a_real_engine_suite() -> None:
    """G29 field 12 rejects mock-based proof, so the set may not contain one.

    Checked structurally rather than by naming convention: a required suite must
    reach FalkorDB through the sanctioned transport helpers, which is what makes
    its skip meaningful and therefore what makes strict mode load-bearing.
    """
    for name in G29_UMBRELLA_REQUIRED_FILES:
        source = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "FALKOR_TEST_HOST" in source or "falkor_host" in source, (
            f"{name} is in the G29 required set but never reaches a real engine"
        )
        assert "pytest.skip" in source, (
            f"{name} cannot skip on an unreachable engine, so requiring it "
            "proves nothing about strict mode"
        )


def test_the_umbrella_set_is_wider_than_any_single_predecessor() -> None:
    """The umbrella is the COMPOSITION. A set no wider than one predecessor's
    would mean G29 was being certified as a fourth copy of that gate."""
    from rbac_backend.tests.authority_band_graph import (
        G30_REQUIRED_READER_FILES,
        G32_STATE_REQUIRED_FILES,
    )

    required = set(G29_UMBRELLA_REQUIRED_FILES)
    assert not required <= set(G30_REQUIRED_READER_FILES)
    assert not required <= set(G32_STATE_REQUIRED_FILES)
    # It must genuinely span the writer, state and consumer seams.
    names = {Path(name).name for name in required}
    assert "test_graph_writer_authority_falkor.py" in names
    assert "test_graph_letter_state_migration_falkor.py" in names
    assert "test_graph_end_to_end_material_influence_falkor.py" in names
    assert "test_g29_umbrella_material_influence_falkor.py" in names
    assert "test_g29_clause_state_material_influence_falkor.py" in names


def test_the_classifier_recognises_every_required_suite() -> None:
    for name in G29_UMBRELLA_REQUIRED_FILES:
        assert is_g29_umbrella_required_file(name)
        assert is_g29_umbrella_required_file(str(REPO_ROOT / name))
        assert is_g29_umbrella_required_file(name.replace("/", "\\"))


def test_the_classifier_rejects_a_file_outside_the_required_set() -> None:
    assert not is_g29_umbrella_required_file(UNREQUIRED_SUITE)
    # A same-named file in another tree must not satisfy the requirement.
    assert not is_g29_umbrella_required_file(
        "somewhere/else/test_g29_umbrella_material_influence_falkor.py"
    )


def test_the_four_switches_are_independent(monkeypatch) -> None:
    """One flag over four sets would make every claim as wide as the widest run."""
    for env_var in _ALL_GATE_FLAGS:
        monkeypatch.delenv(env_var, raising=False)
    assert not g29_umbrella_certification_mode()

    for env_var in (G30_CERTIFICATION_ENV, G31_CERTIFICATION_ENV, G32_STATE_REHEARSAL_ENV):
        monkeypatch.setenv(env_var, "1")
    assert not g29_umbrella_certification_mode(), (
        "the three predecessor flags together switched on the umbrella claim"
    )

    monkeypatch.setenv(G29_UMBRELLA_CERTIFICATION_ENV, "1")
    assert g29_umbrella_certification_mode()


def test_a_skip_is_only_a_failure_in_umbrella_mode(monkeypatch) -> None:
    monkeypatch.delenv(G29_UMBRELLA_CERTIFICATION_ENV, raising=False)
    assert not g29_umbrella_skip_is_certification_failure(REQUIRED_SUITE, "skipped")

    monkeypatch.setenv(G29_UMBRELLA_CERTIFICATION_ENV, "1")
    assert g29_umbrella_skip_is_certification_failure(REQUIRED_SUITE, "skipped")
    assert not g29_umbrella_skip_is_certification_failure(REQUIRED_SUITE, "passed")
    assert not g29_umbrella_skip_is_certification_failure(UNREQUIRED_SUITE, "skipped")


def test_the_hook_applies_the_g29_umbrella_decision() -> None:
    conftest = TESTS_DIR / "conftest.py"
    source = conftest.read_text(encoding="utf-8")
    assert "g29_umbrella_skip_is_certification_failure" in source
    assert "pytest_runtest_makereport" in source


def _run_pytest(target: str, env_extra: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    for env_var in _ALL_GATE_FLAGS:
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


def test_umbrella_mode_turns_a_required_suite_skip_into_a_nonzero_exit() -> None:
    """The harness proof, in a real subprocess. Everything else asserts intent."""
    permissive = _run_pytest(REQUIRED_SUITE, {})
    assert permissive.returncode == 0, (
        "an ordinary run must still skip when Falkor is absent:\n" + permissive.stdout
    )
    assert "skipped" in permissive.stdout
    assert G29_UMBRELLA_CERTIFICATION_ENV not in permissive.stdout

    strict = _run_pytest(REQUIRED_SUITE, {G29_UMBRELLA_CERTIFICATION_ENV: "1"})
    assert strict.returncode != 0, (
        "umbrella mode allowed the required end-to-end suite to skip, so a G29 "
        "run can still be green while proving nothing:\n" + strict.stdout
    )
    assert "failed" in strict.stdout or "error" in strict.stdout.lower()
    assert G29_UMBRELLA_CERTIFICATION_ENV in strict.stdout


def test_umbrella_mode_does_not_fail_a_skip_outside_the_required_set() -> None:
    result = _run_pytest(UNREQUIRED_SUITE, {G29_UMBRELLA_CERTIFICATION_ENV: "1"})
    assert result.returncode == 0, (
        "umbrella mode failed a skip outside the G29 required set:\n" + result.stdout
    )
    assert G29_UMBRELLA_CERTIFICATION_ENV not in result.stdout


def test_the_predecessor_flags_do_not_require_the_umbrella_suites() -> None:
    """The converse independence, proven by exit code rather than by reading.

    A run offered as G31 writer evidence must not silently demand - or silently
    claim - the umbrella's composition suites.
    """
    result = _run_pytest(REQUIRED_SUITE, {G31_CERTIFICATION_ENV: "1"})
    assert result.returncode == 0, (
        "the writer certification flag failed a skip in a G29-only suite:\n" + result.stdout
    )
