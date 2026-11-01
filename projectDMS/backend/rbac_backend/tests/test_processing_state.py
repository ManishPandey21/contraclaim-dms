"""Processing states must never report unfinished work as completed."""

from __future__ import annotations

from rbac_backend.models.processing_state import (
    RESUMABLE_STATES,
    SUCCESS_STATES,
    TERMINAL_STATES,
    ProcessingState,
    derive_processing_state,
)
from rbac_backend.services.extraction.models import (
    Completeness,
    PageExtractionResult,
)


def _result(**overrides: object) -> PageExtractionResult:
    defaults = dict(
        pages=[],
        combined_text="text",
        ocr_pages_total=0,
        ocr_failed_pages=[],
        ocr_deferred_pages=[],
        unrenderable_pages=[],
        completeness=Completeness.COMPLETE,
        engine_version="1",
    )
    defaults.update(overrides)
    return PageExtractionResult(**defaults)  # type: ignore[arg-type]


def test_completed_is_not_a_resumable_state() -> None:
    assert ProcessingState.COMPLETED in SUCCESS_STATES
    assert ProcessingState.COMPLETED not in RESUMABLE_STATES


def test_partially_processed_is_never_a_success_state() -> None:
    assert ProcessingState.PARTIALLY_PROCESSED not in SUCCESS_STATES
    assert ProcessingState.PARTIALLY_PROCESSED in RESUMABLE_STATES


def test_human_review_required_is_terminal_but_not_success() -> None:
    assert ProcessingState.HUMAN_REVIEW_REQUIRED in TERMINAL_STATES
    assert ProcessingState.HUMAN_REVIEW_REQUIRED not in SUCCESS_STATES
    assert ProcessingState.HUMAN_REVIEW_REQUIRED not in RESUMABLE_STATES


def test_archive_stored_only_is_explicit_and_never_completed() -> None:
    assert ProcessingState.STORED_ONLY in TERMINAL_STATES
    assert ProcessingState.STORED_ONLY not in SUCCESS_STATES
    assert ProcessingState.STORED_ONLY is not ProcessingState.COMPLETED


def test_state_values_are_stable_persisted_strings() -> None:
    # These land in documents.processing_status; changing one is a migration.
    assert ProcessingState.COMPLETED.value == "completed"
    assert ProcessingState.PARTIALLY_PROCESSED.value == "partially_processed"
    assert ProcessingState.HUMAN_REVIEW_REQUIRED.value == "human_review_required"
    assert ProcessingState.STORED_ONLY.value == "stored_only"


def test_existing_lifecycle_values_are_preserved() -> None:
    # queued/processing/completed/failed already exist in production records.
    existing = {"queued", "processing", "completed", "failed"}

    assert existing.issubset({state.value for state in ProcessingState})


def test_complete_extraction_yields_completed() -> None:
    state = derive_processing_state(_result(), attempts_exhausted=False)

    assert state is ProcessingState.COMPLETED


def test_deferred_pages_yield_partially_processed() -> None:
    state = derive_processing_state(
        _result(completeness=Completeness.PARTIAL, ocr_deferred_pages=[7, 8]),
        attempts_exhausted=False,
    )

    assert state is ProcessingState.PARTIALLY_PROCESSED


def test_deferred_pages_with_attempts_exhausted_need_a_human() -> None:
    state = derive_processing_state(
        _result(completeness=Completeness.PARTIAL, ocr_deferred_pages=[7]),
        attempts_exhausted=True,
    )

    assert state is ProcessingState.HUMAN_REVIEW_REQUIRED


def test_failed_pages_with_attempts_exhausted_need_a_human() -> None:
    state = derive_processing_state(
        _result(completeness=Completeness.PARTIAL, ocr_failed_pages=[1, 2]),
        attempts_exhausted=True,
    )

    assert state is ProcessingState.HUMAN_REVIEW_REQUIRED


def test_failed_pages_with_attempts_remaining_are_resumable() -> None:
    state = derive_processing_state(
        _result(completeness=Completeness.PARTIAL, ocr_failed_pages=[1]),
        attempts_exhausted=False,
    )

    assert state is ProcessingState.PARTIALLY_PROCESSED
    assert state in RESUMABLE_STATES


def test_unrenderable_page_is_never_completed() -> None:
    for exhausted in (False, True):
        state = derive_processing_state(
            _result(completeness=Completeness.PARTIAL, unrenderable_pages=[2]),
            attempts_exhausted=exhausted,
        )
        assert state is not ProcessingState.COMPLETED
    assert state is ProcessingState.HUMAN_REVIEW_REQUIRED


def test_unrenderable_needs_a_human_even_with_attempts_remaining() -> None:
    # Retrying cannot fix a page that will not render; only a person can.
    state = derive_processing_state(
        _result(completeness=Completeness.PARTIAL, unrenderable_pages=[2]),
        attempts_exhausted=False,
    )

    assert state is ProcessingState.HUMAN_REVIEW_REQUIRED


def test_partial_extraction_can_never_derive_completed() -> None:
    for exhausted in (True, False):
        state = derive_processing_state(
            _result(completeness=Completeness.PARTIAL, ocr_failed_pages=[3]),
            attempts_exhausted=exhausted,
        )
        assert state is not ProcessingState.COMPLETED


def test_partial_completeness_alone_blocks_completed_even_with_empty_lists() -> None:
    # Defensive: if completeness says PARTIAL, trust it over the page lists.
    state = derive_processing_state(
        _result(completeness=Completeness.PARTIAL), attempts_exhausted=False
    )

    assert state is not ProcessingState.COMPLETED
