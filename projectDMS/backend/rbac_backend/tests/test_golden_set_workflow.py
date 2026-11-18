"""4B-42: the reviewer workflow that assembles a real golden set from expert work.

The fixtures here are TEST DATA: the matters are the committed synthetic matters
relabelled as ``expert_written`` by fictional reviewers "Expert A", "Expert B" and
"Legal Approver C". They exercise the workflow's rules; they are not a golden set, are
never written to the evaluation store, and no label here is an expert's opinion.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from rbac_backend.services.skills import golden_set_workflow as wf
from rbac_backend.services.skills.evaluation import EvalSuite, suite_hash, suite_problems
from rbac_backend.services.skills.system_skills import CLAIMS_SKILL_KEY, EOT_SKILL_KEY

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parents[1]
SYNTHETIC = BACKEND / "skill_eval" / "synthetic"
SCRIPT = PROJECT / "scripts" / "skill_golden_set.py"


def _synthetic(skill_key: str) -> Dict[str, Any]:
    return json.loads((SYNTHETIC / f"{skill_key}.json").read_text(encoding="utf-8"))


def _book(skill_key: str = CLAIMS_SKILL_KEY) -> wf.MatterBook:
    data = _synthetic(skill_key)
    matters = []
    for matter in data["matters"]:
        matter = {key: value for key, value in matter.items() if key != "labels"}
        matter["provenance"] = {"source": "expert_written", "prepared_by": "Expert A"}
        matters.append(matter)
    return wf.MatterBook(suite_key=f"{skill_key}-test-workflow", skill_key=skill_key,
                         prompt_key=data["prompt_key"], matters=matters)


def _sheet(book: wf.MatterBook, expert: str, changes: Dict[str, Dict[str, Any]] | None = None) -> wf.LabelSheet:
    labels = {m["matter_id"]: copy.deepcopy(m["labels"]) for m in _synthetic(book.skill_key)["matters"]}
    for matter_id, change in (changes or {}).items():
        for key, value in change.items():
            if key.startswith("elements."):
                labels[matter_id]["elements"][key.split(".", 1)[1]] = value
            elif key == "classification":
                labels[matter_id]["classification"]["expected"] = value
            else:
                labels[matter_id][key] = value
    return wf.LabelSheet(skill_key=book.skill_key, matter_book_hash=wf.document_hash(book), expert=expert,
                         attestation=wf.INDEPENDENCE_ATTESTATION, labelled_on="2026-11-18", labels=labels)


def _adjudication(book: wf.MatterBook, a: wf.LabelSheet, b: wf.LabelSheet,
                  decisions: Dict[str, Dict[str, Any]], adjudicator: str = "Legal Approver C") -> wf.AdjudicationSheet:
    return wf.AdjudicationSheet(
        skill_key=book.skill_key, matter_book_hash=wf.document_hash(book),
        label_sheet_hashes=[wf.document_hash(a), wf.document_hash(b)], adjudicator=adjudicator,
        adjudicated_on="2026-11-18",
        decisions={m: {k: {"value": v, "reason": "Settled against the record."} for k, v in d.items()}
                   for m, d in decisions.items()},
    )


def _parts():
    book = _book()
    a = _sheet(book, "Expert A")
    b = _sheet(book, "Expert B", {"C08": {"elements.notice": "weak", "classification": "compensable"},
                                  "C11": {"information_required": ["Muster rolls for May 2026"]}})
    return book, a, b


def _refusals(book, a, b, adjudication, names=()) -> List[str]:
    with pytest.raises(wf.AssemblyRefused) as refused:
        wf.assemble(book, a, b, adjudication, names)
    return refused.value.problems


def test_templates_meet_the_composition_and_hold_only_placeholders() -> None:
    for skill_key in (CLAIMS_SKILL_KEY, EOT_SKILL_KEY):
        template = wf.book_template(skill_key)
        assert len(template["matters"]) == 15
        book = wf.MatterBook.model_validate(template)
        assert wf._composition_problems(book) == [], skill_key
        problems = wf.book_problems(book)
        assert "suite_key is still the template's" in problems
        assert sum("still holds" in p for p in problems) == 15
        assert all("labels" not in matter for matter in template["matters"]), "a book carries no labels"
        sheet = wf.label_sheet_template(book, "Expert A")
        assert sheet["matter_book_hash"] == wf.document_hash(book)
        parsed = wf.LabelSheet.model_validate(sheet)
        assert any("attestation" in p for p in wf.sheet_problems(book, parsed))


def test_a_book_refuses_synthetic_matters_and_labels() -> None:
    data = _book().model_dump(mode="json")
    data["matters"][0]["provenance"]["source"] = "synthetic"
    with pytest.raises(wf.AssemblyRefused):
        wf.parse(wf.MatterBook, data, "book")
    data = _book().model_dump(mode="json")
    data["matters"][0]["labels"] = {"elements": {}}
    with pytest.raises(wf.AssemblyRefused):
        wf.parse(wf.MatterBook, data, "book")


def test_anonymisation_and_consent() -> None:
    book = _book()
    book.matters[0].provenance.source = "anonymised_real"
    book.matters[0].payload["facts"] += " Contact site.agent@example.com or +91 98765 43210. PAN ABCDE1234F."
    problems = wf.book_problems(book, names=["Aurora Infra"])
    assert "C01: an anonymised real matter needs the owner's consent reference" in problems
    assert "C01: an anonymised real matter names who anonymised it" in problems
    assert {"C01: contains an email address", "C01: contains a phone number", "C01: contains a PAN"} <= set(problems)
    book.matters[1].payload["facts"] += " Aurora infra served notice."
    assert "C02: names 'Aurora Infra'" in wf.book_problems(book, names=["Aurora Infra"])


def test_disagreements_are_found_label_by_label() -> None:
    _, a, b = _parts()
    assert wf.disagreements(a, b) == {
        "C08": ["classification", "elements.notice"],
        "C11": ["information_required"],
    }
    assert wf.disagreements(a, a) == {}


def test_a_set_is_assembled_from_two_sheets_and_an_adjudication() -> None:
    book, a, b = _parts()
    adjudication = _adjudication(book, a, b, {
        "C08": {"elements.notice": "supported", "classification": "undetermined"},
        "C11": {"information_required": ["Muster rolls for May 2026"]},
    })
    suite, manifest = wf.assemble(book, a, b, adjudication)
    assert suite_problems(suite) == [] and len(suite.matters) == 15
    c08 = next(m for m in suite.matters if m.matter_id == "C08")
    assert c08.labels.elements["notice"] == "supported" and c08.labels.classification.expected == "undetermined"
    assert c08.provenance.labelled_by == ["Expert A", "Expert B"]
    assert c08.provenance.adjudicated_by == "Legal Approver C"
    assert c08.provenance.disagreements == ["classification", "elements.notice"]
    c11 = next(m for m in suite.matters if m.matter_id == "C11")
    assert c11.labels.information_required == ["Muster rolls for May 2026"]
    assert next(m for m in suite.matters if m.matter_id == "C01").provenance.disagreements == []
    assert manifest["suite_hash"] == suite_hash(suite)
    assert manifest["matter_book_hash"] == wf.document_hash(book) and manifest["disputed_labels"] == 3
    assert wf.verify(suite, manifest) == []
    changed = suite.model_copy(deep=True)
    changed.matters[0].labels.elements["event"] = "weak"
    assert wf.verify(changed, manifest)[0].startswith("the golden set has changed")


def test_independence_is_enforced() -> None:
    book, a, b = _parts()
    same = _sheet(book, "Expert A")
    assert any("same expert" in p for p in _refusals(book, a, same, _adjudication(book, a, same, {})))
    decisions = {"C08": {"elements.notice": "weak", "classification": "compensable"},
                 "C11": {"information_required": []}}
    by_labeller = _adjudication(book, a, b, decisions, adjudicator="Expert B")
    assert "the adjudicator labelled the matters; the legal approver must be a third person" in _refusals(book, a, b, by_labeller)
    altered = a.model_copy(update={"attestation": "I labelled these."})
    problems = _refusals(book, altered, b, _adjudication(book, altered, b, decisions))
    assert "Expert A: the independence attestation is missing or altered" in problems


def test_the_adjudicator_settles_every_dispute_and_nothing_else() -> None:
    book, a, b = _parts()
    partial = _adjudication(book, a, b, {"C08": {"elements.notice": "weak"}})
    problems = _refusals(book, a, b, partial)
    assert "C08: 'classification' is disputed and not adjudicated" in problems
    assert "C11: 'information_required' is disputed and not adjudicated" in problems
    overreach = _adjudication(book, a, b, {
        "C08": {"elements.notice": "weak", "classification": "compensable"},
        "C11": {"information_required": []},
        "C01": {"classification": "compensable"},
    })
    assert "C01: 'classification' was agreed by both experts; the adjudicator may not change it" in _refusals(book, a, b, overreach)
    invalid = _adjudication(book, a, b, {
        "C08": {"elements.notice": "probably", "classification": "compensable"},
        "C11": {"information_required": []},
    })
    assert any("C08: adjudicated 'elements.notice' value 'probably'" in p for p in _refusals(book, a, b, invalid))


def test_every_document_is_bound_to_the_others_by_hash() -> None:
    book, a, b = _parts()
    decisions = {"C08": {"elements.notice": "weak", "classification": "compensable"}, "C11": {"information_required": []}}
    adjudication = _adjudication(book, a, b, decisions)
    edited = book.model_copy(deep=True)
    edited.matters[0].payload["facts"] += " Edited after labelling."
    problems = _refusals(edited, a, b, adjudication)
    assert "Expert A: sheet was made from another version of the matter book" in problems
    assert "the adjudication sheet was made for another matter book" in problems
    other = _sheet(book, "Expert B", {"C08": {"elements.notice": "weak", "classification": "compensable"},
                                      "C11": {"information_required": ["Muster rolls for May 2026"]},
                                      "C02": {"elements.quantum": "weak"}})
    assert "the adjudication sheet names other label sheets" in _refusals(book, a, other, adjudication)


def test_a_sheet_must_label_every_matter_in_the_pack_vocabulary() -> None:
    book, a, _ = _parts()
    data = a.model_dump(mode="json")
    del data["labels"]["C03"]
    data["labels"]["C04"]["elements"]["quantum"] = "strong"
    data["labels"]["C05"]["classification"]["expected"] = "maybe"
    data["labels"]["C06"]["elements"].pop("event")
    problems = wf.sheet_problems(book, wf.LabelSheet.model_validate(data))
    assert "Expert A: no labels for C03" in problems
    assert any(p.startswith("Expert A C04: quantum 'strong' is not one of") for p in problems)
    assert any(p.startswith("Expert A C05: classification must be one of") for p in problems)
    assert any(p.startswith("Expert A C06: elements must be exactly") for p in problems)


def test_the_eot_pack_assembles_on_its_own_vocabulary() -> None:
    book = _book(EOT_SKILL_KEY)
    a = _sheet(book, "Expert A")
    b = _sheet(book, "Expert B", {"E09": {"elements.criticality": "not_critical"}})
    suite, _ = wf.assemble(book, a, b, _adjudication(book, a, b, {"E09": {"elements.criticality": "unproven"}}))
    assert suite_problems(suite) == []
    assert next(m for m in suite.matters if m.matter_id == "E09").labels.elements == {"criticality": "unproven"}


def test_the_command_line_refuses_to_write_into_the_repository(tmp_path: Path) -> None:
    inside = PROJECT / "backend" / "rbac_backend" / "skill_eval" / "real-set.json"
    result = subprocess.run([sys.executable, str(SCRIPT), "book-template", CLAIMS_SKILL_KEY, str(inside)],
                            capture_output=True, text=True, cwd=PROJECT)
    assert result.returncode != 0 and "inside the repository" in (result.stderr + result.stdout)
    assert not inside.exists()
    outside = tmp_path / "book.json"
    result = subprocess.run([sys.executable, str(SCRIPT), "book-template", CLAIMS_SKILL_KEY, str(outside)],
                            capture_output=True, text=True, cwd=PROJECT)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([sys.executable, str(SCRIPT), "check-book", str(outside)],
                            capture_output=True, text=True, cwd=PROJECT)
    assert result.returncode == 1 and "still holds 'TO BE WRITTEN'" in result.stdout


def test_the_command_line_runs_the_whole_workflow(tmp_path: Path) -> None:
    book, a, b = _parts()
    files = {"book": book, "a": a, "b": b}
    for name, document in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(document.model_dump(mode="json")), encoding="utf-8")

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=PROJECT)

    worksheet = tmp_path / "adjudication.json"
    result = run("disagreements", str(tmp_path / "book.json"), str(tmp_path / "a.json"), str(tmp_path / "b.json"), str(worksheet))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 disputed label(s) in 2 matter(s)" in result.stdout
    positions = json.loads((tmp_path / "adjudication.json.positions.json").read_text(encoding="utf-8"))
    assert positions["C08"]["elements.notice"] == {"Expert A": "supported", "Expert B": "weak"}
    sheet = json.loads(worksheet.read_text(encoding="utf-8"))
    out = tmp_path / "set.json"
    refused = run("assemble", str(tmp_path / "book.json"), str(tmp_path / "a.json"), str(tmp_path / "b.json"),
                  str(worksheet), str(out))
    assert refused.returncode == 1 and "refused:" in refused.stdout and not out.exists()

    sheet["adjudicator"], sheet["adjudicated_on"] = "Legal Approver C", "2026-11-18"
    sheet["decisions"] = {
        "C08": {"elements.notice": {"value": "weak", "reason": "Notice served late; the record shows it."},
                "classification": {"value": "undetermined", "reason": "Global claim; causation not shown."}},
        "C11": {"information_required": {"value": ["Muster rolls for May 2026"], "reason": "Needed to price standing time."}},
    }
    worksheet.write_text(json.dumps(sheet), encoding="utf-8")
    done = run("assemble", str(tmp_path / "book.json"), str(tmp_path / "a.json"), str(tmp_path / "b.json"),
               str(worksheet), str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    suite = EvalSuite.model_validate(json.loads(out.read_text(encoding="utf-8")))
    assert suite_problems(suite) == []
    assert run("check", str(out)).returncode == 0
    assert run("verify", str(out), str(out) + ".manifest.json").returncode == 0
