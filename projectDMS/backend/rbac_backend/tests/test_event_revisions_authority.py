"""Event revision history embedded ungated span text in before/after snapshots.

`ChronologyService.event_revisions`
(`GET /api/chronologies/{id}/events/{event_id}/revisions`) returns revision
rows whose `before`/`after` are event snapshots carrying `description`/`title`/
`source_spans` - document-derived span text. For an event whose source document
is later blocked, the audit surface kept serving that span text verbatim.

Final-certification MEDIUM. The nested snapshots are gated through the same
projection; manual events (no source_document_id) are untouched.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.chronology import ChronologyService

MARKER = "BLOCKED-REVISION-SPAN"


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = list(rows)

    def sort(self, *a: Any, **k: Any) -> "_Cursor":
        return self

    def __aiter__(self):
        async def _gen():
            for row in self.rows:
                yield row

        return _gen()

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return list(self.rows)


class _Coll:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Optional[Dict[str, Any]] = None) -> _Cursor:
        return _Cursor(self.rows)

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        wanted = (query.get("_id", {}) or {})
        ids = wanted["$in"] if isinstance(wanted, dict) and "$in" in wanted else [wanted]
        for row in self.rows:
            if any(str(row.get("_id")) == str(i) for i in ids) or str(row.get("_id")) == str(query.get("_id")):
                return row
        return None


class _DB:
    def __init__(self, revisions, event, documents) -> None:
        self.matter_chronology_event_revisions = _Coll(revisions)
        self.matter_chronology_events = _Coll([event])
        self.documents = _Coll(documents)


BLOCKED = {"_id": "doc-1", "processing_status": "human_review_required"}
CLEAN = {"_id": "doc-1", "processing_status": "metadata_extracted"}


def _snapshot() -> Dict[str, Any]:
    return {
        "title": "Delay notice",
        "description": f"{MARKER}: culpable delay.",
        "source_document_id": "doc-1",
        "source_spans": [{"page": 1, "text": f"{MARKER}: span"}],
    }


def _event() -> Dict[str, Any]:
    return {"_id": "ev-1", "chronology_id": "chron-1", "source_document_id": "doc-1", **_snapshot()}


def _revisions(documents: List[Dict[str, Any]]) -> str:
    rev = {"_id": "rv-1", "chronology_id": "chron-1", "event_id": "ev-1", "revision": 1,
           "before": _snapshot(), "after": _snapshot()}
    db = _DB([rev], _event(), documents)
    rows = asyncio.run(ChronologyService(db).event_revisions("chron-1", "ev-1"))
    return str(rows)


def test_a_clean_source_keeps_revision_text() -> None:
    assert MARKER in _revisions([CLEAN])


def test_a_blocked_source_revision_text_is_withheld() -> None:
    assert MARKER not in _revisions([BLOCKED]), (
        "event revision history served a blocked document's span text"
    )
