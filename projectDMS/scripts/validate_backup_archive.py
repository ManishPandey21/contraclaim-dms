#!/usr/bin/env python3
"""Prove one backup archive is a recovery artefact, not merely a file.

    scripts/validate_backup_archive.py ARCHIVE --profile redis-persistence
    scripts/validate_backup_archive.py ARCHIVE --require '*a' --require '*b'
    scripts/validate_backup_archive.py ARCHIVE --any-of  '*a' --any-of  '*b'

`--require` is a conjunction: every pattern must be satisfied. `--any-of` is a
disjunction. They are separate flags precisely because the two used to be the
same flag, and the runbook read the disjunction as a conjunction for months.

Exit codes: 0 VALID, 1 the archive fails its contract, 2 the contract could not
be evaluated. There is no exit code that means "probably fine".
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from rbac_backend.services.backup_archive_validation import (  # noqa: E402
    PROFILES,
    ArchiveValidationError,
    validate_archive,
)

VALID_EXIT = 0
INVALID_EXIT = 1
UNDECIDABLE_EXIT = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("archive")
    parser.add_argument(
        "--profile",
        help=f"Semantic contract to apply. One of: {', '.join(sorted(PROFILES))}.",
    )
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="GLOB",
        help="Every occurrence must match a non-empty file in the archive.",
    )
    parser.add_argument(
        "--any-of",
        action="append",
        default=[],
        dest="any_of",
        metavar="GLOB",
        help="At least one occurrence must match a non-empty file in the archive.",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable verdict.")
    args = parser.parse_args(argv)

    try:
        verdict = validate_archive(
            args.archive,
            profile=args.profile,
            require=tuple(args.require),
            any_of=tuple(args.any_of),
        )
    except ArchiveValidationError as exc:
        print(f"UNDECIDABLE: {exc}", file=sys.stderr)
        return UNDECIDABLE_EXIT

    if args.json:
        print(json.dumps(verdict.as_dict(), indent=2, sort_keys=True))
    else:
        stream = sys.stdout if verdict.ok else sys.stderr
        print(f"{verdict.status}: {verdict.path}", file=stream)
        print(f"  contract: {verdict.contract}", file=stream)
        print(f"  {verdict.detail}", file=stream)
        if not verdict.ok and verdict.members:
            print(f"  archive holds {verdict.member_count} entry/entries:", file=stream)
            for name in verdict.members:
                print(f"    {name}", file=stream)

    return VALID_EXIT if verdict.ok else INVALID_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
