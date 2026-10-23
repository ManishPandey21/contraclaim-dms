"""F1: the guard withheld the span, then a raw spread handed it back.

`_chronology_matrix_sources` gates the label (`context.py:866`) and the snippet
(`:892`) through `consumable_derived_text`. Sixty lines later it builds

    event_data = {**row, **{f"event_{k}": v for k, v in event.items()}}

and puts it in `metadata["event"]` (`:924`). That spread reintroduces
`row["event"]` - the exact field just gated - plus `event_description` and
`event_source_spans[].text`, which are the same document span a third time.

The ledger is not an internal structure: it is returned by
`GET /drafts/{id}/source-ledger` and `POST /drafts/{id}/evidence/refresh`
(neither declares a response_model, so the dict is serialised verbatim) and is
persisted into `arbitration_draft_versions.source_ledger`.

This is the Q5 defect inverted. There the fixed-key rebuild DROPPED a safety
field; here an unrestricted spread REINTRODUCES unsafe content. Both are the
same class: a safety decision that does not survive object reconstruction.

The repair is an allowlist - safe metadata is CONSTRUCTED from independent
fields plus authority-approved derivatives, never filtered down from a raw
spread. A denylist over a spread fails open every time a writer adds a field.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from rbac_backend.services.arbitration_drafting.context import ArbitrationContextBuilder

SPAN = "SPAN TEXT the Contractor admits the piling to axis 7 was defective"
TITLE = "Contractor admission of defective piling"


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = list(rows)

    def sort(self, *a: Any, **k: Any) -> "_Cursor":
        return self

    async def to_list(self, length: Any = None) -> List[Dict[str, Any]]:
        return list(self.rows)


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Any = None) -> _Cursor:
        return _Cursor(self.rows)

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        for row in self.rows:
            ok = True
            for key, expected in query.items():
                actual = row.get(key)
                if isinstance(expected, dict) and "$in" in expected:
                    if not any(str(actual) == str(c) for c in expected["$in"]):
                        ok = False
                elif isinstance(expected, dict):
                    continue
                elif str(actual) != str(expected):
                    ok = False
            if ok:
                return row
        return None


class _DB:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self._c = {n: _Collection(r) for n, r in collections.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._c.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


MATRIX_ROW = {
    "_id": "cm-1",
    "case_id": "case-1",
    "chronology_event_id": "ev-1",
    "verification_status": "approved",
    "date": "2024-03-01",
    "event": f"{TITLE}. {SPAN}",
    "source_document_id": "doc-1",
    "party_responsible": "Contractor",
}

EVENT = {
    "_id": "ev-1",
    "title": TITLE,
    "description": SPAN,
    "source_document_id": "doc-1",
    "source_document_name": "letter-42.pdf",
    "source_page": 3,
    "source_spans": [{"page": 3, "text": SPAN}],
    "letter_no": "L-042",
    "event_classification": "delay",
    "verification_status": "approved",
}


def _ledger(status: str, **doc_extra: Any) -> Dict[str, Any]:
    document = {"_id": "doc-1", "processing_status": status, **doc_extra}
    builder = ArbitrationContextBuilder(
        _DB(
            arbitration_chronology_matrix=[dict(MATRIX_ROW)],
            matter_chronology_events=[dict(EVENT)],
            documents=[document],
        )
    )
    rows = asyncio.run(builder._chronology_matrix_sources("case-1", 0, False, []))
    assert rows, "the approved matrix row should still yield a ledger entry"
    return rows[0]


def _serialised(row: Dict[str, Any]) -> str:
    """What actually goes over the wire and into the persisted version."""
    return json.dumps(row, default=str)


# --- the premise ---------------------------------------------------------------


def test_the_guard_itself_still_works() -> None:
    row = _ledger("human_review_required")

    assert SPAN not in str(row["label"])
    assert SPAN not in str(row["snippet"])


# --- the defect: the whole serialised row ---------------------------------------


def test_a_blocked_span_is_absent_from_the_entire_serialised_row() -> None:
    """Not just from label and snippet - from anywhere a consumer can read."""
    row = _ledger("human_review_required")

    assert SPAN not in _serialised(row), (
        "the raw spread put the withheld span back into metadata['event'], "
        "which is returned by the source-ledger route and persisted into the "
        "draft version"
    )


def test_a_quarantined_source_span_is_absent_from_the_serialised_row() -> None:
    row = _ledger("metadata_extracted", duplicate_status="duplicate")

    assert SPAN not in _serialised(row)


def test_a_deleted_source_span_is_absent_from_the_serialised_row() -> None:
    row = _ledger("metadata_extracted", lifecycle_state="deleted")

    assert SPAN not in _serialised(row)


def test_a_missing_source_document_withholds_the_span() -> None:
    builder = ArbitrationContextBuilder(
        _DB(
            arbitration_chronology_matrix=[dict(MATRIX_ROW)],
            matter_chronology_events=[dict(EVENT)],
            documents=[],
        )
    )
    rows = asyncio.run(builder._chronology_matrix_sources("case-1", 0, False, []))

    assert SPAN not in _serialised(rows[0])


# --- availability: the guard must not empty the ledger --------------------------


def test_a_clean_source_still_supplies_the_span() -> None:
    row = _ledger("metadata_extracted")

    assert SPAN in _serialised(row)


def test_an_operational_failure_keeps_the_span_under_last_known_good() -> None:
    row = _ledger("failed")

    assert SPAN in _serialised(row)


def test_independent_metadata_survives_a_blocked_source() -> None:
    """A blocked event must stay identifiable, or counsel cannot act on it.

    Dates, parties, page numbers, letter numbers and ids are recorded about the
    document, not extracted from its body, so authority does not reach them.
    """
    row = _ledger("human_review_required")
    event_metadata = row["metadata"]["event"]

    assert event_metadata.get("date") == "2024-03-01"
    assert event_metadata.get("party_responsible") == "Contractor"
    assert row["metadata"]["chronology_event_id"] == "ev-1"


def test_the_event_metadata_is_built_from_an_allowlist_not_a_spread() -> None:
    """Structural: a new writer field must not auto-publish itself.

    A denylist over `{**row}` fails open the moment somebody adds a field.
    """
    from rbac_backend.services.arbitration_drafting.context import (
        SAFE_EVENT_METADATA_FIELDS,
    )

    builder = ArbitrationContextBuilder(
        _DB(
            arbitration_chronology_matrix=[
                {**MATRIX_ROW, "some_future_extracted_field": SPAN}
            ],
            matter_chronology_events=[
                {**EVENT, "another_future_field": SPAN}
            ],
            documents=[{"_id": "doc-1", "processing_status": "human_review_required"}],
        )
    )
    rows = asyncio.run(builder._chronology_matrix_sources("case-1", 0, False, []))

    assert SPAN not in _serialised(rows[0])
    assert "some_future_extracted_field" not in rows[0]["metadata"]["event"]
    assert SAFE_EVENT_METADATA_FIELDS
