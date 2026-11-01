"""Extractors for standalone images and plain-text uploads.

Images are OCR'd directly and stored as-is: they are never wrapped into a PDF
container, so the FileObject the user uploaded remains the FileObject we hold.
Both extractors produce a single logical page so downstream consumers see the
same PageExtractionResult shape as the PDF path.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple

from .models import (
    Completeness,
    ExtractedPage,
    PageClass,
    PageClassification,
    PageExtractionResult,
    PageSource,
    PageStatus,
)
from .page_store import ImageOcrRunner, PageStore

logger = logging.getLogger(__name__)

ENGINE_VERSION = "1"


def _image_size(source: Path) -> Tuple[float, float]:
    try:
        from PIL import Image

        with Image.open(source) as handle:
            return float(handle.width), float(handle.height)
    except Exception as exc:  # pragma: no cover - unreadable image
        logger.warning("Could not read image dimensions for %s: %s", source.name, exc)
        return 0.0, 0.0


async def extract_image(
    source: Path,
    *,
    store: PageStore,
    image_ocr_runner: ImageOcrRunner,
    language: str,
) -> PageExtractionResult:
    """OCR a standalone image as a single page."""
    width, height = await asyncio.to_thread(_image_size, source)

    batch_id = await store.begin_batch(page_start=1, page_end=1, retry=False)
    text = ""
    status = PageStatus.OCR_COMPLETED
    error: Optional[str] = None

    try:
        text = (await image_ocr_runner.run(source, language) or "").strip()
        if not text:
            status = PageStatus.OCR_EMPTY
            error = "OCR completed but no text was extracted"
        await store.finish_batch(batch_id, status="completed")
    except Exception as exc:
        logger.warning("Standalone image OCR failed for %s: %s", source.name, exc)
        status = PageStatus.OCR_FAILED
        error = str(exc)[:500]
        await store.finish_batch(batch_id, status="failed", error=error)

    page = ExtractedPage(
        number=1,
        text=text,
        source=PageSource.OCR if text else PageSource.EMPTY,
        status=status,
        classification=PageClassification(
            page_class=PageClass.SCANNED_IMAGE,
            char_count=len(text),
            image_count=1,
            image_coverage=1.0,
            table_count=0,
            width=width,
            height=height,
            rotation=0,
        ),
        batch_id=batch_id,
        error=error,
    )
    await store.record_pages([page])

    resolved = status is PageStatus.OCR_COMPLETED
    return PageExtractionResult(
        pages=[page],
        combined_text=text,
        ocr_pages_total=1,
        ocr_failed_pages=[1] if status is PageStatus.OCR_FAILED else [],
        ocr_deferred_pages=[],
        completeness=Completeness.COMPLETE if resolved else Completeness.PARTIAL,
        engine_version=ENGINE_VERSION,
    )


async def extract_text_file(source: Path, *, store: PageStore) -> PageExtractionResult:
    """Read a plain-text upload as a single page. No OCR involved."""

    def _read() -> str:
        return source.read_text(encoding="utf-8", errors="replace")

    text = await asyncio.to_thread(_read)

    page = ExtractedPage(
        number=1,
        text=text,
        source=PageSource.TEXT_LAYER if text.strip() else PageSource.EMPTY,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=PageClass.TEXT_NATIVE,
            char_count=len(text.strip()),
            image_count=0,
            image_coverage=0.0,
            table_count=0,
            width=0.0,
            height=0.0,
            rotation=0,
        ),
    )
    await store.record_pages([page])

    return PageExtractionResult(
        pages=[page],
        combined_text=text,
        ocr_pages_total=0,
        completeness=Completeness.COMPLETE,
        engine_version=ENGINE_VERSION,
    )
