"""Every real-Mongo suite must run in CI, or say in writing why it does not.

The real-Mongo suites skip at import unless their ``*_MONGODB_URI`` variable is
set; CI sets those variables in one step, against a disposable replica set, and
names the modules to run there. Three Contract Master suites (the reprojection
review round, source mutation and generic-ingestion isolation - 48 tests) read a
variable that step exports and were never named in it, and two more (evidence
cutover, provenance) read variables CI never exported at all: every green run
said nothing about any of them.

So the rule is inverted: every ``test_*_mongo.py`` module under ``integration/``
is either run by that step or named in ``NOT_RUN_IN_CI`` with its reason. A new
real-Mongo module is red until someone decides which. These checks also pin the
per-module "ran, not skipped" guard the step relies on.
"""

from __future__ import annotations

import importlib.util
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict, FrozenSet, List, Set

import pytest
import yaml

PROJECT = Path(__file__).resolve().parents[3]
REPO = PROJECT.parent
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
INTEGRATION = PROJECT / "backend" / "rbac_backend" / "tests" / "integration"
GUARD = PROJECT / "scripts" / "assert_suites_ran.py"
STEP = "Run the real-Mongo relationship suites (must run, not skip)"

#: Real-Mongo modules deliberately outside the must-run step, each with why.
#: Present in base e02b71b and never run by any CI job; none exercises the
#: Contract Master runtime. Moving one into the step is welcome - delete its
#: entry and the checks below hold it there.
_TRACERS = (
    "relationship tracer / legacy-backfill suite (pre-existing, never in CI); "
    "outside the Contract Master runtime - open follow-up, not a waiver"
)
NOT_RUN_IN_CI: Dict[str, str] = {
    "test_backfill_cross_role_concurrency_mongo.py": _TRACERS,
    "test_bank_guarantee_event_relationships_mongo.py": _TRACERS,
    "test_bank_guarantee_legacy_backfill_mongo.py": _TRACERS,
    "test_claim_document_relationships_mongo.py": _TRACERS,
    "test_claim_legacy_letter_backfill_mongo.py": _TRACERS,
    "test_claim_legacy_relationship_backfill_mongo.py": _TRACERS,
    "test_insurance_document_relationships_mongo.py": _TRACERS,
    "test_ipc_document_relationships_mongo.py": _TRACERS,
    "test_ipc_legacy_relationship_backfill_mongo.py": _TRACERS,
    "test_key_date_document_relationships_mongo.py": _TRACERS,
    "test_key_date_legacy_backfill_mongo.py": _TRACERS,
    "test_legacy_relationship_backfill_mongo.py": _TRACERS,
}

#: Any gate variable named in a module, however it is read (``os.environ.get``,
#: ``os.getenv``, ``in os.environ``, a constant).
_GATE = re.compile(r"""["']([A-Z0-9_]+_MONGODB_URI)["']""")
#: A module that takes its fixtures (and so its gate) from another real-Mongo module.
_IMPORTS = re.compile(
    r"rbac_backend\.tests\.integration(?:\.|\s+import\s+)\(?\s*(test_\w+_mongo)\b"
)


def _step() -> Dict:
    if not WORKFLOW.is_file():
        pytest.skip(f"{WORKFLOW} is not part of this checkout")
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == STEP:
                return step
    raise AssertionError(f"ci.yml lost the step {STEP!r}")


def _suites(run: str) -> List[str]:
    match = re.search(r"suites=\((.*?)\)", run, re.S)
    assert match, (
        "the must-run step no longer declares its modules in one `suites=( ... )` list"
    )
    return shlex.split(match.group(1))


#: A module that skips unless a ``*_MONGODB_URI`` gate is set, however it is named.
_READS_GATE = re.compile(
    r"""(?:environ\.get|getenv|environ\[)\(?\s*["'][A-Z0-9_]+_MONGODB_URI"""
    r"""|_ENV\s*=\s*["'][A-Z0-9_]+_MONGODB_URI"""
)


def _real_mongo_modules() -> List[Path]:
    """By convention (``integration/**/test_*_mongo.py``) and by behaviour: any
    test module under ``tests/`` that reads a ``*_MONGODB_URI`` gate."""
    named = set(INTEGRATION.rglob("test_*_mongo.py"))
    gated = {
        path
        for path in INTEGRATION.parent.rglob("test_*.py")
        if path.resolve() != Path(__file__).resolve()
        and _READS_GATE.search(path.read_text(encoding="utf-8-sig"))
    }
    return sorted(named | gated)


def _gates(path: Path, seen: FrozenSet[Path] = frozenset()) -> Set[str]:
    """The gate variables of a module, including those it inherits by import."""
    text = path.read_text(encoding="utf-8-sig")
    gates = set(_GATE.findall(text))
    for name in _IMPORTS.findall(text):
        imported = INTEGRATION / f"{name}.py"
        if imported.is_file() and imported not in seen and imported != path:
            gates |= _gates(imported, seen | {path})
    return gates


def test_every_real_mongo_module_is_run_or_says_why_not():
    listed = {Path(p).name for p in _suites(_step()["run"])}
    unaccounted = sorted(
        path.relative_to(INTEGRATION).as_posix()
        for path in _real_mongo_modules()
        if path.name not in listed and path.name not in NOT_RUN_IN_CI
    )
    assert not unaccounted, (
        "real-Mongo modules CI never runs and nobody has said why - add them to the "
        f"must-run step, or to NOT_RUN_IN_CI with a reason: {unaccounted}"
    )


