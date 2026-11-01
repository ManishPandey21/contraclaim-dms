"""A chronology description is a document derivative too.

`ChronologySuggestionService` builds an event's `description` from `span_text`
lifted out of the source document (`chronology.py:530`), and the WRITE side is
already gated - `_document_text` reads through `is_consumable`.

The read side was not. `_chronology_matrix_sources` (`context.py:805`) falls
back to the event's stored `description` for the arbitration source-ledger
snippet, which flows into drafting prompts. An event written while its document
was clean therefore kept feeding that document's extracted text into pleadings
after the quality gate had rejected it - the same stale-derivative shape as
`relevance_note`, on a field nobody had looked at.

The event retains `source_document_id`, so no new provenance is needed: the
existing derived-text helper resolves it.

Manual events carry no `source_document_id` and are user-authored, so they must
keep working untouched - over-blocking a hand-written chronology entry would be
its own defect.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.arbitration_drafting.context import ArbitrationContextBuilder

EXTRACTED = "Access to the site was granted 43 days late."
MANUAL = "Counsel's own note about the access dispute."


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = list(rows)

    def sort(self, *a: Any, **k: Any) -> "_Cursor":
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return list(self.rows)


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Optional[Dict[str, Any]] = None) -> _Cursor:
        return _Cursor(self.rows)

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        for row in self.rows:
            if str(row.get("_id")) == str(query.get("_id")):
                return row
        return None


class _DB:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self._collections = {n: _Collection(r) for n, r in collections.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


MATRIX_ROW = {
    "_id": "chrono-row-1",
    "case_id": "case-1",
    "chronology_event_id": "event-1",
    "verification_status": "approved",
    # `impact` and `evidence` are matrix content authored in-app; leaving them
    # empty is what makes the event description the snippet.
}


def _snippet(event: Dict[str, Any], documents: List[Dict[str, Any]]) -> str:
    builder = ArbitrationContextBuilder(
        _DB(
            arbitration_chronology_matrix=[dict(MATRIX_ROW)],
            matter_chronology_events=[event],
            documents=documents,
        )
    )
    rows = asyncio.run(
        builder._chronology_matrix_sources("case-1", 0, False, [])
    )
    assert rows, "the matrix row should still produce a ledger entry"
    return str(rows[0]["snippet"])


def _event(**extra: Any) -> Dict[str, Any]:
    event = {"_id": "event-1", "title": "Late access", "description": EXTRACTED}
    event.update(extra)
    return event


def _doc(status: str) -> Dict[str, Any]:
    return {"_id": "doc-1", "processing_status": status}


def test_a_clean_source_document_still_supplies_the_snippet() -> None:
    snippet = _snippet(
        _event(source_document_id="doc-1"), [_doc("metadata_extracted")]
    )

    assert EXTRACTED in snippet


def test_a_blocked_source_document_withdraws_its_chronology_description() -> None:
    snippet = _snippet(
        _event(source_document_id="doc-1"), [_doc("human_review_required")]
    )

    assert EXTRACTED not in snippet


def test_an_operational_failure_keeps_the_description_under_last_known_good() -> None:
    snippet = _snippet(_event(source_document_id="doc-1"), [_doc("failed")])

    assert EXTRACTED in snippet


def test_a_manual_event_is_unaffected_by_document_authority() -> None:
    """No source document, so there is no document authority to apply."""
    snippet = _snippet(_event(description=MANUAL), [_doc("human_review_required")])

    assert MANUAL in snippet


def test_manual_notes_survive_when_the_derived_description_is_withdrawn() -> None:
    """The fallback chain must continue, not collapse to empty."""
    snippet = _snippet(
        _event(source_document_id="doc-1", manual_notes=MANUAL),
        [_doc("human_review_required")],
    )

    assert EXTRACTED not in snippet
    assert MANUAL in snippet
