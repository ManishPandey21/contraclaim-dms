"""The G30 reader certification invocation cannot be green while proving nothing.

`GRAPH-GATES.md` U5, applied to the consumer side. Every Falkor suite skips when
the engine is unreachable, so a run offered as G30 reader evidence could report
success having traversed no real node at all - and G30 field 12 already names
fake-only proof as invalid closure evidence for exactly the assertions a real
engine carries (raw node properties, unresolvable provenance, candidate
capacity).

G31 solved this for the writer side with `G31_WRITER_CERTIFICATION=1`. The flag
is deliberately NOT reused here. The two gates have different required sets, and
one flag over both would mean a reader certification run silently demanded the
writer suites and vice versa - a certification claim wider than the evidence
that backs it. `G30_READER_CERTIFICATION=1` is its own switch over its own set,
sharing only the mechanism.

The same three things are pinned as on the writer side:

* the required set is REAL - every listed file exists, and every one is also an
  accepted Authority Band regression, so it cannot be deleted silently;
* the classifier is exact - required files and nothing else, including a
  same-named file in another tree;
* the conversion actually happens - a real pytest subprocess over a REQUIRED
  real-engine suite, pointed at a closed port so it takes its own
  engine-unreachable skip path, EXITS ZERO normally and NON-ZERO in
  certification mode. Only the exit code proves the harness rather than the
  intention.

Certification mode is not the default and must not become it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from rbac_backend.tests.authority_band_graph import (
    G30_CERTIFICATION_ENV,
    G30_REQUIRED_READER_FILES,
    G31_CERTIFICATION_ENV,
    G31_REQUIRED_WRITER_FILES,
    g30_certification_mode,
    g30_skip_is_certification_failure,
    is_g30_required_file,
)

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parents[2]
MANIFEST = TESTS_DIR / "AUTHORITY_BAND.txt"


def _manifest_entries() -> list[str]:
    lines = MANIFEST.read_text(encoding="utf-8").splitlines()
    return [
        stripped
        for stripped in (line.strip() for line in lines)
        if stripped and not stripped.startswith("#")
    ]


def test_the_required_reader_set_is_not_empty_and_has_no_duplicates() -> None:
    assert G30_REQUIRED_READER_FILES, "G30 has no required reader suites"
    duplicates = sorted(
        {name for name in G30_REQUIRED_READER_FILES if G30_REQUIRED_READER_FILES.count(name) > 1}
    )
    assert not duplicates, f"duplicate entries in the G30 required reader set: {duplicates}"


def test_every_required_reader_suite_exists() -> None:
    missing = [name for name in G30_REQUIRED_READER_FILES if not (REPO_ROOT / name).is_file()]
    assert not missing, (
        f"G30 requires reader suites that do not exist: {missing}. "
        "Restore the file, or remove it from the required set in the same change "
        "and say why in the handoff."
    )


def test_every_required_reader_suite_is_an_accepted_authority_band_regression() -> None:
    """A required suite outside the band can be deleted without a failure."""
    registered = {entry.replace("\\", "/") for entry in _manifest_entries()}
    unregistered = [
        name for name in G30_REQUIRED_READER_FILES if name.replace("\\", "/") not in registered
    ]
    assert not unregistered, (
        "G30 required reader suites that are not in AUTHORITY_BAND.txt: "
        f"{unregistered}. Register them, or the band cannot protect what G30 depends on."
    )


def test_the_required_reader_set_carries_at_least_one_real_engine_suite() -> None:
    """A required set of fakes would certify nothing G30 field 12 accepts.

    Raw node poisoning, unresolvable provenance and candidate capacity are
    engine behaviours; the whole point of the strict flag is that the run which
    carries them actually executed.
    """
    real_engine = [name for name in G30_REQUIRED_READER_FILES if "/integration/" in name]
    assert real_engine, (
        "the G30 required reader set contains no real-engine suite, so the "
        "strict flag would guarantee the execution of fakes only"
    )


def test_the_two_gate_required_sets_are_separate() -> None:
    """One flag over both sets would overstate whichever gate is being claimed."""
    assert G30_CERTIFICATION_ENV != G31_CERTIFICATION_ENV
    assert set(G30_REQUIRED_READER_FILES) != set(G31_REQUIRED_WRITER_FILES)


def test_the_classifier_recognises_every_required_suite() -> None:
    for name in G30_REQUIRED_READER_FILES:
        assert is_g30_required_file(name), name
        assert is_g30_required_file(str(REPO_ROOT / name)), name
        assert is_g30_required_file(str(REPO_ROOT / name).replace("/", "\\")), name


def test_the_classifier_rejects_a_file_outside_the_required_set() -> None:
    assert not is_g30_required_file("backend/rbac_backend/tests/test_authority_band_manifest.py")
    assert not is_g30_required_file("")
    # A same-named file in a different tree must not satisfy the requirement.
    assert not is_g30_required_file("somewhere/else/test_graph_consumer_authority.py")


def test_a_skip_is_only_a_failure_in_certification_mode(monkeypatch) -> None:
    required = G30_REQUIRED_READER_FILES[0]

    monkeypatch.delenv(G30_CERTIFICATION_ENV, raising=False)
    assert not g30_certification_mode()
    assert not g30_skip_is_certification_failure(required, "skipped")

    monkeypatch.setenv(G30_CERTIFICATION_ENV, "1")
    assert g30_certification_mode()
    assert g30_skip_is_certification_failure(required, "skipped")
    # Only skips are converted; a pass stays a pass and a failure stays a failure.
    assert not g30_skip_is_certification_failure(required, "passed")
    assert not g30_skip_is_certification_failure(required, "failed")
    # And only for required files.
    assert not g30_skip_is_certification_failure(
        "backend/rbac_backend/tests/test_authority_band_manifest.py", "skipped"
    )


def test_the_reader_flag_does_not_make_writer_suites_required(monkeypatch) -> None:
    """Scoping between the gates, at the decision function."""
    monkeypatch.delenv(G31_CERTIFICATION_ENV, raising=False)
    monkeypatch.setenv(G30_CERTIFICATION_ENV, "1")

    writer_only = [
        name for name in G31_REQUIRED_WRITER_FILES if name not in G30_REQUIRED_READER_FILES
    ]
    assert writer_only, "the two sets are identical, so this proves nothing"
    assert not g30_skip_is_certification_failure(writer_only[0], "skipped")


#: A port nothing listens on, so the required suite takes its real
#: engine-unreachable skip path rather than a synthetic one.
UNREACHABLE_FALKOR_PORT = "6399"

#: The required real-engine reader suite used for the harness proof, and a file
#: deliberately NOT in the required set, so scoping can be shown as well.
REQUIRED_SUITE = (
    "backend/rbac_backend/tests/integration/"
    "test_graph_end_to_end_material_influence_falkor.py"
)
UNREQUIRED_SUITE = "backend/rbac_backend/tests/integration/test_graph_invalidation_falkor.py"


def _run_pytest(target: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop(G30_CERTIFICATION_ENV, None)
    env.pop(G31_CERTIFICATION_ENV, None)
    # Point the suite at a closed port so it exercises its OWN skip block.
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


def test_the_hook_lives_in_the_conftest_that_covers_the_tests_tree() -> None:
    """The decision function is useless unless something applies it everywhere."""
    conftest = TESTS_DIR / "conftest.py"
    assert conftest.is_file(), "the G30 certification hook has no conftest to load it"
    source = conftest.read_text(encoding="utf-8")
    assert "pytest_runtest_makereport" in source
    assert "g30_skip_is_certification_failure" in source


def test_certification_mode_turns_a_required_reader_suite_skip_into_a_failure() -> None:
    """The end-to-end proof, in a real subprocess, on a real required suite.

    Everything above asserts the decision function. This asserts the HARNESS:
    with FalkorDB unreachable the ordinary run is GREEN and proves nothing, and
    the certification run is RED.
    """
    permissive = _run_pytest(REQUIRED_SUITE, {})
    assert permissive.returncode == 0, (
        "an ordinary run must still be allowed to skip when Falkor is absent:\n"
        + permissive.stdout
    )
    assert "skipped" in permissive.stdout
    assert G30_CERTIFICATION_ENV not in permissive.stdout

    strict = _run_pytest(REQUIRED_SUITE, {G30_CERTIFICATION_ENV: "1"})
    # The exit code is the assertion that matters: a conversion that reports a
    # failure and still exits zero closes nothing, and the G31 revision that set
    # `wasxfail` did exactly that.
    assert strict.returncode != 0, (
        "certification mode allowed a required reader suite to skip, so a "
        "certification run can still be green while proving nothing:\n" + strict.stdout
    )
    assert "failed" in strict.stdout or "error" in strict.stdout.lower()
    assert G30_CERTIFICATION_ENV in strict.stdout


def test_certification_mode_does_not_fail_a_skip_outside_the_required_set() -> None:
    """Strictness is scoped. Turning it on must not break unrelated skips."""
    result = _run_pytest(UNREQUIRED_SUITE, {G30_CERTIFICATION_ENV: "1"})

    assert result.returncode == 0, (
        "certification mode failed a skip outside the required reader set:\n" + result.stdout
    )
    assert "skipped" in result.stdout
    assert G30_CERTIFICATION_ENV not in result.stdout


def test_the_writer_flag_still_works_unchanged() -> None:
    """G31 is frozen. Adding the reader flag must not disturb the writer one."""
    writer_suite = "backend/rbac_backend/tests/integration/test_graph_writer_authority_falkor.py"

    permissive = _run_pytest(writer_suite, {})
    assert permissive.returncode == 0

    strict = _run_pytest(writer_suite, {G31_CERTIFICATION_ENV: "1"})
    assert strict.returncode != 0, (
        "the G31 writer certification mechanism regressed while adding G30's:\n" + strict.stdout
    )
    assert G31_CERTIFICATION_ENV in strict.stdout
