"""G27: blocked document text must not reach drafting or planning.

The G23 barrier stopped Qdrant vectors and graph publication, and both are
proven. It did not stop the canonical Mongo text: `_upsert_document_metadata`
writes `ocrText` regardless of `skip_embeddings`, and several consumers read
that field directly as the document body.

Non-test consumers found by repository trace:

  * `agents/service.py:126`                     drafting body
  * `routers/deep_planning.py:385,894`          planning prompt
  * `services/arbitration_drafting/service.py`  snippet
  * `services/arbitration_drafting/workflow_domain.py`
  * `ingestion/pipeline.py:281`

So a review-required document was still draftable. The fix is one server-side
predicate driven by the authoritative state the pipeline already persists -
`documents.processing_status` - rather than a new boolean each consumer has to
remember, which is how G24 happened.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import pytest

from rbac_backend.models.processing_state import ProcessingState
from rbac_backend.services.publication_policy import (
    authoritative_text,
    is_consumable,
)


def _doc(status: Optional[str], **extra: Any) -> Dict[str, Any]:
    document: Dict[str, Any] = {"_id": "doc-1", "ocrText": "SENSITIVE CLAIM TEXT"}
    if status is not None:
        document["processing_status"] = status
    document.update(extra)
    return document


# --- The predicate -------------------------------------------------------------


def test_a_completed_document_is_consumable() -> None:
    assert is_consumable(_doc(ProcessingState.COMPLETED.value)) is True


def test_a_human_review_document_is_not_consumable() -> None:
    assert is_consumable(_doc(ProcessingState.HUMAN_REVIEW_REQUIRED.value)) is False


def test_a_failed_document_is_not_consumable() -> None:
    assert is_consumable(_doc(ProcessingState.FAILED.value)) is False


def test_a_partially_processed_document_is_not_consumable() -> None:
    assert is_consumable(_doc(ProcessingState.PARTIALLY_PROCESSED.value)) is False


@pytest.mark.parametrize(
    "status",
    [ProcessingState.QUEUED.value, ProcessingState.PROCESSING.value],
)
def test_an_in_flight_document_is_not_consumable(status: str) -> None:
    """Processing incomplete is not the same as processing passed."""
    assert is_consumable(_doc(status)) is False


def test_an_unknown_state_fails_closed() -> None:
    """A value nobody recognises must never be treated as safe."""
    assert is_consumable(_doc("some_future_state")) is False


def test_a_legacy_document_without_the_field_stays_consumable() -> None:
    """Backward compatibility, deliberate and tested.

    `processing_status` postdates most of the corpus. Failing closed on an
    ABSENT field would silently remove every historical document from drafting
    and retrieval, which is its own outage. An absent field is legacy; an
    unrecognised value is not.
    """
    assert is_consumable(_doc(None)) is True


def test_an_empty_state_is_treated_as_legacy() -> None:
    assert is_consumable(_doc("")) is True


# --- The text accessor ---------------------------------------------------------


def test_authoritative_text_is_returned_for_a_clean_document() -> None:
    doc = _doc(ProcessingState.COMPLETED.value)

    assert authoritative_text(doc) == "SENSITIVE CLAIM TEXT"


def test_authoritative_text_is_withheld_for_a_blocked_document() -> None:
    doc = _doc(ProcessingState.HUMAN_REVIEW_REQUIRED.value)

    assert authoritative_text(doc) == "", (
        "blocked document text was handed to a downstream consumer"
    )


def test_authoritative_text_prefers_body_then_full_text_then_ocr() -> None:
    doc = _doc(ProcessingState.COMPLETED.value, body="BODY", full_text="FULL")

    assert authoritative_text(doc) == "BODY"


# --- Through the real consumers -------------------------------------------------


class _FakeCollection:
    def __init__(self, doc: Optional[Dict[str, Any]]) -> None:
        self._doc = doc

    async def find_one(self, *_args: Any, **_kwargs: Any):
        return self._doc


class _FakeDB:
    def __init__(self, doc: Dict[str, Any]) -> None:
        self.letters = _FakeCollection(None)
        self.documents = _FakeCollection(doc)


def _load_via_agents(doc: Dict[str, Any]) -> str:
    from rbac_backend.agents.service import DraftingAgentService

    service = DraftingAgentService.__new__(DraftingAgentService)
    service.db = _FakeDB(doc)
    return asyncio.run(service._load_letter_text("doc-1"))


def test_drafting_does_not_receive_blocked_document_text() -> None:
    """The reproduction: this is what made G27 a blocker."""
    text = _load_via_agents(_doc(ProcessingState.HUMAN_REVIEW_REQUIRED.value))

    assert text == "", (
        "the drafting agent was handed the text of a document that needs "
        "human review"
    )


def test_drafting_still_receives_clean_document_text() -> None:
    text = _load_via_agents(_doc(ProcessingState.COMPLETED.value))

    assert text == "SENSITIVE CLAIM TEXT"


def test_drafting_still_receives_legacy_document_text() -> None:
    """The compatibility path must not break existing drafting."""
    text = _load_via_agents(_doc(None))

    assert text == "SENSITIVE CLAIM TEXT"
