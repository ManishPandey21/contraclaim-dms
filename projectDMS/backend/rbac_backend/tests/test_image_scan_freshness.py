"""A maintenance window may not open on image evidence nobody re-measured.

R-A8V watched the exact CI Trivy policy go red three times in one day on
Dockerfiles that had not changed: the advisory database moved underneath images
that had been clean the day before. So "this image was clean" is a statement
about an instant, and the owner decision recorded in
`docs/IMAGE_FRESHNESS_POLICY.md` turns it into a gate: before every staging or
production maintenance window, every release image must carry a Trivy report
that is no older than 24 hours, is a report about THAT image, and holds zero
fixable CRITICAL/HIGH findings. Anything else is NO-GO.

These tests pin `scripts/check_image_scan_freshness.py`, which decides that from
the JSON reports alone. Each refusal has a "still works" case beside it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check_image_scan_freshness.py"

NOW = datetime(2026, 9, 13, 14, 0, 0, tzinfo=timezone.utc)
IMAGE = "contraclaim-stg-backend:rc1-abc1234"
IMAGE_ID = "sha256:" + "a" * 64
GO = 0
NO_GO = 2


def _vuln(severity: str, fixed: str, status: str | None = None) -> dict:
    return {
        "VulnerabilityID": f"CVE-2026-{abs(hash((severity, fixed))) % 100000}",
        "PkgName": "libexample",
        "InstalledVersion": "1.0",
        "FixedVersion": fixed,
        "Status": status or ("fixed" if fixed else "affected"),
        "Severity": severity,
    }


def _report(
    *,
    name: str = IMAGE,
    image_id: str = IMAGE_ID,
    created: datetime | str | None = None,
    vulns: list[dict] | None = None,
    drop: tuple[str, ...] = (),
) -> dict:
    stamp = created if created is not None else NOW - timedelta(hours=1)
    if isinstance(stamp, datetime):
        # Trivy writes nanoseconds and a literal Z; the gate must read that shape.
        stamp = stamp.strftime("%Y-%m-%dT%H:%M:%S.123456789Z")
    document = {
        "SchemaVersion": 2,
        "CreatedAt": stamp,
        "ArtifactName": name,
        "ArtifactType": "container_image",
        "Metadata": {"ImageID": image_id, "RepoTags": [name]},
        "Results": [
            {
                "Target": f"{name} (debian 13.6)",
                "Class": "os-pkgs",
                "Type": "debian",
                "Vulnerabilities": vulns or [],
            }
        ],
    }
    for key in drop:
        document.pop(key, None)
    return document


def _write(directory: Path, filename: str, document: dict | str) -> Path:
    path = directory / filename
    path.write_text(document if isinstance(document, str) else json.dumps(document), encoding="utf-8")
    return path


def _run(report_dir: Path, *args: str, now: datetime | None = NOW, simulated: bool = True) -> subprocess.CompletedProcess:
    command = [sys.executable, str(SCRIPT), "--report-dir", str(report_dir), *args]
    if now is not None:
        command += ["--now", now.isoformat()]
        if simulated:
            command.append("--allow-simulated-clock")
    return subprocess.run(command, capture_output=True, text=True, timeout=60)


def _require(image: str = IMAGE, image_id: str | None = IMAGE_ID) -> list[str]:
    return ["--require", f"{image}={image_id}" if image_id else image]


# ------------------------------------------------------------------ the GO case


def test_a_fresh_clean_report_for_the_required_image_is_a_go(tmp_path: Path) -> None:
    _write(tmp_path, "backend.trivy.json", _report())
    result = _run(tmp_path, *_require())
    assert result.returncode == GO, result.stdout + result.stderr
    assert result.stdout.startswith("GO")


def test_unfixed_and_lower_severity_findings_do_not_block(tmp_path: Path) -> None:
    """The CI policy is `--ignore-unfixed --severity CRITICAL,HIGH`. The gate
    must not be stricter than the policy it certifies, or it becomes the thing
    people learn to override."""
    _write(
        tmp_path,
        "backend.trivy.json",
        _report(vulns=[_vuln("HIGH", ""), _vuln("CRITICAL", "", "will_not_fix"), _vuln("MEDIUM", "2.0")]),
    )
    result = _run(tmp_path, *_require())
    assert result.returncode == GO, result.stdout + result.stderr


# ------------------------------------------------------------- the findings


@pytest.mark.parametrize("severity", ["HIGH", "CRITICAL"])
def test_a_fixable_critical_or_high_is_a_no_go(tmp_path: Path, severity: str) -> None:
    _write(tmp_path, "backend.trivy.json", _report(vulns=[_vuln(severity, "1.1")]))
    result = _run(tmp_path, *_require())
    assert result.returncode == NO_GO
    assert "fixable" in (result.stdout + result.stderr)


# ------------------------------------------------------------------- the clock


def test_exactly_twenty_four_hours_old_is_still_a_go(tmp_path: Path) -> None:
    # Written without a fraction: the fixture's usual .123456789 would make the
    # stamp younger than the boundary and the case would not sit ON it.
    document = _report(created=(NOW - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _write(tmp_path, "backend.trivy.json", document)
    result = _run(tmp_path, *_require())
    assert result.returncode == GO, result.stdout + result.stderr


def test_one_second_older_than_twenty_four_hours_is_a_no_go(tmp_path: Path) -> None:
    document = _report(created=(NOW - timedelta(hours=24, seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _write(tmp_path, "backend.trivy.json", document)
    result = _run(tmp_path, *_require())
    assert result.returncode == NO_GO
    assert "old" in (result.stdout + result.stderr)


def test_a_short_fraction_is_read_as_tenths_not_microseconds() -> None:
    """`.1` is a tenth of a second. Padding on the wrong side would read it as one
    microsecond; harmless for a 24-hour bound, but a parser that is wrong in the
    small is not trusted in the large."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_image_scan_freshness", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # @dataclass resolves its module through sys.modules while the class body runs.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)

    assert module.parse_stamp("2026-09-13T13:25:39.1Z").microsecond == 100000
    assert module.parse_stamp("2026-09-13T13:25:39.471751434Z").microsecond == 471751
    assert module.parse_stamp("2026-09-13T19:00:00+05:30").hour == 13


