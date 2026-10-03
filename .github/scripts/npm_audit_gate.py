#!/usr/bin/env python3
"""Frontend dependency gate: `npm audit --audit-level=high`, plus ONE time-bound
exception.

R3 (projectDMS/docs/NOFIX_ADVISORY_ACCEPTANCE.md): GHSA-vfj7-8cjw-p6xm /
CVE-2026-93687, a stack-exhaustion DoS in `braces` <=3.0.3 with no patched
release. `braces` reaches the client only through build/test tooling
(tailwindcss, typescript-eslint, @types/jest); the deployed client image
carries no npm package at all. The owner accepted it until EXPIRES.

This is deliberately not a general ignore list. Exactly one advisory, one
package, one installed version and one expiry are hard-coded, and the gate
fails - closed - on anything else:

- any CRITICAL finding;
- any HIGH finding whose root advisory is not this one (npm reports the
  packages that depend on braces as HIGH too; each must resolve to it alone);
- the advisory's identity changing (package, GHSA, npm source id, range);
- an installed braces other than the accepted lineage;
- the date passing EXPIRES;
- a braces release newer than the affected range being published (upgrade
  instead of extending the exception);
- npm audit failing to run, or answering with anything but a well-formed
  report.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# --- the one exception (R3) -------------------------------------------------------------
GHSA = "GHSA-vfj7-8cjw-p6xm"
CVE = "CVE-2026-93687"  # recorded with the GHSA; npm's report carries only the GHSA
PACKAGE = "braces"
NPM_SOURCE_ID = 1240992
AFFECTED_RANGE = "<=3.0.3"
INSTALLED_VERSION = "3.0.3"
EXPIRES = dt.date(2026, 10, 10)  # last day the exception applies (UTC)

BANNER = (
    "\n" + "!" * 78 + "\n"
    f"TEMPORARY OWNER-APPROVED SECURITY EXCEPTION:\n"
    f"{GHSA} ({CVE}, {PACKAGE} {AFFECTED_RANGE}, no patched release)\n"
    f"expires {EXPIRES.isoformat()}\n"
    "Every other HIGH or CRITICAL finding still fails this gate.\n" + "!" * 78
)

Runner = Callable[[Sequence[str], Path], Tuple[int, str, str]]


class GateFailure(Exception):
    """The gate refuses; the message says why."""


def _run(cmd: Sequence[str], cwd: Path) -> Tuple[int, str, str]:
    proc = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _is_exception_advisory(advisory: Dict[str, Any]) -> bool:
    return (
        str(advisory.get("url", "")).rstrip("/").endswith("/" + GHSA)
        and advisory.get("name") == PACKAGE
        and advisory.get("dependency") == PACKAGE
        and advisory.get("source") == NPM_SOURCE_ID
        and advisory.get("range") == AFFECTED_RANGE
    )


def _root_advisories(
    vulnerabilities: Dict[str, Any], name: str, seen: Optional[set] = None
) -> List[Dict[str, Any]]:
    """The advisory objects a vulnerable package resolves to through `via`."""
    seen = set() if seen is None else seen
    if name in seen:
        return []
    seen.add(name)
    node = vulnerabilities.get(name)
    if not isinstance(node, dict) or not isinstance(node.get("via"), list):
        raise GateFailure(f"malformed audit entry for {name!r}")
    roots: List[Dict[str, Any]] = []
    for via in node["via"]:
        if isinstance(via, str):
            if via not in vulnerabilities:
                raise GateFailure(f"{name!r} points at {via!r}, which the report lacks")
            roots.extend(_root_advisories(vulnerabilities, via, seen))
        elif isinstance(via, dict):
            roots.append(via)
        else:
            raise GateFailure(f"malformed 'via' entry under {name!r}")
    return roots


def evaluate_report(report: Any) -> bool:
    """True when the exception was needed (and is valid); False when the report
    has no HIGH/CRITICAL at all. Raises GateFailure otherwise."""
    if not isinstance(report, dict):
        raise GateFailure("npm audit output is not a JSON object")
    if "error" in report:
        raise GateFailure(f"npm audit reported an error: {report['error']!r}")
    if report.get("auditReportVersion") != 2:
        raise GateFailure(
            f"unexpected auditReportVersion {report.get('auditReportVersion')!r}"
        )
    vulnerabilities = report.get("vulnerabilities")
    counts = (report.get("metadata") or {}).get("vulnerabilities")
    if not isinstance(vulnerabilities, dict) or not isinstance(counts, dict):
        raise GateFailure("npm audit report lacks vulnerabilities/metadata")
    if int(counts.get("critical", 0) or 0) > 0:
        raise GateFailure(f"{counts['critical']} CRITICAL finding(s)")

    exception_used = False
    for name, node in vulnerabilities.items():
        if not isinstance(node, dict):
            raise GateFailure(f"malformed audit entry for {name!r}")
        severity = node.get("severity")
        if severity == "critical":
            raise GateFailure(f"CRITICAL finding: {name}")
        roots = _root_advisories(vulnerabilities, name)
        if not roots:
            raise GateFailure(f"{name!r} resolves to no advisory")
        serious = [r for r in roots if r.get("severity") in ("high", "critical")]
        for root in serious:
            if root.get("severity") == "critical" or not _is_exception_advisory(root):
                raise GateFailure(
                    f"HIGH/CRITICAL advisory not covered by the exception: {name} <- "
                    f"{root.get('name')} {root.get('url')} ({root.get('severity')})"
                )
        if severity == "high":
            # A HIGH package must be HIGH because of the excepted advisory alone.
            if not serious:
                raise GateFailure(
                    f"{name} is HIGH but no HIGH root advisory explains it"
                )
            exception_used = True
        elif severity not in ("info", "low", "moderate"):
            raise GateFailure(f"unknown severity {severity!r} for {name}")

    reported_high = int(counts.get("high", 0) or 0)
    if reported_high and not exception_used:
        raise GateFailure(
            f"metadata reports {reported_high} HIGH finding(s) the report does not explain"
        )
    if exception_used and PACKAGE not in vulnerabilities:
        raise GateFailure(
            f"the excepted package {PACKAGE!r} is missing from the report"
        )
    return exception_used


def check_installed_lineage(lockfile: Dict[str, Any]) -> None:
    """Every installed braces is the accepted version."""
    packages = lockfile.get("packages")
    if not isinstance(packages, dict):
        raise GateFailure("package-lock.json has no 'packages' map")
    versions = {
        path: (meta or {}).get("version")
        for path, meta in packages.items()
        if path == f"node_modules/{PACKAGE}"
        or path.endswith(f"/node_modules/{PACKAGE}")
    }
    if not versions:
        raise GateFailure(f"no installed {PACKAGE} in package-lock.json")
    wrong = {p: v for p, v in versions.items() if v != INSTALLED_VERSION}
    if wrong:
        raise GateFailure(
            f"installed {PACKAGE} is not the accepted {INSTALLED_VERSION}: {wrong}"
        )


def _version_tuple(version: str) -> Optional[Tuple[int, int, int]]:
    core = version.split("+", 1)[0]
    if "-" in core:  # a pre-release is not a patched release
        return None
    parts = core.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def check_no_patched_release(published: Any) -> None:
    """Fail when braces has published a release above the affected range."""
    if isinstance(published, str):
        published = [published]
    if not isinstance(published, list) or not published:
        raise GateFailure("could not read the published braces versions")
    ceiling = _version_tuple(INSTALLED_VERSION)
    newer = [
        v
        for v in published
        if (t := _version_tuple(str(v))) is not None and t > ceiling
    ]
    if newer:
        raise GateFailure(
            f"{PACKAGE} {', '.join(newer)} is published; upgrade it instead of "
            f"continuing the {GHSA} exception"
        )


def check_not_expired(today: dt.date) -> None:
    if today > EXPIRES:
        raise GateFailure(f"the {GHSA} exception expired on {EXPIRES.isoformat()}")


def run_gate(
    client_dir: Path,
    *,
    runner: Optional[Runner] = None,
    today: Optional[dt.date] = None,
    raw_out: Optional[Path] = None,
) -> str:
    """Run the gate; returns the success message, raises GateFailure."""
    runner = runner or _run
    today = today or dt.datetime.now(dt.timezone.utc).date()
    try:
        code, out, err = runner(["npm", "audit", "--json"], client_dir)
    except OSError as exc:
        raise GateFailure(f"npm audit could not run: {exc}") from exc
    if raw_out is not None:
        raw_out.write_text(out or "", encoding="utf-8")
    if code not in (0, 1):  # 1 = vulnerabilities found; anything else is a failure
        raise GateFailure(f"npm audit exited {code}: {err.strip()[:500]}")
    try:
        report = json.loads(out)
    except (TypeError, ValueError) as exc:
        raise GateFailure(f"npm audit output is not JSON: {exc}") from exc

    if not evaluate_report(report):
        return "npm audit: no HIGH or CRITICAL findings; the exception was not needed"

    check_not_expired(today)
    try:
        lockfile = json.loads(
            (client_dir / "package-lock.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise GateFailure(f"cannot read package-lock.json: {exc}") from exc
    check_installed_lineage(lockfile)
    try:
        code, out, err = runner(
            ["npm", "view", PACKAGE, "versions", "--json"], client_dir
        )
    except OSError as exc:
        raise GateFailure(f"npm view could not run: {exc}") from exc
    if code != 0:
        raise GateFailure(
            f"npm view {PACKAGE} versions exited {code}: {err.strip()[:500]}"
        )
    try:
        published = json.loads(out)
    except (TypeError, ValueError) as exc:
        raise GateFailure(f"npm view output is not JSON: {exc}") from exc
    check_no_patched_release(published)
    return BANNER


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--client-dir", type=Path, default=Path("."))
    parser.add_argument(
        "--raw-out", type=Path, default=None, help="write npm audit's raw JSON here"
    )
    args = parser.parse_args(argv)
    try:
        print(run_gate(args.client_dir, raw_out=args.raw_out))
    except GateFailure as exc:
        print(f"npm audit gate FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
