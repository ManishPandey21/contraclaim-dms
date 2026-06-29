#!/usr/bin/env python3
"""Check local backup freshness for release gates and cron monitors."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from rbac_backend.services.operations_health import DEFAULT_VOLUME_LABELS, build_backup_health  # noqa: E402


def _labels(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_VOLUME_LABELS)
    return [item.strip() for item in raw.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Contraclaim backup freshness.")
    parser.add_argument("--root", default=os.getenv("BACKUP_ROOT", "/var/backups/contractdms"))
    parser.add_argument("--max-age-hours", type=int, default=int(os.getenv("BACKUP_MAX_AGE_HOURS", "26")))
    parser.add_argument(
        "--volume-labels",
        default=os.getenv("BACKUP_REQUIRED_VOLUME_LABELS", ",".join(DEFAULT_VOLUME_LABELS)),
        help="Comma-separated required volume archive labels.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    parser.add_argument("--warn-only", action="store_true", help="Always exit zero after reporting status.")
    args = parser.parse_args()

    result = build_backup_health(
        args.root,
        max_age_hours=args.max_age_hours,
        required_volume_labels=_labels(args.volume_labels),
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Backup status: {result['status']}")
        print(f"Backup root: {result['backup_root']}")
        print(f"Max age hours: {result['max_age_hours']}")
        print(f"Latest required artifact age hours: {result['latest_backup_age_hours']}")
        for label, artifact in result["artifacts"].items():
            detail = artifact.get("path") or "not found"
            age = artifact.get("age_hours")
            age_text = f", age={age}h" if age is not None else ""
            print(f"- {label}: {artifact['status']} ({detail}{age_text})")

    return 0 if args.warn_only or result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
