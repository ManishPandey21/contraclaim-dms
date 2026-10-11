"""The Clause Checking Agent gated on a stale clause-level flag only.

`_load_records` reads `contract_clauses` filtered on `is_authorised_for_ai` -
fixed at index time from clause confidence, never revisited when the parent
document's authority becomes adverse. `contract_clauses` is a
DOCUMENT_CHILD_COLLECTION with no `processing_status`, so the parent must be
resolved. `cleaned_text` flows into `CitedClauseEvaluation.quoted_text` and the
letter-drafting prompt.

These assert the records the agent actually loads.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.letter_drafting.clause_checker import ClauseCheckingAgent

MARKER = "BLOCKED-CLAUSE-TEXT"


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


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Dict[str, Any]) -> _Cursor:
        wanted = (query.get("_id", {}) or {})
        if "$in" in wanted:
            ids = {str(v) for v in wanted["$in"]}
            return _Cursor([r for r in self.rows if str(r.get("_id")) in ids])
        return _Cursor(list(self.rows))


class _DB:
    def __init__(self, clauses: List[Dict[str, Any]], documents: List[Dict[str, Any]]) -> None:
        self._c = {"contract_clauses": _Collection(clauses), "documents": _Collection(documents)}

    def __getitem__(self, name: str) -> _Collection:
        return self._c.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


def _records(status: Optional[str], **doc_extra: Any) -> List[Dict[str, Any]]:
    clause = {
        "_id": "cc-1",
        "document_id": "doc-1",
        "clause_no": "8.4",
        "is_current": True,
        "is_authorised_for_ai": True,
        "cleaned_text": f"{MARKER}: Contractor is entitled to 45 days EOT.",
    }
    documents = []
    if status is not None:
        documents = [{"_id": "doc-1", "processing_status": status, **doc_extra}]
    agent = ClauseCheckingAgent(_DB([clause], documents))
    return asyncio.run(agent._load_records("org-1", "project-1"))


def _texts(rows: List[Dict[str, Any]]) -> str:
    return " ".join(str(r.get("cleaned_text") or "") for r in rows)


def test_a_clean_clause_is_loaded() -> None:
    assert MARKER in _texts(_records("metadata_extracted"))


def test_a_blocked_clause_is_not_loaded() -> None:
    rows = _records("human_review_required")
    assert MARKER not in _texts(rows)
    assert rows == []


def test_a_quarantined_clause_is_not_loaded() -> None:
    assert MARKER not in _texts(_records("metadata_extracted", duplicate_status="duplicate"))


def test_an_operational_failure_keeps_the_clause() -> None:
    assert MARKER in _texts(_records("failed"))


def test_a_clause_with_no_parent_document_is_left_alone() -> None:
    assert MARKER in _texts(_records(None))
