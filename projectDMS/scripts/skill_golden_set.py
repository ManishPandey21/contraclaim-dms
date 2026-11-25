"""Golden sets for the claims and EOT packs (4B-42): check, baseline, and the reviewer workflow.

    python scripts/skill_golden_set.py check FILE
    python scripts/skill_golden_set.py baseline FILE [--json]

``check`` validates an assembled set, prints its hash and every reason it cannot support
an approval (synthetic matters, composition, label provenance), and exits 1 if there are
any. ``baseline`` scores every matter on the pack's deterministic fallback - no model is
called. A model run is made on the server (4B-41), never here.

The reviewer workflow (docs/SKILL_GOLDEN_SETS.md, "Building a real set"). Every output
path must be outside the repository: real matters and labels live in the restricted
evaluation store (AQ-10).

    python scripts/skill_golden_set.py book-template SKILL_KEY OUT
    python scripts/skill_golden_set.py check-book BOOK [--names FILE]
    python scripts/skill_golden_set.py sheet-template BOOK EXPERT OUT
    python scripts/skill_golden_set.py check-sheet BOOK SHEET
    python scripts/skill_golden_set.py disagreements BOOK SHEET_A SHEET_B OUT
    python scripts/skill_golden_set.py assemble BOOK SHEET_A SHEET_B ADJUDICATION OUT [--names FILE]
    python scripts/skill_golden_set.py verify SET MANIFEST

``--names`` is a text file of party, person and place names (one per line) that the
anonymisation scan must not find. ``assemble`` writes OUT (the set) and OUT.manifest.json.
Run from projectDMS with the backend environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _outside_repository(path: Path) -> Path:
    resolved = path.resolve()
    if resolved == REPOSITORY or REPOSITORY in resolved.parents:
        raise SystemExit(f"refused: {path} is inside the repository; real golden-set material belongs in the "
                         "restricted evaluation store (AQ-10)")
    return resolved


def _write(path: Path, data: Any) -> None:
    target = _outside_repository(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {target}")


def _names(path: Path | None) -> List[str]:
    if path is None:
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _report(problems: List[str]) -> int:
    for problem in problems:
        print(f"  - {problem}")
    return 1 if problems else 0


def main() -> int:
    from rbac_backend.services.skills import golden_set_workflow as wf
    from rbac_backend.services.skills.evaluation import EvalSuite, fallback_baseline, suite_hash, suite_problems
    from rbac_backend.services.skills.system_skills import system_skill_version

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["check", "baseline", "book-template", "check-book", "sheet-template",
                                            "check-sheet", "disagreements", "assemble", "verify"])
    parser.add_argument("args", nargs="*")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--names", type=Path)
    options = parser.parse_args()
    command, args = options.command, [Path(arg) for arg in options.args]

    def need(count: int) -> None:
        if len(args) != count:
            parser.error(f"{command} takes {count} argument(s)")

    try:
        if command in {"check", "baseline"}:
            need(1)
            suite = EvalSuite.model_validate(_read(args[0]))
            if command == "check":
                print(f"{suite.suite_key}: {len(suite.matters)} matters, hash {suite_hash(suite)}")
                return _report(suite_problems(suite))
            schema_key = system_skill_version(suite.skill_key).manifest.output_schema_key
            report = fallback_baseline(suite, schema_key)
            if options.json:
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
        if command == "book-template":
            need(2)
            _write(args[1], wf.book_template(str(args[0])))
            return 0
        if command == "verify":
            need(2)
            suite = EvalSuite.model_validate(_read(args[0]))
            print(f"{suite.suite_key}: hash {suite_hash(suite)}")
            return _report(wf.verify(suite, _read(args[1])))
        if not args:
            parser.error(f"{command} needs the matter book")
        book = wf.parse(wf.MatterBook, _read(args[0]), "matter book")
        if command == "check-book":
            need(1)
            print(f"{book.suite_key}: {len(book.matters)} matters, book hash {wf.document_hash(book)}")
            return _report(wf.book_problems(book, _names(options.names)))
        if command == "sheet-template":
            need(3)
            _write(args[2], wf.label_sheet_template(book, str(args[1])))
            return 0
        if command == "check-sheet":
            need(2)
            sheet = wf.parse(wf.LabelSheet, _read(args[1]), "label sheet")
            print(f"{sheet.expert}: {len(sheet.labels)} matters labelled, sheet hash {wf.document_hash(sheet)}")
            return _report(wf.sheet_problems(book, sheet))
        if command == "disagreements":
            need(4)
            first = wf.parse(wf.LabelSheet, _read(args[1]), "first label sheet")
            second = wf.parse(wf.LabelSheet, _read(args[2]), "second label sheet")
            problems = wf.sheet_problems(book, first) + wf.sheet_problems(book, second)
            if first.expert.strip() == second.expert.strip():
                problems.append("both label sheets are by the same expert")
            if problems:
                return _report(problems)
            differ = wf.disagreements(first, second)
            # The adjudicator's worksheet: what each expert said, nothing decided.
            worksheet = {
                "skill_key": book.skill_key,
                "matter_book_hash": wf.document_hash(book),
                "label_sheet_hashes": sorted([wf.document_hash(first), wf.document_hash(second)]),
                "adjudicator": wf.PLACEHOLDER,
                "adjudicated_on": wf.PLACEHOLDER,
                "decisions": {
                    matter_id: {key: {"value": wf.PLACEHOLDER, "reason": wf.PLACEHOLDER} for key in keys}
                    for matter_id, keys in differ.items()
                },
            }
            positions = {
                matter_id: {
                    key: {
                        first.expert: wf.label_values(first.labels[matter_id]).get(key),
                        second.expert: wf.label_values(second.labels[matter_id]).get(key),
                    }
                    for key in keys
                }
                for matter_id, keys in differ.items()
            }
            print(f"{sum(len(keys) for keys in differ.values())} disputed label(s) in {len(differ)} matter(s)")
            _write(args[3], worksheet)
            _write(args[3].with_name(args[3].name + ".positions.json"), positions)
            return 0
        if command == "assemble":
            need(5)
            first = wf.parse(wf.LabelSheet, _read(args[1]), "first label sheet")
            second = wf.parse(wf.LabelSheet, _read(args[2]), "second label sheet")
            adjudication = wf.parse(wf.AdjudicationSheet, _read(args[3]), "adjudication sheet")
            suite, manifest = wf.assemble(book, first, second, adjudication, _names(options.names))
            _write(args[4], suite.model_dump(mode="json"))
            _write(args[4].with_name(args[4].name + ".manifest.json"), manifest)
            print(f"{suite.suite_key}: hash {manifest['suite_hash']}; record this hash in docs/SKILL_GOLDEN_SETS.md")
            return 0
    except wf.AssemblyRefused as refused:
        print("refused:")
        return _report(refused.problems)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
