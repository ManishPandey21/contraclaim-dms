"""The G31 writer certification invocation cannot be green while proving nothing.

`GRAPH-GATES.md` U5: every Falkor suite skips when the engine is unreachable, so
a certification run could report success with zero real-infrastructure evidence.
The mechanism that closes it is `G31_WRITER_CERTIFICATION=1` plus the hook in
`backend/rbac_backend/tests/conftest.py`, which turns a skip inside a required
writer suite into a failure.

A mechanism nobody tests is a comment. These tests pin three separate things:

* the required set is REAL - every listed file exists, and every one of them is
  also an accepted Authority Band regression, so it cannot be deleted silently;
* the classifier is exact - it recognises the required files and nothing else,
  including a same-named file in another directory;
* the conversion actually happens - a real pytest subprocess over a REQUIRED
  real-engine suite, pointed at a closed port so it takes its own
  engine-unreachable skip path, EXITS ZERO normally and NON-ZERO in
  certification mode. That is the only assertion here that proves the harness
  rather than the intention.

Certification mode is not the default and must not become it: ordinary runs skip
when Falkor is absent, which is what makes the band usable day to day.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from rbac_backend.tests.authority_band_graph import (
    G31_CERTIFICATION_ENV,
    G31_REQUIRED_WRITER_FILES,
    g31_certification_mode,
    g31_skip_is_certification_failure,
    is_g31_required_file,
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


def test_the_required_writer_set_is_not_empty_and_has_no_duplicates() -> None:
    assert G31_REQUIRED_WRITER_FILES, "G31 has no required writer suites"
    duplicates = sorted(
        {name for name in G31_REQUIRED_WRITER_FILES if G31_REQUIRED_WRITER_FILES.count(name) > 1}
    )
    assert not duplicates, f"duplicate entries in the G31 required writer set: {duplicates}"


def test_every_required_writer_suite_exists() -> None:
    missing = [name for name in G31_REQUIRED_WRITER_FILES if not (REPO_ROOT / name).is_file()]
    assert not missing, (
        f"G31 requires writer suites that do not exist: {missing}. "
        "Restore the file, or remove it from the required set in the same change "
        "and say why in the handoff."
    )


def test_every_required_writer_suite_is_an_accepted_authority_band_regression() -> None:
    """A required suite that is not in the band can be deleted without a failure.

    The band guard is what stops an accepted regression disappearing; requiring
    a file here without registering it there would give G31 a dependency nothing
    protects.
    """
    registered = {entry.replace("\\", "/") for entry in _manifest_entries()}
    unregistered = [
        name for name in G31_REQUIRED_WRITER_FILES if name.replace("\\", "/") not in registered
    ]
    assert not unregistered, (
        "G31 required writer suites that are not in AUTHORITY_BAND.txt: "
        f"{unregistered}. Register them, or the band cannot protect what G31 depends on."
    )


def test_the_classifier_recognises_every_required_suite() -> None:
    for name in G31_REQUIRED_WRITER_FILES:
        assert is_g31_required_file(name), name
        assert is_g31_required_file(str(REPO_ROOT / name)), name
        assert is_g31_required_file(str(REPO_ROOT / name).replace("/", "\\")), name


def test_the_classifier_rejects_a_file_outside_the_required_set() -> None:
    assert not is_g31_required_file("backend/rbac_backend/tests/test_authority_band_manifest.py")
    assert not is_g31_required_file("")
    # A same-named file in a different tree must not satisfy the requirement:
    # the suffix match is anchored on the full repo-relative path.
    assert not is_g31_required_file("somewhere/else/test_falkor_ro_query.py")


def test_a_skip_is_only_a_failure_in_certification_mode(monkeypatch) -> None:
    required = G31_REQUIRED_WRITER_FILES[0]

    monkeypatch.delenv(G31_CERTIFICATION_ENV, raising=False)
    assert not g31_certification_mode()
    assert not g31_skip_is_certification_failure(required, "skipped")

    monkeypatch.setenv(G31_CERTIFICATION_ENV, "1")
    assert g31_certification_mode()
    assert g31_skip_is_certification_failure(required, "skipped")
    # Only skips are converted; a pass stays a pass and a failure stays a failure.
    assert not g31_skip_is_certification_failure(required, "passed")
    assert not g31_skip_is_certification_failure(required, "failed")
    # And only for required files.
    assert not g31_skip_is_certification_failure(
        "backend/rbac_backend/tests/test_authority_band_manifest.py", "skipped"
    )


#: A port nothing listens on, so the required suite takes its real
#: engine-unreachable skip path rather than a synthetic one.
UNREACHABLE_FALKOR_PORT = "6399"

#: The required real-engine suite used for the harness proof, and a file that is
#: deliberately NOT in the required set, so scoping can be shown as well.
REQUIRED_SUITE = "backend/rbac_backend/tests/integration/test_graph_writer_authority_falkor.py"
UNREQUIRED_SUITE = "backend/rbac_backend/tests/integration/test_graph_invalidation_falkor.py"


def _run_pytest(target: str, env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
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
    """The decision function is useless unless something applies it everywhere.

    Placing it in `tests/conftest.py` is what makes it load for every test under
    that directory, integration suites included, without editing any of them.
    """
    conftest = TESTS_DIR / "conftest.py"
    assert conftest.is_file(), "the G31 certification hook has no conftest to load it"
    source = conftest.read_text(encoding="utf-8")
    assert "pytest_runtest_makereport" in source
    assert "g31_skip_is_certification_failure" in source


def test_certification_mode_turns_a_required_suite_skip_into_a_failure() -> None:
    """The end-to-end proof, in a real subprocess, on a real required suite.

    Everything above asserts the decision function. This asserts the HARNESS:
    with FalkorDB unreachable, the ordinary run is GREEN and proves nothing,
    and the certification run is RED. Without this the mechanism could be
    correct and unwired, which is exactly the failure U5 describes.
    """
    permissive = _run_pytest(REQUIRED_SUITE, {})
    assert permissive.returncode == 0, (
        "an ordinary run must still be allowed to skip when Falkor is absent:\n"
        + permissive.stdout
    )
    assert "skipped" in permissive.stdout
    assert G31_CERTIFICATION_ENV not in permissive.stdout

    strict = _run_pytest(REQUIRED_SUITE, {G31_CERTIFICATION_ENV: "1"})
    # The exit code is the assertion that matters: a conversion that reports a
    # failure and still exits zero closes nothing, and an earlier revision of
    # the hook did exactly that by setting `wasxfail`.
    assert strict.returncode != 0, (
        "certification mode allowed a required writer suite to skip, so a "
        "certification run can still be green while proving nothing:\n" + strict.stdout
    )
    # A fixture-level skip is converted during setup and therefore surfaces as
    # an error rather than a call failure; both are non-green, which is the
    # property being pinned.
    assert "failed" in strict.stdout or "error" in strict.stdout.lower()
    assert G31_CERTIFICATION_ENV in strict.stdout


def test_certification_mode_does_not_fail_a_skip_outside_the_required_set() -> None:
    """Strictness is scoped. Turning it on must not break unrelated skips."""
    result = _run_pytest(UNREQUIRED_SUITE, {G31_CERTIFICATION_ENV: "1"})

    assert result.returncode == 0, (
        "certification mode failed a skip outside the required writer set:\n" + result.stdout
    )
    assert "skipped" in result.stdout
    assert G31_CERTIFICATION_ENV not in result.stdout
