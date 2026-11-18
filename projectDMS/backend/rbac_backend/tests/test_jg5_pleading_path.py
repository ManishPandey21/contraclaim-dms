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
    is_case_analysis,
    is_uncited_context_row,
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
# 5. context rows in the ledger
# --------------------------------------------------------------------------- #

ISSUE = {"source_key": "S1", "source_type": "issue_matrix", "verification_status": "approved", "is_user_supplied": True,
         "quality_flags": ["user_supplied"], "citation": "I-1", "label": "Whether the handover was late"}
REGISTER = {"source_key": "S2", "source_type": "claim", "source_origin": "claim_register", "verification_status": "submitted",
            "is_user_supplied": False, "citation": "CLM-9", "label": "Another claim"}
EVIDENCE = {"source_key": "S3", "source_type": "document", "verification_status": "verified", "is_user_supplied": False,
            "citation": "C-1", "label": "Site diary"}
MANUAL = {"source_key": "S4", "source_type": "manual_fact", "verification_status": "verified", "is_user_supplied": True,
          "quality_flags": ["user_supplied"], "citation": "Note", "label": "A note"}


def _report(ledger: List[Dict[str, Any]], markdown: str) -> Dict[str, Any]:
    context = {"source_ledger": ledger, "draft": {"case_id": "case-1", "draft_type": "statement_of_defence"}}
    return ArbitrationDraftValidator().validation_report(context, markdown)


INADMISSIBLE = "Draft source ledger contains user-supplied or unverified selected evidence that is not admissible for filing."


def test_uncited_analysis_and_register_rows_do_not_block_a_draft() -> None:
    report = _report([ISSUE, REGISTER, EVIDENCE], "1. The handover was late [S3: C-1].\n")
    assert INADMISSIBLE not in report["approval_blockers"], report["approval_blockers"]
    assert not any("not yet verified" in item for item in report["approval_blockers"])


@pytest.mark.parametrize("cited", [ISSUE, REGISTER])
def test_a_cited_analysis_or_register_row_is_an_unverified_citation(cited: Dict[str, Any]) -> None:
    report = _report([ISSUE, REGISTER, EVIDENCE], f"1. The handover was late [S3: C-1] [{cited['source_key']}: {cited['citation']}].\n")
    assert any("not yet verified" in item and cited["source_key"] in item for item in report["approval_blockers"]), report


def test_a_manual_fact_still_blocks_whether_cited_or_not() -> None:
    report = _report([EVIDENCE, MANUAL], "1. The handover was late [S3: C-1].\n")
    assert INADMISSIBLE in report["approval_blockers"]


def test_only_an_approved_analysis_row_is_context() -> None:
    assert is_case_analysis(ISSUE)
    assert not is_case_analysis({**ISSUE, "verification_status": "needs_review"})
    assert not is_case_analysis(MANUAL)
    assert is_uncited_context_row(REGISTER, set()) and not is_uncited_context_row(REGISTER, {"S2"})
    assert not is_uncited_context_row(EVIDENCE, set())
