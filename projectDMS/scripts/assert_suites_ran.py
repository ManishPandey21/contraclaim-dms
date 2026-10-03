#!/usr/bin/env python3
"""Fail unless every named test module actually ran, according to a JUnit report.

A green pytest exit proves only that nothing failed. A module whose environment
gate is unset skips at import, and one dropped from the command line never
runs at all; both read as green. This reads the report pytest wrote and
requires, for every module named on the command line, at least one executed
test case and no skipped one.

    python scripts/assert_suites_ran.py report.xml path/to/test_a.py path/to/test_b.py
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Tuple


def _module_of(case: ET.Element) -> str:
    """The dotted module of a test case, from its ``classname``.

    pytest writes ``pkg.mod`` for a module-level test and ``pkg.mod.Class`` for a
    method. A module-level skip is written with an empty ``classname``; it then
    matches no module, so that module reads as "no test ran".
    """
    parts = (case.get("classname") or "").split(".")
    while parts and parts[-1][:1].isupper():
        parts.pop()
    return ".".join(parts)


def _dotted(path: str) -> str:
    return ".".join(PurePosixPath(path.replace("\\", "/")).with_suffix("").parts)


def _same(dotted_path: str, module: str) -> bool:
    return bool(module) and (
        dotted_path == module or dotted_path.endswith("." + module)
    )


def tally(report: str) -> Tuple[Counter, Counter]:
    ran: Counter = Counter()
    skipped: Counter = Counter()
    for case in ET.parse(report).getroot().iter("testcase"):
        module = _module_of(case)
        if case.find("skipped") is not None:
            skipped[module] += 1
        else:
            ran[module] += 1
    return ran, skipped


def problems(report: str, modules: Iterable[str]) -> List[str]:
    ran, skipped = tally(report)
    found: List[str] = []
    for path in modules:
        dotted = _dotted(path)
        # The report names a module by the import path pytest derived for it,
        # which may be shorter than the file path (rootdir / package roots).
        matches: Dict[str, int] = {m: n for m, n in ran.items() if _same(dotted, m)}
        skips = sum(n for m, n in skipped.items() if _same(dotted, m))
        if skips:
            found.append(f"{path}: {skips} test(s) skipped")
        if not matches:
            found.append(f"{path}: no test ran")
    return found


def main(argv: List[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    report, modules = argv[1], argv[2:]
    found = problems(report, modules)
    for line in found:
        print(f"NOT RUN: {line}", file=sys.stderr)
    if found:
        return 1
    ran, _ = tally(report)
    print(f"all {len(modules)} module(s) ran: {sum(ran.values())} test case(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