def test_a_report_from_the_future_is_a_no_go(tmp_path: Path) -> None:
    _write(tmp_path, "backend.trivy.json", _report(created=NOW + timedelta(hours=2)))
    assert _run(tmp_path, *_require()).returncode == NO_GO


def test_a_timestamp_without_an_offset_is_a_no_go(tmp_path: Path) -> None:
    _write(tmp_path, "backend.trivy.json", _report(created="2026-09-13T13:00:00"))
    assert _run(tmp_path, *_require()).returncode == NO_GO


def test_the_age_bound_cannot_be_loosened(tmp_path: Path) -> None:
    _write(tmp_path, "backend.trivy.json", _report())
    result = _run(tmp_path, *_require(), "--max-age-hours", "48")
    assert result.returncode == NO_GO
    # tightening is allowed
    assert _run(tmp_path, *_require(), "--max-age-hours", "6").returncode == GO


def test_a_simulated_clock_must_be_spelled_out(tmp_path: Path) -> None:
    _write(tmp_path, "backend.trivy.json", _report())
    assert _run(tmp_path, *_require(), simulated=False).returncode == NO_GO


def test_the_real_clock_rejects_a_report_stamped_in_the_past(tmp_path: Path) -> None:
    """Without --now the gate reads the wall clock, so a 2026-09-13 report is
    stale on any run more than a day later. This is the control that the
    freshness check is not a no-op on the real clock."""
    _write(tmp_path, "backend.trivy.json", _report(created=datetime(2020, 1, 1, tzinfo=timezone.utc)))
    assert _run(tmp_path, *_require(), now=None).returncode == NO_GO


# ------------------------------------------------------------ identity


def test_a_required_image_with_no_report_is_a_no_go(tmp_path: Path) -> None:
    _write(tmp_path, "backend.trivy.json", _report())
    result = _run(tmp_path, *_require(), *_require("contraclaim-stg-client:rc1-abc1234", None))
    assert result.returncode == NO_GO
    assert "contraclaim-stg-client:rc1-abc1234" in (result.stdout + result.stderr)


def test_a_report_about_a_different_image_id_is_a_no_go(tmp_path: Path) -> None:
    """A clean report for the tag is not a clean report for the image the tag now
    points at: a rebuild moves the tag and leaves the old report behind."""
    _write(tmp_path, "backend.trivy.json", _report(image_id="sha256:" + "b" * 64))
    result = _run(tmp_path, *_require())
    assert result.returncode == NO_GO
    assert "ImageID" in (result.stdout + result.stderr)


def test_the_image_id_may_be_omitted_but_the_name_must_match(tmp_path: Path) -> None:
    _write(tmp_path, "backend.trivy.json", _report())
    assert _run(tmp_path, *_require(image_id=None)).returncode == GO


def test_two_reports_for_one_image_is_ambiguous_and_a_no_go(tmp_path: Path) -> None:
    _write(tmp_path, "a.trivy.json", _report())
    _write(tmp_path, "b.trivy.json", _report(vulns=[_vuln("HIGH", "1.1")]))
    assert _run(tmp_path, *_require()).returncode == NO_GO


# ------------------------------------------------------ measuring nothing


def test_no_required_image_is_a_no_go(tmp_path: Path) -> None:
    _write(tmp_path, "backend.trivy.json", _report())
    assert _run(tmp_path).returncode == NO_GO


def test_a_report_without_results_measured_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "backend.trivy.json", _report(drop=("Results",)))
    assert _run(tmp_path, *_require()).returncode == NO_GO


def test_a_malformed_report_is_a_no_go(tmp_path: Path) -> None:
    _write(tmp_path, "backend.trivy.json", "{not json")
    assert _run(tmp_path, *_require()).returncode == NO_GO


def test_a_missing_report_directory_is_a_no_go(tmp_path: Path) -> None:
    assert _run(tmp_path / "absent", *_require()).returncode == NO_GO


def test_json_output_carries_the_verdict_per_image(tmp_path: Path) -> None:
    _write(tmp_path, "backend.trivy.json", _report())
    result = _run(tmp_path, *_require(), "--json")
    assert result.returncode == GO
    verdict = json.loads(result.stdout)
    assert verdict["go"] is True
    assert verdict["max_age_hours"] == 24
    [image] = verdict["images"]
    assert image["image"] == IMAGE
    assert image["fixable_critical_high"] == 0
    assert image["go"] is True
