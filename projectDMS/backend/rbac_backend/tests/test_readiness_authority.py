"""Filing readiness must not count evidence nobody may consume.

`_compute_readiness_checks` decides `document_index_verified` and
`clause_support` on presence plus approval only:

    usable_documents = [row for row in document_rows
                        if row.get("source_id") and row.get("exhibit_id")
                        and _is_ready_row(row)]

No text is rendered, so every display-side guard is bypassed - and readiness is
the gate that authorises filing. A document approved into the index while it
was clean keeps satisfying "at least one verified and approved exhibit-backed
document is available" after the quality gate rejects it, and `clause_support`
is satisfied by a `clause_text_excerpt` that all four read paths now withhold.

Influence, not disclosure. This is the same class as the bundle-volume routing
defect: the blocked content never appears, it just decides something.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.arbitration_drafting.case_workspace import (
    ArbitrationCaseWorkspaceService,
)

CASE = {"_id": "case-1", "organization_id": "org-1", "project_id": "project-1"}


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        expected = query.get("_id")
        for row in self.rows:
            if isinstance(expected, dict) and "$in" in expected:
                if any(str(row.get("_id")) == str(c) for c in expected["$in"]):
                    return row
            elif str(row.get("_id")) == str(expected):
                return row
        return None


class _DB:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self._collections = {n: _Collection(r) for n, r in collections.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


APPROVED = {
    "approval_status": "approved",
    "verification_status": "approved",
    "readiness_status": "approved",
    "human_approval_status": "approved",
}


def _document_row() -> Dict[str, Any]:
    return {
        "_id": "idx-1",
        "case_id": "case-1",
        "source_id": "doc-1",
        "source_type": "document",
        "exhibit_id": "C-1",
        "title": "Delay notice",
        **APPROVED,
    }


def _clause_row() -> Dict[str, Any]:
    return {
        "_id": "cm-1",
        "case_id": "case-1",
        "clause_source_id": "clause-21-2",
        "clause_number": "21.2",
        "clause_text_excerpt": "The Contractor shall give notice.",
        **APPROVED,
    }


def _check(documents: List[Dict[str, Any]], key: str, matrix: str, row) -> str:
    service = ArbitrationCaseWorkspaceService(
        _DB(documents=documents, contract_clauses=[{"_id": "clause-21-2", "document_id": "doc-1"}])
    )
    checks = asyncio.run(
        service._compute_readiness_checks(CASE, {matrix: [row]}, draft_type="statement_of_claim")
    )
    for check in checks:
        if check.get("check_key") == key:
            return str(check.get("status"))
    raise AssertionError(f"{key} check not produced")


def _doc(status: str, **extra: Any) -> Dict[str, Any]:
    return {"_id": "doc-1", "processing_status": status, **extra}


def _document_check(status: Optional[str], **extra: Any) -> str:
    documents = [_doc(status, **extra)] if status else []
    return _check(documents, "document_index_verified", "document-index", _document_row())


def _clause_check(status: str, **extra: Any) -> str:
    return _check([_doc(status, **extra)], "clause_support", "clause-matrix", _clause_row())


# --- documents -----------------------------------------------------------------


def test_a_clean_approved_document_makes_the_case_ready() -> None:
    """The guard must not make filing impossible."""
    assert "ready" in _document_check("metadata_extracted").lower()


def test_a_blocked_document_does_not_make_the_case_ready() -> None:
    status = _document_check("human_review_required")

    assert "ready" not in status.lower(), (
        "the case reported filing-ready evidence on the strength of a document "
        "under an adverse quality verdict"
    )


def test_a_quarantined_document_does_not_make_the_case_ready() -> None:
    assert "ready" not in _document_check(
        "metadata_extracted", duplicate_status="duplicate"
    ).lower()


def test_a_deleted_document_does_not_make_the_case_ready() -> None:
    assert "ready" not in _document_check(
        "metadata_extracted", lifecycle_state="deleted"
    ).lower()


def test_an_operationally_failed_document_stays_ready_under_last_known_good() -> None:
    """Model B: a worker crash must not un-file a prepared case."""
    assert "ready" in _document_check("failed").lower()


def test_a_vanished_source_document_does_not_make_the_case_ready() -> None:
    assert "ready" not in _document_check(None).lower()


# --- clauses -------------------------------------------------------------------


def test_a_clean_parent_supports_the_clause_readiness_check() -> None:
    assert "ready" in _clause_check("metadata_extracted").lower()


def test_a_blocked_parent_does_not_support_the_clause_readiness_check() -> None:
    assert "ready" not in _clause_check("human_review_required").lower()


def test_the_arbitration_agreement_still_supports_clause_readiness() -> None:
    """The synthetic row has no extraction behind it; it must still count."""
    from rbac_backend.services.publication_policy import synthetic_case_clause_id

    row = {**_clause_row(), "clause_source_id": synthetic_case_clause_id("case-1")}
    status = _check([_doc("human_review_required")], "clause_support", "clause-matrix", row)

    assert "ready" in status.lower()
