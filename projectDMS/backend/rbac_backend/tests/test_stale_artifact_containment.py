"""G28: artifacts from an older successful run must not survive a later block.

The publication barrier stops a *current* blocked run from publishing. It does
not retract what an *earlier* successful run already published, and the purge
that would have removed stale vectors lives inside
`_create_and_store_embeddings` - the very function the barrier skips.

So: document passes, gets embedded and graph-published, is reprocessed, now
fails quality, and its previous vectors keep answering queries.

Two independent defences are required, and the logical one is primary:

  * **Logical denial** - retrieval resolves every hit's document and drops the
    ones whose current state is not consumable. This holds even if a stale point
    survives deletion, which is the point: containment must not depend on a
    remote delete succeeding.
  * **Physical cleanup** - stale vectors are purged for hygiene and
    defence-in-depth.

`retrieval/service.py:_fetch_documents_meta` already loads the Mongo document
for every hit, and used only `title`/`letterNo`. That is the chokepoint.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.models.processing_state import ProcessingState
from rbac_backend.services.publication_policy import is_consumable


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self._docs = docs

    def __aiter__(self):
        async def gen():
            for doc in self._docs:
                yield doc

        return gen()


class _Documents:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self._docs = docs

    def find(self, query: Dict[str, Any], *args: Any, **kwargs: Any) -> _Cursor:
        wanted = {str(v) for v in query.get("_id", {}).get("$in", [])}
        return _Cursor([d for d in self._docs if str(d["_id"]) in wanted])


class _DB:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self.documents = _Documents(docs)


def _service(docs: List[Dict[str, Any]]):
    from rbac_backend.retrieval.service import RetrievalService

    service = RetrievalService.__new__(RetrievalService)
    service.db = _DB(docs)
    return service


def _doc(doc_id: str, status: Optional[str]) -> Dict[str, Any]:
    document: Dict[str, Any] = {
        "_id": doc_id,
        "subject": f"Subject {doc_id}",
        "letterNo": f"L-{doc_id}",
    }
    if status is not None:
        document["processing_status"] = status
    return document


def _meta(service, ids: List[str]) -> Dict[str, Dict[str, Any]]:
    return asyncio.run(service._fetch_documents_meta(ids))


# --- Logical denial at the retrieval chokepoint --------------------------------


def test_a_blocked_document_is_dropped_from_retrieval_metadata() -> None:
    """The core G28 defence: a surviving stale point must still be unusable."""
    service = _service(
        [_doc("blocked", ProcessingState.HUMAN_REVIEW_REQUIRED.value)]
    )

    meta = _meta(service, ["blocked"])

    assert "blocked" not in meta, (
        "retrieval resolved a document whose current extraction is blocked; a "
        "stale vector would still be served"
    )


def test_a_clean_document_is_still_returned() -> None:
    service = _service([_doc("clean", ProcessingState.COMPLETED.value)])

    assert "clean" in _meta(service, ["clean"])


def test_a_legacy_document_is_still_returned() -> None:
    """Historical records predate the state field and must not vanish."""
    service = _service([_doc("legacy", None)])

    assert "legacy" in _meta(service, ["legacy"])


@pytest.mark.parametrize(
    "status",
    [
        ProcessingState.FAILED.value,
        ProcessingState.HUMAN_REVIEW_REQUIRED.value,
    ],
)
def test_every_adverse_state_is_dropped(status: str) -> None:
    """Safety half: a terminal verdict against the run denies retrieval."""
    service = _service([_doc("d", status)])

    assert _meta(service, ["d"]) == {}


@pytest.mark.parametrize(
    "status",
    [
        ProcessingState.PROCESSING.value,
        ProcessingState.PARTIALLY_PROCESSED.value,
        "retrying",
        "metadata_extracted",
    ],
)
def test_an_in_flight_or_settled_document_is_still_retrievable(status: str) -> None:
    """Availability half, updated for G33.

    These previously asserted the document was dropped. `metadata_extracted` is
    the terminal success state of the normal path, so that assertion amounted to
    dropping every healthy document from retrieval.
    """
    service = _service([_doc("d", status)])

    assert "d" in _meta(service, ["d"])


def test_a_mixed_result_set_keeps_only_the_consumable_documents() -> None:
    service = _service(
        [
            _doc("clean", ProcessingState.COMPLETED.value),
            _doc("blocked", ProcessingState.HUMAN_REVIEW_REQUIRED.value),
            _doc("legacy", None),
        ]
    )

    meta = _meta(service, ["clean", "blocked", "legacy"])

    assert set(meta) == {"clean", "legacy"}


# --- The scenario the defect describes ----------------------------------------


def test_previously_published_then_blocked_is_denied() -> None:
    """Scenario A: run 1 published, run 2 blocked. Stale points may exist.

    Containment must not depend on the purge having succeeded.
    """
    published = _doc("doc-1", ProcessingState.COMPLETED.value)
    service = _service([published])
    assert "doc-1" in _meta(service, ["doc-1"])

    # Run 2 blocks the document. The vector is deliberately NOT removed here.
    published["processing_status"] = ProcessingState.HUMAN_REVIEW_REQUIRED.value

    assert _meta(service, ["doc-1"]) == {}, (
        "a stale vector from the earlier successful run was still served"
    )


def test_blocked_then_clean_republishes() -> None:
    """A document must not be permanently poisoned by one blocked run."""
    doc = _doc("doc-1", ProcessingState.HUMAN_REVIEW_REQUIRED.value)
    service = _service([doc])
    assert _meta(service, ["doc-1"]) == {}

    doc["processing_status"] = ProcessingState.COMPLETED.value

    assert "doc-1" in _meta(service, ["doc-1"])


# --- Policy agreement ----------------------------------------------------------


def test_blocked_ids_are_reported_for_dropping_results() -> None:
    """The filter must identify the RESULT to drop, not just its title.

    An earlier revision filtered only the metadata lookup and left `snippet`
    flowing from the raw payload, so blocked text still reached the caller with
    its title stripped. An adversarial review caught it; this pins it.
    """
    service = _service(
        [
            _doc("clean", ProcessingState.COMPLETED.value),
            _doc("blocked", ProcessingState.HUMAN_REVIEW_REQUIRED.value),
        ]
    )

    blocked = asyncio.run(service._blocked_document_ids(["clean", "blocked"]))

    assert blocked == {"blocked"}


def test_a_document_absent_from_mongo_is_not_reported_blocked() -> None:
    """An orphaned vector is a different problem; do not change that behaviour."""
    service = _service([])

    assert asyncio.run(service._blocked_document_ids(["ghost"])) == set()


def test_the_summary_is_withheld_for_a_blocked_document() -> None:
    """`summary` is derived from the same extracted text.

    Withholding `ocrText` while serving `summary` withholds nothing, and three
    consumers were doing exactly that - two of them inside guards that had
    already been added.
    """
    from rbac_backend.services.publication_policy import authoritative_summary

    blocked = {
        "_id": "d",
        "processing_status": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
        "summary": "CONDENSED CLAIM CONTENT",
    }

    assert authoritative_summary(blocked) == ""


def test_the_summary_is_served_for_a_clean_document() -> None:
    from rbac_backend.services.publication_policy import authoritative_summary

    clean = {
        "_id": "d",
        "processing_status": ProcessingState.COMPLETED.value,
        "summary": "CONDENSED CLAIM CONTENT",
    }

    assert authoritative_summary(clean) == "CONDENSED CLAIM CONTENT"


def test_authoritative_text_also_falls_back_to_summary() -> None:
    """A blocked document must not leak through the body accessor either."""
    from rbac_backend.services.publication_policy import authoritative_text

    blocked = {
        "_id": "d",
        "processing_status": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
        "summary": "CONDENSED CLAIM CONTENT",
    }

    assert authoritative_text(blocked) == ""


def test_the_retrieval_filter_uses_the_same_policy_as_the_consumers() -> None:
    """One decision, not two that can drift apart."""
    for status, expected in [
        (ProcessingState.COMPLETED.value, True),
        ("metadata_extracted", True),
        (ProcessingState.HUMAN_REVIEW_REQUIRED.value, False),
        (ProcessingState.FAILED.value, False),
        (None, True),
        ("unknown_state", True),
    ]:
        service = _service([_doc("d", status)])
        in_retrieval = "d" in _meta(service, ["d"])

        assert in_retrieval is is_consumable(_doc("d", status)) is expected
