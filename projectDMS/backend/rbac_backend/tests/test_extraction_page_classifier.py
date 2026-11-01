"""Page classification beyond the minimum-character threshold."""

from __future__ import annotations

import pdfplumber

from rbac_backend.services.extraction.models import PageClass
from rbac_backend.services.extraction.page_classifier import (
    SCANNED_IMAGE_COVERAGE_THRESHOLD,
    PageClassifier,
)
from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf


class _FakePage:
    """Stands in for a pdfplumber Page without needing a real PDF."""

    def __init__(
        self,
        *,
        text: str = "",
        images: list[dict[str, float]] | None = None,
        tables: int = 0,
        width: float = 595.0,
        height: float = 842.0,
        rotation: int = 0,
    ) -> None:
        self._text = text
        self.images = images or []
        self._tables = tables
        self.width = width
        self.height = height
        self.rotation = rotation

    def extract_text(self) -> str:
        return self._text

    def find_tables(self) -> list[object]:
        return [object()] * self._tables


def test_blank_page_is_classified_blank() -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)

    result = classifier.classify(_FakePage())

    assert result.page_class is PageClass.BLANK
    assert result.char_count == 0


def test_full_page_image_with_no_text_is_scanned() -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)
    page = _FakePage(images=[{"x0": 0, "top": 0, "x1": 595, "bottom": 842}])

    result = classifier.classify(page)

    assert result.page_class is PageClass.SCANNED_IMAGE
    assert result.image_coverage >= SCANNED_IMAGE_COVERAGE_THRESHOLD


def test_many_small_images_on_a_text_page_is_not_scanned() -> None:
    # Companion evidence 3.9: every text page of a real claim carries 3-6 images.
    classifier = PageClassifier(min_text_chars_per_page=40)
    page = _FakePage(
        text="x" * 400,
        images=[{"x0": 10, "top": 10, "x1": 60, "bottom": 60} for _ in range(6)],
    )

    result = classifier.classify(page)

    assert result.page_class is not PageClass.SCANNED_IMAGE
    assert result.image_count == 6


def test_thin_text_page_under_a_full_page_image_is_still_scanned() -> None:
    # A scan with a stray OCR artefact or a page number must not escape the
    # SCANNED_IMAGE classification.
    classifier = PageClassifier(min_text_chars_per_page=40)
    page = _FakePage(
        text="12",
        images=[{"x0": 0, "top": 0, "x1": 595, "bottom": 842}],
    )

    result = classifier.classify(page)

    assert result.page_class is PageClass.SCANNED_IMAGE


def test_text_page_with_a_table_is_table_heavy() -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)
    page = _FakePage(text="x" * 400, tables=2)

    result = classifier.classify(page)

    assert result.page_class is PageClass.TABLE_HEAVY
    assert result.table_count == 2


def test_text_page_with_substantial_imagery_is_mixed() -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)
    page = _FakePage(
        text="x" * 400,
        images=[{"x0": 0, "top": 0, "x1": 595, "bottom": 400}],
    )

    result = classifier.classify(page)

    assert result.page_class is PageClass.MIXED_CONTENT


def test_plain_text_page_is_text_native() -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)

    result = classifier.classify(_FakePage(text="x" * 400))

    assert result.page_class is PageClass.TEXT_NATIVE


def test_overlapping_images_do_not_report_impossible_coverage() -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)
    page = _FakePage(
        images=[{"x0": 0, "top": 0, "x1": 595, "bottom": 842} for _ in range(3)]
    )

    result = classifier.classify(page)

    assert result.image_coverage <= 1.0


def test_a_page_that_cannot_be_read_is_unrenderable() -> None:
    class _BrokenPage:
        width = 595.0
        height = 842.0
        rotation = 0
        images: list[dict[str, float]] = []

        def extract_text(self) -> str:
            raise ValueError("damaged content stream")

        def find_tables(self) -> list[object]:
            return []

    classifier = PageClassifier(min_text_chars_per_page=40)

    result = classifier.classify(_BrokenPage())

    assert result.page_class is PageClass.UNRENDERABLE


def test_table_detection_failure_does_not_fail_the_page() -> None:
    class _NoTables:
        width = 595.0
        height = 842.0
        rotation = 0
        images: list[dict[str, float]] = []

        def extract_text(self) -> str:
            return "x" * 400

        def find_tables(self) -> list[object]:
            raise RuntimeError("table finder blew up")

    classifier = PageClassifier(min_text_chars_per_page=40)

    result = classifier.classify(_NoTables())

    assert result.page_class is PageClass.TEXT_NATIVE
    assert result.table_count == 0


def test_landscape_orientation_is_recorded(tmp_path) -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)
    target = build_mixed_pdf(tmp_path / "mixed.pdf")

    with pdfplumber.open(target) as pdf:
        landscape = classifier.classify(pdf.pages[4])
        portrait = classifier.classify(pdf.pages[3])

    assert landscape.is_landscape is True
    assert portrait.is_landscape is False


def test_real_fixture_pages_one_and_two_are_blank_or_scanned(tmp_path) -> None:
    classifier = PageClassifier(min_text_chars_per_page=40)
    target = build_mixed_pdf(tmp_path / "mixed.pdf")

    with pdfplumber.open(target) as pdf:
        classes = [classifier.classify(page).page_class for page in pdf.pages]

    assert classes[0] in {PageClass.BLANK, PageClass.SCANNED_IMAGE}
    assert classes[1] in {PageClass.BLANK, PageClass.SCANNED_IMAGE}
    assert all(
        page_class
        in {PageClass.TEXT_NATIVE, PageClass.TABLE_HEAVY, PageClass.MIXED_CONTENT}
        for page_class in classes[2:]
    )
