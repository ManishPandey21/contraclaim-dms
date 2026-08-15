"""Partial extraction must be a durable checkpoint, not an in-memory enum.

The job loop already claims atomically, heartbeats, and retries whole-job
exceptions. What it did not know is that a *successful* processor return can
still contain deferred or failed pages - so it marked such documents
`completed`. These tests pin the checkpoint that prevents that.
"""

from __future__ import annotations

from rbac_backend.models.processing_state import (
    ProcessingState,
    build_attempt_outcome,
)
from rbac_backend.services.extraction.models import (
    Completeness,
    ExtractedPage,
    PageClass,
    PageClassification,
    PageExtractionResult,
    PageSource,
    PageStatus,
)


def _page(number: int, status: PageStatus) -> ExtractedPage:
    return ExtractedPage(
        number=number,
        text="text" if status is PageStatus.TEXT_LAYER else "",
        source=PageSource.TEXT_LAYER,
        status=status,
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
    )


def _result(pages, **overrides) -> PageExtractionResult:
    defaults = dict(
        pages=pages,
        combined_text="",
        ocr_pages_total=0,
        ocr_failed_pages=[],
        ocr_deferred_pages=[],
        unrenderable_pages=[],
        completeness=Completeness.COMPLETE,
        engine_version="1",
    )
    defaults.update(overrides)
    return PageExtractionResult(**defaults)  # type: ignore[arg-type]


def test_complete_extraction_leaves_nothing_remaining() -> None:
    result = _result([_page(n, PageStatus.TEXT_LAYER) for n in (1, 2, 3)])

    outcome = build_attempt_outcome(
        result, prior_page_attempts={}, attempts_exhausted=False
    )

    assert outcome.state is ProcessingState.COMPLETED
    assert outcome.expected_page_numbers == [1, 2, 3]
    assert outcome.resolved_page_numbers == [1, 2, 3]
    assert outcome.remaining_page_numbers == []


def test_deferred_pages_are_remaining_and_block_completion() -> None:
    pages = [_page(n, PageStatus.TEXT_LAYER) for n in (1, 2)]
    pages += [_page(n, PageStatus.OCR_DEFERRED) for n in (3, 4)]
    result = _result(
        pages, completeness=Completeness.PARTIAL, ocr_deferred_pages=[3, 4]
    )

    outcome = build_attempt_outcome(
        result, prior_page_attempts={}, attempts_exhausted=False
    )

    assert outcome.state is ProcessingState.PARTIALLY_PROCESSED
    assert outcome.remaining_page_numbers == [3, 4]
    assert outcome.resolved_page_numbers == [1, 2]


def test_deferred_pages_do_not_consume_a_retry_allowance() -> None:
    # They were never submitted to OCR, so charging them an attempt would
    # exhaust the budget for work that never ran.
    pages = [_page(3, PageStatus.OCR_DEFERRED)]
    result = _result(
        pages, completeness=Completeness.PARTIAL, ocr_deferred_pages=[3]
    )

    outcome = build_attempt_outcome(
        result, prior_page_attempts={}, attempts_exhausted=False
    )

    assert outcome.page_attempts == {}


def test_failed_pages_consume_an_attempt() -> None:
    pages = [_page(2, PageStatus.OCR_FAILED)]
    result = _result(pages, completeness=Completeness.PARTIAL, ocr_failed_pages=[2])

    outcome = build_attempt_outcome(
        result, prior_page_attempts={}, attempts_exhausted=False
    )

    assert outcome.page_attempts == {"2": 1}
    assert outcome.remaining_page_numbers == [2]


def test_page_attempts_accumulate_across_resumes() -> None:
    pages = [_page(2, PageStatus.OCR_FAILED)]
    result = _result(pages, completeness=Completeness.PARTIAL, ocr_failed_pages=[2])

    outcome = build_attempt_outcome(
        result, prior_page_attempts={"2": 2}, attempts_exhausted=False
    )

    assert outcome.page_attempts == {"2": 3}


def test_resolved_pages_are_never_listed_as_remaining() -> None:
    pages = [_page(1, PageStatus.OCR_COMPLETED), _page(2, PageStatus.OCR_FAILED)]
    result = _result(pages, completeness=Completeness.PARTIAL, ocr_failed_pages=[2])

    outcome = build_attempt_outcome(
        result, prior_page_attempts={}, attempts_exhausted=False
    )

    assert 1 in outcome.resolved_page_numbers
    assert 1 not in outcome.remaining_page_numbers


def test_exhausted_attempts_end_in_human_review_not_completion() -> None:
    pages = [_page(2, PageStatus.OCR_FAILED)]
    result = _result(pages, completeness=Completeness.PARTIAL, ocr_failed_pages=[2])

    outcome = build_attempt_outcome(
        result, prior_page_attempts={"2": 3}, attempts_exhausted=True
    )

    assert outcome.state is ProcessingState.HUMAN_REVIEW_REQUIRED
    assert outcome.state is not ProcessingState.COMPLETED


def test_unrenderable_page_goes_straight_to_human_review() -> None:
    pages = [_page(2, PageStatus.UNRENDERABLE)]
    result = _result(
        pages, completeness=Completeness.PARTIAL, unrenderable_pages=[2]
    )

    outcome = build_attempt_outcome(
        result, prior_page_attempts={}, attempts_exhausted=False
    )

    assert outcome.state is ProcessingState.HUMAN_REVIEW_REQUIRED


def test_ocr_disabled_pages_are_remaining_but_not_charged_an_attempt() -> None:
    pages = [_page(1, PageStatus.OCR_DISABLED)]
    result = _result(pages, completeness=Completeness.PARTIAL)

    outcome = build_attempt_outcome(
        result, prior_page_attempts={}, attempts_exhausted=False
    )

    assert outcome.remaining_page_numbers == [1]
    assert outcome.page_attempts == {}


