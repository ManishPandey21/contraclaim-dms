"""Class C: the second contract-retrieval stack never resolved authority.

`RetrievalService.search` drops every fused result whose `document_id` is
non-consumable (`retrieval/service.py:173`). `ContractService.search_contracts`
is the same operation done in parallel - it reads `document_vectors` /
`contract_clauses` directly and returned their text with no authority axis at
all, so a blocked or quarantined contract's clause reached the drafting prompt,
the pleading markdown and `POST /api/contracts/search`.

The fix is a shared filter, not a copied one: both stacks call
`publication_policy.blocked_document_ids`. These tests drive the real
`_assemble_results` join - the boundary where chunk text is materialised - with
an injected fake `documents` collection carrying the authority state, and
assert the ACTUAL returned chunks.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.contract_service import ContractService

MARKER = "SECRET-BLOCKED-EXTRACTION-TEXT"


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = list(rows)

    def sort(self, *a: Any, **k: Any) -> "_Cursor":
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return list(self.rows)

    def __aiter__(self):
        async def _gen():
            for row in self.rows:
                yield row

        return _gen()


class _VectorsCollection:
    def __init__(self, chunks: List[Dict[str, Any]]) -> None:
        self.chunks = chunks
        self.database: Any = None  # set to the owning _DB, like Motor's .database

    def find(self, query: Dict[str, Any]) -> _Cursor:
        return _Cursor(self.chunks)


class _DocumentsCollection:
    def __init__(self, documents: List[Dict[str, Any]]) -> None:
        self.documents = documents

    def find(self, query: Dict[str, Any]) -> _Cursor:
        wanted = {str(v) for v in (query.get("_id", {}) or {}).get("$in", [])}
        return _Cursor([d for d in self.documents if str(d.get("_id")) in wanted])


class _DB:
    def __init__(self, documents: List[Dict[str, Any]]) -> None:
        self.documents = _DocumentsCollection(documents)


def _chunk(document_id: str) -> Dict[str, Any]:
    return {
        "document_id": document_id,
        "clause_number": "8.4",
        "clause_start_position": 0,
        "chunk_index": 0,
        "text": f"GCC 8.4: {MARKER} the contractor is in culpable delay.",
        "uploadType": "contract",
    }


def _assemble(document_status: Optional[str], **doc_extra: Any) -> List[Dict[str, Any]]:
    """Run the real join with one candidate clause over one document."""
    service = ContractService()
    vectors = _VectorsCollection([_chunk("doc-1")])
    documents = []
    if document_status is not None:
        documents = [{"_id": "doc-1", "processing_status": document_status, **doc_extra}]
    db = _DB(documents)
    vectors.database = db
    service._vectors = vectors
    service._db = db

    lexical = [
        {
            "document_id": "doc-1",
            "clause_number": "8.4",
            "clause_start_position": 0,
            "best_score": 1.0,
        }
    ]
    normalized, _total, _more = asyncio.run(
        service._assemble_results(service._vectors, lexical, [], [], 10, 0)
    )
    return normalized


def _texts(rows: List[Dict[str, Any]]) -> str:
    return " ".join(str(row.get("text") or "") for row in rows)


# --- adverse axis --------------------------------------------------------------


def test_a_clean_contract_clause_is_returned() -> None:
    rows = _assemble("metadata_extracted")

    assert MARKER in _texts(rows)


def test_a_blocked_contract_clause_is_withheld() -> None:
    rows = _assemble("human_review_required")

    assert MARKER not in _texts(rows), (
        "clause text from a document under adverse quality verdict was still "
        "returned by the parallel contract-retrieval stack"
    )
    assert rows == []


# --- quarantine axis -----------------------------------------------------------


def test_a_quarantined_duplicate_clause_is_withheld() -> None:
    rows = _assemble("metadata_extracted", duplicate_status="duplicate")

    assert MARKER not in _texts(rows)


def test_a_deleted_document_clause_is_withheld() -> None:
    rows = _assemble("metadata_extracted", lifecycle_state="deleted")

    assert MARKER not in _texts(rows)


# --- Model B -------------------------------------------------------------------


def test_an_operational_failure_keeps_the_clause_under_last_known_good() -> None:
    rows = _assemble("failed")

    assert MARKER in _texts(rows)


# --- missing canonical source --------------------------------------------------


def test_a_clause_whose_document_is_absent_is_left_alone() -> None:
    """An orphaned vector is a different problem; do not silently swallow it.

    `blocked_document_ids` only denies documents it positively resolved and
    judged unusable, matching RetrievalService, so a missing document does not
    turn contract search into a blanket refusal.
    """
    rows = _assemble(None)

    assert MARKER in _texts(rows)


# --- the two stacks must not drift again ---------------------------------------


def test_both_retrieval_stacks_share_one_authority_filter() -> None:
    """Anti-drift: the defect was two implementations of one rule.

    RetrievalService's private filter must delegate to the shared helper, so a
    future change to containment cannot pass one stack and miss the other.
    """
    import inspect

    from rbac_backend.retrieval.service import RetrievalService

    source = inspect.getsource(RetrievalService._blocked_document_ids)

    # Not the method's own name (tautological) - the module-level helper import.
    assert "publication_policy" in source and "blocked_document_ids" in source, (
        "RetrievalService must delegate to publication_policy.blocked_document_ids"
    )
