"""`evidence_event_links.evidence_text` is the chronology description again.

`_sync_verified_event` (`chronology.py:713`, `:733`) copies the chronology
event's `description` - span text lifted from the source document - onto every
evidence link it creates. `_verified_graph_sources` (`context.py:1520`) then
feeds that field to the drafting ledger as a snippet, with
`verification_status` defaulting to "verified" and no authority call anywhere.

Same root cause as the chronology event itself, one collection further out: the
text was gated where it was born and ungated where it was copied. Provenance is
retained, so no new plumbing is needed - a document-target link names the
document in `target_id`, and a clause-target link reaches it through
`metadata.chronology_event_id`.

A link with no document behind it (manual evidence) must stay untouched.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.arbitration_drafting.context import ArbitrationContextBuilder

SPAN = "SPAN TEXT copied from the blocked document into the evidence link"


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
        return _Cursor(self.rows)

    async def find_one(self, query: Optional[Dict[str, Any]] = None, *a: Any, **k: Any):
        expected = (query or {}).get("_id")
        for row in self.rows:
            if isinstance(expected, dict) and "$in" in expected:
                if any(str(row.get("_id")) == str(c) for c in expected["$in"]):
                    return row
            elif expected is None or str(row.get("_id")) == str(expected):
                return row
        return None


class _DB:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self._collections = {n: _Collection(r) for n, r in collections.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


DRAFT = {"organization_id": "org-1", "project_id": "project-1"}


def _link(**extra: Any) -> Dict[str, Any]:
    link = {
        "_id": "link-1",
        "link_group_id": "lg-1",
        "source_type": "project_event",
        "target_type": "document",
        "target_id": "doc-1",
        "relation_type": "refers_to",
        "status": "user_verified",
        "evidence_text": SPAN,
        "metadata": {"chronology_event_id": "ev-1"},
    }
    link.update(extra)
    return link


def _snippet(link: Dict[str, Any], status: Optional[str]) -> str:
    documents = [{"_id": "doc-1", "processing_status": status}] if status else []
    builder = ArbitrationContextBuilder(
        _DB(
            documents=documents,
            matter_chronology_events=[
                {"_id": "ev-1", "source_document_id": "doc-1", "description": SPAN}
            ],
        )
    )
    return str(_rows(builder, link)[0]["snippet"])


class _StubGraph:
    """Stands in for EvidenceGraphService, returning one link."""

    links: List[Dict[str, Any]] = []

    def __init__(self, db: Any) -> None:
        self.db = db

    async def downstream_links(self, *a: Any, **k: Any) -> List[Dict[str, Any]]:
        return list(self.links)


def _rows(builder: ArbitrationContextBuilder, link: Dict[str, Any]):
    from rbac_backend.services.arbitration_drafting import context as context_module

    original = context_module.EvidenceGraphService
    _StubGraph.links = [link]
    context_module.EvidenceGraphService = _StubGraph  # type: ignore[assignment]
    try:
        rows = asyncio.run(builder._verified_graph_sources(DRAFT, False, 0, []))
    finally:
        context_module.EvidenceGraphService = original  # type: ignore[assignment]
    assert rows, "the verified link should still produce a ledger entry"
    return rows


def test_a_clean_source_still_supplies_the_link_evidence() -> None:
    assert SPAN in _snippet(_link(), "metadata_extracted")


def test_a_blocked_document_target_withdraws_the_link_evidence() -> None:
    assert SPAN not in _snippet(_link(), "human_review_required"), (
        "the chronology description was gated at its own read path and copied "
        "verbatim into an evidence link that nobody checked"
    )


def test_a_quarantined_document_target_withdraws_the_link_evidence() -> None:
    builder_documents = [
        {"_id": "doc-1", "processing_status": "metadata_extracted", "duplicate_status": "duplicate"}
    ]
    builder = ArbitrationContextBuilder(_DB(documents=builder_documents))

    assert SPAN not in str(_rows(builder, _link())[0]["snippet"])


def test_an_operational_failure_keeps_the_link_evidence() -> None:
    assert SPAN in _snippet(_link(), "failed")


def test_a_clause_target_link_reaches_the_document_through_its_event() -> None:
    """The clause-target link carries the same text but names no document."""
    clause_link = _link(target_type="clause", target_id="21.2")

    assert SPAN not in _snippet(clause_link, "human_review_required")


def test_a_link_with_no_document_provenance_is_untouched() -> None:
    """Manual evidence: document authority has no jurisdiction over it."""
    manual = _link(target_type="clause", target_id="21.2", metadata={})

    assert SPAN in _snippet(manual, "human_review_required")
