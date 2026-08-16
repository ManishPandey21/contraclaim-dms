"""Authority is a judgement about content, not a workflow string.

Two corrections are pinned here.

**`failed` is operational, not adverse.** Both production writers of
`processing_status="failed"` are pipeline breakage, not verdicts on the
document: `_mark_processing_failure` (`document_service.py:1472`) fires when
retries are exhausted after an exception in the job loop, and `:1927` is a bare
`except Exception` around the whole run. An OCR crash, a Mongo timeout or an S3
error all land there. Treating that as an adverse judgement invalidated
last-known-good content because a worker died, which contradicts the
last-known-good lifecycle: only an *outcome about the content* may retract a
previous publication.

The adverse quality verdict lives elsewhere - `human_review_required`, written
by `_mark_human_review` from the gate's `pages_human_review`, and `publishable`
on the extraction result.

**Authority must be resolved from the canonical document.** Asking an arbitrary
record whether it is consumable is vacuous when that record has no authority
fields: `document_vectors` and `contract_clauses` carry no `processing_status`,
so `is_consumable(clause)` returned True for a clause belonging to a blocked
document, and the caller fell through to raw text (G34). The resolver takes a
document id and reads the canonical record.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.services.publication_policy import (
    ADVERSE_STATES,
    OPERATIONAL_STATES,
    is_consumable,
    resolve_document_authority,
)

TEXT = "AUTHORITATIVE CLAIM TEXT"


def _doc(status=None, **extra):
    document = {"_id": "doc-1", "ocrText": TEXT}
    if status is not None:
        document["processing_status"] = status
    document.update(extra)
    return document


# --- `failed` is operational, not a verdict on the content --------------------


def test_failed_is_classified_as_operational_not_adverse() -> None:
    assert "failed" in OPERATIONAL_STATES
    assert "failed" not in ADVERSE_STATES


def test_a_transient_failure_does_not_retract_last_known_good() -> None:
    """Case A. A worker crash is not a judgement about the document.

    Retries exhausted, provider timeout, unhandled exception - all write
    `failed`, and under last-known-good none of them may remove content that
    previously passed.
    """
    assert is_consumable(_doc("failed")) is True


def test_an_adverse_quality_decision_does_retract() -> None:
    """Case B. The gate reached a verdict about the content itself."""
    assert is_consumable(_doc("human_review_required")) is False


def test_the_two_cases_are_distinguishable() -> None:
    """The whole point: they must not both collapse onto one string."""
    assert is_consumable(_doc("failed")) != is_consumable(
        _doc("human_review_required")
    )


# --- The canonical authority resolver -----------------------------------------


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


def _resolve(docs: List[Dict[str, Any]], document_id: Optional[str]):
    return asyncio.run(resolve_document_authority(_DB(docs), document_id))


def test_the_resolver_reads_the_canonical_document_not_the_caller_record() -> None:
    """G34's root cause. The clause carries no authority fields at all."""
    blocked = _doc("human_review_required")
    clause = {"_id": "clause-9", "document_id": "doc-1", "text": TEXT}

    # The record itself looks consumable because it has no state to inspect.
    assert is_consumable(clause) is True

    # The resolver goes to the canonical document and gets the truth.
    decision = _resolve([blocked], clause["document_id"])
    assert decision.consumable is False
    assert decision.reason == "adverse_quality_judgement"


def test_the_resolver_allows_a_clean_document() -> None:
    decision = _resolve([_doc("metadata_extracted")], "doc-1")

    assert decision.consumable is True
    assert decision.reason == "settled"


def test_the_resolver_allows_after_a_transient_failure() -> None:
    decision = _resolve([_doc("failed")], "doc-1")

    assert decision.consumable is True
    assert decision.reason == "operational_failure_last_known_good"


def test_the_resolver_denies_a_confirmed_duplicate() -> None:
    decision = _resolve([_doc("metadata_extracted", lifecycle_state="duplicate")], "doc-1")

    assert decision.consumable is False
    assert decision.reason == "quarantined"


def test_a_missing_document_fails_closed() -> None:
    """No canonical record means no basis to authorise anything."""
    decision = _resolve([], "ghost")

    assert decision.consumable is False
    assert decision.reason == "document_not_found"


def test_a_missing_document_id_fails_closed() -> None:
    decision = _resolve([_doc("metadata_extracted")], None)

    assert decision.consumable is False


def test_a_lookup_error_fails_closed() -> None:
    """Unlike the graph supporter rule, a read guard must deny on uncertainty."""

    class _Broken:
        async def find_one(self, *a: Any, **k: Any):
            raise RuntimeError("mongo unavailable")

    class _BrokenDB:
        documents = _Broken()

    decision = asyncio.run(resolve_document_authority(_BrokenDB(), "doc-1"))

    assert decision.consumable is False
    assert decision.reason == "resolution_error"


def test_the_decision_carries_its_basis() -> None:
    """A decision without a reason cannot be audited or debugged."""
    decision = _resolve([_doc("human_review_required")], "doc-1")

    assert decision.document_id == "doc-1"
    assert decision.reason
    assert decision.consumable is False


def test_the_resolver_agrees_with_the_document_level_predicate() -> None:
    """One model, two entry points - they must not drift."""
    for status in ("metadata_extracted", "failed", "human_review_required", "skipped"):
        document = _doc(status)
        assert _resolve([document], "doc-1").consumable is is_consumable(document)


# --- The writer-side complement (Review A, HIGH) -------------------------------


@pytest.mark.parametrize(
    "doc",
    [
        {"_id": "d", "processing_status": "metadata_extracted"},
        {"_id": "d", "processing_status": "human_review_required"},
        {"_id": "d", "processing_status": "failed"},
        {"_id": "d", "processing_status": "processing"},
        {"_id": "d", "processing_status": "some_future_state"},
        {"_id": "d"},
        {"_id": "d", "processing_status": "metadata_extracted", "duplicate_status": "duplicate"},
        {"_id": "d", "processing_status": "metadata_extracted", "lifecycle_state": "deleted"},
        {"_id": "d", "processing_status": "metadata_extracted", "lifecycle_state": "duplicate"},
    ],
)
def test_publication_blocked_is_the_exact_complement_of_consumable(doc) -> None:
    """It had no test at all, and had already drifted.

    The earlier version restated the rules instead of deriving them and omitted
    the quarantine axis, so a confirmed duplicate was neither consumable NOR
    blocked - and a writer trusting the complement would have republished
    quarantined content.
    """
    from rbac_backend.services.publication_policy import (
        is_consumable,
        is_publication_blocked,
    )

    assert is_publication_blocked(doc) == (not is_consumable(doc))


def test_a_quarantined_document_is_blocked_for_writers() -> None:
    from rbac_backend.services.publication_policy import is_publication_blocked

    assert is_publication_blocked(
        {"_id": "d", "processing_status": "metadata_extracted",
         "duplicate_status": "duplicate"}
    ) is True
