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
from typing import Any, Dict, List, Optional, Sequence, Tuple, cast

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
from .numeric_integrity import (
    phantom_space_count,
    phantom_space_repair_record,
    readable_page,
    repair_split_grouped_numbers,
)
from .page_classifier import PageClassifier
from .page_store import (
    InconsistentExtractionRunError,
    MeterCallback,
    OcrRunner,
    PageStore,
    ResumablePageStore,
)
from .text_quality import (
    NativeTextQuality,
    assess_native_text_quality,
    withhold_unusable,
)

logger = logging.getLogger(__name__)


def _run_loader(store: PageStore) -> Optional[ResumablePageStore]:
    """The store itself when it can hand back the run's recorded pages.

    A feature test, not an isinstance check: the capability is optional and
    only the document adapter has a run-scoped identity to answer it with.
    """
    if callable(getattr(store, "load_run_pages", None)):
        return cast(ResumablePageStore, store)
    return None

ENGINE_VERSION = "1"

#: The error an OCR_EMPTY page carries when OCR ran and returned no text at
#: all - as opposed to OCR text judged unusable, which shares the status. A
#: consumer that treats an empty read as meaningful keys on this, not the status.
OCR_RETURNED_NO_TEXT = "OCR completed but no text was extracted"

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
        #: Pages whose native read dropped phantom space glyphs, as
        #: page -> (the unaided reading, glyphs dropped). Evidence for `_merge`.
        self._native_repairs: Dict[int, Tuple[str, int]] = {}

    async def extract(
        self, source: Path, *, retry_pages: Optional[Sequence[int]] = None
    ) -> PageExtractionResult:
        native = await asyncio.to_thread(self._read_native_pages, source)

        unusable_native = self._unusable_native_text(native)

        retry_set = {int(page) for page in (retry_pages or []) if int(page) > 0}

        # What this run already holds decides what this attempt may change.
        # Keying that on retry_pages alone was not enough: an attempt that
        # crashed after writing its pages but before its checkpoint leaves the
        # job with an empty remaining list, and the next attempt - carrying no
        # retry pages at all - would re-extract and replace every resolved row
        # of the same run. The run, not the retry list, is the authority.
        stored: Dict[int, ExtractedPage] = {}
        resumable = _run_loader(self.store)
        if resumable is not None:
            stored = {page.number: page for page in await resumable.load_run_pages()}
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
            if resumable is not None
            else None
        )

        if mutable is not None:
            # `mutable` decides which pages this attempt may touch at all, so
            # it decides the candidates too. Within it the caller's retry list
            # is honoured as asked, and every other page is judged by the same
            # two rules as the branch below: a cumulative run narrows *which*
            # pages an attempt may rework, not what makes a page need OCR.
            # Selecting by the retry list alone let a page the run must rework
            # - a row whose text cannot be published - go unOCR'd because
            # nobody had listed it; selecting it outside `mutable` metered a
            # call whose result was then discarded.
            candidates = [
                number
                for number, (text, classification, _) in sorted(native.items())
                if number in mutable
                and classification.page_class is not PageClass.UNRENDERABLE
                and (
                    number in retry_set
                    or len(text.strip()) < self.policy.min_text_chars_per_page
                    or number in unusable_native
                )
            ]
        elif retry_set:
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
            withheld_pages=self._withheld_page_numbers(pages),
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
            if not stored:
                return set(retry_set)
            # A checkpoint can be stale: an attempt that wrote its pages and
            # then crashed before recording them leaves the job still asking
            # for pages the run has since resolved. Re-extracting those would
            # replace recovered OCR text with whatever the native layer says
            # now - usually nothing. The durable row wins unless it is itself
            # unresolved or unusable, which is the only evidence that the
            # retry still has work to do.
            return {
                number
                for number in retry_set
                if number not in stored or self._page_is_unresolved(stored[number])
            } | self._unusable_stored_pages(stored)
        if not stored:
            return None
        return {
            number
            for number in native_numbers
            if number not in stored or self._page_is_unresolved(stored[number])
        }

    @classmethod
    def _unusable_stored_pages(cls, stored: Dict[int, ExtractedPage]) -> set:
        """Stored pages whose published text cannot be read, whatever their status.

        A row written before the text-quality policy is settled by status and
        unusable by content. Left out of the retry list it would be carried,
        emptied, and the run would report itself complete with that page's
        content gone - so it is always this attempt's to rework.
        """
        return {
            number
            for number, page in stored.items()
            if cls._page_text_is_unpublishable(page)
        }

    @classmethod
    def _page_is_unresolved(cls, page: ExtractedPage) -> bool:
        """Whether the run still owes work on a page it has already recorded.

        Status alone is not enough: a row written before the text-quality
        policy existed can be a settled TEXT_LAYER page whose text is nothing
        but ``(cid:N)`` placeholders, and that page does still need OCR.
        """
        if page.status in _UNRESOLVED_STATUSES:
            return True
        return cls._page_text_is_unpublishable(page)

    @staticmethod
    def _page_text_is_unpublishable(page: ExtractedPage) -> bool:
        """Whether a stored page's text cannot stand as the page's content.

        Two shapes, both written by builds this one succeeds: the text is
        still the unusable ``(cid:N)`` layer, or it was already blanked and
        the evidence kept beside it. Either way the page owes its content.
        """
        if page.text_withheld and not (page.text or "").strip():
            return True
        return assess_native_text_quality(page.text or "").unusable

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

        if fresh:
            # A stale checkpoint can leave an attempt with nothing of its own
            # to write. Recording an empty batch would only add a write that
            # changes nothing.
            await self.store.record_pages([fresh[number] for number in sorted(fresh)])

        pages: List[ExtractedPage] = []
        for number in run_numbers:
            if number in fresh:
                pages.append(fresh[number])
                continue
            carried = stored[number]
            carried.carried_forward = True
            pages.append(self._withhold_carried_text(carried))
        return pages

    @staticmethod
    def _withhold_carried_text(page: ExtractedPage) -> ExtractedPage:
        """Apply the publication policy to a page read back from the store.

        Rows written before that policy existed hold their unusable text as
        published text, and a cumulative run would put it straight back into
        combined_text - and so into full_text, chunking and embeddings - with
        no attempt having looked at it. Carrying a page forward is not a
        reason to trust what it carries.
        """
        published, withheld = withhold_unusable(page.text or "")
        if withheld is None:
            return page
        page.text = published
        page.tables = []
        if page.raw_text is None:
            page.raw_text = withheld
        page.text_withheld = True
        # This attempt could not replace the text, so the page is not settled:
        # an unresolved status keeps the run PARTIAL and the page in the
        # checkpoint, instead of completing a document a page short.
        page.status = PageStatus.OCR_PENDING
        return page

    @staticmethod
    def _withheld_page_numbers(pages: Sequence[ExtractedPage]) -> List[int]:
        """Pages whose own text was unusable and was withheld from `text`.

        Read from the page's recorded decision, not from `raw_text`: a
        deterministic repair also fills `raw_text`, and a run that carries
        resolved pages forward would then report a repaired page - whose
        published text is good - as withheld.
        """
        return sorted(page.number for page in pages if page.text_withheld)

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
                    OCR_RETURNED_NO_TEXT,
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

            raw_text: Optional[str] = None
            applied_repairs: List[Dict[str, Any]] = []
            if number in overrides:
                text = overrides[number]
                page_source = PageSource.OCR
                # OCR can break a grouped number at a word gap. Joined only
                # when the pieces provably belong together.
                repaired, ocr_repairs = repair_split_grouped_numbers(text)
                if ocr_repairs:
                    raw_text, text = text, repaired
                    applied_repairs = ocr_repairs
            else:
                text = native_text
                if number in self._native_repairs:
                    raw_text, dropped = self._native_repairs[number]
                    applied_repairs = [phantom_space_repair_record(dropped)]

            # Unusable text is evidence, never published: `text` - and so
            # combined_text, page records, chunking and embeddings - carries
            # none of it, and `raw_text` keeps it whole beside the page's
            # fail-visible status. Tables read from the same unusable layer
            # go with it.
            text, withheld = withhold_unusable(text)
            if withheld is not None:
                tables = []
                if status is PageStatus.TEXT_LAYER:
                    # Nothing replaced this page's text and none of it can be
                    # published, so the page is not settled. Left TEXT_LAYER
                    # it would complete a run a page short of the document.
                    status = PageStatus.OCR_PENDING
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
                    # Withheld text outranks a repair: nothing was published,
                    # so the unusable reading is the evidence to keep.
                    raw_text=withheld if withheld is not None else raw_text,
                    text_withheld=withheld is not None,
                    applied_repairs=[] if withheld is not None else applied_repairs,
                )
            )
        return pages

    def _read_native_pages(self, source: Path) -> Dict[int, _NativePage]:
        import pdfplumber

        pages: Dict[int, _NativePage] = {}
        self._native_repairs = {}
        try:
            with pdfplumber.open(source) as pdf:
                for index, page in enumerate(pdf.pages, start=1):
                    classification = self.classifier.classify(page)
                    # Excel padding glyphs drawn inside a digit split amounts
                    # ("1 9,292,171"); every read goes through the same filter.
                    try:
                        text = readable_page(page).extract_text() or ""
                    except Exception:
                        text = ""
                    try:
                        tables = readable_page(page).extract_tables() or []
                    except Exception:
                        tables = []
                    dropped = phantom_space_count(page)
                    if dropped:
                        try:
                            unaided = (
                                readable_page(page, keep_phantoms=True).extract_text() or ""
                            )
                        except Exception:
                            unaided = ""
                        if unaided != text:
                            self._native_repairs[index] = (unaided, dropped)
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
