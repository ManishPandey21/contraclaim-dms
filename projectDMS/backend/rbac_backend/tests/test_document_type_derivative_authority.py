"""F4: `document_type` is a classification OF extracted text, not metadata.

`_document_type` (`deterministic.py:1422`) keyword-scans
`_document_title(document)` plus `subject`/`summary`/`description`/
`extracted_text` - the SAME field list `relevance_note` is condensed from at
`:193`. On the LLM path it is worse: `llm.py:571-574` writes `document_type`
and `relevance_note` out of one JSON object produced from a prompt containing
`full_content`. One sibling was on the derived-field registry; the other was
not.

`documents` rows carry no `document_type` of their own, so the keyword scan is
what decides in practice.

The consequence is not cosmetic. `_looks_like_notice` reads `document_type`,
and a hit PERSISTS a notice-compliance row - a standing assertion that a
contractual notice was served, which then feeds the source ledger and the
`notice_compliance_risk` readiness check. Condition-precedent analysis is
decided by it.

These tests assert the persisted compliance row, not that a field was blanked.
The control removes `document_type` to prove it is the sole cause.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.arbitration_drafting.agents.deterministic import (
    DeterministicArbitrationAgent,
)

CASE = {"_id": "case-1", "organization_id": "org-1", "project_id": "project-1"}


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = list(rows)

    def sort(self, *a: Any, **k: Any) -> "_Cursor":
        return self

    def limit(self, value: int) -> "_Cursor":
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return list(self.rows)


class _Collection:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None) -> None:
        self.rows = list(rows or [])

    def find(self, query: Optional[Dict[str, Any]] = None) -> _Cursor:
        return _Cursor([r for r in self.rows if _matches(r, query or {})])

    async def find_one(self, query: Optional[Dict[str, Any]] = None, *a: Any, **k: Any):
        rows = await self.find(query).to_list()
        return rows[0] if rows else None

    async def insert_one(self, row: Dict[str, Any]):
        self.rows.append(dict(row))
        return type("R", (), {"inserted_id": row.get("_id")})()

    async def update_one(self, query=None, update=None, *a: Any, **k: Any):
        row = await self.find_one(query)
        if row and update and "$set" in update:
            row.update(update["$set"])
        return type("R", (), {"matched_count": 1 if row else 0})()


def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in query.items():
        if isinstance(expected, dict) and "$exists" in expected:
            if bool(expected["$exists"]) != (key in row):
                return False
            continue
        if isinstance(expected, dict):
            continue
        if str(row.get(key)) != str(expected):
            return False
    return True


class _DB:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self._c = {n: _Collection(r) for n, r in collections.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._c.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


def _index_row(document_type: Optional[str]) -> Dict[str, Any]:
    """Neutral identity metadata; the classification is the only notice signal."""
    row = {
        "_id": "idx-1",
        "case_id": "case-1",
        "source_id": "doc-1",
        "source_type": "document",
        "exhibit_id": "C-1",
        "title": "SCAN-0042.pdf",
        "letter_no": "",
    }
    if document_type is not None:
        row["document_type"] = document_type
    return row


def _notice_rows(document_type: Optional[str], status: Optional[str], **doc: Any):
    documents = [{"_id": "doc-1", "processing_status": status, **doc}] if status else []
    db = _DB(documents=documents, arbitration_document_index=[_index_row(document_type)])
    agent = DeterministicArbitrationAgent(
        db=db, case=CASE, draft_id="draft-1", options={}, current_user={"id": "u-1"}
    )
    asyncio.run(agent.run("notice-compliance"))
    return db["arbitration_notice_compliance"].rows


# --- the premise: document_type alone creates the legal finding ----------------


def test_the_classification_alone_creates_the_compliance_row() -> None:
    assert _notice_rows("EOT notice", "metadata_extracted")
    assert not _notice_rows(None, "metadata_extracted")


# --- the defect ----------------------------------------------------------------


def test_a_blocked_source_cannot_create_a_notice_compliance_finding() -> None:
    assert not _notice_rows("EOT notice", "human_review_required"), (
        "a stale classification derived from rejected extracted text still "
        "asserted that a contractual notice was served"
    )


def test_a_quarantined_source_cannot_create_a_compliance_finding() -> None:
    assert not _notice_rows(
        "EOT notice", "metadata_extracted", duplicate_status="duplicate"
    )


def test_a_deleted_source_cannot_create_a_compliance_finding() -> None:
    assert not _notice_rows(
        "EOT notice", "metadata_extracted", lifecycle_state="deleted"
    )


def test_a_vanished_source_cannot_create_a_compliance_finding() -> None:
    assert not _notice_rows("EOT notice", None)


def test_an_operational_failure_keeps_the_classification_usable() -> None:
    """Model B: a worker crash must not erase a notice from the analysis."""
    assert _notice_rows("EOT notice", "failed")


# --- the registry, so the next sibling field cannot slip through ---------------


def test_document_type_is_registered_as_a_document_derivative() -> None:
    from rbac_backend.services.arbitration_drafting.agents.deterministic import (
        _DERIVED_MATRIX_FIELDS,
    )

    names = {field for field, _, _ in _DERIVED_MATRIX_FIELDS["document-index"]}

    assert "document_type" in names
    assert "relevance_note" in names
