"""Blocked derivative text must not steer a decision it never displays.

Both sites here were classified NOT-A-DEFECT on a bad test - "the note is not
shown to anyone" - and both were wrong. Authority protects against material
INFLUENCE as well as disclosure:

* `_exhibit_volume` (`case_workspace.py:89`) reads `relevance_note` to decide
  which volume of the arbitration bundle an exhibit is filed in. Nothing is
  displayed; the note silently moves a legal exhibit between volumes.

* `ANALYSIS_FINDING_FIELDS["document_understanding"]`
  (`workflow_domain.py:60`) merely NAMES `relevance_note`, but the list is
  generically dereferenced at `:439` to build `findings["facts"]`, so naming it
  is what puts it in the payload.

These assert the final routing decision and the final findings payload, not
that a helper ran.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.arbitration_drafting.case_workspace import (
    BUNDLE_VOLUME_CORRESPONDENCE,
    BUNDLE_VOLUME_PROGRAMME,
    ArbitrationCaseWorkspaceService,
    _exhibit_volume,
)

# The note names a programme/delay topic, so an ungated read files the exhibit
# in the programme volume instead of general correspondence.
BLOCKED_NOTE = "Contains the delay and extension of time analysis"
CORRESPONDENCE = BUNDLE_VOLUME_CORRESPONDENCE


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        for row in self.rows:
            expected = query.get("_id")
            if isinstance(expected, dict) and "$in" in expected:
                if any(str(row.get("_id")) == str(c) for c in expected["$in"]):
                    return row
            elif str(row.get("_id")) == str(expected):
                return row
        return None


class _DB:
    def __init__(self, documents: List[Dict[str, Any]]) -> None:
        self._collections = {"documents": _Collection(documents)}

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        if name == "arbitration_analysis_leases":
            # `_fanout_slot` takes the no-lease path for lightweight doubles.
            raise AttributeError(name)
        return self[name]


def _index_row() -> Dict[str, Any]:
    """A document-index row whose only routing signal is the derived note.

    `document_type` and `title` are metadata written from the source document's
    identity fields, not from its extracted body, so they legitimately keep
    steering placement.
    """
    return {
        "exhibit_id": "C-1",
        "source_id": "doc-1",
        "source_type": "document",
        "title": "Letter 42",
        "document_type": "Letter",
        "relevance_note": BLOCKED_NOTE,
    }


def _volumes(document: Dict[str, Any]) -> str:
    service = ArbitrationCaseWorkspaceService(_DB([document]))

    async def _stub_load(row: Dict[str, Any]) -> Dict[str, Any]:
        return {"content": b"pdf-bytes", "filename": "letter-42.pdf"}

    service._load_exhibit_file = _stub_load  # type: ignore[method-assign]
    entries = asyncio.run(service._collect_exhibit_files([_index_row()]))
    return str(entries[0]["volume"])


# --- the premise: this note really does move the exhibit -----------------------


def test_the_note_alone_changes_the_bundle_volume() -> None:
    """Without this, the tests below would prove nothing."""
    with_note = _exhibit_volume(_index_row())
    without_note = _exhibit_volume({**_index_row(), "relevance_note": ""})

    assert with_note == BUNDLE_VOLUME_PROGRAMME
    assert without_note == CORRESPONDENCE


# --- routing ------------------------------------------------------------------


def test_a_blocked_source_cannot_route_its_exhibit(  ) -> None:
    volume = _volumes({"_id": "doc-1", "processing_status": "human_review_required"})

    assert volume == CORRESPONDENCE, (
        "the blocked document's relevance_note still decided which volume of "
        "the arbitration bundle this exhibit was filed in"
    )


def test_a_clean_source_still_routes_its_exhibit() -> None:
    volume = _volumes({"_id": "doc-1", "processing_status": "metadata_extracted"})

    assert volume == BUNDLE_VOLUME_PROGRAMME


def test_a_quarantined_source_cannot_route_its_exhibit() -> None:
    volume = _volumes(
        {
            "_id": "doc-1",
            "processing_status": "metadata_extracted",
            "duplicate_status": "duplicate",
        }
    )

    assert volume == CORRESPONDENCE


def test_an_operational_failure_still_routes_under_last_known_good() -> None:
    volume = _volumes({"_id": "doc-1", "processing_status": "failed"})

    assert volume == BUNDLE_VOLUME_PROGRAMME


# --- generic field extraction --------------------------------------------------


class _CapturingRepository:
    """Stands in for snapshot persistence and keeps the artifact payload."""

    def __init__(self) -> None:
        self.payloads: List[Dict[str, Any]] = []

    async def create_snapshot(self, *, run_id, kind, payload, effect_key):
        self.payloads.append(payload)
        return {"_id": "snap-1", "snapshot_hash": "hash-1"}


def _facts(document: Dict[str, Any]) -> Dict[str, Any]:
    """Drive the real branch and read `findings[0]["facts"]` off the artifact."""
    from rbac_backend.services.arbitration_drafting.workflow_domain import (
        ArbitrationWorkflowDomain,
    )

    domain = ArbitrationWorkflowDomain(_DB([document]))
    repository = _CapturingRepository()
    domain.repository = repository  # type: ignore[assignment]

    context = {
        "pleading_type": "statement_of_claim",
        "rows_by_matrix": {"document-index": [_index_row()]},
        "document_signals": [],
        "opponent_snapshot": None,
    }
    asyncio.run(
        domain._analyze_branch(
            {"_id": "run-1", "organization_id": "org-1", "project_id": "project-1"},
            "document_understanding",
            context,
        )
    )
    findings = repository.payloads[0]["findings"]
    assert findings, "the branch produced no finding to inspect"
    return findings[0]["facts"]


def test_relevance_note_is_a_declared_field_that_becomes_content() -> None:
    """Pin the reason the declaration mattered: it is dereferenced."""
    from rbac_backend.services.arbitration_drafting.workflow_domain import (
        ANALYSIS_FINDING_FIELDS,
    )

    assert "relevance_note" in ANALYSIS_FINDING_FIELDS["document_understanding"]


def test_a_blocked_note_does_not_reach_the_findings_payload() -> None:
    facts = _facts({"_id": "doc-1", "processing_status": "human_review_required"})

    assert not facts.get("relevance_note")
    assert BLOCKED_NOTE not in str(facts)
    # Metadata fields are unaffected - the guard must not empty the finding.
    assert facts.get("title") == "Letter 42"


def test_a_clean_note_does_reach_the_findings_payload() -> None:
    facts = _facts({"_id": "doc-1", "processing_status": "metadata_extracted"})

    assert facts.get("relevance_note") == BLOCKED_NOTE
