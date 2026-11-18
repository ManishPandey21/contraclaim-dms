"""Test support for 4B-41: a synthetic golden set in a temporary evaluation store, and stored runs.

Only synthetic fixtures live in the repository (AQ-10). These matters are invented.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from rbac_backend.models.skill import SkillVersion
from rbac_backend.services.skills.approvals import manifest_policy_hash
from rbac_backend.services.skills.evaluation import (
    SKILL_EVAL_RUNS,
    EvalScores,
    EvalSuite,
    SkillEvalRun,
    suite_hash,
)
from rbac_backend.services.skills.executor import skill_model_name

ELEMENTS = ("event", "notice", "contractual_basis", "causation", "quantum")


def synthetic_matter(matter_id: str, states: Optional[Dict[str, str]] = None, classification: str = "excusable") -> Dict[str, Any]:
    labels = states or {name: "supported" for name in ELEMENTS}
    return {
        "matter_id": matter_id,
        "role": "contractor",
        "payload": {
            "facts": "Site handed over on 2026-03-20, 18 days after the date in the contract. Notice served 2026-04-01.",
            "title": "Late access to Site",
            "kind": "time and money",
            "event_summary": "Possession of part of the Site was given late.",
            "clauses": "Sub-Clause 2.1: right of access. Sub-Clause 20.1: notice within 28 days.",
        },
        "record": {"clauses": ["2.1", "20.1"], "notice_days": [28], "privileged_markers": ["settle at 60 percent"]},
        "labels": {"elements": labels, "classification": {"field": "classification", "expected": classification}},
        "composition": ["straightforward"],
    }


def synthetic_suite(skill_key: str, matters: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    from rbac_backend.services.claim_bench import CLAIM_HEAD_TEST_PROMPT

    return {
        "suite_key": f"{skill_key}-synthetic",
        "skill_key": skill_key,
        "prompt_key": CLAIM_HEAD_TEST_PROMPT,
        "matters": matters or [synthetic_matter("S01"), synthetic_matter("S02")],
    }


def use_suite_store(monkeypatch: Any, skill_key: str, suite: Optional[Dict[str, Any]] = None) -> Path:
    """Write a golden set into a fresh temporary store and point the server at it."""
    from rbac_backend.core.config import settings

    directory = Path(tempfile.mkdtemp(prefix="eval-store-"))
    (directory / f"{skill_key}.json").write_text(json.dumps(suite or synthetic_suite(skill_key)), encoding="utf-8")
    monkeypatch.setattr(settings, "SKILL_EVAL_SUITE_DIR", str(directory))
    return directory


async def stored_run(db: Any, record: SkillVersion, recorded_by: str = "evaluator", passed: bool = True) -> SkillEvalRun:
    """A completed run for ``record`` against the store's current golden set, as the runner writes it."""
    from rbac_backend.services.skills.evaluation import load_suite

    suite: EvalSuite = load_suite(record.skill_key)
    run = SkillEvalRun(
        skill_key=record.skill_key,
        version=record.version,
        content_hash=record.content_hash,
        policy_hash=manifest_policy_hash(record.manifest),
        suite_key=suite.suite_key,
        suite_hash=suite_hash(suite),
        model=skill_model_name(),
        prompt_key=suite.prompt_key,
        recorded_by=recorded_by,
        threshold=0.85,
        case_count=len(suite.matters),
        passed=passed,
        failures=[] if passed else ["element agreement 0.40 is below 0.85"],
        scores=EvalScores(element_agreement=0.95 if passed else 0.4, degraded_rate=0.0, schema_validity=1.0),
    )
    await db[SKILL_EVAL_RUNS].insert_one(run.model_dump(by_alias=True, mode="python"))
    return run
