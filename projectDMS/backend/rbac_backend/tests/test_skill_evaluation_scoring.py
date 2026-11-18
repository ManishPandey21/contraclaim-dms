"""4B-41: the evaluation gates and scores, and the suite store (pure)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi import HTTPException

from rbac_backend.services.skills.evaluation import (
    EvalMatter,
    MatterResult,
    aggregate,
    load_suite,
    score_matter,
    suite_hash,
)
from rbac_backend.services.skills.executor import ExecutionResult
from rbac_backend.tests.skill_eval_support import ELEMENTS, synthetic_matter, synthetic_suite

MATTER = EvalMatter.model_validate(synthetic_matter("S01"))


def _output(**over: Any) -> Dict[str, Any]:
    return {
        **{name: {"state": "supported", "reason": "Shown by the records.", "source_ids": []} for name in ELEMENTS},
        "classification": "excusable",
        "clause_refs": ["2.1"],
        "residual_risks": [],
        "missing_information": [],
        **over,
    }


def _executed(output: Dict[str, Any], degraded: bool = False, error: str | None = None) -> ExecutionResult:
    return ExecutionResult(output=output, degraded=degraded, error=error, model="m", compiled=None, prompt_version=1)  # type: ignore[arg-type]


def test_a_clean_output_passes_every_hard_gate() -> None:
    result = score_matter(MATTER, _executed(_output(residual_risks=["Access was 18 days late under Sub-Clause 2.1."])))
    assert result.hard_failures == []
    assert result.elements["event"] == ["supported", "supported"] and result.classification == ["excusable", "excusable"]


@pytest.mark.parametrize(
    "over, gate",
    [
        ({"residual_risks": ["The Contractor may recover INR 9,00,000."]}, "figures: not in the matter: 900000"),
        ({"clause_refs": ["8.4"]}, "clauses: cited but not in the matter: 8.4"),
        ({"residual_risks": ["Under Clause 14.2 payment is due."]}, "clauses: cited but not in the matter: 14.2"),
        ({"residual_risks": ["Notice is due within 56 days."]}, "notice: periods the contract does not state: 56"),
        ({"residual_risks": ["We would settle at 60 percent of the claim."]}, "privileged: 1 privileged passage"),
    ],
)
def test_each_hard_gate(over: Dict[str, Any], gate: str) -> None:
    failures = score_matter(MATTER, _executed(_output(**over))).hard_failures
    assert any(failure.startswith(gate) for failure in failures), failures


def test_a_notice_period_the_contract_states_is_not_assumed() -> None:
    assert score_matter(MATTER, _executed(_output(residual_risks=["Notice within 28 days was given."]))).hard_failures == []


def test_a_schema_failure_is_a_hard_failure() -> None:
    result = score_matter(MATTER, _executed(_output(), degraded=True, error="ValidationError: bad"))
    assert result.schema_valid is False and result.hard_failures[0].startswith("schema")


def _result(matter_id: str, agree: int, total: int = 5, classification: bool = True, degraded: bool = False) -> MatterResult:
    names = list(ELEMENTS)[:total]
    return MatterResult(
        matter_id=matter_id,
        degraded=degraded,
        elements={name: ["supported", "supported" if index < agree else "weak"] for index, name in enumerate(names)},
        classification=["excusable", "excusable" if classification else "compensable"],
    )


def test_scored_gates() -> None:
    scores, failures, passed = aggregate([_result(f"M{i}", 5) for i in range(20)], 0.85)
    assert passed and failures == [] and scores.element_agreement == 1.0 and scores.classification_agreement == 1.0

    # Overall agreement below the threshold.
    _s, failures, passed = aggregate([_result(f"M{i}", 4) for i in range(10)], 0.85)
    assert not passed and any("element agreement 0.80" in item for item in failures)

    # One element type below 0.75 while the pack is above the threshold.
    results = [_result(f"M{i}", 5) for i in range(10)]
    for result in results[:3]:
        result.elements["quantum"] = ["supported", "weak"]
    scores, failures, passed = aggregate(results, 0.85)
    assert scores.element_agreement == 0.94 and not passed
    assert failures == ["element agreement on quantum 0.70 is below 0.75"]

    # Classification below 0.85.
    _s, failures, passed = aggregate([_result(f"M{i}", 5, classification=i > 1) for i in range(10)], 0.85)
    assert not passed and failures == ["classification agreement 0.80 is below 0.85"]

    # More than 5 % degraded.
    _s, failures, passed = aggregate([_result(f"M{i}", 5, degraded=i == 0) for i in range(10)], 0.85)
    assert not passed and failures == ["degraded-step rate 0.10 is above 0.05"]

    # Nothing scored is not a pass.
    _s, failures, passed = aggregate([MatterResult(matter_id="M")], 0.85)
    assert not passed and failures == ["no labelled elements were scored"]

    # A hard failure fails the run whatever the agreement.
    hard = _result("M0", 5)
    hard.hard_failures = ["figures: not in the matter: 9"]
    _s, failures, passed = aggregate([hard, *[_result(f"M{i}", 5) for i in range(1, 10)]], 0.85)
    assert not passed and failures == ["hard gate - M0: figures: not in the matter: 9"]


def test_the_suite_store(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as unset:
        load_suite("indian-infra-claims", None)
    assert unset.value.detail["code"] == "eval_suite_store_not_configured"
    with pytest.raises(HTTPException) as missing:
        load_suite("indian-infra-claims", tmp_path)
    assert missing.value.detail["code"] == "eval_suite_missing"
    # A key cannot reach a file outside the store, even one that is a valid golden set.
    store = tmp_path / "store"
    store.mkdir()
    (tmp_path / "outside.json").write_text(json.dumps(synthetic_suite("../outside")))
    with pytest.raises(HTTPException) as escape:
        load_suite("../outside", store)
    assert escape.value.detail["code"] == "eval_suite_missing"

    (tmp_path / "indian-infra-claims.json").write_text(json.dumps(synthetic_suite("indian-infra-claims")))
    suite = load_suite("indian-infra-claims", tmp_path)
    assert len(suite.matters) == 2 and len(suite_hash(suite)) == 64
    changed = synthetic_suite("indian-infra-claims")
    changed["matters"][0]["labels"]["elements"]["notice"] = "weak"
    assert suite_hash(load_suite("indian-infra-claims", tmp_path)) != suite_hash(type(suite).model_validate(changed))

    (tmp_path / "indian-infra-eot-claims.json").write_text(json.dumps(synthetic_suite("indian-infra-claims")))
    with pytest.raises(HTTPException) as wrong:
        load_suite("indian-infra-eot-claims", tmp_path)
    assert wrong.value.detail["code"] == "eval_suite_invalid"
    (tmp_path / "indian-infra-eot-claims.json").write_text("{not json")
    with pytest.raises(HTTPException) as broken:
        load_suite("indian-infra-eot-claims", tmp_path)
    assert broken.value.detail["code"] == "eval_suite_invalid"
