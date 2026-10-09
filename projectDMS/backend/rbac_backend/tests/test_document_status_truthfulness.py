"""The document's status and review message must summarise what actually ran.

Two defects measured in Canary A (unified_v1, mixed-content PDFs):

1. A document read ``completed`` while its embeddings had failed and its vector
   sync row read ``error``. ``process_document_job`` decided completion from
   the extraction checkpoint alone; the downstream indexing outcome the same
   run had recorded (``job.metadata.partial_failures`` and the
   ``vector_sync_status`` row) was never consulted.
2. The claim letter went to human review because the quality gate failed page
   5, yet the document said "0 page(s) could not be extracted ... pages: []".
   ``_mark_human_review`` built its message from ``remaining_page_numbers``,
   which lists pages extraction still owes. A gate-failed page is resolved
   extraction, so it is never in that list, and the checkpoint did not record
   the review pages at all.

Invariants pinned here (existing vocabulary, no new states):

- ``completed``: extraction resolved every page, no page needs review, and
  the indexing the run attempted finished. A stage switched off by
  configuration (vector store ``disabled``) or deliberately skipped by policy
  (a confirmed duplicate is never indexed) is not a failure.
- ``human_review_required``: a page or stage explicitly needs a person. The
  message names every such page, sorted and deduplicated, and says why.
- ``partially_processed``: usable extraction exists, but required processing
  did not finish. Here: embeddings failed, or the vector sync ended
  ``error``/``mismatch``/``pending``, or stayed ``deferred`` for a document
  that was not a confirmed duplicate.
- ``failed``: unchanged; written only by the operational failure paths.

Metadata that degraded to the deterministic fallback stays ``completed``: that
is the documented policy (``metadata_quality.degraded`` carries it visibly).

The whole-document embedding withholding behaviour (one review page keeps the
entire document out of the vector store) is unchanged and not tested here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from bson import ObjectId

from rbac_backend.models.document_metadata import ProcessingResult
from rbac_backend.models.processing_state import (
    ProcessingState,
    build_attempt_outcome,
)
from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.extraction.models import (
    Completeness,
    ExtractedPage,
    PageClass,
    PageClassification,
    PageExtractionResult,
    PageSource,
    PageStatus,
)
from rbac_backend.tests.test_document_processing_jobs import (
    FakeCollection,
    FakeDB,
    insert_document,
    make_document,
)


class _DB(FakeDB):
    def __init__(self) -> None:
        super().__init__()
        self.vector_sync_status = FakeCollection()


def _page(number: int, *, needs_review: bool = False) -> ExtractedPage:
    return ExtractedPage(
        number=number,
        text="text",
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=PageClass.TEXT_NATIVE,
            char_count=4,
            image_count=0,
            image_coverage=0.0,
            table_count=0,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
        needs_review=needs_review,
    )


def _extraction(pages: List[ExtractedPage], **overrides: Any) -> PageExtractionResult:
    defaults: Dict[str, Any] = dict(
        pages=pages,
        combined_text="text",
        ocr_pages_total=0,
        completeness=Completeness.COMPLETE,
    )
    defaults.update(overrides)
    return PageExtractionResult(**defaults)


async def _queued(db: _DB, **document_fields: Any):
    service = DocumentService(db)
    document_id = ObjectId()
    document = make_document(document_id)
    await insert_document(db, document, document_id)
    if document_fields:
        await db.documents.update_one({"_id": document_id}, {"$set": document_fields})
    job_id = await service.queue_document_processing(document, "letter.pdf")
    return service, job_id, document_id


def _run(
    db: _DB,
    service: DocumentService,
    job_id: str,
    document_id: ObjectId,
    *,
    checkpoint: Optional[Dict[str, Any]] = None,
    result: Optional[ProcessingResult] = None,
    partial_failures: Optional[Dict[str, Any]] = None,
    vector_status: Optional[str] = None,
):
    """Stand in for ``process_document_async``: record what one run produced.

    Either a raw ``checkpoint`` or a real ``ProcessingResult`` passed through
    the real ``_checkpoint_extraction_attempt``, then the job metadata and
    vector sync row exactly as the real run writes them.
    """

    async def fake_process(*args: Any, **kwargs: Any) -> bool:
        if result is not None:
            job_record = await db.document_processing_jobs.find_one({"_id": job_id})
            await service._checkpoint_extraction_attempt(db, job_id, job_record, result)
        if checkpoint is not None:
            await db.document_processing_jobs.update_one(
                {"_id": job_id}, {"$set": checkpoint}
            )
        await db.document_processing_jobs.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "stage": "metadata_updated",
                    "metadata": {
                        "metadata_source": "ocr_fallback_regex",
                        "chunks_created": 0,
                        "partial_failures": dict(partial_failures or {}),
                    },
                }
            },
        )
        if vector_status is not None:
            await db.vector_sync_status.insert_one(
                {"document_id": str(document_id), "sync_status": vector_status}
            )
        return True

    return fake_process


_COMPLETED = {"processing_state": ProcessingState.COMPLETED.value, "remaining_page_numbers": []}


async def _finish(db: _DB, monkeypatch, service, job_id, document_id, **run):
    monkeypatch.setattr(
        service, "process_document_async", _run(db, service, job_id, document_id, **run)
    )
    ok = await service.process_document_job(job_id)
    job = await db.document_processing_jobs.find_one({"_id": job_id})
    stored = await db.documents.find_one({"_id": document_id})
    return ok, job, stored


# --- The checkpoint records which pages need review -------------------------


def test_checkpoint_records_gate_review_pages_separately_from_unresolved() -> None:
    extraction = _extraction(
        [_page(1), _page(5, needs_review=True), _page(3, needs_review=True)],
        completeness=Completeness.PARTIAL,
    )

    outcome = build_attempt_outcome(
        extraction, prior_page_attempts={}, attempts_exhausted=False
    )
    checkpoint = outcome.to_checkpoint()

    # Extraction resolved every page: nothing is owed, so nothing resumes...
    assert checkpoint["remaining_page_numbers"] == []
    # ...but two pages failed the quality gate, and the checkpoint says so.
    assert checkpoint["review_page_numbers"] == [3, 5]
    assert checkpoint["processing_state"] == ProcessingState.PARTIALLY_PROCESSED.value


def test_clean_extraction_checkpoint_has_no_review_pages() -> None:
    outcome = build_attempt_outcome(
        _extraction([_page(1), _page(2)]), prior_page_attempts={}, attempts_exhausted=False
    )

    assert outcome.to_checkpoint()["review_page_numbers"] == []


# --- A / B: success, and metadata degraded to its fallback -------------------


@pytest.mark.asyncio
async def test_a_everything_succeeds_is_completed(monkeypatch) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db)

    ok, job, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint=_COMPLETED, vector_status="synced",
    )

    assert ok is True
    assert job["status"] == "completed"
    assert stored["processing_status"] == "completed"
    assert stored["processing_error"] is None


@pytest.mark.asyncio
async def test_b_metadata_degraded_to_fallback_stays_completed(monkeypatch) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db)

    ok, job, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint=_COMPLETED,
        vector_status="synced",
        partial_failures={
            "ai_extraction": {"stage": "ocr_text_extraction", "message": "no provider"},
            "metadata_parse": {"status": "degraded", "warnings": ["fallback"]},
        },
    )

    assert ok is True
    assert job["status"] == "completed"
    assert stored["processing_status"] == "completed"


@pytest.mark.asyncio
async def test_vector_store_disabled_by_configuration_is_not_a_failure(monkeypatch) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db)

    ok, _, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint=_COMPLETED, vector_status="disabled",
    )

    assert ok is True
    assert stored["processing_status"] == "completed"


@pytest.mark.asyncio
async def test_confirmed_duplicate_left_unindexed_is_not_a_failure(monkeypatch) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db, duplicate_status="duplicate")

    ok, _, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint=_COMPLETED, vector_status="deferred",
    )

    assert ok is True
    assert stored["processing_status"] == "completed"


# --- C / D: embeddings or vector sync failed --------------------------------


@pytest.mark.asyncio
async def test_c_embeddings_failed_is_not_completed(monkeypatch) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db)

    ok, job, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint=_COMPLETED,
        vector_status="error",
        partial_failures={
            "embeddings": {"stage": "embeddings", "message": "Embedding storage failed: 401"}
        },
    )

    assert ok is False
    assert stored["processing_status"] == ProcessingState.PARTIALLY_PROCESSED.value
    assert job["status"] == ProcessingState.PARTIALLY_PROCESSED.value
    error = stored["processing_error"]
    assert "embeddings" in error["message"]
    assert error["stages"]["embeddings"] == "failed"
    assert error["stages"]["vector_sync"] == "error"
    assert error["terminal"] is True
    assert job["error"]["stages"] == error["stages"]


@pytest.mark.asyncio
async def test_c_embeddings_failure_recorded_without_a_sync_row_is_not_completed(
    monkeypatch,
) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db)

    ok, _, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint=_COMPLETED,
        partial_failures={"embeddings": {"stage": "embeddings", "message": "boom"}},
    )

    assert ok is False
    assert stored["processing_status"] == ProcessingState.PARTIALLY_PROCESSED.value


@pytest.mark.parametrize("vector_status", ["error", "mismatch", "pending", "deferred"])
@pytest.mark.asyncio
async def test_d_vector_sync_not_finished_is_not_completed(
    monkeypatch, vector_status: str
) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db)

    ok, job, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint=_COMPLETED, vector_status=vector_status,
    )

    assert ok is False
    assert stored["processing_status"] == ProcessingState.PARTIALLY_PROCESSED.value
    assert stored["processing_status"] != "completed"
    assert job["status"] != "completed"
    assert stored["processing_error"]["stages"] == {"vector_sync": vector_status}
    assert vector_status in stored["processing_error"]["message"]


# --- E / F / G: quality-gate review names its pages -------------------------


@pytest.mark.asyncio
async def test_e_one_gate_review_page_is_named(monkeypatch) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db)
    result = ProcessingResult(
        success=True,
        extraction_result=_extraction(
            [_page(1), _page(2), _page(3), _page(4), _page(5, needs_review=True), _page(6)],
            completeness=Completeness.PARTIAL,
        ),
        pages_human_review=[5],
        publishable=False,
    )

    ok, job, stored = await _finish(
        db, monkeypatch, service, job_id, document_id, result=result
    )

    assert ok is False
    assert job["status"] == ProcessingState.HUMAN_REVIEW_REQUIRED.value
    assert stored["processing_status"] == ProcessingState.HUMAN_REVIEW_REQUIRED.value
    error = stored["processing_error"]
    assert error["pages"] == [5]
    assert error["quality_review_pages"] == [5]
    assert error["unresolved_pages"] == []
    assert "0 page" not in error["message"]
    assert "5" in error["message"]
    assert "could not be extracted" not in error["message"]


@pytest.mark.asyncio
async def test_f_several_review_pages_are_sorted_and_deduplicated(monkeypatch) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db)

    ok, _, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint={
            "processing_state": ProcessingState.PARTIALLY_PROCESSED.value,
            "remaining_page_numbers": [],
            "review_page_numbers": [7, 3, 7, 5],
        },
    )

    assert ok is False
    error = stored["processing_error"]
    assert error["pages"] == [3, 5, 7]
    assert "3 page(s)" in error["message"]
    assert "3, 5, 7" in error["message"]


@pytest.mark.asyncio
async def test_g_attempts_exhausted_with_only_gate_review_does_not_say_zero_pages(
    monkeypatch,
) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db)

    ok, _, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint={
            "processing_state": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
            "remaining_page_numbers": [],
            "review_page_numbers": [5],
        },
    )

    assert ok is False
    assert stored["processing_status"] == ProcessingState.HUMAN_REVIEW_REQUIRED.value
    assert stored["processing_error"]["pages"] == [5]
    assert "0 page" not in stored["processing_error"]["message"]


@pytest.mark.asyncio
async def test_unresolved_and_gate_review_pages_are_both_reported(monkeypatch) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db)

    ok, _, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint={
            "processing_state": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
            "remaining_page_numbers": [2],
            "review_page_numbers": [5, 2],
        },
    )

    assert ok is False
    error = stored["processing_error"]
    assert error["pages"] == [2, 5]
    assert error["unresolved_pages"] == [2]
    assert error["quality_review_pages"] == [2, 5]
    assert "2 page(s)" in error["message"]


@pytest.mark.asyncio
async def test_checkpoint_without_review_pages_never_claims_zero_pages(monkeypatch) -> None:
    """A checkpoint written before review pages were recorded (an in-flight job
    across a deploy) carries neither list. The message must not invent a count."""
    db = _DB()
    service, job_id, document_id = await _queued(db)

    ok, _, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint={
            "processing_state": ProcessingState.PARTIALLY_PROCESSED.value,
            "remaining_page_numbers": [],
        },
    )

    assert ok is False
    assert stored["processing_status"] == ProcessingState.HUMAN_REVIEW_REQUIRED.value
    message = stored["processing_error"]["message"]
    assert "0 page" not in message
    assert stored["processing_error"]["pages"] == []


# --- H: partial extraction failure ------------------------------------------


@pytest.mark.asyncio
async def test_h_unresolved_pages_with_attempts_left_resume_not_complete(monkeypatch) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db)

    ok, job, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint={
            "processing_state": ProcessingState.PARTIALLY_PROCESSED.value,
            "remaining_page_numbers": [4],
            "review_page_numbers": [],
        },
        vector_status="deferred",
    )

    assert ok is False
    assert job["status"] == "retrying"
    assert stored["processing_status"] == ProcessingState.PARTIALLY_PROCESSED.value


@pytest.mark.asyncio
async def test_h_unresolved_pages_with_attempts_gone_name_their_pages(monkeypatch) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db)

    ok, _, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint={
            "processing_state": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
            "remaining_page_numbers": [4],
            "review_page_numbers": [],
        },
    )

    assert ok is False
    error = stored["processing_error"]
    assert error["pages"] == [4]
    assert "could not be extracted" in error["message"]


# --- I: a later clean run clears stale text; only a clean run does ----------


@pytest.mark.asyncio
async def test_i_clean_reprocess_clears_a_previous_review_message(monkeypatch) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(
        db,
        processing_status=ProcessingState.HUMAN_REVIEW_REQUIRED.value,
        processing_error={"message": "1 page(s) failed the quality check", "pages": [5]},
    )

    ok, _, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint=_COMPLETED, vector_status="synced",
    )

    assert ok is True
    assert stored["processing_status"] == "completed"
    assert stored["processing_error"] is None


@pytest.mark.asyncio
async def test_i_reprocess_with_failed_indexing_replaces_stale_review_text(
    monkeypatch,
) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(
        db,
        processing_status=ProcessingState.HUMAN_REVIEW_REQUIRED.value,
        processing_error={"message": "1 page(s) failed the quality check", "pages": [5]},
    )

    ok, _, stored = await _finish(
        db, monkeypatch, service, job_id, document_id,
        checkpoint=_COMPLETED, vector_status="error",
    )

    assert ok is False
    assert stored["processing_status"] == ProcessingState.PARTIALLY_PROCESSED.value
    assert "pages" not in stored["processing_error"]
    assert "quality check" not in stored["processing_error"]["message"]


# --- J: finalisation is idempotent -------------------------------------------


@pytest.mark.parametrize(
    "run",
    [
        {"checkpoint": _COMPLETED, "vector_status": "error"},
        {
            "checkpoint": {
                "processing_state": ProcessingState.PARTIALLY_PROCESSED.value,
                "remaining_page_numbers": [],
                "review_page_numbers": [5],
            }
        },
    ],
    ids=["downstream_incomplete", "gate_review"],
)
@pytest.mark.asyncio
async def test_j_repeated_finalisation_does_not_change_a_settled_verdict(
    monkeypatch, run: Dict[str, Any]
) -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db)
    _, job_before, stored_before = await _finish(
        db, monkeypatch, service, job_id, document_id, **run
    )

    ok = await service.process_document_job(job_id)
    job_after = await db.document_processing_jobs.find_one({"_id": job_id})
    stored_after = await db.documents.find_one({"_id": document_id})

    assert ok is False
    assert job_after == job_before
    assert stored_after == stored_before


@pytest.mark.asyncio
async def test_j_marking_review_twice_writes_the_same_verdict() -> None:
    db = _DB()
    service, job_id, document_id = await _queued(db)
    checkpoint = {
        "processing_state": ProcessingState.PARTIALLY_PROCESSED.value,
        "remaining_page_numbers": [],
        "review_page_numbers": [5],
    }

    await service._mark_human_review(job_id, str(document_id), checkpoint)
    first = await db.documents.find_one({"_id": document_id})
    await service._mark_human_review(job_id, str(document_id), checkpoint)
    second = await db.documents.find_one({"_id": document_id})

    def _without_time(error: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in error.items() if key != "timestamp"}

    assert first["processing_status"] == second["processing_status"]
    assert _without_time(first["processing_error"]) == _without_time(
        second["processing_error"]
    )
