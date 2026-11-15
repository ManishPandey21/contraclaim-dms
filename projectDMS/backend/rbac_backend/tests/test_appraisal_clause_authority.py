"""`GET /api/contracts/clauses` was a third parallel retrieval stack.

`AppraisalService.list_clauses` reads `document_vectors` filtered only by scope
and returns `text`/`text_enriched` as `snippet`, straight into the API
response. `document_vectors` is a DOCUMENT_CHILD_COLLECTION with no
`processing_status` of its own, so the parent document must be resolved - and
it never was. Same class as the ContractService bug, on a whole endpoint.

Fixed with the shared `blocked_document_ids` filter; these assert the actual
returned clause rows.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.contract_appraisal.service import AppraisalService

MARKER = "SECRET-BLOCKED-CLAUSE"


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = list(rows)

    def limit(self, value: int) -> "_Cursor":
        self.rows = self.rows[:value]
        return self

    def __aiter__(self):
        async def _gen():
            for row in self.rows:
                yield row

        return _gen()

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return list(self.rows)

    def find(self, query: Dict[str, Any]) -> "_Cursor":
        wanted = {str(v) for v in (query.get("_id", {}) or {}).get("$in", [])}
        return _Cursor([r for r in self.rows if str(r.get("_id")) in wanted])


class _Vectors:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Dict[str, Any]) -> _Cursor:
        return _Cursor(self.rows)


class _Documents:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Dict[str, Any]) -> _Cursor:
        wanted = {str(v) for v in (query.get("_id", {}) or {}).get("$in", [])}
        return _Cursor([r for r in self.rows if str(r.get("_id")) in wanted])


class _DB:
    def __init__(self, vectors: List[Dict[str, Any]], documents: List[Dict[str, Any]]) -> None:
        self.document_vectors = _Vectors(vectors)
        self.documents = _Documents(documents)

    def __getattr__(self, name: str) -> _Vectors:
        # Collections the repository touches but this test does not exercise.
        return _Vectors([])


def _clauses(status: Optional[str], **doc_extra: Any) -> List[Dict[str, Any]]:
    chunk = {
        "_id": "vec-1",
        "document_id": "doc-1",
        "clause_number": "8.4",
        "clause_title": "EOT",
        "text": f"{MARKER}: Contractor is entitled to 45 days EOT.",
    }
    documents = []
    if status is not None:
        documents = [{"_id": "doc-1", "processing_status": status, **doc_extra}]
    service = AppraisalService(db=_DB([chunk], documents))
    return asyncio.run(service.list_clauses({"organization_id": "org-1"}))


def _snippets(rows: List[Dict[str, Any]]) -> str:
    return " ".join(str(r.get("snippet") or "") for r in rows)


def test_a_clean_clause_is_listed() -> None:
    assert MARKER in _snippets(_clauses("metadata_extracted"))


def test_a_blocked_clause_is_withheld() -> None:
    rows = _clauses("human_review_required")
    assert MARKER not in _snippets(rows)
    assert rows == []


def test_a_quarantined_clause_is_withheld() -> None:
    assert MARKER not in _snippets(_clauses("metadata_extracted", duplicate_status="duplicate"))


def test_an_operational_failure_keeps_the_clause() -> None:
    assert MARKER in _snippets(_clauses("failed"))


def test_a_clause_with_no_parent_document_is_left_alone() -> None:
    assert MARKER in _snippets(_clauses(None))
