"""G37/C3: a derivative cannot outlive the authority of its source.

`relevance_note` is condensed from a document's summary/full_content when the
arbitration document index is built. Once stored it looks like independent,
human-approved matrix evidence - and the matrix approval flag says nothing about
whether the underlying extraction is still trusted.

Write-time guarding cannot fix that on its own, because authority changes AFTER
the derivative exists: a document that was clean when indexed and is later sent
to human review leaves a stale note behind. Case E below is the one that matters.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.services.publication_policy import consumable_derived_text

NOTE = "Condensed from the source document's extracted text."


class _Documents:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self._docs = docs

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        wanted = str(query.get("_id"))
        for doc in self._docs:
            if str(doc.get("_id")) == wanted:
                return doc
        return None


class _DB:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self.documents = _Documents(docs)


def _row(source_id: str = "doc-1") -> Dict[str, Any]:
    return {"source_id": source_id, "relevance_note": NOTE, "document_type": "Letter"}


def _derived(docs: List[Dict[str, Any]], row: Dict[str, Any]) -> str:
    return asyncio.run(consumable_derived_text(_DB(docs), row, "relevance_note"))


def _doc(status: Optional[str] = None, **extra) -> Dict[str, Any]:
    document: Dict[str, Any] = {"_id": "doc-1"}
    if status is not None:
        document["processing_status"] = status
    document.update(extra)
    return document


# --- Case A: clean source -------------------------------------------------------


def test_a_derivative_of_a_clean_source_is_usable() -> None:
    assert _derived([_doc("metadata_extracted")], _row()) == NOTE


# --- Case B/C: blocked and quarantined ------------------------------------------


def test_a_derivative_of_a_blocked_source_is_denied() -> None:
    assert _derived([_doc("human_review_required")], _row()) == ""


def test_a_derivative_of_a_quarantined_source_is_denied() -> None:
    assert _derived([_doc("metadata_extracted", lifecycle_state="duplicate")], _row()) == ""


# --- Case D: Model B ------------------------------------------------------------


def test_a_derivative_survives_an_operational_failure() -> None:
    """`failed` is pipeline breakage, not a verdict on the content."""
    assert _derived([_doc("failed")], _row()) == NOTE


# --- Case E: the stale derivative (mandatory) -----------------------------------


def test_a_stale_derivative_stops_being_consumable_when_its_source_blocks() -> None:
    """The case write-time guarding cannot cover.

    The note was derived while the source was authoritative and is still
    physically present. Once the source is sent to human review the derivative
    must stop being consumable, without anything having rewritten it.
    """
    document = _doc("metadata_extracted")
    row = _row()

    assert _derived([document], row) == NOTE

    document["processing_status"] = "human_review_required"

    assert _derived([document], row) == "", (
        "a persisted derivative outlived the publication authority of its "
        "source - blocked extraction laundered into approved evidence"
    )


def test_the_derivative_becomes_usable_again_if_the_source_clears() -> None:
    document = _doc("human_review_required")
    row = _row()
    assert _derived([document], row) == ""

    document["processing_status"] = "metadata_extracted"
    assert _derived([document], row) == NOTE


# --- Fail-closed paths ----------------------------------------------------------


def test_a_derivative_with_no_source_identity_is_denied() -> None:
    assert _derived([_doc("metadata_extracted")], {"relevance_note": NOTE}) == ""


def test_a_derivative_whose_source_is_missing_is_denied() -> None:
    assert _derived([], _row("ghost")) == ""


def test_an_empty_row_is_denied() -> None:
    assert _derived([_doc("metadata_extracted")], {}) == ""
