#!/usr/bin/env python3
"""Backend dependency gate for R4: a time-bound PyMongo advisory exception.

R4 (projectDMS/docs/NOFIX_ADVISORY_ACCEPTANCE.md, tracking issue #46): four
advisories against `pymongo==4.16.0` are fixed only in 4.18.1/4.18.2, and no
RELEASED `langgraph-checkpoint-mongodb` - which the live letter-drafting engine
imports - permits that line. The owner accepted exactly these four ids, scoped
to that one pinned version, until EXPIRES. This does NOT fix them.

pip-audit's own `--ignore-vuln` flags (one per id, in .github/workflows/ci.yml)
and `.github/trivy/pymongo-r4.trivyignore.yaml` are what let the scans pass;
`test_ci_static_gates.py` pins both to exactly these ids. This gate keeps the
exception honest, failing closed when:

- the date passes EXPIRES (UTC, inclusive): reassess or upgrade, never extend
  silently;
- the pinned pymongo is no longer the accepted vulnerable version (an upgrade
  landed: remove the exception in the same change), or the pin cannot be read;
- a RELEASED `langgraph-checkpoint-mongodb` on PyPI permits pymongo>=4.18.2
  (the unblock condition: upgrade instead of continuing the exception), or the
  release metadata cannot be read with certainty. GitHub main does not count.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

# --- the one exception (R4) -------------------------------------------------------------
ADVISORIES = (
    "CVE-2026-88029",  # GHSA-8fvv-fgr5-f8ch  GridFS id match            fixed 4.18.1
    "CVE-2026-96747",  # GHSA-qx36-8mw2-4r3x  CSFLE KMS .sock endpoint   fixed 4.18.2
    "CVE-2026-96748",  # GHSA-vp6j-j7w5-5xjj  URI host injection         fixed 4.18.2
    "CVE-2026-96749",  # GHSA-v4x9-3549-crwv  BSON encode size overflow  fixed 4.18.2
)
#: The two HIGH ids Trivy reports at CRITICAL,HIGH; the mediums are below its threshold.
TRIVY_ADVISORIES = ("CVE-2026-96748", "CVE-2026-96749")
PACKAGE = "pymongo"
ACCEPTED_VERSION = "4.16.0"
FIXED_VERSION = Version("4.18.2")
CHECKPOINT_PACKAGE = "langgraph-checkpoint-mongodb"
EXPIRES = dt.date(2026, 11, 5)  # last day the exception applies (UTC, inclusive)
TRACKING_ISSUE = "ManishPandey21/contraclaim-dms#46"

REQUIREMENTS = Path("projectDMS/backend/rbac_backend/requirements.txt")
PYPI_JSON = "https://pypi.org/pypi/{name}/json"

Fetcher = Callable[[str], Any]


class GateFailure(Exception):
    pass


def check_not_expired(today: dt.date) -> None:
    if today > EXPIRES:
        raise GateFailure(
            "PyMongo temporary security exception expired; reassess or upgrade "
            f"(R4 expired {EXPIRES.isoformat()}, see {TRACKING_ISSUE})"
        )


def pinned_version(requirements_text: str) -> str:
    pins = re.findall(rf"^{PACKAGE}==(\S+)\s*$", requirements_text, flags=re.MULTILINE | re.IGNORECASE)
    if len(pins) != 1:
        raise GateFailure(f"expected exactly one '{PACKAGE}==' pin in {REQUIREMENTS}, found {len(pins)}")
    return pins[0]


def check_pin_is_the_accepted_one(requirements_text: str) -> None:
    pinned = pinned_version(requirements_text)
    if pinned != ACCEPTED_VERSION:
        raise GateFailure(
            f"{PACKAGE} is pinned to {pinned}, not the accepted {ACCEPTED_VERSION}; "
            f"R4 covers only {ACCEPTED_VERSION}. If this is the upgrade, remove the "
            f"exception in the same change ({TRACKING_ISSUE})."
        )


def _released_versions(meta: Any) -> Dict[str, Any]:
    releases = meta.get("releases") if isinstance(meta, dict) else None
    if not isinstance(releases, dict) or not releases:
        raise GateFailure(f"could not read the released {CHECKPOINT_PACKAGE} versions")
    return releases


def _pymongo_specifier(release_meta: Any) -> Optional[SpecifierSet]:
    info = release_meta.get("info") if isinstance(release_meta, dict) else None
    if not isinstance(info, dict):
        raise GateFailure(f"could not read {CHECKPOINT_PACKAGE} release metadata")
    for requirement in info.get("requires_dist") or []:
        if not isinstance(requirement, str) or "extra ==" in requirement:
            continue
        match = re.match(rf"^\s*{PACKAGE}\s*(?:\[[^\]]*\])?\s*\(?([^;)]*)\)?", requirement, re.IGNORECASE)
        if match:
            try:
                return SpecifierSet(match.group(1).strip())
            except InvalidSpecifier as exc:
                raise GateFailure(f"unreadable {PACKAGE} requirement {requirement!r}") from exc
    return None  # no pymongo requirement at all


def check_no_compatible_checkpoint_release(fetch: Fetcher) -> None:
    """Fail when any non-yanked, final release permits pymongo >= FIXED_VERSION."""
    releases = _released_versions(fetch(PYPI_JSON.format(name=CHECKPOINT_PACKAGE)))
    compatible = []
    for text, files in releases.items():
        try:
            version = Version(text)
        except InvalidVersion as exc:
            raise GateFailure(f"unrecognised {CHECKPOINT_PACKAGE} version {text!r}") from exc
        if version.is_prerelease or version.is_devrelease:
            continue  # not a release we would ship
        if not files or all(isinstance(f, dict) and f.get("yanked") for f in files):
            continue  # nothing installable
        meta = fetch(f"https://pypi.org/pypi/{CHECKPOINT_PACKAGE}/{text}/json")
        specifier = _pymongo_specifier(meta)
        if specifier is None or specifier.contains(FIXED_VERSION, prereleases=False):
            compatible.append(text)
    if compatible:
        raise GateFailure(
            f"{CHECKPOINT_PACKAGE} {', '.join(sorted(compatible, key=Version))} is released and "
            f"permits {PACKAGE}>={FIXED_VERSION}: upgrade both and remove the R4 exception "
            f"instead of continuing it ({TRACKING_ISSUE})"
        )


def _fetch_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed https URL
        return json.loads(response.read().decode("utf-8"))


def run_gate(
    repo_root: Path,
    *,
    today: Optional[dt.date] = None,
    fetch: Optional[Fetcher] = None,
) -> str:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    check_not_expired(today)
    try:
        text = (repo_root / REQUIREMENTS).read_text(encoding="utf-8")
    except OSError as exc:
        raise GateFailure(f"cannot read {REQUIREMENTS}: {exc}") from exc
    check_pin_is_the_accepted_one(text)
    try:
        check_no_compatible_checkpoint_release(fetch or _fetch_json)
    except GateFailure:
        raise
    except Exception as exc:  # network, JSON - an unreadable answer is not "no release"
        raise GateFailure(f"could not check {CHECKPOINT_PACKAGE} releases: {exc}") from exc
    return (
        f"R4 PyMongo exception in force for {', '.join(ADVISORIES)} on {PACKAGE}=={ACCEPTED_VERSION}; "
        f"expires {EXPIRES.isoformat()} ({(EXPIRES - today).days} days); "
        f"no released {CHECKPOINT_PACKAGE} permits {PACKAGE}>={FIXED_VERSION} yet ({TRACKING_ISSUE})"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        print(run_gate(args.repo_root))
    except GateFailure as exc:
        print(f"::error::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
