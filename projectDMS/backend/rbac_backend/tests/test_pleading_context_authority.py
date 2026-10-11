"""`pleading_context` served blocked span text; its sibling `list_events` did not.

`ChronologyService.list_events` runs collected events through
`safe_event_records` because a document-derived event's `description`/`title`/
`source_spans` are span text lifted from the source document. `pleading_context`
reads the same `matter_chronology_events` and builds a source ledger via
`_ledger_row` WITHOUT that projection, so a verified event whose source document
is later blocked kept serving up to ~800 chars of that document's body through
`GET /api/chronologies/{id}/pleading-context`.

Final-certification HIGH. The fix routes the events through the same projection
before `_ledger_row`; manual events (no source_document_id) are untouched.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.chronology import ChronologyService

MARKER = "BLOCKED-CHRONOLOGY-SPAN-TEXT"


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = list(rows)

    def sort(self, *a: Any, **k: Any) -> "_Cursor":
        return self

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


class _Events:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Optional[Dict[str, Any]] = None) -> _Cursor:
        return _Cursor(self.rows)


class _Documents:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        wanted = (query.get("_id", {}) or {})
        ids = wanted["$in"] if isinstance(wanted, dict) and "$in" in wanted else [wanted]
        for row in self.rows:
            if any(str(row.get("_id")) == str(i) for i in ids):
                return row
        return None


class _Chronologies:
    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        return {"_id": "chron-1", "organization_id": "org-1", "project_id": "project-1"}


class _DB:
    def __init__(self, events: List[Dict[str, Any]], documents: List[Dict[str, Any]]) -> None:
        self.matter_chronology_events = _Events(events)
        self.documents = _Documents(documents)
        self.matter_chronologies = _Chronologies()


CLEAN = {"_id": "doc-1", "processing_status": "metadata_extracted"}
BLOCKED = {"_id": "doc-1", "processing_status": "human_review_required"}
FAILED = {"_id": "doc-1", "processing_status": "failed"}
DUPLICATE = {"_id": "doc-1", "processing_status": "metadata_extracted", "duplicate_status": "duplicate"}


def _event(**extra: Any) -> Dict[str, Any]:
    row = {
        "_id": "ev-1",
        "chronology_id": "chron-1",
        "verification_status": "verified",
        "title": "Delay notice",
        "description": f"{MARKER}: the contractor is in culpable delay.",
        "source_document_id": "doc-1",
        "event_date": "2024-03-01",
    }
    row.update(extra)
    return row


def _snippets(status: Optional[str], event: Optional[Dict[str, Any]] = None) -> str:
    documents = [] if status is None else [{"_id": "doc-1", "processing_status": status,
                                            **({k: v for k, v in _status_extra(status).items()})}]
    db = _DB([event or _event()], documents)
    ctx = asyncio.run(ChronologyService(db).pleading_context("chron-1"))
    ledger = ctx.model_dump().get("source_ledger") or ctx.model_dump().get("ledger") or []
    return " ".join(str(r.get("snippet") or "") + " " + str(r.get("label") or "") for r in ledger)


def _status_extra(status: str) -> Dict[str, Any]:
    return {}


def test_a_clean_source_supplies_the_pleading_snippet() -> None:
    assert MARKER in _snippets("metadata_extracted")


def test_a_blocked_source_snippet_is_withheld_from_pleading_context() -> None:
    assert MARKER not in _snippets("human_review_required"), (
        "pleading-context served a blocked document's span text via _ledger_row"
    )


def test_a_quarantined_source_snippet_is_withheld() -> None:
    db = _DB([_event()], [DUPLICATE])
    ctx = asyncio.run(ChronologyService(db).pleading_context("chron-1"))
    ledger = ctx.model_dump().get("source_ledger") or []
    assert MARKER not in " ".join(str(r.get("snippet") or "") for r in ledger)


def test_an_operational_failure_keeps_the_snippet() -> None:
    assert MARKER in _snippets("failed")


def test_a_manual_event_is_untouched() -> None:
    manual = _event(source_document_id=None, description="Counsel's own note", title="Manual entry")
    db = _DB([manual], [BLOCKED])
    ctx = asyncio.run(ChronologyService(db).pleading_context("chron-1"))
    ledger = ctx.model_dump().get("source_ledger") or []
    assert any("Counsel's own note" in str(r.get("snippet") or "") for r in ledger)
