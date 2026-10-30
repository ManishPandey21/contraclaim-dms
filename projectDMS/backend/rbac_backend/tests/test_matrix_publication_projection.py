"""One safe projection for the matrix publication boundary.

`GET /cases/{id}/{matrix_slug}` returns `list_matrix_rows` output wrapped in
`ArbitrationMatrixRow(**row)` (extra="allow"), so every derived field is
serialized raw. The internal agent path (`_authorised_matrix_rows`) gates these;
the API read did not. `safe_matrix_rows` is the shared boundary - internal and
API may format differently but must agree on publication eligibility.

Each matrix slug's derived fields resolve through the SAME authority resolver
that already governs the ledger: document-index via the document, clause-matrix
via the clause's parent, chronology-matrix via the event's provenance. Manual /
application rows (issue, claim, etc.) carry matrix-authored content and are
untouched.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.publication_policy import safe_matrix_rows

MARKER = "MATRIX-DERIVED-FROM-BLOCKED"


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
        self._c = {n: _Collection(r) for n, r in collections.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._c.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


CLEAN = {"_id": "doc-1", "processing_status": "metadata_extracted"}
BLOCKED = {"_id": "doc-1", "processing_status": "human_review_required"}
FAILED = {"_id": "doc-1", "processing_status": "failed"}
DUPLICATE = {"_id": "doc-1", "processing_status": "metadata_extracted", "duplicate_status": "duplicate"}


def _project(slug, rows, **collections):
    return asyncio.run(safe_matrix_rows(_DB(**collections), rows, slug))


# --- document-index: relevance_note / summary / document_type ------------------


def _doc_index_row() -> Dict[str, Any]:
    return {
        "_id": "idx-1", "source_type": "document", "source_id": "doc-1", "exhibit_id": "C-1",
        "relevance_note": f"{MARKER}note", "summary": f"{MARKER}sum", "document_type": f"{MARKER}type",
    }


def _di_text(rows) -> str:
    r = rows[0]
    return " ".join(str(r.get(k) or "") for k in ("relevance_note", "summary", "document_type"))


def test_a_clean_document_index_row_keeps_its_derivatives() -> None:
    assert MARKER in _di_text(_project("document-index", [_doc_index_row()], documents=[CLEAN]))


def test_a_blocked_document_index_row_withholds_all_derivatives() -> None:
    rows = _project("document-index", [_doc_index_row()], documents=[BLOCKED])

    assert MARKER not in _di_text(rows)
    assert rows[0]["exhibit_id"] == "C-1"  # identity metadata preserved


def test_a_quarantined_document_index_row_withholds_derivatives() -> None:
    assert MARKER not in _di_text(_project("document-index", [_doc_index_row()], documents=[DUPLICATE]))


def test_an_operationally_failed_document_index_row_keeps_derivatives() -> None:
    assert MARKER in _di_text(_project("document-index", [_doc_index_row()], documents=[FAILED]))


def test_a_missing_parent_document_fails_closed() -> None:
    assert MARKER not in _di_text(_project("document-index", [_doc_index_row()], documents=[]))


# --- clause-matrix: clause_text_excerpt via the clause's parent ----------------


def test_a_blocked_clause_matrix_row_withholds_the_excerpt() -> None:
    row = {"_id": "cm-1", "source_type": "clause", "clause_source_id": "clause-9",
           "clause_text_excerpt": f"{MARKER}excerpt"}
    rows = _project("clause-matrix", [row], contract_clauses=[{"_id": "clause-9", "document_id": "doc-1"}], documents=[BLOCKED])

    assert MARKER not in str(rows[0].get("clause_text_excerpt") or "")


def test_a_clean_clause_matrix_row_keeps_the_excerpt() -> None:
    row = {"_id": "cm-1", "source_type": "clause", "clause_source_id": "clause-9",
           "clause_text_excerpt": f"{MARKER}excerpt"}
    rows = _project("clause-matrix", [row], contract_clauses=[{"_id": "clause-9", "document_id": "doc-1"}], documents=[CLEAN])

    assert MARKER in str(rows[0]["clause_text_excerpt"])


# --- chronology-matrix: event via the event's provenance -----------------------


def test_a_blocked_chronology_matrix_row_withholds_the_event() -> None:
    row = {"_id": "ch-1", "chronology_event_id": "ev-1", "event": f"{MARKER}event"}
    rows = _project(
        "chronology-matrix", [row],
        matter_chronology_events=[{"_id": "ev-1", "source_document_id": "doc-1"}],
        documents=[BLOCKED],
    )

    assert MARKER not in str(rows[0].get("event") or "")


def test_a_manual_chronology_matrix_row_keeps_the_event() -> None:
    row = {"_id": "ch-1", "chronology_event_id": "ev-1", "event": f"{MARKER}event"}
    rows = _project(
        "chronology-matrix", [row],
        matter_chronology_events=[{"_id": "ev-1", "description": "manual"}],
        documents=[BLOCKED],
    )

    assert MARKER in str(rows[0]["event"])


# --- application matrix slugs are untouched ------------------------------------


def test_an_application_matrix_slug_is_returned_unchanged() -> None:
    row = {"_id": "cl-1", "claim_head": "EOT", "facts": f"{MARKER}matrix-authored facts"}
    rows = _project("claim-matrix", [row], documents=[BLOCKED])

    assert MARKER in str(rows[0]["facts"])
