"""Reading-order and text-density coherence checks."""

from __future__ import annotations

from rbac_backend.services.extraction.models import PageClass, PageClassification
from rbac_backend.services.extraction.quality.models import Verdict
from rbac_backend.services.extraction.quality.reading_order import (
    check_reading_order,
    check_text_density,
)


def _classification(page_class: PageClass, char_count: int) -> PageClassification:
    return PageClassification(
        page_class=page_class,
        char_count=char_count,
        image_count=0,
        image_coverage=0.0,
        table_count=1,
        width=595.0,
        height=842.0,
        rotation=0,
    )


def test_coherent_narrative_passes() -> None:
    text = (
        "The Contractor hereby submits its revised cost claim in respect of the "
        "reworks necessitated by the soil collapse at Nayaganj Station, as "
        "directed by the Engineer in charge."
    )

    result = check_reading_order(
        text, _classification(PageClass.TEXT_NATIVE, len(text))
    )

    assert result.verdict is Verdict.PASS


def test_shredded_narrative_is_detected() -> None:
    # Companion 3.5, measured verbatim: a sentence interleaved with the
    # adjacent table's header cells.
    text = (
        "GUIDE WALL & D-WALL REWORKS COST WITH INCLUDING ALL TOOLS AS PER THE "
        "Depth Thickness Length Volume\n"
        "DW No Area\n"
        "SPECIFICATION, DRAWINGS AND DIRECTION OF ENGINEER IN CHARGE "
        "(mtr) (mtr) (mtr) (m3)"
    )

    result = check_reading_order(
        text, _classification(PageClass.MIXED_CONTENT, len(text))
    )

    assert result.verdict is Verdict.FAIL
    assert "reading order" in result.detail.lower()


def test_pure_table_text_is_not_checkable_for_reading_order() -> None:
    text = "1 100 200\n2 300 400\n3 500 600"

    result = check_reading_order(
        text, _classification(PageClass.TABLE_HEAVY, len(text))
    )

    assert result.verdict is Verdict.NOT_CHECKABLE


def test_empty_text_is_not_checkable_for_reading_order() -> None:
    result = check_reading_order("", _classification(PageClass.BLANK, 0))

    assert result.verdict is Verdict.NOT_CHECKABLE


def test_a_normal_letter_body_is_not_falsely_shredded() -> None:
    # Guards the false-positive direction: ordinary correspondence with a
    # reference line must not be called shredded.
    text = (
        "Ref: CC/UPMRC/2026/0142\n"
        "Sub: Revised cost claim for reworks at Nayaganj Station\n"
        "Dear Sir,\n"
        "We refer to the Engineer's instruction dated 09 March 2021 and submit "
        "herewith our revised cost claim for the reworks necessitated by the "
        "soil collapse."
    )

    result = check_reading_order(
        text, _classification(PageClass.MIXED_CONTENT, len(text))
    )

    assert result.verdict is not Verdict.FAIL


def test_mixed_content_page_with_no_text_fails_density() -> None:
    # The measured 2.1 defect: a page that plainly has content, extracted empty.
    result = check_text_density("", _classification(PageClass.MIXED_CONTENT, 0))

    assert result.verdict is Verdict.FAIL


def test_scanned_page_with_no_text_fails_density() -> None:
    result = check_text_density("", _classification(PageClass.SCANNED_IMAGE, 0))

    assert result.verdict is Verdict.FAIL


def test_table_heavy_page_with_no_text_fails_density() -> None:
    result = check_text_density("", _classification(PageClass.TABLE_HEAVY, 0))

    assert result.verdict is Verdict.FAIL


def test_blank_page_with_no_text_is_expected_and_passes() -> None:
    result = check_text_density("", _classification(PageClass.BLANK, 0))

    assert result.verdict is Verdict.PASS


def test_text_page_with_text_passes_density() -> None:
    text = "x" * 500

    result = check_text_density(text, _classification(PageClass.TEXT_NATIVE, len(text)))

    assert result.verdict is Verdict.PASS


def test_unrenderable_page_is_not_checkable_for_density() -> None:
    result = check_text_density("", _classification(PageClass.UNRENDERABLE, 0))

    assert result.verdict is Verdict.NOT_CHECKABLE
