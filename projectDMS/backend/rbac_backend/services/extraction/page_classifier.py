"""Classify a PDF page by what it actually contains.

Classification is descriptive metadata, not a second OCR gate: the engine's
character threshold and native-text quality check make the routing decision.
A page of ``(cid:N)`` placeholders is still classified as text here - its
glyphs are really there - and the engine sends it to OCR regardless (see
text_quality). Only SCANNED_IMAGE and BLANK change
behaviour downstream - the first suppresses whole-page-raster asset
candidates, the second suppresses a pointless OCR call.

The signal that does NOT work here: image presence. Companion evidence 3.9
measured 3-6 embedded images on every page of a real contractor claim,
including all seven text pages - stamps, signatures, logos, scanned insets.
A classifier keyed on `image_count > 0` would route all nine pages to OCR and
triple the cost. Only a single image covering most of the page, combined with
thin text, identifies a scan.
"""

from __future__ import annotations

import logging
from typing import Any, List, Tuple

from .models import PageClass, PageClassification

logger = logging.getLogger(__name__)

SCANNED_IMAGE_COVERAGE_THRESHOLD = 0.85
MIXED_CONTENT_COVERAGE_THRESHOLD = 0.20


class PageClassifier:
    def __init__(self, *, min_text_chars_per_page: int) -> None:
        self.min_text_chars_per_page = max(0, int(min_text_chars_per_page))

    def classify(self, page: Any) -> PageClassification:
        width = float(getattr(page, "width", 0.0) or 0.0)
        height = float(getattr(page, "height", 0.0) or 0.0)
        rotation = int(getattr(page, "rotation", 0) or 0)

        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.warning("Page text could not be extracted for classification: %s", exc)
            return PageClassification(
                page_class=PageClass.UNRENDERABLE,
                char_count=0,
                image_count=0,
                image_coverage=0.0,
                table_count=0,
                width=width,
                height=height,
                rotation=rotation,
            )

        char_count = len(text.strip())
        images = list(getattr(page, "images", None) or [])
        coverage = self._image_coverage(images, width, height)
        table_count = self._table_count(page)

        page_class = self._page_class(
            char_count=char_count,
            image_count=len(images),
            coverage=coverage,
            table_count=table_count,
        )

        return PageClassification(
            page_class=page_class,
            char_count=char_count,
            image_count=len(images),
            image_coverage=coverage,
            table_count=table_count,
            width=width,
            height=height,
            rotation=rotation,
        )

    def _page_class(
        self, *, char_count: int, image_count: int, coverage: float, table_count: int
    ) -> PageClass:
        has_text = char_count >= self.min_text_chars_per_page

        # A page dominated by one raster is a scan, whether or not a stray
        # character or page number survived on top of it.
        if coverage >= SCANNED_IMAGE_COVERAGE_THRESHOLD and not has_text:
            return PageClass.SCANNED_IMAGE

        if not has_text and image_count == 0 and table_count == 0:
            return PageClass.BLANK

        if not has_text:
            return PageClass.SCANNED_IMAGE if image_count else PageClass.BLANK

        if table_count > 0:
            return PageClass.TABLE_HEAVY

        if coverage >= MIXED_CONTENT_COVERAGE_THRESHOLD:
            return PageClass.MIXED_CONTENT

        return PageClass.TEXT_NATIVE

    @staticmethod
    def _image_coverage(
        images: List[Any], width: float, height: float
    ) -> float:
        page_area = width * height
        if not images or page_area <= 0:
            return 0.0

        largest = 0.0
        for image in images:
            try:
                box: Tuple[float, float, float, float] = (
                    float(image["x0"]),
                    float(image["top"]),
                    float(image["x1"]),
                    float(image["bottom"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            area = abs((box[2] - box[0]) * (box[3] - box[1]))
            largest = max(largest, area)

        # Coverage is the LARGEST single image, not the sum: six small stamps
        # summing to 90% of the page is not a scan, one raster covering 90% is.
        return min(1.0, largest / page_area)

    @staticmethod
    def _table_count(page: Any) -> int:
        try:
            return len(page.find_tables() or [])
        except Exception as exc:
            logger.debug("Table detection failed during classification: %s", exc)
            return 0
