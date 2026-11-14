"""The producer gate closed one read path and left its twin open.

`_find_source_rows` now filters raw source collections through
`_authorised_source_rows`. `_matrix_rows` - which reads PERSISTED matrix rows
written by an earlier run - had no equivalent, and nothing ever revisits a
`document-index` row when its document's authority later changes
(`_insert_matrix_row` returns early on an existing row and never deletes).

So a `relevance_note` condensed while the document was clean keeps deciding
things after the quality gate rejects that document. Three deterministic agents
read it, and none of them display it - which is exactly why it went unnoticed:

* `_notice_compliance` - `_looks_like_notice` decides whether a notice-
  compliance row EXISTS, and `_notice_type` labels it. Notice compliance is
  condition-precedent analysis, so a phantom row changes the legal position.
* `_expert_report_documents` - nominates which document is the delay/quantum
  expert report source.
* `_review_consistency` - raises `final_bill_or_no_dues_waiver_risk`, a legal
  risk assertion persisted onto claim rows and shown to the user.

Each test below removes the note as a control, to prove the note - and not the
row's metadata - is what drives the outcome.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.arbitration_drafting.agents.deterministic import (
    DeterministicArbitrationAgent,
)

CASE = {"_id": "case-1", "organization_id": "org-1", "project_id": "project-1"}

# Metadata carrying no notice, expert or waiver signal of its own.
NEUTRAL = {"title": "Scan_0142.pdf", "document_type": "Correspondence", "letter_no": ""}

NOTICE_NOTE = "Contractor's extension of time notice recording hindrance to the critical path."
EXPERT_NOTE = "Independent delay expert analysis report on critical path impact."
WAIVER_NOTE = "Acceptance of the final bill and issue of a no dues certificate."


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
        if isinstance(expected, dict) and "$in" in expected:
            if not any(str(row.get(key)) == str(c) for c in expected["$in"]):
                return False
            continue
        if str(row.get(key)) != str(expected):
            return False
    return True


class _DB:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self._collections = {n: _Collection(r) for n, r in collections.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


def _index_row(note: Optional[str]) -> Dict[str, Any]:
    row = {
        "_id": "idx-1",
        "case_id": "case-1",
        "source_id": "doc-1",
        "source_type": "document",
        "exhibit_id": "C-1",
        **NEUTRAL,
    }
    if note is not None:
        row["relevance_note"] = note
    return row


def _run(agent_type: str, note: Optional[str], status: Optional[str]) -> _DB:
    documents = []
    if status is not None:
        documents = [{"_id": "doc-1", "processing_status": status}]
    db = _DB(
        documents=documents,
        arbitration_document_index=[_index_row(note)],
        arbitration_claim_matrix=[
            {"_id": "claim-1", "case_id": "case-1", "claim_no": "C-1", "claim_head": "EOT"}
        ],
    )
    agent = DeterministicArbitrationAgent(
        db=db, case=CASE, draft_id="draft-1", options={}, current_user={"id": "u-1"}
    )
    asyncio.run(agent.run(agent_type))
    return db


# --- notice compliance: the row's very existence -------------------------------


def _notice_rows(note: Optional[str], status: Optional[str]) -> List[Dict[str, Any]]:
    return _run("notice-compliance", note, status)["arbitration_notice_compliance"].rows


def test_the_note_alone_creates_the_notice_row() -> None:
    """Control. Without this the tests below prove nothing."""
    assert _notice_rows(NOTICE_NOTE, "metadata_extracted")
    assert not _notice_rows(None, "metadata_extracted")


def test_a_blocked_document_cannot_create_a_notice_compliance_row() -> None:
    assert not _notice_rows(NOTICE_NOTE, "human_review_required"), (
        "a document under an adverse quality verdict was still promoted into "
        "the notice-compliance matrix by its stale relevance_note"
    )


def test_a_quarantined_document_cannot_create_a_notice_compliance_row() -> None:
    db = _DB(
        documents=[
            {"_id": "doc-1", "processing_status": "metadata_extracted", "duplicate_status": "duplicate"}
        ],
        arbitration_document_index=[_index_row(NOTICE_NOTE)],
    )
    agent = DeterministicArbitrationAgent(
        db=db, case=CASE, draft_id="draft-1", options={}, current_user={"id": "u-1"}
    )
    asyncio.run(agent.run("notice-compliance"))

    assert not db["arbitration_notice_compliance"].rows


def test_an_operationally_failed_document_still_creates_its_notice_row() -> None:
    """Model B: a worker crash must not delete a notice from the analysis."""
    assert _notice_rows(NOTICE_NOTE, "failed")


def test_a_vanished_source_document_cannot_create_a_notice_row() -> None:
    assert not _notice_rows(NOTICE_NOTE, None)


# --- expert report nomination --------------------------------------------------


def _expert_source_ids(note: Optional[str], status: Optional[str]) -> List[Any]:
    rows = _run("delay-expert", note, status)["arbitration_expert_alignment"].rows
    return [row.get("expert_report_source_id") for row in rows]


def test_a_blocked_document_is_not_nominated_as_the_expert_report() -> None:
    assert "doc-1" in _expert_source_ids(EXPERT_NOTE, "metadata_extracted")
    assert "doc-1" not in _expert_source_ids(EXPERT_NOTE, "human_review_required")


# --- case-level red flag -------------------------------------------------------


def _warnings(note: Optional[str], status: Optional[str]) -> str:
    db = _DB(
        documents=[{"_id": "doc-1", "processing_status": status}] if status else [],
        arbitration_document_index=[_index_row(note)],
        arbitration_claim_matrix=[
            {"_id": "claim-1", "case_id": "case-1", "claim_no": "C-1", "claim_head": "EOT"}
        ],
    )
    agent = DeterministicArbitrationAgent(
        db=db, case=CASE, draft_id="draft-1", options={}, current_user={"id": "u-1"}
    )
    asyncio.run(agent.run("review-consistency"))
    return " ".join(agent.warnings)


def test_a_blocked_document_cannot_raise_the_waiver_red_flag() -> None:
    flag = "final_bill_or_no_dues_waiver_risk"

    assert flag in _warnings(WAIVER_NOTE, "metadata_extracted")
    assert flag not in _warnings(WAIVER_NOTE, "human_review_required")
    assert flag not in _warnings(None, "metadata_extracted")
