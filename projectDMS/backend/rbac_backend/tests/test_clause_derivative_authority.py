"""The clause axis of the same defect, left open when relevance_note closed.

`clause_text_excerpt` is up to 900 characters of a document's extracted text,
condensed into a clause-matrix row at derivation time
(`agents/deterministic.py:242`). The codebase already classifies these rows as
document-governed - `_authorised_source_rows` resolves `contract_clauses.
document_id` through `resolve_document_authority` before indexing them - but
only on the WRITE side. Nothing re-checked the excerpt on read, which is
precisely the case the policy exists for: authority changes after the
derivative exists.

Two things had to be true at once for this to hide:

* the id is stored under `clause_source_id`, not `source_id`, so the derived-
  text helper found no id at all and would have denied everything, turning a
  leak into a total over-block if the guard were bolted on unchanged;
* a clause-matrix row carries no `source_type`, so the helper would have
  defaulted to `document` and looked a CHILD id up in `documents`.

So the fix has to supply both the type and the right id field. These tests pin
all four read sites, plus the over-block that a careless fix would create.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.arbitration_drafting.case_workspace import (
    ArbitrationCaseWorkspaceService,
)
from rbac_backend.services.arbitration_drafting.context import ArbitrationContextBuilder

EXCERPT = "21.2 The Contractor shall within 28 days give notice of any claim."
FALLBACK = "Obligation to notify within 28 days."


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = list(rows)

    def sort(self, *a: Any, **k: Any) -> "_Cursor":
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return list(self.rows)


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Optional[Dict[str, Any]] = None) -> _Cursor:
        return _Cursor(self.rows)

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        expected = query.get("_id")
        for row in self.rows:
            if isinstance(expected, dict) and "$in" in expected:
                if any(str(row.get("_id")) == str(c) for c in expected["$in"]):
                    return row
            elif expected is None or str(row.get("_id")) == str(expected):
                return row
        return None


class _DB:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self._collections = {n: _Collection(r) for n, r in collections.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


CLAUSE = {"_id": "clause-21-2", "document_id": "doc-contract-1", "text": EXCERPT}


def _matrix_row(**extra: Any) -> Dict[str, Any]:
    row = {
        "_id": "cm-1",
        "case_id": "case-1",
        "clause_number": "21.2",
        "topic": "Notice of claim",
        "clause_source_id": "clause-21-2",
        "clause_text_excerpt": EXCERPT,
        "obligation_or_right": FALLBACK,
        "approval_status": "approved",
        "verification_status": "approved",
    }
    row.update(extra)
    return row


def _db(status: Optional[str], row: Optional[Dict[str, Any]] = None) -> _DB:
    documents = [{"_id": "doc-contract-1", "processing_status": status}] if status else []
    return _DB(
        documents=documents,
        contract_clauses=[dict(CLAUSE)],
        arbitration_clause_matrix=[row or _matrix_row()],
    )


# --- site 1: the arbitration source ledger (reaches the drafting LLM) ----------


def _ledger_snippet(status: Optional[str], row: Optional[Dict[str, Any]] = None) -> str:
    builder = ArbitrationContextBuilder(_db(status, row))
    rows = asyncio.run(builder._clause_matrix_sources("case-1", 0, False, []))
    assert rows, "the approved clause row should still produce a ledger entry"
    return str(rows[0]["snippet"])


def test_a_clean_parent_document_still_supplies_the_clause_excerpt() -> None:
    assert EXCERPT in _ledger_snippet("metadata_extracted")


def test_a_blocked_parent_document_withdraws_the_clause_excerpt() -> None:
    snippet = _ledger_snippet("human_review_required")

    assert EXCERPT not in snippet, (
        "900 characters of a rejected document's extracted text still reached "
        "the arbitration source ledger, reported as approved and strong"
    )


def test_the_in_app_fallback_survives_when_the_excerpt_is_withdrawn() -> None:
    """`obligation_or_right` is authored in the matrix, not extracted."""
    assert FALLBACK in _ledger_snippet("human_review_required")


def test_an_operational_failure_keeps_the_excerpt() -> None:
    assert EXCERPT in _ledger_snippet("failed")


def test_a_quarantined_parent_document_withdraws_the_excerpt() -> None:
    db = _DB(
        documents=[
            {
                "_id": "doc-contract-1",
                "processing_status": "metadata_extracted",
                "duplicate_status": "duplicate",
            }
        ],
        contract_clauses=[dict(CLAUSE)],
        arbitration_clause_matrix=[_matrix_row()],
    )
    rows = asyncio.run(
        ArbitrationContextBuilder(db)._clause_matrix_sources("case-1", 0, False, [])
    )

    assert EXCERPT not in str(rows[0]["snippet"])


def test_a_missing_parent_document_denies() -> None:
    assert EXCERPT not in _ledger_snippet(None)


# --- the over-block a careless fix would create --------------------------------


def test_a_clause_row_with_no_source_id_does_not_lose_its_in_app_text() -> None:
    """Guard against curing the leak by denying everything.

    A row with no resolvable clause id must still surrender the excerpt (it
    cannot be shown to be safe), but must keep the in-app `obligation_or_right`
    the matrix author wrote.
    """
    row = _matrix_row()
    row.pop("clause_source_id")

    snippet = _ledger_snippet("metadata_extracted", row)

    assert FALLBACK in snippet


# --- site 2: selected references built from case rows --------------------------


def _reference_snippet(status: Optional[str]) -> str:
    service = ArbitrationCaseWorkspaceService(_db(status))
    references = asyncio.run(
        service._references_from_case_rows(
            "draft-1", [], [_matrix_row()], {"id": "user-1"}
        )
    )
    clause_refs = [r for r in references if r.get("source_type") == "clause"]
    assert clause_refs, "the clause row should still produce a reference"
    return str(clause_refs[0].get("snippet") or "")


def test_a_blocked_parent_withdraws_the_excerpt_from_selected_references() -> None:
    assert EXCERPT not in _reference_snippet("human_review_required")


def test_a_clean_parent_keeps_the_excerpt_in_selected_references() -> None:
    assert EXCERPT in _reference_snippet("metadata_extracted")


# --- site 3: rehydration of an approved matrix selection -----------------------


def _rehydrated_snippet(status: Optional[str]) -> str:
    builder = ArbitrationContextBuilder(_db(status))
    result = asyncio.run(
        builder._rehydrate_matrix_reference(
            {"case_id": "case-1", "organization_id": None, "project_id": None},
            "cm-1",
            "clause",
        )
    )
    assert result, "the approved matrix row should rehydrate"
    return str(result.get("snippet") or "")


def test_a_blocked_parent_withdraws_the_excerpt_on_rehydration() -> None:
    assert EXCERPT not in _rehydrated_snippet("human_review_required")


def test_a_clean_parent_keeps_the_excerpt_on_rehydration() -> None:
    assert EXCERPT in _rehydrated_snippet("metadata_extracted")


# --- site 4: the generic analysis dereference ----------------------------------


class _CapturingRepository:
    def __init__(self) -> None:
        self.payloads: List[Dict[str, Any]] = []

    async def create_snapshot(self, *, run_id, kind, payload, effect_key):
        self.payloads.append(payload)
        return {"_id": "snap-1", "snapshot_hash": "hash-1"}


class _LeaselessDB(_DB):
    def __getattr__(self, name: str) -> _Collection:
        if name == "arbitration_analysis_leases":
            raise AttributeError(name)
        return self[name]


def _clause_facts(status: str) -> Dict[str, Any]:
    from rbac_backend.services.arbitration_drafting.workflow_domain import (
        ArbitrationWorkflowDomain,
    )

    domain = ArbitrationWorkflowDomain(
        _LeaselessDB(
            documents=[{"_id": "doc-contract-1", "processing_status": status}],
            contract_clauses=[dict(CLAUSE)],
        )
    )
    repository = _CapturingRepository()
    domain.repository = repository  # type: ignore[assignment]

    asyncio.run(
        domain._analyze_branch(
            {"_id": "run-1", "organization_id": "org-1", "project_id": "project-1"},
            "clause_interpretation",
            {
                "pleading_type": "statement_of_claim",
                "rows_by_matrix": {"clause-matrix": [_matrix_row()]},
                "document_signals": [],
                "opponent_snapshot": None,
            },
        )
    )
    findings = repository.payloads[0]["findings"]
    assert findings, "the branch produced no finding to inspect"
    return findings[0]["facts"]


def test_clause_text_excerpt_is_a_declared_field_that_becomes_content() -> None:
    from rbac_backend.services.arbitration_drafting.workflow_domain import (
        ANALYSIS_FINDING_FIELDS,
    )

    assert "clause_text_excerpt" in ANALYSIS_FINDING_FIELDS["clause_interpretation"]


def test_a_blocked_parent_keeps_the_excerpt_out_of_the_analysis_artifact() -> None:
    facts = _clause_facts("human_review_required")

    assert EXCERPT not in str(facts)
    # Metadata must survive - the clause is still identifiable.
    assert facts.get("clause_number") == "21.2"


def test_a_clean_parent_keeps_the_excerpt_in_the_analysis_artifact() -> None:
    assert _clause_facts("metadata_extracted").get("clause_text_excerpt") == EXCERPT
