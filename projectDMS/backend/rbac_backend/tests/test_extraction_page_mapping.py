"""Source-to-output page mapping for OCR batches.

Replaces the `output_count >= max(ordered)` heuristic in contracts_ingest,
which silently mis-attributed page text when its two branches disagreed - it
would store page 8's text under page 1 without raising. This module accepts
only the two shapes that are actually possible and fails loudly on anything
else.

Measured on production 2026-08-14 (docs/architecture/phase0_extraction_measurements_2026-08-14.md):
`ocrmypdf --pages 1-2` on a 9-page input emits a 9-page output, so the
full-length branch is the live one and the trimmed branch is defensive.
"""

from __future__ import annotations

import pytest

from rbac_backend.services.extraction.page_mapping import (
    PageMappingError,
    map_source_pages_to_output,
)


def test_measured_production_shape_is_full_length() -> None:
    """Pins the Phase 0 measurement: ocrmypdf retains every input page.

    If this ever changes, the OCR runner should fail visibly rather than
    quietly switching interpretations.
    """
    mapping = map_source_pages_to_output([1, 2], output_page_count=9, input_page_count=9)

    assert mapping == {1: 0, 2: 1}


def test_full_length_output_maps_absolutely() -> None:
    mapping = map_source_pages_to_output([1, 2], output_page_count=9, input_page_count=9)

    assert mapping == {1: 0, 2: 1}


def test_full_length_output_maps_late_batch_absolutely() -> None:
    mapping = map_source_pages_to_output([8, 9], output_page_count=9, input_page_count=9)

    assert mapping == {8: 7, 9: 8}


def test_trimmed_output_maps_positionally() -> None:
    # Defensive branch: no released ocrmypdf we have measured trims its output,
    # but a different tool or flag set could.
    mapping = map_source_pages_to_output([8, 9], output_page_count=2, input_page_count=9)

    assert mapping == {8: 0, 9: 1}


def test_single_page_batch_on_trimmed_output() -> None:
    mapping = map_source_pages_to_output([5], output_page_count=1, input_page_count=9)

    assert mapping == {5: 0}


def test_single_page_document_is_unambiguous() -> None:
    # Both interpretations agree here; the result must still be correct.
    mapping = map_source_pages_to_output([1], output_page_count=1, input_page_count=1)

    assert mapping == {1: 0}


def test_ambiguous_shape_raises_rather_than_guessing() -> None:
    with pytest.raises(PageMappingError) as excinfo:
        map_source_pages_to_output([1, 2], output_page_count=5, input_page_count=9)

    assert "neither" in str(excinfo.value).lower()


def test_output_smaller_than_batch_raises() -> None:
    with pytest.raises(PageMappingError):
        map_source_pages_to_output([1, 2, 3], output_page_count=2, input_page_count=9)


def test_empty_request_maps_to_nothing() -> None:
    assert map_source_pages_to_output([], output_page_count=9, input_page_count=9) == {}


def test_requested_page_beyond_input_raises() -> None:
    with pytest.raises(PageMappingError):
        map_source_pages_to_output([10], output_page_count=9, input_page_count=9)


def test_duplicate_and_unordered_requests_are_normalised() -> None:
    mapping = map_source_pages_to_output(
        [2, 1, 2], output_page_count=9, input_page_count=9
    )

    assert mapping == {1: 0, 2: 1}


def test_non_positive_page_numbers_are_ignored() -> None:
    mapping = map_source_pages_to_output(
        [0, -3, 4], output_page_count=9, input_page_count=9
    )

    assert mapping == {4: 3}


def test_error_message_names_the_pages_it_refused_to_map() -> None:
    with pytest.raises(PageMappingError) as excinfo:
        map_source_pages_to_output([3, 4], output_page_count=7, input_page_count=9)

    message = str(excinfo.value)
    assert "[3, 4]" in message
    assert "7" in message
