"""The chronology `event` field: the same derivative, one column over.

`_chronology_matrix_sources` gates the chronology event's `description`
(`context.py`), and on the line above it takes `row["event"]` as the ledger
label with no check at all. Both hold the same text: the adapter writes
``event = f"{title}. {description}"`` (`deterministic.py:203`), where `title`
and `description` both come from the source document's extracted span
(`chronology.py:517`, `:529`).

Three separate paths read it and none were gated:

* WRITE - `_authorised_source_rows` filters `documents`, `document_vectors` and
  `contract_clauses`, and lets `matter_chronology_events` through untouched. So
  a blocked document could still father a brand-new chronology matrix row: the
  laundering case the guard exists to stop.
* READ, matrix - `_authorised_matrix_rows` returns early unless the matrix is
  `document-index`.
* READ, analysis - `ANALYSIS_FINDING_FIELDS["chronology"]` names `event`, but
  `DERIVED_ANALYSIS_FIELDS` did not, so the generic dereference passed it
  through verbatim into the immutable analysis artifact.

A manual chronology event has no `source_document_id` and must be unaffected -
over-blocking counsel's own timeline entry is its own defect.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.arbitration_drafting.agents.deterministic import (
    DeterministicArbitrationAgent,
)
from rbac_backend.services.arbitration_drafting.context import ArbitrationContextBuilder

SPAN = "SECRET BODY TEXT lifted verbatim from the blocked document"
MANUAL = "Counsel's own timeline entry."
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
        if name == "arbitration_analysis_leases":
            raise AttributeError(name)
        return self[name]


def _event(**extra: Any) -> Dict[str, Any]:
    event = {
        "_id": "ev-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "title": "Hindrance letter",
        "description": SPAN,
        "event_date": "2026-01-04",
        "verification_status": "approved",
    }
    event.update(extra)
    return event


def _doc(status: str) -> Dict[str, Any]:
    return {"_id": "doc-1", "processing_status": status}


# --- WRITE SIDE: the adapter must not launder a blocked source ------------------


def _adapter_rows(event: Dict[str, Any], documents: List[Dict[str, Any]]):
    db = _DB(matter_chronology_events=[event], documents=documents)
    agent = DeterministicArbitrationAgent(
        db=db, case=CASE, draft_id="draft-1", options={}, current_user={"id": "u-1"}
    )
    asyncio.run(agent.run("chronology-builder-adapter"))
    return db["arbitration_chronology_matrix"].rows


def test_a_clean_source_still_produces_a_chronology_matrix_row() -> None:
    rows = _adapter_rows(_event(source_document_id="doc-1"), [_doc("metadata_extracted")])

    assert rows and SPAN in str(rows[0].get("event"))


def test_a_blocked_source_cannot_father_a_new_chronology_row() -> None:
    rows = _adapter_rows(
        _event(source_document_id="doc-1"), [_doc("human_review_required")]
    )

    assert not [row for row in rows if SPAN in str(row.get("event") or "")], (
        "the adapter summarised a rejected extraction into a brand-new matrix "
        "row, which is exactly the laundering the write-side guard exists for"
    )


def test_a_quarantined_source_cannot_father_a_new_chronology_row() -> None:
    rows = _adapter_rows(
        _event(source_document_id="doc-1"),
        [{"_id": "doc-1", "processing_status": "metadata_extracted", "duplicate_status": "duplicate"}],
    )

    assert not [row for row in rows if SPAN in str(row.get("event") or "")]


def test_an_operational_failure_still_produces_the_row() -> None:
    rows = _adapter_rows(_event(source_document_id="doc-1"), [_doc("failed")])

    assert rows and SPAN in str(rows[0].get("event"))


def test_a_manual_event_is_unaffected_by_document_authority() -> None:
    rows = _adapter_rows(_event(description=MANUAL), [_doc("human_review_required")])

    assert rows and MANUAL in str(rows[0].get("event"))


# --- READ SIDE 1: the ledger label ---------------------------------------------


def _ledger(matrix_row: Dict[str, Any], documents: List[Dict[str, Any]]):
    builder = ArbitrationContextBuilder(
        _DB(
            arbitration_chronology_matrix=[matrix_row],
            matter_chronology_events=[_event(source_document_id="doc-1")],
            documents=documents,
        )
    )
    rows = asyncio.run(builder._chronology_matrix_sources("case-1", 0, False, []))
    assert rows, "the approved matrix row should still produce a ledger entry"
    return rows[0]


def _matrix_row(**extra: Any) -> Dict[str, Any]:
    row = {
        "_id": "cm-1",
        "case_id": "case-1",
        "chronology_event_id": "ev-1",
        "source_document_id": "doc-1",
        "event": f"Hindrance letter. {SPAN}",
        "verification_status": "approved",
    }
    row.update(extra)
    return row


def test_a_blocked_source_cannot_supply_the_ledger_label() -> None:
    entry = _ledger(_matrix_row(), [_doc("human_review_required")])

    assert SPAN not in str(entry["label"]), (
        "the event text was gated as a snippet on one line and taken as the "
        "label on the line above"
    )
    assert SPAN not in str(entry["snippet"])


def test_a_clean_source_still_supplies_the_ledger_label() -> None:
    entry = _ledger(_matrix_row(), [_doc("metadata_extracted")])

    assert SPAN in str(entry["label"])


def test_the_label_never_becomes_empty() -> None:
    """A ledger row with no label is unciteable; it must degrade, not vanish."""
    entry = _ledger(_matrix_row(), [_doc("human_review_required")])

    assert str(entry["label"]).strip()


# --- READ SIDE 2: the analysis artifact ----------------------------------------


class _CapturingRepository:
    def __init__(self) -> None:
        self.payloads: List[Dict[str, Any]] = []

    async def create_snapshot(self, *, run_id, kind, payload, effect_key):
        self.payloads.append(payload)
        return {"_id": "snap-1", "snapshot_hash": "hash-1"}


def _facts(status: str) -> Dict[str, Any]:
    from rbac_backend.services.arbitration_drafting.workflow_domain import (
        ArbitrationWorkflowDomain,
    )

    domain = ArbitrationWorkflowDomain(_DB(documents=[_doc(status)]))
    repository = _CapturingRepository()
    domain.repository = repository  # type: ignore[assignment]
    asyncio.run(
        domain._analyze_branch(
            {"_id": "run-1", "organization_id": "org-1", "project_id": "project-1"},
            "chronology",
            {
                "pleading_type": "statement_of_claim",
                "rows_by_matrix": {"chronology-matrix": [_matrix_row(date="2026-01-04")]},
                "document_signals": [],
                "opponent_snapshot": None,
            },
        )
    )
    findings = repository.payloads[0]["findings"]
    assert findings, "the branch produced no finding to inspect"
    return findings[0]["facts"]


def test_event_is_a_declared_analysis_field() -> None:
    from rbac_backend.services.arbitration_drafting.workflow_domain import (
        ANALYSIS_FINDING_FIELDS,
    )

    assert "event" in ANALYSIS_FINDING_FIELDS["chronology"]


def test_a_blocked_source_keeps_the_event_out_of_the_analysis_artifact() -> None:
    facts = _facts("human_review_required")

    assert SPAN not in str(facts)
    assert facts.get("date") == "2026-01-04"


def test_a_clean_source_keeps_the_event_in_the_analysis_artifact() -> None:
    assert SPAN in str(_facts("metadata_extracted").get("event"))
