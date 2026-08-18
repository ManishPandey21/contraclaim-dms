"""F3/M2: a chronology event is an application record only when a human wrote it.

`_rehydrate_direct_reference` sorted collections into three buckets: canonical
documents, extraction child records, and "independent application records".
Chronology events landed in the third, so `decision_ok = True` and the snippet
chain fell through to `record["description"]`.

But `ChronologySuggestionService` writes `description` from `span_text` lifted
out of the source document's extracted body (`chronology.py:530`) and records
`source_document_id` beside it. An AI-suggested event is therefore a document
derivative wearing an application record's clothes - and this was the one path
in the file that was never converted, while the sibling
`_chronology_matrix_sources` gates the same field correctly.

The discriminator is the provenance pointer the writer sets, not `source_type`.
That matters in both directions: gating every chronology event as a document
would deny counsel's own hand-written timeline entries, which document
authority has no jurisdiction over. Both directions are pinned here.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from rbac_backend.services.arbitration_drafting.context import ArbitrationContextBuilder

SPAN = "SPAN the Employer admitted liability for the delay on 2024-03-01"
MANUAL = "Counsel's own note: raised at the 12 March progress meeting."
DRAFT = {"organization_id": "org-1", "project_id": "project-1", "case_id": "case-1"}


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        for row in self.rows:
            ok = True
            for key, expected in query.items():
                actual = row.get(key)
                if isinstance(expected, dict) and "$in" in expected:
                    if not any(str(actual) == str(c) for c in expected["$in"]):
                        ok = False
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


def _event(**extra: Any) -> Dict[str, Any]:
    event = {
        "_id": "ev-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "title": "Employer admission",
        "description": SPAN,
    }
    event.update(extra)
    return event


def _reference(event: Dict[str, Any], documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    builder = ArbitrationContextBuilder(
        _DB(matter_chronology_events=[event], documents=documents)
    )
    result = asyncio.run(
        builder._rehydrate_direct_reference(
            DRAFT, {"source_type": "chronology_event", "source_id": "ev-1"}
        )
    )
    assert result is not None, "the event should still hydrate"
    return result


def _doc(status: str, **extra: Any) -> Dict[str, Any]:
    return {"_id": "doc-1", "processing_status": status, **extra}


# --- document-derived events ---------------------------------------------------


def test_a_clean_source_document_still_supplies_the_span() -> None:
    reference = _reference(_event(source_document_id="doc-1"), [_doc("metadata_extracted")])

    assert SPAN in str(reference["snippet"])
    assert reference["authority_denied"] is False


def test_a_blocked_source_document_withholds_the_span() -> None:
    reference = _reference(
        _event(source_document_id="doc-1"), [_doc("human_review_required")]
    )

    assert SPAN not in str(reference["snippet"]), (
        "the chronology event was treated as an independent application "
        "record, so the document's extracted span was handed straight back"
    )
    assert reference["authority_denied"] is True


def test_a_quarantined_source_document_withholds_the_span() -> None:
    reference = _reference(
        _event(source_document_id="doc-1"),
        [_doc("metadata_extracted", duplicate_status="duplicate")],
    )

    assert SPAN not in str(reference["snippet"])
    assert reference["authority_denied"] is True


def test_an_operational_failure_keeps_the_span_under_last_known_good() -> None:
    reference = _reference(_event(source_document_id="doc-1"), [_doc("failed")])

    assert SPAN in str(reference["snippet"])
    assert reference["authority_denied"] is False


def test_a_vanished_source_document_fails_closed() -> None:
    reference = _reference(_event(source_document_id="doc-1"), [])

    assert SPAN not in str(reference["snippet"])
    assert reference["authority_denied"] is True


# --- manually authored events: the over-block half -----------------------------


def test_a_manual_event_is_not_governed_by_document_authority() -> None:
    """M2. Counsel's own timeline entry has no originating document.

    Denying it would be its own defect - authority would be deleting content it
    has no jurisdiction over.
    """
    reference = _reference(
        _event(description=MANUAL), [_doc("human_review_required")]
    )

    assert MANUAL in str(reference["snippet"])
    assert reference["authority_denied"] is False


def test_a_manual_event_survives_with_no_documents_at_all() -> None:
    reference = _reference(_event(description=MANUAL), [])

    assert MANUAL in str(reference["snippet"])
    assert reference["authority_denied"] is False


def test_the_discriminator_is_provenance_not_source_type() -> None:
    """Same source_type, opposite outcomes, decided by the writer's pointer."""
    derived = _reference(_event(source_document_id="doc-1"), [_doc("human_review_required")])
    manual = _reference(_event(description=MANUAL), [_doc("human_review_required")])

    assert derived["authority_denied"] is True
    assert manual["authority_denied"] is False


# --- other application records must not regress --------------------------------


def test_a_claim_record_is_still_ungated() -> None:
    builder = ArbitrationContextBuilder(
        _DB(
            claims=[
                {
                    "_id": "claim-1",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "claim_number": "C-1",
                    "description": "Claim for prolongation costs.",
                }
            ],
            documents=[_doc("human_review_required")],
        )
    )
    reference = asyncio.run(
        builder._rehydrate_direct_reference(
            DRAFT, {"source_type": "claim", "source_id": "claim-1"}
        )
    )

    assert reference["authority_denied"] is False
    assert "prolongation" in str(reference["snippet"])
