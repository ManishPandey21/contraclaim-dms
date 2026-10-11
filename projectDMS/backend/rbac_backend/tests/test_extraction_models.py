"""Domain models shared by every extraction caller."""

from __future__ import annotations

from rbac_backend.services.extraction.models import (
    Completeness,
    ExtractedPage,
    PageClass,
    PageClassification,
    PageExtractionPolicy,
    PageExtractionResult,
    PageSource,
    PageStatus,
    SourceKind,
)


def _classification(**overrides: object) -> PageClassification:
    defaults = dict(
        page_class=PageClass.TEXT_NATIVE,
        char_count=120,
        image_count=0,
        image_coverage=0.0,
        table_count=0,
        width=595.0,
        height=842.0,
        rotation=0,
    )
    defaults.update(overrides)
    return PageClassification(**defaults)  # type: ignore[arg-type]


def test_enum_values_are_stable_strings() -> None:
    # These strings are persisted; changing one is a migration, not a rename.
    assert SourceKind.PDF.value == "pdf"
    assert SourceKind.ARCHIVE.value == "archive"
    assert PageSource.TEXT_LAYER.value == "text_layer"
    assert PageSource.RECONSTRUCTED.value == "reconstructed"
    assert PageStatus.OCR_DEFERRED.value == "ocr_deferred"
    assert PageStatus.UNRENDERABLE.value == "unrenderable"
    assert PageClass.SCANNED_IMAGE.value == "scanned_image"
    assert Completeness.PARTIAL.value == "partial"


def test_contract_page_status_vocabulary_is_preserved() -> None:
    # The contract path already persists these five values; they must survive.
    existing = {"text_layer", "ocr_completed", "ocr_empty", "ocr_failed", "ocr_disabled"}

    assert existing.issubset({status.value for status in PageStatus})


def test_extracted_page_reports_char_count() -> None:
    page = ExtractedPage(
        number=3,
        text="  hello world  ",
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=_classification(),
    )

    assert page.char_count == len("hello world")


def test_extracted_page_defaults_have_no_batch_or_error() -> None:
    page = ExtractedPage(
        number=1,
        text="",
        source=PageSource.EMPTY,
        status=PageStatus.OCR_PENDING,
        classification=_classification(char_count=0),
    )

    assert page.batch_id is None
    assert page.error is None
    assert page.quality_verdict is None
    assert page.quality_checks == []
    assert page.needs_review is False
    assert page.tables == []


def test_extracted_pages_do_not_share_mutable_defaults() -> None:
    first = ExtractedPage(
        number=1,
        text="",
        source=PageSource.EMPTY,
        status=PageStatus.OCR_PENDING,
        classification=_classification(char_count=0),
    )
    second = ExtractedPage(
        number=2,
        text="",
        source=PageSource.EMPTY,
        status=PageStatus.OCR_PENDING,
        classification=_classification(char_count=0),
    )

    first.quality_checks.append({"check": "example"})
    first.tables.append([["a"]])

    assert second.quality_checks == []
    assert second.tables == []


def test_classification_reports_landscape_orientation() -> None:
    portrait = _classification(width=595.0, height=842.0)
    landscape = _classification(width=842.0, height=595.0)

    assert portrait.is_landscape is False
    assert landscape.is_landscape is True


def test_result_is_partial_when_pages_are_deferred() -> None:
    result = PageExtractionResult(
        pages=[],
        combined_text="",
        ocr_pages_total=2,
        ocr_failed_pages=[],
        ocr_deferred_pages=[7, 8],
        unrenderable_pages=[],
        completeness=Completeness.PARTIAL,
        engine_version="1",
    )

    assert result.completeness is Completeness.PARTIAL
    assert result.ocr_deferred_pages == [7, 8]


def test_result_defaults_to_complete_with_empty_page_lists() -> None:
    result = PageExtractionResult(pages=[], combined_text="", ocr_pages_total=0)

    assert result.completeness is Completeness.COMPLETE
    assert result.ocr_failed_pages == []
    assert result.ocr_deferred_pages == []
    assert result.unrenderable_pages == []


def test_policy_carries_a_resumability_boundary_not_a_cap() -> None:
    policy = PageExtractionPolicy(
        ocr_enabled=True,
        min_text_chars_per_page=40,
        batch_size=25,
        max_ocr_pages_per_attempt=50,
        ocr_language="eng",
    )

    assert policy.max_ocr_pages_per_attempt == 50
    assert policy.min_text_chars_per_page == 40
