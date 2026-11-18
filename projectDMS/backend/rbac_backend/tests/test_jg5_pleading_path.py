"""JG-5: the defects that kept every case from an approved Statement of Claim.

The real-route journey (``integration/test_claim_journey_http_mongo.py``) walks the
whole path; these pin each defect on its own, without a database:

1. a clause source never resolved against real Motor: ``collection and ...``
   raises on a PyMongo/Motor collection, so every clause id that missed
   ``document_vectors`` read ``resolution_error``;
2. the pleading ledger cites claim, defence, counterclaim, rejoinder, quantum,
   notice and expert-alignment rows under types the authority registry did not
   know, so each was denied as an unknown origin;
3. a chronology row carried by claim escalation names no event, so the matrix
   projection denied it; the claim it came from is now its source, and a client
   cannot claim that provenance;
4. a draft-linked workflow read only rows bound to its draft, never the case's;
5. approved case analysis (issues, jurisdiction checks) and the project
   registers, added to every ledger as context, counted as inadmissible evidence
   and as manual facts whether cited or not.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from rbac_backend.services.arbitration_drafting.citation_states import (
    CONTEXT_EXEMPTION_POLICY_HASH,
    classify_citations,
    exempt_context_keys,
    is_case_analysis,
    is_exempt_context_row,
    ledger_context_class,
)
from rbac_backend.services.arbitration_drafting.validator import ArbitrationDraftValidator
from rbac_backend.services.publication_policy import resolve_derived_authority, safe_matrix_rows


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# 1. clause authority under a collection that refuses truth testing
# --------------------------------------------------------------------------- #


class _StrictCollection:
    """Like a PyMongo/Motor collection: bool() raises."""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def __bool__(self) -> bool:
        raise NotImplementedError("Collection objects do not implement truth value testing or bool()")

    async def find_one(self, query: Dict[str, Any]) -> Any:
        for row in self.rows:
            if all(_matches(row.get(key), expected) for key, expected in query.items()):
                return row
        return None


def _matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict) and "$in" in expected:
        return actual in expected["$in"]
    return actual == expected


class _StrictDb:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self._collections = {name: _StrictCollection(rows) for name, rows in collections.items()}

    def __getitem__(self, name: str) -> _StrictCollection:
        return self._collections.setdefault(name, _StrictCollection([]))

    def __getattr__(self, name: str) -> _StrictCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


def test_a_clause_resolves_to_its_document_under_a_real_driver_collection() -> None:
    db = _StrictDb(
        contract_clauses=[{"_id": "cl-8-4", "document_id": "gcc-1"}],
        documents=[{"_id": "gcc-1", "processing_status": "completed", "lifecycle_state": "active"}],
    )
    decision = _run(resolve_derived_authority(db, "clause", "cl-8-4"))
    assert decision.consumable, decision
    assert decision.reason != "resolution_error"


def test_a_clause_of_a_quarantined_document_is_still_denied() -> None:
    db = _StrictDb(
        contract_clauses=[{"_id": "cl-8-4", "document_id": "gcc-1"}],
        documents=[{"_id": "gcc-1", "processing_status": "completed", "lifecycle_state": "deleted"}],
    )
    assert not _run(resolve_derived_authority(db, "clause", "cl-8-4")).consumable


# --------------------------------------------------------------------------- #
# 2. the ledger's matrix source types
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "source_type",
    [
        "claim_matrix",
        "defence_matrix",
        "counterclaim_matrix",
        "rejoinder_matrix",
        "quantum_annexure",
        "notice_compliance",
        "expert_alignment",
        "chronology_entry",
        "issue_matrix",
        "jurisdiction_check",
    ],
)
def test_the_case_matrices_the_ledger_cites_are_application_records(source_type: str) -> None:
    decision = _run(resolve_derived_authority(_StrictDb(), source_type, "row-1"))
    assert decision.consumable and decision.reason == "independent_application_record", decision


def test_an_unregistered_source_type_is_still_denied() -> None:
    decision = _run(resolve_derived_authority(_StrictDb(), "made_up_matrix", "row-1"))
    assert not decision.consumable and decision.reason == "unknown_source_type"


def test_an_expert_report_names_a_document_and_stays_document_governed() -> None:
    assert not _run(resolve_derived_authority(_StrictDb(), "expert_report", "not-a-document")).consumable


# --------------------------------------------------------------------------- #
# 3. escalated chronology rows
# --------------------------------------------------------------------------- #


def _chronology(**fields: Any) -> Dict[str, Any]:
    return {"_id": "row", "case_id": "case-1", "event": "CLM-1: the event giving rise to the claim occurred.", **fields}


def test_an_escalated_chronology_row_is_sourced_from_its_claim() -> None:
    row = _chronology(origin={"claim_id": "c1"}, origin_key="claim:c1:date:event")
    [safe] = _run(safe_matrix_rows(_StrictDb(), [row], "chronology-matrix"))
    assert not safe.get("authority_denied") and safe["event"] == row["event"]


@pytest.mark.parametrize(
    "fields",
    [
        {},  # a row typed by hand naming no event: unresolved, as before
        {"origin": {"claim_id": "c1"}, "origin_key": "claim:c2:date:event"},  # origin and key disagree
        {"origin": {"claim_id": "c1"}},  # no key
        {"origin_key": "claim:c1:date:event"},  # no origin
    ],
)
def test_a_chronology_row_without_a_consistent_server_origin_is_denied(fields: Dict[str, Any]) -> None:
    [safe] = _run(safe_matrix_rows(_StrictDb(), [_chronology(**fields)], "chronology-matrix"))
    assert safe.get("authority_denied") and safe["event"] == ""


def test_a_client_cannot_write_a_rows_origin_but_the_server_can() -> None:
    from pydantic import ValidationError

    from rbac_backend.models.arbitration_drafting import ArbitrationMatrixRowCreate
    from rbac_backend.services.arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService
    from rbac_backend.tests.test_arbitration_drafting import _FakeDb, _FakeUser

    forged = {"event": "Typed by hand", "date": "2026-03-02", "origin": {"claim_id": "c1"}, "origin_key": "claim:c1:date:event"}
    with pytest.raises(ValidationError, match="origin"):
        ArbitrationMatrixRowCreate(**forged)  # refused at the request model
    db = _FakeDb()
    service = ArbitrationCaseWorkspaceService(db)
    # And stripped by the service, should a body ever reach it unvalidated.
    stored = _run(service.create_matrix_row("case-1", "chronology-matrix", ArbitrationMatrixRowCreate.model_construct(**forged), _FakeUser()))
    assert "origin" not in stored and "origin_key" not in stored
    carried = _run(
        service.create_matrix_row(
            "case-1",
            "chronology-matrix",
            ArbitrationMatrixRowCreate(event="Carried", date="2026-03-02"),
            _FakeUser(),
            server_fields={"origin": {"claim_id": "c1"}, "origin_key": "claim:c1:date:event"},
        )
    )
    assert carried["origin"] == {"claim_id": "c1"} and carried["origin_key"] == "claim:c1:date:event"


# --------------------------------------------------------------------------- #
# 4. which rows a draft-linked workflow reads
# --------------------------------------------------------------------------- #


def test_a_draft_linked_read_includes_the_case_rows_but_not_another_drafts() -> None:
    from rbac_backend.services.arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService
    from rbac_backend.tests.test_arbitration_drafting import _FakeDb

    db = _FakeDb()
    db.arbitration_issue_matrix.rows[:] = [
        {"_id": "case-row", "case_id": "case-1", "issue": "Case level"},
        {"_id": "own-row", "case_id": "case-1", "draft_id": "draft-1", "issue": "This draft"},
        {"_id": "other-row", "case_id": "case-1", "draft_id": "draft-2", "issue": "Another draft"},
    ]
    service = ArbitrationCaseWorkspaceService(db)
    with_case = _run(service.list_matrix_rows("case-1", "issue-matrix", draft_id="draft-1", with_case_rows=True))
    assert {row["_id"] for row in with_case} == {"case-row", "own-row"}
    draft_only = _run(service.list_matrix_rows("case-1", "issue-matrix", draft_id="draft-1"))
    assert {row["_id"] for row in draft_only} == {"own-row"}


# --------------------------------------------------------------------------- #
# 5. context rows in the ledger (JG-5(f), owner's conditional approval)
# --------------------------------------------------------------------------- #

SCOPE = {"case_id": "case-1", "project_id": "project-1"}
ISSUE = {"source_key": "S1", "source_type": "issue_matrix", "source_origin": "case_issue_matrix",
         "ledger_context": "case_analysis", "context_scope": {"case_id": "case-1"},
         "verification_status": "approved", "is_user_supplied": True, "quality_flags": ["user_supplied"],
         "citation": "ISS-1", "label": "Whether the handover was late", "snippet": "Limitation runs from 14/03/2026."}
REGISTER = {"source_key": "S2", "source_type": "claim", "source_origin": "claim_register",
            "ledger_context": "project_register", "context_scope": {"project_id": "project-1"},
            "verification_status": "submitted", "is_user_supplied": False, "quality_flags": [],
            "citation": "CLM-009", "label": "Another claim", "snippet": "Amount claimed INR 9,99,999.00"}
EVIDENCE = {"source_key": "S3", "source_type": "document", "verification_status": "verified", "is_user_supplied": False,
            "citation": "C-1", "label": "Site diary", "snippet": "Handover on 20 March 2026."}
MANUAL = {"source_key": "S4", "source_type": "manual_fact", "verification_status": "verified", "is_user_supplied": True,
          "quality_flags": ["user_supplied"], "citation": "Note", "label": "A note"}
PLEADING = "1. The handover was late [S3: C-1].\n"
INADMISSIBLE = "Draft source ledger contains user-supplied or unverified selected evidence that is not admissible for filing."


def _report(ledger: List[Dict[str, Any]], markdown: str, **draft: Any) -> Dict[str, Any]:
    context = {"source_ledger": ledger, "draft": {"case_id": "case-1", "project_id": "project-1",
                                                  "draft_type": "statement_of_defence", **draft}}
    return ArbitrationDraftValidator().validation_report(context, markdown)


def test_uncited_server_classified_context_does_not_block_and_is_recorded() -> None:
    report = _report([ISSUE, REGISTER, EVIDENCE], PLEADING)
    assert INADMISSIBLE not in report["approval_blockers"], report["approval_blockers"]
    assert report["context_rows_exempted"] == ["S1", "S2"]
    assert report["context_policy"]["hash"] == CONTEXT_EXEMPTION_POLICY_HASH and report["context_policy"]["version"] == 1


@pytest.mark.parametrize(
    "forged",
    [
        # Control 1: only the server's classification counts - a type, an origin, or the flag alone does not.
        {**REGISTER, "ledger_context": None},
        {**REGISTER, "source_origin": "selected_reference"},
        {**ISSUE, "source_type": "document"},
        {**REGISTER, "ledger_context": "case_analysis"},
        {**REGISTER, "ledger_context": "made_up"},
    ],
)
def test_a_row_the_server_did_not_classify_as_context_still_blocks(forged: Dict[str, Any]) -> None:
    assert INADMISSIBLE in _report([forged, EVIDENCE], PLEADING)["approval_blockers"]


def test_a_selected_reference_cannot_become_context() -> None:
    from rbac_backend.services.arbitration_drafting.context import ArbitrationContextBuilder

    builder = ArbitrationContextBuilder.__new__(ArbitrationContextBuilder)
    row = builder._ledger_row(
        {"source_type": "claim", "source_id": "c9", "ledger_context": "project_register",
         "context_scope": {"project_id": "project-1"},
         "metadata": {"source_origin": "claim_register", "verification_status": "submitted"}},
        1,
    )
    assert row["ledger_context"] is None and ledger_context_class(row) is None
    assert not is_exempt_context_row(row, PLEADING, SCOPE)


@pytest.mark.parametrize(
    "markdown",
    [
        "1. The handover was late [S3: C-1] [S2].\n",  # cited by key alone
        "1. The handover was late [S3: C-1] [S2: CLM-009].\n",  # cited by key and reference
        "1. The handover was late [S3: C-1], as in claim CLM-009.\n",  # named by its reference
    ],
)
def test_a_relied_upon_context_row_is_checked_as_a_citation(markdown: str) -> None:
    report = _report([ISSUE, REGISTER, EVIDENCE], markdown)
    assert "S2" not in report["context_rows_exempted"]
    assert INADMISSIBLE in report["approval_blockers"] or any("not yet verified" in b for b in report["approval_blockers"])


def test_cited_case_analysis_is_refused_as_a_source_of_fact() -> None:
    report = _report([ISSUE, REGISTER, EVIDENCE], "1. The handover was late [S3: C-1] [S1: ISS-1].\n")
    assert any("not yet verified" in item and "S1" in item for item in report["approval_blockers"])
    item = next(i for i in classify_citations("[S1: ISS-1]", [ISSUE])["items"] if i["source_key"] == "S1")
    assert item["state"] == "unverified" and "not evidence" in item["reason"]


@pytest.mark.parametrize(
    "changed",
    [
        {**REGISTER, "verification_status": "withdrawn"},
        {**REGISTER, "verification_status": "stale"},
        {**ISSUE, "verification_status": "revoked"},
        {**ISSUE, "verification_status": "needs_review"},
        {**REGISTER, "authority_denied": True},
        {**REGISTER, "quality_flags": ["authority_denied"]},
        {**REGISTER, "quality_flags": ["unverified_ai_suggestion"]},
        {**REGISTER, "is_user_supplied": True},
        {**REGISTER, "current_revision_id": "r2", "source_revision_id": "r1"},
        {**REGISTER, "current_sha256": "b" * 64, "sha256": "a" * 64},
        {**ISSUE, "context_scope": {"case_id": "case-2"}},
        {**REGISTER, "context_scope": {"project_id": "project-2"}},
        {**REGISTER, "context_scope": {}},
    ],
)
def test_missing_stale_altered_unauthorised_or_cross_case_context_is_never_exempt(changed: Dict[str, Any]) -> None:
    # Control 3.
    assert not is_exempt_context_row(changed, PLEADING, SCOPE)
    assert INADMISSIBLE in _report([changed, EVIDENCE], PLEADING)["approval_blockers"]


def test_context_rows_never_support_an_amount_date_or_clause() -> None:
    # Control 5: the figure and the date are only in context rows, so the pleading's use of them is unsupported.
    report = _report([ISSUE, REGISTER, EVIDENCE], "1. The Claimant claims INR 9,99,999.00 from 14/03/2026 [S3: C-1].\n")
    blockers = " ".join(report["approval_blockers"])
    assert "monetary amounts not present" in blockers and "dates not present" in blockers


def test_a_manual_fact_still_blocks_whether_cited_or_not() -> None:
    assert INADMISSIBLE in _report([EVIDENCE, MANUAL], PLEADING)["approval_blockers"]


def test_only_an_approved_server_classified_analysis_row_is_case_analysis() -> None:
    # Control 4.
    assert is_case_analysis(ISSUE)
    assert not is_case_analysis({**ISSUE, "verification_status": "needs_review"})
    assert not is_case_analysis({**ISSUE, "ledger_context": None})
    assert not is_case_analysis(MANUAL)
    assert exempt_context_keys([ISSUE, REGISTER, EVIDENCE], PLEADING, SCOPE) == ["S1", "S2"]


def test_the_generated_annexure_list_names_no_context_row() -> None:
    from rbac_backend.services.arbitration_drafting.generator import ArbitrationDraftGenerator

    text = ArbitrationDraftGenerator.__new__(ArbitrationDraftGenerator)._annexure_text([ISSUE, REGISTER, EVIDENCE])
    assert "Site diary" in text and "CLM-009" not in text and "ISS-1" not in text


def test_the_filed_source_schedule_carries_evidence_only() -> None:
    from rbac_backend.services.arbitration_drafting.case_workspace import _filing_projection

    filed = _filing_projection({"_id": "v1", "source_ledger": [ISSUE, REGISTER, EVIDENCE], "full_markdown": PLEADING})
    assert [row["source_key"] for row in filed["source_ledger"]] == ["S3"]
    assert filed["source_schedule"]["context_policy"]["hash"] == CONTEXT_EXEMPTION_POLICY_HASH


def test_the_workflow_honours_only_exemptions_recorded_under_the_current_policy() -> None:
    from rbac_backend.services.arbitration_drafting.workflow_validation import ArbitrationValidationOrchestrator

    def version(policy_hash: str) -> Dict[str, Any]:
        return {"_id": "v1", "draft_id": "d1", "version": 1, "full_markdown": PLEADING, "version_hash": "h" * 64,
                "source_ledger": [REGISTER, EVIDENCE],
                "structured_output": {"approval_blockers": [], "context_rows_exempted": ["S2"],
                                      "context_policy": {"hash": policy_hash}}}

    def drift(report: Dict[str, Any]) -> List[str]:
        return [i["message"] for i in report["blockers"] if i.get("code") == "source_not_authoritative"]

    validation = ArbitrationValidationOrchestrator()
    current = _run(validation.evaluate(version(CONTEXT_EXEMPTION_POLICY_HASH), branches=("source_drift",)))
    assert drift(current) == [], drift(current)
    stale_policy = _run(validation.evaluate(version("0" * 64), branches=("source_drift",)))
    assert any("S2" in message for message in drift(stale_policy))
