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
from .page_store import (
    InconsistentExtractionRunError,
    MeterCallback,
    OcrRunner,
    PageStore,
)

logger = logging.getLogger(__name__)


def _is_resumable(store: PageStore) -> bool:
    """Whether this store can hand back the run's already-recorded pages.

    A feature test, not an isinstance check: the capability is optional and
    only the document adapter has a run-scoped identity to answer it with.
    """
    return callable(getattr(store, "load_run_pages", None))

ENGINE_VERSION = "1"

_NativePage = Tuple[str, PageClassification, List[List[List[str]]]]
_PageState = Tuple[PageStatus, Optional[str], Optional[str]]

_UNRESOLVED_STATUSES = {
    PageStatus.OCR_FAILED,
    PageStatus.OCR_DEFERRED,
    PageStatus.OCR_DISABLED,
    PageStatus.OCR_EMPTY,
    PageStatus.OCR_PENDING,
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

        retry_set = {int(page) for page in (retry_pages or []) if int(page) > 0}

        # What this run already holds decides what this attempt may change.
        # Keying that on retry_pages alone was not enough: an attempt that
        # crashed after writing its pages but before its checkpoint leaves the
        # job with an empty remaining list, and the next attempt - carrying no
        # retry pages at all - would re-extract and replace every resolved row
        # of the same run. The run, not the retry list, is the authority.
        stored: Dict[int, ExtractedPage] = {}
        if _is_resumable(self.store):
            stored = {page.number: page for page in await self.store.load_run_pages()}
        elif retry_set:
            logger.warning(
                "Retrying pages %s of %s against a page store with no "
                "run-scoped read: every page of this run will be rewritten "
                "from this attempt",
                sorted(retry_set),
                source.name,
            )

        mutable = (
            self._mutable_page_numbers(
                native_numbers=sorted(native), stored=stored, retry_set=retry_set
            )
            if _is_resumable(self.store)
            else None
        )

        if retry_set:
            candidates = [number for number in sorted(native) if number in retry_set]
        elif mutable is not None:
            candidates = [
                number
                for number, (text, classification, _) in sorted(native.items())
                if number in mutable
                and classification.page_class is not PageClass.UNRENDERABLE
                and len(text.strip()) < self.policy.min_text_chars_per_page
            ]
        else:
            candidates = [
                number
                for number, (text, classification, _) in sorted(native.items())
                # A page that could not be rendered cannot be OCR'd either;
                # sending it would burn a metered OCR call to learn nothing.
                if classification.page_class is not PageClass.UNRENDERABLE
                and len(text.strip()) < self.policy.min_text_chars_per_page
            ]

        attempted, deferred = self._split_at_attempt_boundary(candidates)

        overrides: Dict[int, str] = {}
        statuses: Dict[int, _PageState] = {}

        if not self.policy.ocr_enabled:
            for number in candidates:
                statuses[number] = (PageStatus.OCR_DISABLED, None, "OCR is disabled")
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
        if mutable is not None:
            # This attempt mutates only the pages it may. Everything else is
            # read back from the run it belongs to, so an already resolved page
            # is never rewritten - and never rewritten with the empty native
            # text this attempt just re-read for it.
            pages = await self._assemble_cumulative_run(
                pages,
                native_numbers=sorted(native),
                stored=stored,
                mutable=mutable,
            )
        else:
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
            completeness=Completeness.PARTIAL if unresolved else Completeness.COMPLETE,
            engine_version=ENGINE_VERSION,
        )

    def _mutable_page_numbers(
        self,
        *,
        native_numbers: Sequence[int],
        stored: Dict[int, ExtractedPage],
        retry_set: set,
    ) -> Optional[set]:
        """Which pages this attempt is allowed to change.

        ``None`` means "everything this attempt produced is this attempt's" -
        a first attempt against an empty run. Callers pass ``None`` for a store
        with no run-scoped read as well, so the contract adapter and the
        in-memory stores keep exactly the behaviour they had.

        With a retry list, only those pages. Without one, against a run that
        already holds rows, every page the run has *not* yet resolved: a
        resumed attempt may finish unresolved work but may not undo finished
        work, which is what makes resolved pages monotonic across attempts.
        """
        if retry_set:
            return set(retry_set)
        if not stored:
            return None
        return {
            number
            for number in native_numbers
            if number not in stored
            or stored[number].status in _UNRESOLVED_STATUSES
        }

    async def _assemble_cumulative_run(
        self,
        produced: Sequence[ExtractedPage],
        *,
        native_numbers: Sequence[int],
        stored: Dict[int, ExtractedPage],
        mutable: set,
    ) -> List[ExtractedPage]:
        """Persist this attempt's pages, then return the whole run in order.

        The stable extraction run - not this attempt - is the canonical unit.
        Downstream consumers (quality gate, full_text, embeddings) therefore
        see every page the run has resolved so far, assembled by page number,
        while the store holds exactly one row per page and this attempt only
        replaced the rows it actually re-extracted.

        The run's page identity is the union of what this attempt read, what
        the run already holds and what it was asked to retry - deliberately
        not just this attempt's read. Taking the read alone would let a source
        that now yields fewer pages silently drop the rest of the run and
        still report a complete extraction, which is the same class of silent
        loss as overwriting a resolved page.

        Both consistency checks run before anything is written, so an attempt
        that cannot honour the run does not mutate it first.
        """
        readable = set(native_numbers)
        run_numbers = sorted(set(stored) | readable | set(mutable))

        unreadable = [number for number in run_numbers if number not in readable]
        if unreadable:
            raise InconsistentExtractionRunError(
                extraction_run_id=getattr(self.store, "extraction_run_id", None),
                expected_page_numbers=run_numbers,
                persisted_page_numbers=sorted(stored),
                missing_page_numbers=unreadable,
                reason="this attempt's read of the source no longer covers the run",
            )

        fresh = {page.number: page for page in produced if page.number in mutable}
        missing = [
            number
            for number in run_numbers
            if number not in fresh and number not in stored
        ]
        if missing:
            raise InconsistentExtractionRunError(
                extraction_run_id=getattr(self.store, "extraction_run_id", None),
                expected_page_numbers=run_numbers,
                persisted_page_numbers=sorted(stored),
                missing_page_numbers=missing,
                reason="pages this attempt was told were resolved are absent",
            )

        await self.store.record_pages([fresh[number] for number in sorted(fresh)])

        pages: List[ExtractedPage] = []
        for number in run_numbers:
            if number in fresh:
                pages.append(fresh[number])
                continue
            carried = stored[number]
            carried.carried_forward = True
            pages.append(carried)
        return pages

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
            if text:
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