def test_no_module_is_both_run_and_excused():
    listed = {Path(p).name for p in _suites(_step()["run"])}
    assert not listed & set(NOT_RUN_IN_CI), sorted(listed & set(NOT_RUN_IN_CI))
    present = {p.name for p in _real_mongo_modules()}
    assert set(NOT_RUN_IN_CI) <= present, sorted(set(NOT_RUN_IN_CI) - present)


def test_every_real_mongo_module_has_a_gate_this_check_can_read():
    """A module whose gate cannot be classified would pass every rule above
    without anyone knowing what it needs to run."""
    blind = sorted(p.name for p in _real_mongo_modules() if not _gates(p))
    assert not blind, f"no *_MONGODB_URI gate found (directly or by import) in: {blind}"


def test_every_listed_module_exists_and_its_gate_is_exported():
    step = _step()
    exported = set((step.get("env") or {}).keys())
    for path in _suites(step["run"]):
        module = PROJECT / path
        assert module.is_file(), f"ci.yml runs {path}, which does not exist"
        gates = _gates(module)
        assert gates, f"{path} has no *_MONGODB_URI gate this check can read"
        missing = sorted(gates - exported)
        assert not missing, (
            f"{path} is gated on {missing}, which the step does not export: it would skip"
        )


def test_the_same_list_is_run_and_then_proven_to_have_run():
    run = _step()["run"]
    assert re.search(r'pytest[^\n]*--junitxml=(\S+)[^\n]*"\$\{suites\[@\]\}"', run), (
        "pytest no longer runs the declared list with a JUnit report"
    )
    report = re.search(r"--junitxml=(\S+)", run).group(1)
    assert re.search(
        rf'scripts/assert_suites_ran\.py\s+{re.escape(report)}\s+"\$\{{suites\[@\]\}}"',
        run,
    ), "the per-module ran-not-skipped guard no longer checks the declared list"


def test_the_reprojection_suites_are_in_the_list():
    listed = {Path(p).name for p in _suites(_step()["run"])}
    for name in (
        "test_contract_reprojection_worker_mongo.py",
        "test_contract_reprojection_runtime_mongo.py",
        "test_contract_reprojection_review3_mongo.py",
        "test_contract_source_mutation_mongo.py",
        "test_contract_generic_ingestion_isolation_mongo.py",
        "test_contract_evidence_cutover_mongo.py",
        "test_contract_provenance_mongo.py",
    ):
        assert name in listed, name


# --------------------------------------------------------------------------- #
# the guard itself
# --------------------------------------------------------------------------- #

A = "backend/rbac_backend/tests/integration/test_a_mongo.py"
B = "backend/rbac_backend/tests/integration/test_b_mongo.py"


def _guard():
    spec = importlib.util.spec_from_file_location("assert_suites_ran", GUARD)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _report(tmp_path: Path, cases: str) -> str:
    path = tmp_path / "report.xml"
    path.write_text(
        f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{cases}</testsuite></testsuites>',
        encoding="utf-8",
    )
    return str(path)


def test_the_guard_passes_when_every_module_ran(tmp_path):
    report = _report(
        tmp_path,
        '<testcase classname="backend.rbac_backend.tests.integration.test_a_mongo" name="test_x"/>'
        '<testcase classname="backend.rbac_backend.tests.integration.test_b_mongo.TestB" name="test_y"/>',
    )
    assert _guard().problems(report, [A, B]) == []


def test_the_guard_fails_a_module_that_never_ran(tmp_path):
    report = _report(
        tmp_path,
        '<testcase classname="backend.rbac_backend.tests.integration.test_a_mongo" name="test_x"/>',
    )
    assert _guard().problems(report, [A, B]) == [f"{B}: no test ran"]


def test_the_guard_fails_a_module_that_skipped(tmp_path):
    report = _report(
        tmp_path,
        '<testcase classname="backend.rbac_backend.tests.integration.test_a_mongo" name="test_x"/>'
        '<testcase classname="backend.rbac_backend.tests.integration.test_b_mongo" name="test_b_mongo">'
        '<skipped message="B_MONGODB_URI is not set"/></testcase>',
    )
    found = _guard().problems(report, [A, B])
    assert f"{B}: 1 test(s) skipped" in found


def test_the_guard_never_matches_a_module_by_a_name_suffix(tmp_path):
    report = _report(
        tmp_path,
        '<testcase classname="backend.rbac_backend.tests.integration.xtest_a_mongo" name="test_x"/>',
    )
    assert _guard().problems(report, [A]) == [f"{A}: no test ran"]


def test_the_guard_reads_a_real_pytest_report_of_a_module_level_skip(tmp_path):
    """The shape pytest actually writes for ``pytest.skip(allow_module_level=True)``
    - the way every real-Mongo module skips - must read as skipped, not ran."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "test_gated_mongo.py").write_text(
        "import pytest\n"
        "pytest.skip('GATED_MONGODB_URI is not set', allow_module_level=True)\n"
        "def test_never():\n    pass\n",
        encoding="utf-8",
    )
    (package / "test_open_mongo.py").write_text(
        "def test_runs():\n    pass\n", encoding="utf-8"
    )
    report = tmp_path / "report.xml"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={report}",
            "pkg",
        ],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    found = _guard().problems(
        str(report), ["pkg/test_gated_mongo.py", "pkg/test_open_mongo.py"]
    )
    assert any(line.startswith("pkg/test_gated_mongo.py:") for line in found), found
    assert not any(line.startswith("pkg/test_open_mongo.py:") for line in found), found
