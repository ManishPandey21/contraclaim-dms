"""Check a golden set and report its records-only baseline (4B-42).

    python scripts/skill_golden_set.py check FILE
    python scripts/skill_golden_set.py baseline FILE [--json]

``check`` validates the file, prints its hash and every reason it cannot support an
approval (synthetic matters, composition, label provenance), and exits 1 if there are any.
``baseline`` scores every matter on the pack's deterministic fallback - no model is
called - and prints the result. A model run is made on the server (4B-41), never here.
Run from projectDMS with the backend environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main() -> int:
    from rbac_backend.services.skills.evaluation import EvalSuite, fallback_baseline, suite_hash, suite_problems
    from rbac_backend.services.skills.system_skills import system_skill_version

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["check", "baseline"])
    parser.add_argument("file", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    suite = EvalSuite.model_validate(json.loads(args.file.read_text(encoding="utf-8")))
    if args.command == "check":
        problems = suite_problems(suite)
        print(f"{suite.suite_key}: {len(suite.matters)} matters, hash {suite_hash(suite)}")
        for problem in problems:
            print(f"  - {problem}")
        return 1 if problems else 0
    schema_key = system_skill_version(suite.skill_key).manifest.output_schema_key
    report = fallback_baseline(suite, schema_key)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        scores = report["scores"]
        print(f"{report['suite_key']} ({report['matters']} matters, hash {report['suite_hash'][:16]}): "
              f"{'passed' if report['passed'] else 'did not pass'}")
        print(f"  element agreement {scores['element_agreement']:.2f}; classification "
              f"{scores['classification_agreement']}; degraded {scores['degraded_rate']:.2f}")
        for failure in report["failures"]:
            print(f"  - {failure}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
