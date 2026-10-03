"""The braces exception gate passes exactly one advisory, until its expiry.

The base fixture is the real `npm audit --json` of projectDMS/client on
2026-10-03 (17 HIGH, all resolving to GHSA-vfj7-8cjw-p6xm; 3 MODERATE).
Every case mutates a copy of it.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import npm_audit_gate as gate  # noqa: E402

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "npm_audit_braces_2026-10-03.json"
)
BEFORE_EXPIRY = dt.date(2026, 10, 3)
LOCKFILE = {
    "lockfileVersion": 3,
    "packages": {
        "": {"name": "client"},
        "node_modules/braces": {"version": "3.0.3"},
        "node_modules/micromatch": {"version": "4.0.8"},
    },
}
PUBLISHED = ["2.3.2", "3.0.0", "3.0.1", "3.0.2", "3.0.3"]


def _report() -> Dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _client(tmp_path: Path, lockfile: Dict[str, Any] = LOCKFILE) -> Path:
    (tmp_path / "package-lock.json").write_text(json.dumps(lockfile), encoding="utf-8")
    (tmp_path / "src").mkdir(exist_ok=True)
    return tmp_path


class _Npm:
    """Stands in for npm: one answer for `audit`, one for `view`."""

    def __init__(
        self,
        audit: Any,
        *,
        audit_code: int = 1,
        view: Any = PUBLISHED,
        view_code: int = 0,
    ):
        self.audit = audit if isinstance(audit, str) else json.dumps(audit)
        self.audit_code = audit_code
        self.view = view if isinstance(view, str) else json.dumps(view)
        self.view_code = view_code
        self.calls: List[Tuple[str, ...]] = []

    def __call__(self, cmd: Sequence[str], cwd: Path) -> Tuple[int, str, str]:
        self.calls.append(tuple(cmd))
        if list(cmd[:2]) == ["npm", "audit"]:
            return self.audit_code, self.audit, "audit stderr"
        if list(cmd[:2]) == ["npm", "view"]:
            return self.view_code, self.view, "view stderr"
        raise AssertionError(cmd)


def _gate(
    tmp_path: Path, npm: _Npm, today: dt.date = BEFORE_EXPIRY, lockfile=LOCKFILE
) -> str:
    return gate.run_gate(_client(tmp_path, lockfile), runner=npm, today=today)


def _add_high(
    report: Dict[str, Any], name: str, ghsa: str, *, severity: str = "high"
) -> None:
    report["vulnerabilities"][name] = {
        "name": name,
        "severity": severity,
        "isDirect": False,
        "via": [
            {
                "source": 1999999,
                "name": name,
                "dependency": name,
                "title": "unrelated advisory",
                "url": f"https://github.com/advisories/{ghsa}",
                "severity": severity,
                "range": "<9.9.9",
            }
        ],
        "effects": [],
        "range": "<9.9.9",
        "nodes": [f"node_modules/{name}"],
        "fixAvailable": True,
    }
    report["metadata"]["vulnerabilities"][severity] += 1


# 1 --------------------------------------------------------------------------------------


def test_the_current_braces_advisory_alone_passes_with_the_exception_banner(tmp_path):
    message = _gate(tmp_path, _Npm(_report()))
    assert "TEMPORARY OWNER-APPROVED SECURITY EXCEPTION" in message
    assert "GHSA-vfj7-8cjw-p6xm" in message and "expires 2026-10-10" in message


def test_the_last_day_still_passes(tmp_path):
    assert "EXCEPTION" in _gate(tmp_path, _Npm(_report()), today=dt.date(2026, 10, 10))


def test_a_clean_report_passes_without_the_exception(tmp_path):
    clean = {
        "auditReportVersion": 2,
        "vulnerabilities": {},
        "metadata": {
            "vulnerabilities": {
                "info": 0,
                "low": 0,
                "moderate": 0,
                "high": 0,
                "critical": 0,
                "total": 0,
            }
        },
    }
    npm = _Npm(clean, audit_code=0)
    message = _gate(tmp_path, npm)
    assert "EXCEPTION" not in message
    assert not any(c[:2] == ("npm", "view") for c in npm.calls)


# 2 / 3 ----------------------------------------------------------------------------------


def test_braces_plus_an_unrelated_high_fails(tmp_path):
    report = _report()
    _add_high(report, "left-pad", "GHSA-aaaa-bbbb-cccc")
    with pytest.raises(gate.GateFailure, match="not covered by the exception"):
        _gate(tmp_path, _Npm(report))


def test_a_high_hidden_behind_a_braces_dependent_fails(tmp_path):
    """A package npm marks HIGH through braces AND through another HIGH root."""
    report = _report()
    _add_high(report, "left-pad", "GHSA-aaaa-bbbb-cccc")
    report["vulnerabilities"]["micromatch"]["via"].append("left-pad")
    with pytest.raises(gate.GateFailure, match="not covered by the exception"):
        _gate(tmp_path, _Npm(report))


def test_any_critical_fails(tmp_path):
    report = _report()
    _add_high(report, "left-pad", "GHSA-aaaa-bbbb-cccc", severity="critical")
    with pytest.raises(gate.GateFailure, match="CRITICAL"):
        _gate(tmp_path, _Npm(report))


def test_a_package_marked_critical_fails_even_through_the_excepted_advisory(tmp_path):
    """Each CRITICAL check stands alone: here only the package's own severity
    says CRITICAL (the metadata count and its root advisory do not)."""
    report = _report()
    report["vulnerabilities"]["micromatch"]["severity"] = "critical"
    with pytest.raises(gate.GateFailure, match="CRITICAL"):
        _gate(tmp_path, _Npm(report))


def test_braces_itself_turning_critical_fails(tmp_path):
    report = _report()
    report["vulnerabilities"]["braces"]["severity"] = "critical"
    report["vulnerabilities"]["braces"]["via"][0]["severity"] = "critical"
    with pytest.raises(gate.GateFailure, match="CRITICAL"):
        _gate(tmp_path, _Npm(report))


# 4 / 5 : identity -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, value",
    [
        ("url", "https://github.com/advisories/GHSA-vfj7-8cjw-p6xx"),  # wrong GHSA
        ("source", 1240993),  # a re-issued advisory
        ("range", "<=3.0.4"),  # widened range
        ("name", "braces-fork"),  # wrong package
        ("dependency", "micromatch"),  # wrong package
    ],
)
def test_a_changed_advisory_identity_fails(tmp_path, field, value):
    report = _report()
    report["vulnerabilities"]["braces"]["via"][0][field] = value
    with pytest.raises(gate.GateFailure, match="not covered by the exception"):
        _gate(tmp_path, _Npm(report))


def test_the_same_ghsa_on_another_package_fails(tmp_path):
    report = _report()
    advisory = copy.deepcopy(report["vulnerabilities"]["braces"]["via"][0])
    advisory["name"] = advisory["dependency"] = "minimatch"
    report["vulnerabilities"]["minimatch"] = {
        "name": "minimatch",
        "severity": "high",
        "isDirect": False,
        "via": [advisory],
        "effects": [],
        "range": "*",
        "nodes": ["node_modules/minimatch"],
        "fixAvailable": False,
    }
    with pytest.raises(gate.GateFailure, match="not covered by the exception"):
        _gate(tmp_path, _Npm(report))


def test_an_installed_braces_outside_the_accepted_lineage_fails(tmp_path):
    lockfile = copy.deepcopy(LOCKFILE)
    lockfile["packages"]["node_modules/fast-glob/node_modules/braces"] = {
        "version": "2.3.2"
    }
    with pytest.raises(gate.GateFailure, match="not the accepted 3.0.3"):
        _gate(tmp_path, _Npm(_report()), lockfile=lockfile)


def test_no_installed_braces_fails(tmp_path):
    lockfile = {"lockfileVersion": 3, "packages": {"": {}}}
    with pytest.raises(gate.GateFailure, match="no installed braces"):
        _gate(tmp_path, _Npm(_report()), lockfile=lockfile)


# 6 / 7 : expiry and a patched release ----------------------------------------------------


def test_after_expiry_fails(tmp_path):
    with pytest.raises(gate.GateFailure, match="expired on 2026-10-10"):
        _gate(tmp_path, _Npm(_report()), today=dt.date(2026, 10, 11))


@pytest.mark.parametrize(
    "published", [PUBLISHED + ["3.0.4"], PUBLISHED + ["3.1.0"], PUBLISHED + ["4.0.0"]]
)
def test_a_patched_braces_release_fails_and_demands_the_upgrade(tmp_path, published):
    with pytest.raises(gate.GateFailure, match="upgrade it instead"):
        _gate(tmp_path, _Npm(_report(), view=published))


def test_a_pre_release_is_not_a_patched_release(tmp_path):
    assert "EXCEPTION" in _gate(
        tmp_path, _Npm(_report(), view=PUBLISHED + ["3.0.4-beta.1"])
    )


def test_unreadable_published_versions_fail_closed(tmp_path):
    with pytest.raises(gate.GateFailure, match="npm view"):
        _gate(tmp_path, _Npm(_report(), view="", view_code=1))
    with pytest.raises(gate.GateFailure, match="not JSON"):
        _gate(tmp_path, _Npm(_report(), view="E404 <html>"))


# 8 / 9 : malformed output and npm failure ------------------------------------------------


@pytest.mark.parametrize(
    "output",
    [
        "",
        "not json",
        "[]",
        json.dumps(
            {
                "auditReportVersion": 1,
                "vulnerabilities": {},
                "metadata": {"vulnerabilities": {}},
            }
        ),
        json.dumps({"auditReportVersion": 2, "metadata": {"vulnerabilities": {}}}),
        json.dumps(
            {
                "auditReportVersion": 2,
                "vulnerabilities": {"braces": {"severity": "high", "via": ["ghost"]}},
                "metadata": {"vulnerabilities": {"high": 1}},
            }
        ),
        json.dumps(
            {
                "auditReportVersion": 2,
                "vulnerabilities": {"x": {"severity": "high", "via": [42]}},
                "metadata": {"vulnerabilities": {"high": 1}},
            }
        ),
        json.dumps(
            {
                "auditReportVersion": 2,
                "vulnerabilities": {},
                "metadata": {"vulnerabilities": {"high": 3, "critical": 0}},
            }
        ),
    ],
)
def test_malformed_audit_output_fails_closed(tmp_path, output):
    with pytest.raises(gate.GateFailure):
        _gate(tmp_path, _Npm(output))


def test_an_npm_error_report_fails_closed(tmp_path):
    error = {"error": {"code": "ENOTFOUND", "summary": "registry unreachable"}}
    with pytest.raises(gate.GateFailure, match="reported an error"):
        _gate(tmp_path, _Npm(error, audit_code=1))


@pytest.mark.parametrize("code", [2, 127, -9])
def test_an_npm_audit_command_failure_fails_closed(tmp_path, code):
    with pytest.raises(gate.GateFailure, match="exited"):
        _gate(tmp_path, _Npm(_report(), audit_code=code))


def test_npm_missing_fails_closed(tmp_path):
    def absent(cmd, cwd):
        raise FileNotFoundError("npm")

    with pytest.raises(gate.GateFailure, match="could not run"):
        gate.run_gate(_client(tmp_path), runner=absent, today=BEFORE_EXPIRY)


# the CLI -----------------------------------------------------------------------------------


def test_main_returns_nonzero_on_failure_and_writes_the_raw_report(
    tmp_path, monkeypatch, capsys
):
    report = _report()
    _add_high(report, "left-pad", "GHSA-aaaa-bbbb-cccc")
    monkeypatch.setattr(gate, "_run", _Npm(report))
    raw = tmp_path / "raw.json"
    _client(tmp_path)
    assert gate.main(["--client-dir", str(tmp_path), "--raw-out", str(raw)]) == 1
    assert "FAILED" in capsys.readouterr().err
    assert (
        json.loads(raw.read_text(encoding="utf-8"))["metadata"]["vulnerabilities"][
            "high"
        ]
        == 18
    )


# --- the runtime non-exposure basis (security review MEDIUM) ------------------------------


def test_a_new_package_reaching_braces_fails(tmp_path):
    """The acceptance rests on WHICH packages bring braces in. A new dependent
    (say a runtime library that bundles micromatch) is a new exposure question."""
    report = _report()
    dependent = copy.deepcopy(report["vulnerabilities"]["micromatch"])
    dependent["name"] = "glob-runtime-lib"
    dependent["nodes"] = ["node_modules/glob-runtime-lib"]
    dependent["via"] = ["micromatch"]
    report["vulnerabilities"]["glob-runtime-lib"] = dependent
    report["metadata"]["vulnerabilities"]["high"] += 1
    report["metadata"]["vulnerabilities"]["total"] += 1
    with pytest.raises(gate.GateFailure, match="glob-runtime-lib"):
        _gate(tmp_path, _Npm(report))


def test_a_shrinking_dependent_set_still_passes(tmp_path):
    """Dropping a path to braces (e.g. upgrading typescript-eslint) is safe."""
    report = _report()
    for name in [n for n in report["vulnerabilities"] if "typescript-eslint" in n]:
        del report["vulnerabilities"][name]
        report["metadata"]["vulnerabilities"]["high"] -= 1
        report["metadata"]["vulnerabilities"]["total"] -= 1
    assert "EXCEPTION" in _gate(tmp_path, _Npm(report))


@pytest.mark.parametrize(
    "line",
    [
        'import micromatch from "micromatch";',
        "import { expand } from 'braces';",
        'const chokidar = require("chokidar");',
        'const fg = await import("fast-glob");',
        'export { default } from "tailwindcss/lib/util";',
        'import plugin from "@typescript-eslint/parser";',
    ],
)
def test_application_source_importing_the_chain_fails(tmp_path, line):
    client = _client(tmp_path)
    (client / "src" / "lib").mkdir(parents=True)
    (client / "src" / "lib" / "glob.ts").write_text(
        f"{line}\nexport const x = 1;\n", encoding="utf-8"
    )
    with pytest.raises(gate.GateFailure, match="src/lib/glob.ts"):
        gate.run_gate(client, runner=_Npm(_report()), today=BEFORE_EXPIRY)


def test_application_source_naming_braces_innocently_passes(tmp_path):
    client = _client(tmp_path)
    (client / "src" / "parse.ts").write_text(
        "let braces = 0;\n// micromatch is not used\n", encoding="utf-8"
    )
    assert "EXCEPTION" in gate.run_gate(
        client, runner=_Npm(_report()), today=BEFORE_EXPIRY
    )


def test_a_missing_application_source_tree_fails_closed(tmp_path):
    client = _client(tmp_path)
    (client / "src").rmdir()
    with pytest.raises(gate.GateFailure, match="src"):
        gate.run_gate(client, runner=_Npm(_report()), today=BEFORE_EXPIRY)


# --- review LOWs ------------------------------------------------------------------------------


@pytest.mark.parametrize("severity", ["High", None, "severe"])
def test_an_unknown_root_advisory_severity_fails_closed(tmp_path, severity):
    report = _report()
    advisory = {
        "source": 7,
        "name": "micromatch",
        "dependency": "micromatch",
        "title": "x",
        "url": "https://github.com/advisories/GHSA-zzzz-zzzz-zzzz",
        "range": "*",
    }
    if severity is not None:
        advisory["severity"] = severity
    report["vulnerabilities"]["micromatch"]["via"].append(advisory)
    with pytest.raises(gate.GateFailure, match="severity"):
        _gate(tmp_path, _Npm(report))


@pytest.mark.parametrize("field, value", [("high", 999), ("moderate", 0), ("total", 1)])
def test_metadata_counts_must_match_the_report(tmp_path, field, value):
    report = _report()
    report["metadata"]["vulnerabilities"][field] = value
    with pytest.raises(gate.GateFailure, match="metadata"):
        _gate(tmp_path, _Npm(report))


def test_an_aliased_braces_install_is_part_of_the_lineage(tmp_path):
    lockfile = copy.deepcopy(LOCKFILE)
    lockfile["packages"]["node_modules/bx"] = {"name": "braces", "version": "2.3.2"}
    with pytest.raises(gate.GateFailure, match="not the accepted 3.0.3"):
        _gate(tmp_path, _Npm(_report()), lockfile=lockfile)


@pytest.mark.parametrize(
    "published", [PUBLISHED + ["v3.0.4"], PUBLISHED + [3.1], PUBLISHED + ["3.0.4.1"]]
)
def test_an_unparseable_published_version_fails_closed(tmp_path, published):
    with pytest.raises(gate.GateFailure, match="unrecognised"):
        _gate(tmp_path, _Npm(_report(), view=published))
