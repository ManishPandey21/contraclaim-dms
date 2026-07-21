"""Phase 0 immutable baseline for the LangGraph migration.

These checks intentionally avoid model calls and external services.  They make
the regression corpus and the legacy API contract explicit before the
asynchronous engine is introduced in later phases.
"""

from __future__ import annotations

import json
from pathlib import Path

from rbac_backend.models.letter_drafting import DraftRun, DraftRunCreateRequest


FIXTURE = Path(__file__).parent / "fixtures" / "letter_drafting_langgraph_phase0_cases.json"


def test_phase0_regression_corpus_has_all_required_contractual_categories():
    corpus = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert corpus["schema_version"] == 1
    assert corpus["required_artifacts"] == [
        "source_ledger",
        "probing_questions",
        "planning_sheet",
        "reply_matrix",
        "draft_artifact",
        "validation_report",
        "legal_risk_report",
    ]
    assert {case["case_id"] for case in corpus["cases"]} == {
        "delay_eot_notice",
        "variation_instruction",
        "payment_ipc",
        "quality_ncr",
        "claim_reply",
        "dispute_escalation",
        "progress_delay",
    }
    assert all(case["expected_question_categories"] for case in corpus["cases"])


def test_phase0_legacy_drafting_contract_is_explicit_before_async_cutover():
    """Record the synchronous v2 surface that remains compatible in rollout=off."""
    assert "mode" in DraftRunCreateRequest.model_fields
    assert "run_id" in DraftRun.model_fields
    assert "sources" in DraftRun.model_fields
    assert "probing_questions" in DraftRun.model_fields
    assert "planning_sheet" in DraftRun.model_fields
    assert "reply_matrix" in DraftRun.model_fields
    assert "draft_artifact" in DraftRun.model_fields
    assert "validation_report" in DraftRun.model_fields
    assert "legal_risk_report" in DraftRun.model_fields
