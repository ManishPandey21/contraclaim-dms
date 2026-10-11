#!/usr/bin/env python3
"""Refuse a maintenance window whose image evidence is stale, foreign or red.

Owner decision R-A8W (docs/IMAGE_FRESHNESS_POLICY.md): before every staging or
production maintenance window, every release image must carry a Trivy report
that is no older than 24 hours, is about that exact image, and holds zero
fixable CRITICAL/HIGH findings. Any other state is NO-GO.

    # one JSON report per image, produced with the exact CI policy:
    #   trivy image --ignore-unfixed --severity CRITICAL,HIGH --exit-code 1 \
    #       --format json --output <dir>/<name>.trivy.json <image>
    python3 scripts/check_image_scan_freshness.py \
        --report-dir /var/backups/contraclaim-stg-evidence/<run>/trivy \
        --require contraclaim-stg-backend:rc1-abc1234=sha256:<ImageID> \
        --require contraclaim-stg-client:rc1-abc1234=sha256:<ImageID>

Exit codes: 0 GO, 2 NO-GO, 2 for anything the gate cannot decide. There is no
exit code that means "probably fine".

What it decides from the reports alone, and what it cannot:

* It counts fixable CRITICAL/HIGH findings itself rather than trusting the
  scanner's exit code, so a report copied from a red run cannot read as green.
* It cannot see the flags a report was produced with. A report generated with a
  narrower `--severity` would hide findings; the runbook therefore records the
  scan command beside the reports, and this gate is not a substitute for that.
* Stdlib only: it runs with the host's python3, where the application's
  dependencies are deliberately absent.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

GO = 0
NO_GO = 2

POLICY_MAX_AGE_HOURS = 24
# A report stamped a little ahead of this host's clock is clock skew between two
# containers on one machine; one stamped hours ahead is not a measurement.
FUTURE_SKEW = timedelta(minutes=5)
BLOCKING_SEVERITIES = frozenset({"CRITICAL", "HIGH"})
REPORT_GLOB = "*.trivy.json"

_STAMP = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d+))?(?P<tz>Z|[+-]\d{2}:\d{2})$"
)


class GateError(Exception):
    """An input the gate cannot decide on."""


@dataclass
class ImageVerdict:
    image: str
    go: bool
    report: str | None = None
    created_at: str | None = None
    age_seconds: int | None = None
    image_id: str | None = None
    fixable_critical_high: int | None = None
    reasons: list[str] = field(default_factory=list)


def parse_stamp(value: object) -> datetime:
    """Trivy writes `2026-09-13T13:25:39.471751434Z`: nanoseconds and a literal Z,
    neither of which `datetime.fromisoformat` accepts before Python 3.11."""
    if not isinstance(value, str):
        raise GateError(f"CreatedAt is {value!r}, not a timestamp")
    match = _STAMP.match(value.strip())
    if not match:
        raise GateError(f"CreatedAt {value!r} is not an ISO-8601 timestamp with an explicit UTC offset")
    fraction = (match.group("frac") or "0")[:6].ljust(6, "0")
    tz = "+00:00" if match.group("tz") == "Z" else match.group("tz")
    return datetime.fromisoformat(f"{match.group('base')}.{fraction}{tz}").astimezone(timezone.utc)


def parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateError(f"--now {value!r} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise GateError(f"--now {value!r} carries no UTC offset")
    return parsed.astimezone(timezone.utc)


def parse_requirement(value: str) -> tuple[str, str | None]:
    name, _, image_id = value.partition("=")
    name, image_id = name.strip(), image_id.strip()
    if not name:
        raise GateError(f"--require {value!r} names no image")
    if image_id and not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise GateError(f"--require {value!r}: the image id must be sha256:<64 hex>")
    return name, image_id or None


def fixable_blocking_findings(report: dict) -> int:
    results = report.get("Results")
    if not isinstance(results, list):
        raise GateError("the report carries no Results list, so the scan measured nothing")
    count = 0
    for result in results:
        for vulnerability in (result or {}).get("Vulnerabilities") or []:
            severity = str(vulnerability.get("Severity", "")).upper()
            fixable = bool(str(vulnerability.get("FixedVersion") or "").strip()) or (
                str(vulnerability.get("Status", "")).lower() == "fixed"
            )
            if severity in BLOCKING_SEVERITIES and fixable:
                count += 1
    return count


def load_reports(report_dir: pathlib.Path) -> dict[str, list[tuple[pathlib.Path, dict]]]:
    if not report_dir.is_dir():
        raise GateError(f"report directory {report_dir} does not exist")
    by_image: dict[str, list[tuple[pathlib.Path, dict]]] = {}
    for path in sorted(report_dir.glob(REPORT_GLOB)):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GateError(f"{path.name} is not a readable JSON report: {exc}") from exc
        if not isinstance(document, dict):
            raise GateError(f"{path.name} is not a Trivy JSON report")
        by_image.setdefault(str(document.get("ArtifactName", "")), []).append((path, document))
    return by_image


def decide_image(
    name: str,
    expected_id: str | None,
    candidates: list[tuple[pathlib.Path, dict]],
    *,
    now: datetime,
    max_age: timedelta,
) -> ImageVerdict:
    verdict = ImageVerdict(image=name, go=False)
    if not candidates:
        verdict.reasons.append(f"no {REPORT_GLOB} report names {name}")
        return verdict
    if len(candidates) > 1:
        verdict.reasons.append(
            f"{len(candidates)} reports name {name} ({', '.join(p.name for p, _ in candidates)}); "
            "which one is current is not something the gate will guess"
        )
        return verdict

    path, report = candidates[0]
    verdict.report = path.name
    try:
        if report.get("SchemaVersion") != 2:
            raise GateError(f"SchemaVersion {report.get('SchemaVersion')!r} is not the Trivy v2 JSON schema")
        if report.get("ArtifactType") != "container_image":
            raise GateError(f"ArtifactType {report.get('ArtifactType')!r} is not a container image")
        created = parse_stamp(report.get("CreatedAt"))
        verdict.created_at = created.isoformat()
        age = now - created
        verdict.age_seconds = int(age.total_seconds())
        verdict.image_id = (report.get("Metadata") or {}).get("ImageID")
        verdict.fixable_critical_high = fixable_blocking_findings(report)
    except GateError as exc:
        verdict.reasons.append(str(exc))
        return verdict

    if age > max_age:
        verdict.reasons.append(
            f"report is {age} old, older than the {max_age} the policy allows; re-scan before the window"
        )
    if age < -FUTURE_SKEW:
        verdict.reasons.append(f"report is stamped {-age} in the future; it is not a measurement of now")
    if expected_id and verdict.image_id != expected_id:
        verdict.reasons.append(
            f"report ImageID {verdict.image_id} is not the required {expected_id}; "
            "the tag has moved since this scan"
        )
    if verdict.fixable_critical_high:
        verdict.reasons.append(
            f"{verdict.fixable_critical_high} fixable CRITICAL/HIGH finding(s); a fix exists and the image does not carry it"
        )
    verdict.go = not verdict.reasons
    return verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report-dir", required=True, help=f"Directory holding one {REPORT_GLOB} per image.")
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        help="IMAGE[=sha256:ImageID]. Repeat for every release image the window will run.",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=POLICY_MAX_AGE_HOURS,
        help=f"May be tightened, never loosened past {POLICY_MAX_AGE_HOURS}.",
    )
    parser.add_argument("--now", help="Override the clock. Testing and replay only.")
    parser.add_argument("--allow-simulated-clock", action="store_true", help="Required with --now.")
    parser.add_argument("--json", action="store_true", help="Machine-readable verdict.")
    args = parser.parse_args(argv)

    def refuse(reason: str) -> int:
        print("NO-GO", file=sys.stderr)
        print(f"  the image-freshness gate cannot decide: {reason}", file=sys.stderr)
        return NO_GO

    if args.now and not args.allow_simulated_clock:
        return refuse("--now was supplied without --allow-simulated-clock; a fabricated instant is a GO nobody measured")
    if not args.require:
        return refuse("no --require was given, so there is no image to certify and the gate would measure nothing")
    if not 0 < args.max_age_hours <= POLICY_MAX_AGE_HOURS:
        return refuse(f"--max-age-hours {args.max_age_hours} is outside (0, {POLICY_MAX_AGE_HOURS}]")

    try:
        now = parse_now(args.now)
        requirements = [parse_requirement(value) for value in args.require]
        reports = load_reports(pathlib.Path(args.report_dir))
    except GateError as exc:
        return refuse(str(exc))

    max_age = timedelta(hours=args.max_age_hours)
    verdicts = [
        decide_image(name, expected_id, reports.get(name, []), now=now, max_age=max_age)
        for name, expected_id in requirements
    ]
    go = all(verdict.go for verdict in verdicts)

    if args.json:
        print(
            json.dumps(
                {
                    "go": go,
                    "now": now.isoformat(),
                    "max_age_hours": args.max_age_hours if args.max_age_hours % 1 else int(args.max_age_hours),
                    "images": [asdict(verdict) for verdict in verdicts],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print("GO" if go else "NO-GO")
        print(f"  now          : {now.isoformat()}")
        print(f"  max age      : {args.max_age_hours} h")
        for verdict in verdicts:
            state = "GO   " if verdict.go else "NO-GO"
            print(
                f"  {state} {verdict.image}  report={verdict.report} created={verdict.created_at} "
                f"image_id={verdict.image_id} fixable_critical_high={verdict.fixable_critical_high}"
            )
            for reason in verdict.reasons:
                print(f"        - {reason}")
    return GO if go else NO_GO


if __name__ == "__main__":
    raise SystemExit(main())