def test_missing_extraction_result_is_a_failure_not_a_completion() -> None:
    outcome = build_attempt_outcome(
        None, prior_page_attempts={}, attempts_exhausted=False
    )

    assert outcome.state is ProcessingState.FAILED
    assert outcome.state is not ProcessingState.COMPLETED


def test_outcome_is_serialisable_for_the_durable_checkpoint_() -> None:
    result = _result([_page(1, PageStatus.TEXT_LAYER)])

    outcome = build_attempt_outcome(
        result, prior_page_attempts={}, attempts_exhausted=False
    )
    checkpoint = outcome.to_checkpoint()

    assert checkpoint["processing_state"] == "completed"
    assert checkpoint["remaining_page_numbers"] == []
    assert checkpoint["resolved_page_numbers"] == [1]
    assert checkpoint["expected_page_numbers"] == [1]
    assert checkpoint["page_attempts"] == {}


# --- End-to-end through the durable job loop --------------------------------
#
# The unit tests above pin the decision. These pin the consequence: a job whose
# extraction left pages unresolved must not reach `completed`.

import pytest  # noqa: E402
from bson import ObjectId  # noqa: E402

from rbac_backend.services.document_service import DocumentService  # noqa: E402
from rbac_backend.tests.test_document_processing_jobs import (  # noqa: E402
    FakeDB,
    insert_document,
    make_document,
)


async def _queued_job(db: FakeDB) -> tuple[DocumentService, str, ObjectId]:
    service = DocumentService(db)
    document_id = ObjectId()
    document = make_document(document_id)
    await insert_document(db, document, document_id)
    job_id = await service.queue_document_processing(document, "letter.pdf")
    return service, job_id, document_id


async def test_queued_job_starts_with_an_empty_checkpoint() -> None:
    db = FakeDB()
    _, job_id, _ = await _queued_job(db)

    job = await db.document_processing_jobs.find_one({"_id": job_id})

    assert job["processing_state"] == "queued"
    assert job["remaining_page_numbers"] == []
    assert job["page_attempts"] == {}
    assert job["resume_count"] == 0


@pytest.mark.asyncio
async def test_partial_extraction_requeues_instead_of_completing(monkeypatch) -> None:
    db = FakeDB()
    service, job_id, document_id = await _queued_job(db)

    async def fake_process(*args, **kwargs):
        await db.document_processing_jobs.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "processing_state": ProcessingState.PARTIALLY_PROCESSED.value,
                    "remaining_page_numbers": [7, 8],
                    "resolved_page_numbers": [1, 2, 3],
                }
            },
        )
        return True

    monkeypatch.setattr(service, "process_document_async", fake_process)

    ok = await service.process_document_job(job_id)
    job = await db.document_processing_jobs.find_one({"_id": job_id})
    stored = await db.documents.find_one({"_id": document_id})

    assert ok is False
    assert job["status"] == "retrying"
    assert job["remaining_page_numbers"] == [7, 8]
    assert job["resume_count"] == 1
    assert stored["processing_status"] == "partially_processed"
    assert stored["processing_status"] != "completed"


@pytest.mark.asyncio
async def test_unresolved_pages_with_attempts_gone_end_in_human_review(
    monkeypatch,
) -> None:
    db = FakeDB()
    service, job_id, document_id = await _queued_job(db)

    async def fake_process(*args, **kwargs):
        await db.document_processing_jobs.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "processing_state": ProcessingState.HUMAN_REVIEW_REQUIRED.value,
                    "remaining_page_numbers": [2],
                }
            },
        )
        return True

    monkeypatch.setattr(service, "process_document_async", fake_process)

    ok = await service.process_document_job(job_id)
    job = await db.document_processing_jobs.find_one({"_id": job_id})
    stored = await db.documents.find_one({"_id": document_id})

    assert ok is False
    assert job["status"] == "human_review_required"
    assert stored["processing_status"] == "human_review_required"
    assert stored["processing_status"] != "completed"
    assert stored["processing_error"]["pages"] == [2]


@pytest.mark.asyncio
async def test_fully_resolved_extraction_still_completes(monkeypatch) -> None:
    db = FakeDB()
    service, job_id, document_id = await _queued_job(db)

    async def fake_process(*args, **kwargs):
        await db.document_processing_jobs.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "processing_state": ProcessingState.COMPLETED.value,
                    "remaining_page_numbers": [],
                }
            },
        )
        return True

    monkeypatch.setattr(service, "process_document_async", fake_process)

    ok = await service.process_document_job(job_id)
    job = await db.document_processing_jobs.find_one({"_id": job_id})
    stored = await db.documents.find_one({"_id": document_id})

    assert ok is True
    assert job["status"] == "completed"
    assert stored["processing_status"] == "completed"


@pytest.mark.asyncio
async def test_archive_job_is_stored_only_never_completed(monkeypatch) -> None:
    db = FakeDB()
    service, job_id, document_id = await _queued_job(db)

    async def fake_process(*args, **kwargs):
        await db.document_processing_jobs.update_one(
            {"_id": job_id},
            {"$set": {"processing_state": ProcessingState.STORED_ONLY.value}},
        )
        return True

    monkeypatch.setattr(service, "process_document_async", fake_process)

    ok = await service.process_document_job(job_id)
    job = await db.document_processing_jobs.find_one({"_id": job_id})
    stored = await db.documents.find_one({"_id": document_id})

    assert ok is False
    assert job["status"] == "stored_only"
    assert stored["processing_status"] == "stored_only"
    assert stored["processing_status"] != "completed"
