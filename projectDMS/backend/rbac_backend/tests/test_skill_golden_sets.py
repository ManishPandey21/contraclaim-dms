"""4B-42: golden sets for the claims and EOT packs - format, composition, provenance, baseline.

The synthetic sets in ``skill_eval/synthetic`` are invented and marked so: they load, hash
and run, and support no approval. Real sets live outside the repository (AQ-10); this file
fixes the rules a real set must meet to support one (plan section 7).
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from rbac_backend.services.skills.evaluation import (
    GOLDEN_SET_REQUIREMENTS,
    EvalSuite,
    fallback_baseline,
    load_suite,
    suite_hash,
    suite_problems,
)
from rbac_backend.services.skills.system_skills import CLAIMS_SKILL_KEY, EOT_SKILL_KEY, system_skill_version
from rbac_backend.tests.skill_eval_support import ready_matters, synthetic_suite

BACKEND = Path(__file__).resolve().parents[1]
SYNTHETIC = BACKEND / "skill_eval" / "synthetic"
PROJECT = BACKEND.parents[1]
#: Recorded in docs/SKILL_GOLDEN_SETS.md. A change to a set changes its hash, and every run on it goes stale.
HASHES = {
    CLAIMS_SKILL_KEY: "e1c77ea7619c142d1cb07a14dd0a970ea7cad612e805553bb4e8db8ca1fb58b7",
    EOT_SKILL_KEY: "b55de2f8dc34ff3241b36a2fa65b8ccc08abf8da6f423e7b3d5d19267b455413",
}
SYNTHETIC_ONLY = ["15 matter(s) are synthetic: a synthetic golden set supports no approval"]


def test_the_committed_sets_are_the_generator_output() -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT / "scripts" / "build_synthetic_golden_sets.py"), "--check"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("skill_key", [CLAIMS_SKILL_KEY, EOT_SKILL_KEY])
def test_each_synthetic_set_loads_hashes_and_meets_every_rule_but_provenance(skill_key: str) -> None:
    suite = load_suite(skill_key, SYNTHETIC)
    assert len(suite.matters) == GOLDEN_SET_REQUIREMENTS[skill_key]["matters"] == 15
    assert suite_hash(suite) == HASHES[skill_key]
    assert suite_problems(suite) == SYNTHETIC_ONLY
    # Every matter's prompt inputs are what the pack's prompt takes.
    keys = {CLAIMS_SKILL_KEY: {"facts", "title", "kind", "event_summary", "clauses"},
            EOT_SKILL_KEY: {"task", "subject", "contract_form", "material", "sources", "inputs"}}[skill_key]
    assert all(set(matter.payload) == keys for matter in suite.matters)


def _suite(**changes: Any) -> EvalSuite:
    raw = synthetic_suite(CLAIMS_SKILL_KEY)
    raw.update(changes)
    return EvalSuite.model_validate(raw)


def test_a_ready_set_has_no_problems_and_each_rule_bites() -> None:
    assert suite_problems(_suite()) == []

    too_few = _suite(matters=ready_matters()[:14])
    assert any("14 matters; the pack needs 15" in item for item in suite_problems(too_few))

    contractors = copy.deepcopy(ready_matters())
    for matter in contractors:
        matter["role"] = "contractor"
    assert any("Engineer or Employer perspective" in item for item in suite_problems(_suite(matters=contractors)))

    for tag, words in (("global_claim", "global claim"), ("late_notice_with_prejudice", "with prejudice"),
                       ("variation_derived", "variation-derived"), ("missing_records", "missing records")):
        stripped = copy.deepcopy(ready_matters())
        for matter in stripped:
            matter["composition"] = [item for item in matter["composition"] if item != tag]
        assert any(words in item for item in suite_problems(_suite(matters=stripped))), tag


@pytest.mark.parametrize(
    "provenance, expected",
    [
        ({"source": "expert_written", "labelled_by": ["expert-a"], "adjudicated_by": "legal"}, "two independent experts"),
        ({"source": "expert_written", "labelled_by": ["expert-a", "expert-a"], "adjudicated_by": "legal"}, "two independent experts"),
        ({"source": "expert_written", "labelled_by": ["expert-a", "expert-b"]}, "no adjudicator"),
        ({"source": "expert_written", "labelled_by": ["expert-a", "expert-b"], "adjudicated_by": "expert-b"}, "adjudicator is one of the labellers"),
        ({"source": "anonymised_real", "labelled_by": ["expert-a", "expert-b"], "adjudicated_by": "legal"}, "consent reference"),
        ({"source": "synthetic"}, "synthetic"),
    ],
)
def test_label_provenance(provenance: Dict[str, Any], expected: str) -> None:
    matters = ready_matters()
    matters[0]["provenance"] = provenance
    assert any(expected in item for item in suite_problems(_suite(matters=matters)))
    matters[0]["provenance"] = {**provenance, "source": "anonymised_real", "labelled_by": ["a", "b"],
                                "adjudicated_by": "legal", "consent_reference": "OWNER/2026/07"}
    assert suite_problems(_suite(matters=matters)) == []


@pytest.mark.parametrize(
    "skill_key, agreement, classification",
    [(CLAIMS_SKILL_KEY, 0.093333, 0.2), (EOT_SKILL_KEY, 0.2, 0.066667)],
)
def test_the_records_only_baseline_is_recorded_and_fails(skill_key: str, agreement: float, classification: float) -> None:
    """The baseline in docs/SKILL_GOLDEN_SETS.md: every step degraded, so no pass is possible."""
    suite = load_suite(skill_key, SYNTHETIC)
    report = fallback_baseline(suite, system_skill_version(skill_key).manifest.output_schema_key)
    assert report["passed"] is False and report["suite_hash"] == HASHES[skill_key]
    assert report["scores"]["degraded_rate"] == 1.0
    assert report["scores"]["element_agreement"] == agreement
    assert report["scores"]["classification_agreement"] == classification
    assert "degraded-step rate 1.00 is above 0.05" in report["failures"]


def test_the_check_script_reports_and_fails_on_a_synthetic_set() -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT / "scripts" / "skill_golden_set.py"), "check",
         str(SYNTHETIC / f"{CLAIMS_SKILL_KEY}.json")],
        capture_output=True, text=True, cwd=PROJECT,
    )
    assert result.returncode == 1, result.stderr
    assert HASHES[CLAIMS_SKILL_KEY] in result.stdout and "synthetic" in result.stdout
    baseline = subprocess.run(
        [sys.executable, str(PROJECT / "scripts" / "skill_golden_set.py"), "baseline",
         str(SYNTHETIC / f"{EOT_SKILL_KEY}.json"), "--json"],
        capture_output=True, text=True, cwd=PROJECT,
    )
    assert baseline.returncode == 0, baseline.stderr
    assert json.loads(baseline.stdout)["passed"] is False
