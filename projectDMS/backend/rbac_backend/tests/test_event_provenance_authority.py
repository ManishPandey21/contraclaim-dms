"""Class B: an event's authority depends on its RECORD, not its type.

`chronology_event`, `project_event`, `event_link` and `delay_event` all map to
collections that are neither the canonical `documents` nor a document child, so
`_is_document_governed` returned False and `resolve_derived_authority` returned
`consumable=True` before any lookup. But these records can carry document-
derived span text - the writers stamp `source_document_id` (chronology/project,
`chronology.py:530`, `evidence_graph_service.py:149`) or
`source_entity_type=document`/`source_entity_id` (event_link,
`evidence_graph_service.py:737`). So a blocked document's extracted text, once
copied into an event, was consumable forever.

The fix resolves per record: load the event, read its provenance, and if it
points at a document, that document's CURRENT authority governs. An event with
no document provenance is a manual/application record and stays usable.

These assert the resolver decision for each family and lifecycle state.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.publication_policy import resolve_derived_authority


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


def _decide(source_type: str, event: Dict[str, Any], documents: List[Dict[str, Any]]):
    # Each event family is resolved against the collection it is ACTUALLY
    # stored in - project events live in `project_events`, not
    # `matter_chronology_events`. The earlier fixture stored them together and
    # masked a wrong-collection mapping (Review B).
    collection = {
        "chronology_event": "matter_chronology_events",
        "project_event": "project_events",
        "delay_event": "matter_chronology_events",
        "event_link": "event_links",
    }[source_type]
    db = _DB(**{collection: [event], "documents": documents})
    return asyncio.run(resolve_derived_authority(db, source_type, event["_id"]))


# --- chronology_event / project_event / delay_event (source_document_id) --------


def _run_document_provenance(source_type: str) -> None:
    ev = {"_id": "ev-1", "source_document_id": "doc-1", "description": "span text"}

    assert _decide(source_type, ev, [CLEAN]).consumable is True
    assert _decide(source_type, ev, [BLOCKED]).consumable is False
    assert _decide(source_type, ev, [DUPLICATE]).consumable is False
    assert _decide(source_type, ev, [FAILED]).consumable is True  # Model B
    assert _decide(source_type, ev, []).consumable is False  # missing parent, fail closed


def test_chronology_event_follows_its_parent_document() -> None:
    _run_document_provenance("chronology_event")


def test_project_event_follows_its_parent_document() -> None:
    _run_document_provenance("project_event")


def test_delay_event_follows_its_parent_document() -> None:
    _run_document_provenance("delay_event")


# --- event_link (source_entity_type=document / source_entity_id) ----------------


def test_event_link_follows_its_document_entity() -> None:
    ev = {"_id": "el-1", "source_entity_type": "document", "source_entity_id": "doc-1"}

    assert _decide("event_link", ev, [CLEAN]).consumable is True
    assert _decide("event_link", ev, [BLOCKED]).consumable is False
    assert _decide("event_link", ev, [FAILED]).consumable is True


# --- manual events (no document provenance) -------------------------------------


def test_a_manual_chronology_event_is_independent() -> None:
    ev = {"_id": "ev-1", "description": "Counsel's own note", "created_by": "user-1"}

    assert _decide("chronology_event", ev, [BLOCKED]).consumable is True


def test_a_manual_project_event_is_independent() -> None:
    ev = {"_id": "ev-1", "title": "Kickoff meeting"}

    assert _decide("project_event", ev, [BLOCKED]).consumable is True


def test_an_event_link_with_a_non_document_entity_is_independent() -> None:
    ev = {"_id": "el-1", "source_entity_type": "claim", "source_entity_id": "claim-9"}

    assert _decide("event_link", ev, [BLOCKED]).consumable is True


# --- a vanished event record fails closed for derivative content ----------------


def test_a_missing_event_record_fails_closed() -> None:
    db = _DB(matter_chronology_events=[], documents=[CLEAN])
    decision = asyncio.run(resolve_derived_authority(db, "chronology_event", "ghost"))

    assert decision.consumable is False
