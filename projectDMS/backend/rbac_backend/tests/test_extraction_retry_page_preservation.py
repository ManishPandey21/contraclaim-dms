"""A retry must not erase what an earlier attempt already recovered.

One stable ``extraction_run_id`` is one cumulative extraction run. A second
attempt that re-runs only the pages the job still owes must leave every
already-resolved page - its text, source, status, verdict, repairs and batch
evidence - exactly as the earlier attempt left it, and the text that reaches
``documents.full_text`` and the embedding seam must be the whole run in page
order, not the latest attempt's slice of it.

These tests drive the real service/processor/engine/store chain (see
``retry_harness``); only OCR, OpenAI and publication are stubbed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from rbac_backend.tests.retry_harness import CountingLadder, RetryHarness

NATIVE_THREE = "NATIVE PAGE THREE"
OCR_ONE = "OCR PAGE ONE"
OCR_TWO = "OCR PAGE TWO"

#: page 1 scan, page 2 scan, page 3 native text.
_TWO_SCANS_ONE_NATIVE: List[Optional[List[str]]] = [None, None, [NATIVE_THREE]]


async def _partial_then_recovered(tmp_path: Path, monkeypatch: Any) -> RetryHarness:
    """Attempt 1 resolves pages 1 and 3 and fails page 2; attempt 2 fixes it."""
    return await RetryHarness.create(
        tmp_path,
        monkeypatch,
        page_lines=_TWO_SCANS_ONE_NATIVE,
        script={
            1: {1: OCR_ONE, 2: RuntimeError("OCRmyPDF batch failed (exit=2)")},
            2: {2: OCR_TWO},
        },
    )


# --- Attempt 1: the checkpoint the retry builds on -----------------------


async def test_first_attempt_resolves_what_it_can_and_records_the_rest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _partial_then_recovered(tmp_path, monkeypatch)

    completed = await harness.run_attempt()
    pages = await harness.page_state()
    job = await harness.job()

    assert completed is False
    assert pages[1]["text"] == OCR_ONE
    assert pages[1]["status"] == "ocr_completed"
    assert pages[1]["source"] == "ocr"
    assert pages[2]["status"] == "ocr_failed"
    assert pages[3]["text"].strip() == NATIVE_THREE
    assert pages[3]["status"] == "text_layer"
    assert job["remaining_page_numbers"] == [2]
    assert job["resolved_page_numbers"] == [1, 3]
    assert job["processing_state"] == "partially_processed"
    assert {page["extraction_run_id"] for page in pages.values()} == {harness.job_id}


# --- Attempt 2: the defect ----------------------------------------------


async def test_retry_preserves_the_page_an_earlier_attempt_recovered(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _partial_then_recovered(tmp_path, monkeypatch)
    await harness.run_attempt()
    before = await harness.page_state()

    await harness.run_attempt()
    after = await harness.page_state()

    assert before[1]["text"] == OCR_ONE
    # The row an untouched page owns must be byte-identical across the retry.
    assert after[1] == before[1]
    assert after[3] == before[3]
    assert after[2]["text"] == OCR_TWO
    assert after[2]["status"] == "ocr_completed"


async def test_retry_indexes_the_complete_run_in_page_order(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _partial_then_recovered(tmp_path, monkeypatch)
    await harness.run_attempt()
    await harness.run_attempt()

    full_text = harness.full_text()
    embedding_text = harness.embedding_text()

    for fragment in (OCR_ONE, OCR_TWO, NATIVE_THREE):
        assert fragment in full_text, f"{fragment!r} missing from documents.full_text"
        assert fragment in embedding_text, f"{fragment!r} missing from embedding text"
    assert (
        full_text.index(OCR_ONE) < full_text.index(OCR_TWO) < full_text.index(NATIVE_THREE)
    )


async def test_retry_writes_the_complete_run_to_the_document_record(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _partial_then_recovered(tmp_path, monkeypatch)
    await harness.run_attempt()
    await harness.run_attempt()

    stored = await harness.document()

    assert OCR_ONE in str(stored.get("full_text") or "")
    assert OCR_TWO in str(stored.get("full_text") or "")
    assert NATIVE_THREE in str(stored.get("full_text") or "")


async def test_successful_retry_closes_the_checkpoint_without_resetting_history(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _partial_then_recovered(tmp_path, monkeypatch)
    await harness.run_attempt()
    first = await harness.job()

    await harness.run_attempt()
    job = await harness.job()
    stored = await harness.document()

    assert job["expected_page_numbers"] == [1, 2, 3]
    assert job["resolved_page_numbers"] == [1, 2, 3]
    assert job["remaining_page_numbers"] == []
    assert job["processing_state"] == "completed"
    assert stored["processing_status"] == "completed"
    # Page 2 was charged for its failed attempt and keeps that history; the
    # pages that were never retried are not charged again.
    assert first["page_attempts"] == {"2": 1}
    assert job["page_attempts"] == {"2": 1}


# --- Cost control: only the retried page may be OCR'd --------------------


async def test_a_retry_only_ocrs_the_pages_it_owes(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _partial_then_recovered(tmp_path, monkeypatch)
    await harness.run_attempt()
    await harness.run_attempt()

    assert harness.runner.requested_by_attempt[1] == [[1], [2]]
    assert harness.runner.requested_by_attempt[2] == [[2]]


async def test_a_retry_does_not_re_spend_the_fallback_ladder_on_settled_pages(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ladder = CountingLadder()
    harness = await RetryHarness.create(
        tmp_path,
        monkeypatch,
        page_lines=_TWO_SCANS_ONE_NATIVE,
        script={
            1: {1: OCR_ONE, 2: RuntimeError("OCRmyPDF batch failed (exit=2)")},
            2: {2: OCR_TWO},
        },
        fallback_ladder=ladder,
    )

    await harness.run_attempt()
    spent_after_first = list(ladder.calls)
    await harness.run_attempt()

    # Whatever the first attempt escalated, the retry must not pay for again:
    # the only page it may reassess is the one it actually re-extracted.
    assert [page for page in ladder.calls[len(spent_after_first):] if page != 2] == []
    assert harness.gate_assessed[1] == [2]


# --- Repeated retries ----------------------------------------------------


async def test_three_attempts_accumulate_and_never_lose_a_resolved_page(
    tmp_path: Path, monkeypatch: Any
) -> None:
    failure = RuntimeError("OCRmyPDF batch failed (exit=2)")
    harness = await RetryHarness.create(
        tmp_path,
        monkeypatch,
        page_lines=[None, None, None],
        script={
            1: {1: "OCR PAGE ONE", 2: failure, 3: failure},
            2: {2: "OCR PAGE TWO", 3: failure},
            3: {3: "OCR PAGE THREE"},
        },
    )

    await harness.run_attempt()
    await harness.run_attempt()
    third = await harness.run_attempt()

    pages = await harness.page_state()
    job = await harness.job()

    assert third is True
    assert pages[1]["text"] == "OCR PAGE ONE"
    assert pages[2]["text"] == "OCR PAGE TWO"
    assert pages[3]["text"] == "OCR PAGE THREE"
    assert job["remaining_page_numbers"] == []
    full_text = harness.full_text()
    assert (
        full_text.index("OCR PAGE ONE")
        < full_text.index("OCR PAGE TWO")
        < full_text.index("OCR PAGE THREE")
    )


async def test_mixed_native_and_ocr_pages_keep_source_order_not_attempt_order(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await RetryHarness.create(
        tmp_path,
        monkeypatch,
        page_lines=[["NATIVE PAGE ONE"], None, None, ["NATIVE PAGE FOUR"]],
        script={
            1: {2: "OCR PAGE TWO", 3: RuntimeError("OCRmyPDF batch failed (exit=2)")},
            2: {3: "OCR PAGE THREE"},
        },
    )

    await harness.run_attempt()
    await harness.run_attempt()

    full_text = harness.full_text()
    positions = [
        full_text.index(fragment)
        for fragment in (
            "NATIVE PAGE ONE",
            "OCR PAGE TWO",
            "OCR PAGE THREE",
            "NATIVE PAGE FOUR",
        )
    ]
    assert positions == sorted(positions)


async def test_a_failed_retry_leaves_the_earlier_success_intact(
    tmp_path: Path, monkeypatch: Any
) -> None:
    failure = RuntimeError("OCRmyPDF batch failed (exit=2)")
    harness = await RetryHarness.create(
        tmp_path,
        monkeypatch,
        page_lines=[None, None],
        script={1: {1: OCR_ONE, 2: failure}, 2: {2: failure}},
    )

    await harness.run_attempt()
    before = await harness.page_state()
    completed = await harness.run_attempt()
    after = await harness.page_state()
    job = await harness.job()

    assert completed is False
    assert after[1] == before[1]
    assert after[1]["text"] == OCR_ONE
    assert after[2]["status"] == "ocr_failed"
    assert job["remaining_page_numbers"] == [2]
    assert job["processing_state"] != "completed"


# --- Crash safety --------------------------------------------------------


async def test_a_retry_that_dies_downstream_leaves_the_run_reconstructable(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Attempt 2 writes its page, then the job dies before it finishes."""
    harness = await _partial_then_recovered(tmp_path, monkeypatch)
    await harness.run_attempt()

    async def _die(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise RuntimeError("worker died after the page write")

    harness.attempt += 1
    harness.runner.start_attempt(harness.attempt)
    harness.gate_assessed.append([])
    processor = harness._processor()
    monkeypatch.setattr(processor, "_extract_and_persist", _die)
    monkeypatch.setattr(
        "rbac_backend.services.document_service.create_document_processor",
        lambda config=None: processor,
    )
    await harness.service.process_document_job(harness.job_id)

    pages = await harness.page_state()

    assert pages[1]["text"] == OCR_ONE
    assert pages[1]["status"] == "ocr_completed"
    assert pages[2]["text"] == OCR_TWO


# --- A checkpoint the run cannot honour must fail visibly ----------------


async def test_a_retry_whose_earlier_page_row_vanished_fails_instead_of_regenerating(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _partial_then_recovered(tmp_path, monkeypatch)
    await harness.run_attempt()

    # The row page 1 wrote is gone (lost write, manual deletion, wrong run).
    pages = harness.db["document_ocr_pages"]
    for key, row in list(pages.docs.items()):
        if row["page_number"] == 1:
            del pages.docs[key]

    saves_before = len(harness.saves())
    completed = await harness.run_attempt()
    remaining_rows = await harness.page_state()
    job = await harness.job()

    assert completed is False
    assert job["processing_state"] != "completed"
    # Page 1 is absent, not resurrected as an empty native page...
    assert 1 not in remaining_rows
    # ...and the attempt reached neither persistence nor the index, so no
    # document text was rewritten from an incomplete run.
    assert len(harness.saves()) == saves_before
    assert (await harness.document())["processing_status"] != "completed"


# --- An attempt that carries no retry list is still bound by the run ------


async def test_an_attempt_after_a_lost_checkpoint_keeps_the_resolved_pages(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The crash window: pages written, checkpoint never reached.

    `_checkpoint_extraction_attempt` returns early when the processor produced
    no extraction result, so a failure after the page write leaves the job with
    an empty `remaining_page_numbers`. The next attempt therefore carries no
    retry list at all - and must still not rewrite what the run resolved.
    """
    harness = await _partial_then_recovered(tmp_path, monkeypatch)
    await harness.run_attempt()
    before = await harness.page_state()

    await harness.db.document_processing_jobs.update_one(
        {"_id": harness.job_id}, {"$set": {"remaining_page_numbers": []}}
    )
    # This attempt has no OCR answers scripted at all, so anything it is
    # allowed to re-extract comes back empty.
    await harness.run_attempt()
    after = await harness.page_state()

    assert after[1] == before[1]
    assert after[1]["text"] == OCR_ONE
    assert after[3] == before[3]
    assert OCR_ONE in harness.full_text()


async def test_an_attempt_without_a_retry_list_may_still_finish_unresolved_pages(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await RetryHarness.create(
        tmp_path,
        monkeypatch,
        page_lines=_TWO_SCANS_ONE_NATIVE,
        script={
            1: {1: OCR_ONE, 2: RuntimeError("OCRmyPDF batch failed (exit=2)")},
            2: {2: OCR_TWO},
        },
    )
    await harness.run_attempt()
    await harness.db.document_processing_jobs.update_one(
        {"_id": harness.job_id}, {"$set": {"remaining_page_numbers": []}}
    )

    await harness.run_attempt()
    pages = await harness.page_state()

    # Page 2 was still unresolved, so it is re-extracted and recovered...
    assert pages[2]["text"] == OCR_TWO
    # ...while page 1 is not re-OCR'd at all.
    assert harness.runner.requested_by_attempt[2] == [[2]]
    assert pages[1]["text"] == OCR_ONE


async def test_a_source_that_lost_pages_fails_closed_instead_of_truncating(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from rbac_backend.tests.fixtures.pdf_builders import build_page_text_map_pdf

    harness = await _partial_then_recovered(tmp_path, monkeypatch)
    await harness.run_attempt()
    before = await harness.page_state()

    # The file now reads as two pages: page 3's evidence is no longer covered.
    build_page_text_map_pdf(harness.source, page_lines=[None, None])
    saves_before = len(harness.saves())
    completed = await harness.run_attempt()

    job = await harness.job()
    assert completed is False
    assert job["processing_state"] != "completed"
    # Nothing was re-indexed from the truncated read...
    assert len(harness.saves()) == saves_before
    # ...and the run's own evidence is untouched.
    assert await harness.page_state() == before


async def test_an_unreadable_page_row_fails_closed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _partial_then_recovered(tmp_path, monkeypatch)
    await harness.run_attempt()

    rows = harness.db["document_ocr_pages"]
    for row in rows.docs.values():
        if row["page_number"] == 1:
            row["status"] = "a_status_no_version_of_this_code_wrote"

    saves_before = len(harness.saves())
    completed = await harness.run_attempt()
    job = await harness.job()

    assert completed is False
    assert job["processing_state"] == "human_review_required"
    assert len(harness.saves()) == saves_before


async def test_an_inconsistent_run_goes_to_review_instead_of_burning_attempts(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _partial_then_recovered(tmp_path, monkeypatch)
    await harness.run_attempt()

    pages = harness.db["document_ocr_pages"]
    for key, row in list(pages.docs.items()):
        if row["page_number"] == 1:
            del pages.docs[key]

    await harness.run_attempt()

    job = await harness.job()
    stored = await harness.document()

    # Terminal, not "retrying": another attempt would re-derive the same
    # contradiction and dead-letter the job with an opaque message.
    assert job["status"] == "human_review_required"
    assert job["processing_state"] == "human_review_required"
    assert stored["processing_status"] == "human_review_required"
    assert stored["processing_error"]["pages"] == [1]


# --- The gate's own state across attempts --------------------------------


async def test_a_carried_page_that_was_never_assessed_is_still_assessed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _partial_then_recovered(tmp_path, monkeypatch)
    await harness.run_attempt()

    # A row the engine wrote whose verdict never landed: unassessed, not clean.
    for row in harness.db["document_ocr_pages"].docs.values():
        if row["page_number"] == 1:
            row["quality_verdict"] = None

    await harness.run_attempt()

    assert 1 in harness.gate_assessed[1]
    pages = await harness.page_state()
    assert pages[1]["quality_verdict"] is not None
    assert pages[1]["text"] == OCR_ONE


async def test_a_review_blocked_document_is_not_made_consumable_by_a_resume(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Page 2 still owes work, so the job resumes - but page 1 failed review.

    `partially_processed` is a consumable status, so writing it over the block
    would serve a gate-failed document's text to retrieval and drafting for as
    long as the resumes continue.
    """
    harness = await RetryHarness.create(
        tmp_path,
        monkeypatch,
        page_lines=[[NATIVE_THREE], None],
        script={1: {2: RuntimeError("OCRmyPDF batch failed (exit=2)")}},
        escalate_pages={1},
    )

    completed = await harness.run_attempt()
    stored = await harness.document()
    job = await harness.job()

    assert completed is False
    assert job["remaining_page_numbers"] == [2]
    assert stored["processing_status"] == "human_review_required"
    assert stored["processing_status"] != "partially_processed"
