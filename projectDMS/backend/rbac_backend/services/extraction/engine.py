"""Page-level extraction: decide, batch, OCR, merge - once, for every caller.

This is the module the contract path proved and the general path lacked. It
owns the page decision, contiguous batching, OCR invocation, source-to-output
mapping, and the native/OCR merge. It owns none of the persistence: batches and
page records go out through the PageStore seam, and usage metering goes out
through the meter callback, because billing and collection names differ per
caller.

Resumability, not truncation: max_ocr_pages_per_attempt bounds one attempt.
Pages beyond it are marked OCR_DEFERRED and the result is PARTIAL, so the
caller re-claims them. Nothing is ever silently dropped.

A text layer is not trusted by its length alone. A page whose text is unusable
pdfminer's ``(cid:N)`` placeholders (see text_quality) is an OCR candidate
however long that text is, so it ends a first attempt either OCR'd or in an
unresolved status - never as a TEXT_LAYER page. A retry OCRs exactly the pages
the caller's checkpoint still owes, which includes any such page left
unresolved; placeholder pages outside that set are not re-selected, because
they were resolved by an earlier attempt and re-OCRing them each retry would
spend metered calls without end. The structural classification is left as it
is - the page does carry a text layer - because classification describes the
page and routing is this module's decision.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .models import (
    Completeness,
    ExtractedPage,
    PageClass,
    PageClassification,
    PageExtractionPolicy,
    PageExtractionResult,
    PageSource,
    PageStatus,
)
from .page_classifier import PageClassifier
from .page_store import MeterCallback, OcrRunner, PageStore
from .text_quality import (
    NativeTextQuality,
    assess_native_text_quality,
    withhold_unusable,
)

logger = logging.getLogger(__name__)

ENGINE_VERSION = "1"

_NativePage = Tuple[str, PageClassification, List[List[List[str]]]]
_PageState = Tuple[PageStatus, Optional[str], Optional[str]]

_UNRESOLVED_STATUSES = {
    PageStatus.OCR_FAILED,
    PageStatus.OCR_DEFERRED,
    PageStatus.OCR_DISABLED,
    PageStatus.OCR_EMPTY,
    PageStatus.UNRENDERABLE,
}


def group_contiguous_pages(
    page_numbers: Sequence[int], batch_size: int
) -> List[List[int]]:
    """Group sorted, de-duplicated page numbers into contiguous runs.

    Behaviour is identical to the contract path's original _group_page_numbers:
    a run breaks on a gap or when the batch size is reached.
    """
    groups: List[List[int]] = []
    current: List[int] = []
    previous: Optional[int] = None
    for page_number in sorted({int(page) for page in page_numbers if int(page) > 0}):
        if current and (
            previous is None
            or page_number != previous + 1
            or len(current) >= max(1, batch_size)
        ):
            groups.append(current)
            current = []
        current.append(page_number)
        previous = page_number
    if current:
        groups.append(current)
    return groups


class PageExtractionEngine:
    def __init__(
        self,
        *,
        policy: PageExtractionPolicy,
        ocr_runner: OcrRunner,
        store: PageStore,
        meter: Optional[MeterCallback] = None,
    ) -> None:
        self.policy = policy
        self.ocr_runner = ocr_runner
        self.store = store
        self.meter = meter
        self.classifier = PageClassifier(
            min_text_chars_per_page=policy.min_text_chars_per_page
        )
        #: Set when the source could not be opened at all, so the resulting
        #: unrenderable page can carry a reason a human can act on.
        self._open_error: Optional[str] = None

    async def extract(
        self, source: Path, *, retry_pages: Optional[Sequence[int]] = None
    ) -> PageExtractionResult:
        native = await asyncio.to_thread(self._read_native_pages, source)

        unusable_native = self._unusable_native_text(native)

        retry_set = {int(page) for page in (retry_pages or []) if int(page) > 0}
        if retry_set:
            candidates = [number for number in sorted(native) if number in retry_set]
        else:
            candidates = [
                number
                for number, (text, classification, _) in sorted(native.items())
                # A page that could not be rendered cannot be OCR'd either;
                # sending it would burn a metered OCR call to learn nothing.
                if classification.page_class is not PageClass.UNRENDERABLE
                and (
                    len(text.strip()) < self.policy.min_text_chars_per_page
                    or number in unusable_native
                )
            ]

        attempted, deferred = self._split_at_attempt_boundary(candidates)

        overrides: Dict[int, str] = {}
        statuses: Dict[int, _PageState] = {}

        if not self.policy.ocr_enabled:
            for number in candidates:
                reason = "OCR is disabled"
                if number in unusable_native:
                    reason = (
                        "OCR is disabled and the native text layer is unusable: "
                        f"{unusable_native[number].describe()}"
                    )
                statuses[number] = (PageStatus.OCR_DISABLED, None, reason)
        else:
            if attempted and self.meter is not None:
                await self.meter(
                    page_count=len(attempted),
                    page_numbers=attempted,
                    retry=bool(retry_set),
                )
            for batch in group_contiguous_pages(attempted, self.policy.batch_size):
                await self._run_batch(
                    source, batch, overrides, statuses, retry=bool(retry_set)
                )
            for number in deferred:
                statuses[number] = (
                    PageStatus.OCR_DEFERRED,
                    None,
                    "Deferred past this attempt's OCR page boundary",
                )

        pages = self._merge(native, overrides, statuses)
        await self.store.record_pages(pages)

        failed = sorted(
            page.number for page in pages if page.status is PageStatus.OCR_FAILED
        )
        unrenderable = sorted(
            page.number
            for page in pages
            if page.status is PageStatus.UNRENDERABLE
        )
        unresolved = [page for page in pages if page.status in _UNRESOLVED_STATUSES]

        return PageExtractionResult(
            pages=pages,
            combined_text="\n\n".join(page.text or "" for page in pages),
            ocr_pages_total=len(attempted) if self.policy.ocr_enabled else 0,
            ocr_failed_pages=failed,
            ocr_deferred_pages=sorted(deferred),
            unrenderable_pages=unrenderable,
            withheld_pages=sorted(
                page.number for page in pages if page.raw_text is not None
            ),
            completeness=Completeness.PARTIAL if unresolved else Completeness.COMPLETE,
            engine_version=ENGINE_VERSION,
        )

    @staticmethod
    def _unusable_native_text(
        native: Dict[int, _NativePage],
    ) -> Dict[int, NativeTextQuality]:
        """Pages whose native text is unusable ``(cid:N)`` placeholders."""
        unusable: Dict[int, NativeTextQuality] = {}
        for number, (text, classification, _) in native.items():
            if classification.page_class is PageClass.UNRENDERABLE:
                continue
            quality = assess_native_text_quality(text)
            if quality.unusable:
                unusable[number] = quality
        return unusable

    def _split_at_attempt_boundary(
        self, candidates: Sequence[int]
    ) -> Tuple[List[int], List[int]]:
        limit = max(0, int(self.policy.max_ocr_pages_per_attempt))
        ordered = sorted(candidates)
        if limit <= 0 or len(ordered) <= limit:
            return ordered, []
        return ordered[:limit], ordered[limit:]

    async def _run_batch(
        self,
        source: Path,
        batch: Sequence[int],
        overrides: Dict[int, str],
        statuses: Dict[int, _PageState],
        *,
        retry: bool,
    ) -> None:
        batch_id = await self.store.begin_batch(
            page_start=batch[0], page_end=batch[-1], retry=retry
        )
        try:
            extracted = await self.ocr_runner.run(
                source, batch, self.policy.ocr_language
            )
        except Exception as exc:
            error = str(exc)[:500]
            logger.warning(
                "OCR batch failed for %s pages %s-%s: %s",
                source.name,
                batch[0],
                batch[-1],
                error,
            )
            for number in batch:
                statuses[number] = (PageStatus.OCR_FAILED, batch_id, error)
            await self.store.finish_batch(batch_id, status="failed", error=error)
            return

        for number in batch:
            text = (extracted.get(number) or "").strip()
            quality = assess_native_text_quality(text)
            if text and quality.unusable:
                # Re-read from OCRmyPDF's output PDF, so the same placeholder
                # hazard applies; OCR output that is itself unreadable is not a
                # completed page.
                statuses[number] = (
                    PageStatus.OCR_EMPTY,
                    batch_id,
                    f"OCR completed but its text is unusable: {quality.describe()}",
                )
            elif text:
                overrides[number] = extracted[number]
                statuses[number] = (PageStatus.OCR_COMPLETED, batch_id, None)
            else:
                statuses[number] = (
                    PageStatus.OCR_EMPTY,
                    batch_id,
                    "OCR completed but no text was extracted",
                )
        await self.store.finish_batch(batch_id, status="completed")

    def _merge(
        self,
        native: Dict[int, _NativePage],
        overrides: Dict[int, str],
        statuses: Dict[int, _PageState],
    ) -> List[ExtractedPage]:
        pages: List[ExtractedPage] = []
        for number in sorted(native):
            native_text, classification, tables = native[number]
            status, batch_id, error = statuses.get(
                number, (PageStatus.TEXT_LAYER, None, None)
            )
            if (
                classification.page_class is PageClass.UNRENDERABLE
                and status is not PageStatus.OCR_COMPLETED
            ):
                status = PageStatus.UNRENDERABLE
                error = (
                    error
                    or getattr(self, "_open_error", None)
                    or "Page could not be rendered or extracted"
                )

            if number in overrides:
                text = overrides[number]
                page_source = PageSource.OCR
            else:
                text = native_text

            # Unusable text is evidence, never published: `text` - and so
            # combined_text, page records, chunking and embeddings - carries
            # none of it, and `raw_text` keeps it whole beside the page's
            # fail-visible status. Tables read from the same unusable layer
            # go with it.
            text, withheld = withhold_unusable(text)
            if withheld is not None:
                tables = []
            if number not in overrides:
                page_source = (
                    PageSource.TEXT_LAYER if text.strip() else PageSource.EMPTY
                )

            pages.append(
                ExtractedPage(
                    number=number,
                    text=text,
                    source=page_source,
                    status=status,
                    classification=classification,
                    tables=tables,
                    batch_id=batch_id,
                    error=error,
                    raw_text=withheld,
                )
            )
        return pages

    def _read_native_pages(self, source: Path) -> Dict[int, _NativePage]:
        import pdfplumber

        pages: Dict[int, _NativePage] = {}
        try:
            with pdfplumber.open(source) as pdf:
                for index, page in enumerate(pdf.pages, start=1):
                    classification = self.classifier.classify(page)
                    try:
                        text = page.extract_text() or ""
                    except Exception:
                        text = ""
                    try:
                        tables = page.extract_tables() or []
                    except Exception:
                        tables = []
                    pages[index] = (text, classification, tables)
        except Exception as exc:
            # A document that will not open is a visible unrenderable page, not
            # an opaque crash. It then flows through the normal review path
            # instead of surfacing as a generic job failure with no evidence.
            logger.warning("Could not open %s for extraction: %s", source.name, exc)
            self._open_error = str(exc)[:500]

        if not pages:
            pages[1] = (
                "",
                PageClassification(
                    page_class=PageClass.UNRENDERABLE,
                    char_count=0,
                    image_count=0,
                    image_coverage=0.0,
                    table_count=0,
                    width=0.0,
                    height=0.0,
                    rotation=0,
                ),
                [],
            )
        return pages
