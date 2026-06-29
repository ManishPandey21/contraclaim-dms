#!/usr/bin/env python
"""Calculate the current production-readiness score from the release gate.

The score is intentionally evidence-based: unchecked launch-gate items receive
no credit even when implementation work exists but staging/CI/live proof has not
been captured yet.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GATE_FILE = ROOT / "docs" / "PRODUCTION_READINESS_RELEASE_GATE.md"

GATE_WEIGHTS = {
    "Gate 1: CI And Local Test Baseline": 15,
    "Gate 2: Live Integration Baseline": 10,
    "Gate 3: Browser E2E Coverage": 12,
    "Gate 4: Security And RBAC": 15,
    "Gate 5: Upload And Content Safety": 10,
    "Gate 6: Database, Migrations, And Seeds": 10,
    "Gate 7: Deployment And Environment": 10,
    "Gate 8: Backup, Restore, And Rollback": 10,
    "Gate 9: Final Production Readiness Review": 8,
}


def _section(lines: List[str], start_heading: str, stop_pattern: str) -> List[str]:
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == start_heading:
            start = idx + 1
            break
    if start is None:
        return []

    stop_re = re.compile(stop_pattern)
    end = len(lines)
    for idx in range(start, len(lines)):
        if stop_re.match(lines[idx].strip()):
            end = idx
            break
    return lines[start:end]


def _parse_gate_sections(lines: List[str]) -> List[Dict[str, Any]]:
    launch_lines = _section(lines, "## Launch Gates", r"^## ")
    gates: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None

    for line in launch_lines:
        heading = line.strip()
        if heading.startswith("### Gate "):
            if current is not None:
                gates.append(current)
            current = {"name": heading[4:], "checked": 0, "total": 0, "pending": []}
            continue
        if current is None:
            continue

        match = re.match(r"^- \[([ xX])\] (.+)$", line.strip())
        if not match:
            continue
        current["total"] += 1
        if match.group(1).lower() == "x":
            current["checked"] += 1
        else:
            current["pending"].append(match.group(2))

    if current is not None:
        gates.append(current)
    return gates


def _parse_phase_pending(lines: List[str]) -> Dict[str, List[str]]:
    pending_by_phase: Dict[str, List[str]] = {}
    current_phase: str | None = None

    for line in lines:
        heading = line.strip()
        phase_match = re.match(r"^## (Phase \d+) Acceptance Status$", heading)
        if phase_match:
            current_phase = phase_match.group(1)
            pending_by_phase.setdefault(current_phase, [])
            continue
        if heading.startswith("## "):
            current_phase = None
            continue
        if current_phase is None:
            continue
        pending_match = re.match(r"^- \[ \] (.+)$", heading)
        if pending_match:
            pending_by_phase[current_phase].append(pending_match.group(1))

    return pending_by_phase


def calculate(gate_file: Path = DEFAULT_GATE_FILE) -> Dict[str, Any]:
    lines = gate_file.read_text(encoding="utf-8").splitlines()
    gates = _parse_gate_sections(lines)
    score = 0.0
    gate_scores = []
    for gate in gates:
        weight = GATE_WEIGHTS.get(gate["name"], 0)
        ratio = (gate["checked"] / gate["total"]) if gate["total"] else 0.0
        points = weight * ratio
        score += points
        gate_scores.append(
            {
                "gate": gate["name"],
                "checked": gate["checked"],
                "total": gate["total"],
                "weight": weight,
                "points": round(points, 2),
                "pending": gate["pending"],
            }
        )

    rounded_score = round(score)
    verdict = "Ready" if rounded_score >= 85 else "Ready with Conditions" if rounded_score >= 70 else "Not Ready"
    return {
        "score": rounded_score,
        "raw_score": round(score, 2),
        "target": 85,
        "verdict": verdict,
        "gate_scores": gate_scores,
        "pending_by_phase": _parse_phase_pending(lines),
    }


def _print_text(result: Dict[str, Any]) -> None:
    print(f"Production readiness score: {result['score']}/100")
    print(f"Verdict: {result['verdict']}")
    print(f"Target: {result['target']}/100")
    print()
    print("Gate scores:")
    for gate in result["gate_scores"]:
        print(
            f"- {gate['gate']}: {gate['points']}/{gate['weight']} "
            f"({gate['checked']}/{gate['total']} checked)"
        )
    print()
    print("Pending blockers by phase:")
    for phase, blockers in result["pending_by_phase"].items():
        if blockers:
            print(f"- {phase}: {len(blockers)} pending")
        else:
            print(f"- {phase}: none")


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate production readiness score from release gate checkboxes.")
    parser.add_argument("--gate-file", type=Path, default=DEFAULT_GATE_FILE)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    result = calculate(args.gate_file)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_text(result)


if __name__ == "__main__":
    main()
