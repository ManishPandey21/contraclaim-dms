"""One safe projection for every event-serving consumer.

The provenance resolver is correct, but `list_project_events`, `get_project_event`,
`list_links` and `timeline` return raw Mongo dicts - so a blocked document's
span text (`description`, `evidence_text`, `source_spans[].text`) reached the
API verbatim. The fix is a single projection every read path applies, not
authority logic copied into ten routes.

Independent metadata (dates, parties, ids, status) is preserved; only the
authority-controlled derivative fields are withheld, and only when the record's
originating document is currently non-consumable. A manual event (no document
provenance) is untouched.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.publication_policy import safe_event_records

MARKER = "SPAN-TEXT-FROM-BLOCKED-DOC"


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


def _project_event(**extra: Any) -> Dict[str, Any]:
    row = {
        "_id": "pe-1",
        "event_date": "2024-03-01",
        "party": "Contractor",
        "status": "open",
        "source_document_id": "doc-1",
        "description": f"{MARKER}: the contractor is in culpable delay.",
    }
    row.update(extra)
    return row


def _project(records, documents):
    return asyncio.run(
        safe_event_records(_DB(documents=documents), records, ("description",))
    )


def _texts(rows) -> str:
    return " ".join(str(r.get("description") or "") for r in rows)


# --- document-derived project event --------------------------------------------


def test_a_clean_event_keeps_its_description() -> None:
    assert MARKER in _texts(_project([_project_event()], [CLEAN]))


def test_a_blocked_event_description_is_withheld() -> None:
    rows = _project([_project_event()], [BLOCKED])

    assert MARKER not in _texts(rows)
    # Independent metadata survives - the event stays identifiable.
    assert rows[0]["party"] == "Contractor"
    assert rows[0]["event_date"] == "2024-03-01"


def test_a_quarantined_event_description_is_withheld() -> None:
    assert MARKER not in _texts(_project([_project_event()], [DUPLICATE]))


def test_an_operational_failure_keeps_the_description() -> None:
    assert MARKER in _texts(_project([_project_event()], [FAILED]))


def test_a_missing_parent_document_fails_closed() -> None:
    assert MARKER not in _texts(_project([_project_event()], []))


def test_a_manual_event_is_untouched() -> None:
    manual = {"_id": "pe-2", "party": "Counsel", "description": "Counsel's own note"}
    rows = _project([manual], [BLOCKED])

    assert rows[0]["description"] == "Counsel's own note"


# --- event link (source_entity_type / source_entity_id, nested spans) ----------


def _event_link(**extra: Any) -> Dict[str, Any]:
    # Real event_links name the document via target_type/target_id.
    row = {
        "_id": "el-1",
        "target_type": "document",
        "target_id": "doc-1",
        "evidence_text": f"{MARKER}: quoted from the document.",
        "source_spans": [{"page": 3, "text": f"{MARKER}: span"}],
    }
    row.update(extra)
    return row


def _project_links(records, documents):
    return asyncio.run(
        safe_event_records(
            _DB(documents=documents), records, ("evidence_text",), span_fields=("source_spans",)
        )
    )


def test_a_blocked_link_withholds_evidence_text_and_spans() -> None:
    rows = _project_links([_event_link()], [BLOCKED])

    assert MARKER not in str(rows[0].get("evidence_text") or "")
    assert MARKER not in str(rows[0].get("source_spans") or "")


def test_a_clean_link_keeps_its_evidence() -> None:
    rows = _project_links([_event_link()], [CLEAN])

    assert MARKER in str(rows[0]["evidence_text"])


def test_a_non_document_link_is_untouched() -> None:
    link = {"_id": "el-2", "target_type": "claim", "target_id": "c-1",
            "evidence_text": "claim-authored text"}
    rows = _project_links([link], [BLOCKED])

    assert rows[0]["evidence_text"] == "claim-authored text"


# --- Finding 1: project_event.title is document-derived (parsed.subject) --------


def test_a_blocked_project_event_title_is_withheld() -> None:
    ev = _project_event(title=f"{MARKER}: subject line")
    rows = asyncio.run(
        safe_event_records(_DB(documents=[BLOCKED]), [ev], ("description", "title"))
    )

    assert MARKER not in str(rows[0].get("title") or "")


def test_a_manual_project_event_title_is_kept() -> None:
    ev = {"_id": "pe-9", "title": "Kickoff meeting"}
    rows = asyncio.run(
        safe_event_records(_DB(documents=[BLOCKED]), [ev], ("description", "title"))
    )

    assert rows[0]["title"] == "Kickoff meeting"


# --- Finding 2: a link's document is reachable only via its project_event -------


def _metadata_link(**extra: Any) -> Dict[str, Any]:
    """A document-ingest suggested link: no direct document provenance, but its
    source project_event points at the document."""
    row = {
        "_id": "el-3",
        "source_type": "project_event",
        "source_id": "pe-doc",
        "target_type": "clause",
        "target_id": "clause-8.4",
        "evidence_text": f"{MARKER}: reference text from the document.",
        "source_spans": [{"start": 0, "end": 5, "text": f"{MARKER}: span"}],
    }
    row.update(extra)
    return row


def _project_links_with_events(records, events, documents):
    db = _DB(project_events=events, documents=documents)
    return asyncio.run(
        safe_event_records(db, records, ("evidence_text",), span_fields=("source_spans",))
    )


def test_a_link_reaching_a_blocked_document_via_its_event_is_withheld() -> None:
    event = {"_id": "pe-doc", "source_entity_type": "document", "source_entity_id": "doc-1"}
    rows = _project_links_with_events([_metadata_link()], [event], [BLOCKED])

    assert MARKER not in str(rows[0].get("evidence_text") or "")
    assert MARKER not in str(rows[0].get("source_spans") or "")


def test_a_link_reaching_a_clean_document_via_its_event_is_kept() -> None:
    event = {"_id": "pe-doc", "source_entity_type": "document", "source_entity_id": "doc-1"}
    rows = _project_links_with_events([_metadata_link()], [event], [CLEAN])

    assert MARKER in str(rows[0]["evidence_text"])


def test_a_link_whose_event_is_manual_is_kept() -> None:
    event = {"_id": "pe-doc", "title": "manual event"}
    rows = _project_links_with_events([_metadata_link()], [event], [BLOCKED])

    assert MARKER in str(rows[0]["evidence_text"])


# --- project_event.metadata is document-extracted for document-derived events ---


def test_a_blocked_project_event_metadata_is_withheld() -> None:
    ev = _project_event(
        metadata={"keywords": [f"{MARKER}kw"], "clauses": [f"{MARKER}clause"], "document_class": "notice"}
    )
    rows = asyncio.run(
        safe_event_records(_DB(documents=[BLOCKED]), [ev], ("description", "title"), dict_fields=("metadata",))
    )

    assert MARKER not in str(rows[0].get("metadata") or "")
    # A dict field must stay a dict, not become "".
    assert isinstance(rows[0].get("metadata"), dict)


def test_a_manual_project_event_metadata_is_kept() -> None:
    ev = {"_id": "pe-9", "title": "Kickoff", "metadata": {"note": f"{MARKER}user-note"}}
    rows = asyncio.run(
        safe_event_records(_DB(documents=[BLOCKED]), [ev], ("description", "title"), dict_fields=("metadata",))
    )

    assert MARKER in str(rows[0]["metadata"])


def test_a_clean_project_event_metadata_is_kept() -> None:
    ev = _project_event(metadata={"keywords": [f"{MARKER}kw"]})
    rows = asyncio.run(
        safe_event_records(_DB(documents=[CLEAN]), [ev], ("description", "title"), dict_fields=("metadata",))
    )

    assert MARKER in str(rows[0]["metadata"])
