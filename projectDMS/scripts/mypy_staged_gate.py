#!/usr/bin/env python3
"""Run mypy and fail only on findings the accepted baseline does not contain.

Why a gate rather than a plain mypy hook: repairing the pre-commit file scope
turned mypy on for the first time and it found a historical backlog. Making CI
green by disabling error codes or excluding files would have hidden it. This
gate keeps mypy running over exactly the same files, keeps every remaining
finding visible in the log, and fails the moment a NEW one appears.

The comparison is a subset test, not equality:

    current findings  ⊆  accepted baseline      -> pass
    anything outside the baseline               -> fail

so fixing historical debt never breaks the build, and the baseline may only
ever be made smaller by ordinary work.

Findings are normalised to (path, error code, message) with the line number
dropped, so moving code around is not reported as a new error. Identical
findings are counted, and a count above the baseline's count fails.

Regenerate deliberately, never in CI:

    python projectDMS/scripts/mypy_staged_gate.py --regenerate

Exit codes:
    0  no new findings
    1  new findings, or a malformed baseline
    2  mypy itself could not run (configuration, environment, crash)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, List, Tuple

Finding = Tuple[str, str, str]  # (path, code, message)

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = Path(__file__).resolve().parents[1] / "backend" / "mypy-baseline.txt"

ERROR_RX = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?:\d+:)? error: (?P<msg>.*?)\s*\[(?P<code>[a-z-]+)\]$")
SUMMARY_RX = re.compile(r"^(Found \d+ error|Success: no issues found)", re.M)
SEP = "\t"


def _normalise(raw: str) -> List[Finding]:
    findings: List[Finding] = []
    for line in raw.splitlines():
        m = ERROR_RX.match(line.strip())
        if not m:
            continue
        path = m.group("path").replace("\\", "/")
        findings.append((path, m.group("code"), m.group("msg")))
    return findings


def _run_mypy(paths: Iterable[str]) -> Tuple[str, int]:
    files = list(paths)
    if not files:
        return "", 0
    # The same flags mirrors-mypy passed before this gate replaced it, so the
    # finding set is a property of the code and not of the invocation.
    proc = subprocess.run(
        [sys.executable, "-m", "mypy", "--ignore-missing-imports", "--scripts-are-modules", *files],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return proc.stdout + proc.stderr, proc.returncode


def _load_baseline() -> Counter:
    if not BASELINE.is_file():
        raise SystemExit(f"baseline not found: {BASELINE}\nrun with --regenerate to create it")
    counts: Counter = Counter()
    for number, line in enumerate(BASELINE.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split(SEP)
        if len(parts) != 4:
            raise SystemExit(
                f"{BASELINE.name}:{number}: expected 'count<TAB>path<TAB>code<TAB>message', "
                f"got {len(parts)} field(s). The file is machine-generated; "
                f"regenerate it rather than editing by hand."
            )
        count, path, code, message = parts
        if not count.isdigit() or int(count) < 1:
            raise SystemExit(f"{BASELINE.name}:{number}: count must be a positive integer, got {count!r}")
        if not path or not code or not message:
            raise SystemExit(f"{BASELINE.name}:{number}: empty field in {line!r}")
        counts[(path, code, message)] += int(count)
    return counts


def _write_baseline(counts: Counter, merge_base: str) -> None:
    header = [
        "# Accepted historical mypy findings. MACHINE-GENERATED - do not hand-edit.",
        "#",
        "# Every entry here was present before this release branch existed. The gate",
        "# in scripts/mypy_staged_gate.py fails on any finding NOT listed, and passes",
        "# when the list only shrinks, so this file is a ceiling and never a target.",
        "#",
        f"# Provenance: findings common to this branch and merge-base {merge_base}.",
        "# Regenerate: python projectDMS/scripts/mypy_staged_gate.py --regenerate",
        "#",
        "# Format: count<TAB>path<TAB>error-code<TAB>message",
        "",
    ]
    body = [
        SEP.join((str(n), path, code, message))
        for (path, code, message), n in sorted(counts.items())
    ]
    BASELINE.write_text("\n".join(header + body) + "\n", encoding="utf-8")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regenerate", action="store_true", help="rewrite the baseline from the current tree")
    parser.add_argument("--merge-base", default="unknown", help="commit recorded as the baseline's provenance")
    parser.add_argument("files", nargs="*", help="files to check (pre-commit supplies these)")
    args = parser.parse_args(argv)

    raw, returncode = _run_mypy(args.files)
    if not args.files:
        print("MYPY STAGED GATE: no files in scope, nothing to check")
        return 0

    if not SUMMARY_RX.search(raw or "") and returncode != 0:
        print("MYPY EXECUTION FAILURE - mypy did not complete. This is not baselined debt.")
        print(raw.strip()[:4000])
        return 2

    current = Counter(_normalise(raw))

    if args.regenerate:
        _write_baseline(current, args.merge_base)
        print(f"wrote {BASELINE} with {sum(current.values())} findings")
        return 0

    baseline = _load_baseline()
    new: Counter = Counter()
    for finding, count in current.items():
        excess = count - baseline.get(finding, 0)
        if excess > 0:
            new[finding] = excess

    remaining = sum(current.values())
    improved = sum(max(0, n - current.get(f, 0)) for f, n in baseline.items())

    if new:
        print(f"MYPY STAGED GATE FAILED - {sum(new.values())} NEW ERROR(S) OUTSIDE THE ACCEPTED BASELINE:")
        for (path, code, message), count in sorted(new.items()):
            print(f"  {count}x {path} [{code}] {message}")
        print("\nFix them, or - if the finding is genuinely pre-existing - regenerate")
        print("the baseline in its own reviewed commit.")
        return 1

    print(f"MYPY STAGED GATE PASSED - 0 NEW ERRORS, {remaining} ACCEPTED HISTORICAL FINDINGS REMAIN.")
    if improved:
        print(f"  ({improved} baseline finding(s) no longer reproduce - regenerate to bank the improvement)")
    for (path, code, message), count in sorted(current.items()):
        print(f"  baselined: {count}x {path} [{code}] {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
