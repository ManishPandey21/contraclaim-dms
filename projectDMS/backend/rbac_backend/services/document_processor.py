# services/document_processor.py

import asyncio
import logging
import time
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any, Dict, List, Sequence
from uuid import uuid4

from .text_processing_service import TextProcessingService
from .database_service import DatabaseService
from .file_service import FileService
from .ocr_service import OCRService
from .openai_service import OpenAIService
from .pydantic_ai_service import PydanticAIService, PydanticAIMetadataError
from .metadata_integrity import assess_metadata_quality

from .extraction.fallback.models import FallbackOutcome
from .extraction.image_extractor import extract_image, extract_text_file
from .extraction.image_ocr_runner import TesseractImageOcrRunner
from .extraction.models import Completeness, PageStatus, SourceKind
from .extraction.page_store import InconsistentExtractionRunError
from .extraction.quality.gate import ExtractionQualityGate
from .extraction.source_kind import SourceKindRouter
from .extraction.text_quality import withhold_unusable
from .pipeline_routing import LEGACY_PIPELINE, UNIFIED_PIPELINE

from ..core.config import settings
from ..config.document_processing_config import DocumentProcessingConfig
from ..models.document_metadata import ParsedDocumentMetadata, ProcessingResult
# _UNRESOLVED_PAGE_STATUSES is the set derive_processing_state judges a run
# by, so the result rebuilt after the quality gate and the state derived
# from it cannot disagree about a page.
from ..models.processing_state import _UNRESOLVED_PAGE_STATUSES, ProcessingState

from ..utils.exceptions import DocumentProcessingError, ModelOutputIncompleteError
from .source_text import (
    FULL_TEXT_SOURCE_SOURCE,
    OCR_TEXT_KIND_REPORT,
    OCR_TEXT_KIND_SOURCE,
    SOURCE_TEXT_ABSENT,
    SOURCE_TEXT_COMPLETE,
    SOURCE_TEXT_UNVERIFIED,
    source_text_is_complete,
)
from ..utils.pipeline_logging import configure_pipeline_logger

logger = logging.getLogger(__name__)
configure_pipeline_logger(logger)

class DocumentProcessorError(DocumentProcessingError):
    """Alias for document processor errors"""
    pass


class UnsupportedSourceKindError(DocumentProcessingError):
    """Raised when an upload's detected MIME has no extractor.

    Deliberately fail-visible: the previous behaviour fed every admitted MIME
    into the PDF reader, which produced nothing and still reported success.
    """


@dataclass(frozen=True)
class SourceDispatchResult:
    """Which extractor ran, and what it produced."""

    kind: SourceKind
    extraction: Optional[Any] = None
    processing_state: Optional[ProcessingState] = None


@dataclass(frozen=True)
class LegacyDispatchResult:
    """What the pre-unified path produced: text, and nothing else.

    No pages, no per-page evidence, no quality verdicts. That absence is the
    point - this is the known-good path kept so a canary has something real to
    roll back to, not a second implementation of the new one.
    """

    raw_text: Optional[str]
    processed_path: Path
    extraction: None = None
    pages_human_review: List[int] = field(default_factory=list)


class DocumentProcessor:
    """Main document processor service"""

    def __init__(
        self,
        config: Optional[DocumentProcessingConfig] = None,
        *,
        source_kind_router: Any = None,
        image_ocr_runner: Any = None,
        image_extractor: Any = None,
        text_extractor: Any = None,
        quality_gate: Any = None,
        fallback_ladder: Any = None,
    ):
        self.config = config or DocumentProcessingConfig()
        self.ocr_service = OCRService(self.config)
        self.openai_service = OpenAIService(self.config)
        self.text_service = TextProcessingService(self.config)
        self.database_service = DatabaseService(self.config)
        self.file_service = FileService(self.config)

        self.pydantic_ai_service = PydanticAIService(self.config)

        # Explicit seams: production defaults, injectable for tests so dispatch
        # can be exercised without monkeypatching module globals.
        self.source_kind_router = source_kind_router or SourceKindRouter
        self.image_ocr_runner = image_ocr_runner or TesseractImageOcrRunner()
        self._image_extractor = image_extractor or extract_image
        self._text_extractor = text_extractor or extract_text_file

        # The gate runs unconditionally; the ladder is opt-in and off by
        # default, because it is the only part of this pipeline that spends
        # money at runtime.
        self.quality_gate = quality_gate or ExtractionQualityGate()
        self.fallback_ladder = fallback_ladder
        self.fallback_max_pages_per_document = int(
            getattr(settings, "EXTRACTION_FALLBACK_MAX_PAGES_PER_DOCUMENT", 0)
        )

    #: The ladder rasterizes and sends page images to a model, so it is
    #: restricted to the source kinds that have pages to rasterize. Per the
    #: committed specification.
    FALLBACK_ELIGIBLE_SOURCE_KINDS = frozenset({SourceKind.PDF, SourceKind.IMAGE})

    def resolve_fallback_ladder(
        self, source_kind: Any, *, enabled: Optional[bool] = None, db: Any = None
    ) -> Any:
        """Decide whether this document gets a fallback ladder.

        This is the production reader `EXTRACTION_FALLBACK_ENABLED` never had.
        The flag was defined, documented, shipped in compose and reported by the
        status script, while nothing in the application read it - so the ladder
        was inert because no code constructed one, and the runbook's rollback
        lever did nothing at all.

        Fails closed on every uncertain path: flag off, ineligible source kind,
        unrecognised source kind, or a ladder that cannot be constructed all
        yield None, which means deterministic-only and zero model spend.
        """
        if enabled is None:
            enabled = bool(getattr(settings, "EXTRACTION_FALLBACK_ENABLED", False))
        if not enabled:
            return None
        if source_kind not in self.FALLBACK_ELIGIBLE_SOURCE_KINDS:
            return None

        # An explicitly injected ladder wins: tests and callers that build their
        # own must not be overridden by configuration.
        if getattr(self, "fallback_ladder", None) is not None:
            return self.fallback_ladder

        try:
            from .extraction.fallback.ladder import ExtractionFallbackLadder
            from .extraction.fallback.ledger import InterventionLedger
            from .extraction.rasterizer import PageRasterizer

            return ExtractionFallbackLadder(
                gate=self.quality_gate,
                rasterizer=PageRasterizer(
                    dpi=int(getattr(settings, "EXTRACTION_FALLBACK_DPI", 200))
                ),
                ledger=InterventionLedger(db=db),
                tier1=getattr(self, "reconstruction_tier1", None),
                tier2=(
                    getattr(self, "reconstruction_tier2", None)
                    if getattr(settings, "EXTRACTION_FALLBACK_TIER2_ENABLED", False)
                    else None
                ),
                budget=int(
                    getattr(settings, "EXTRACTION_FALLBACK_MAX_PAGES_PER_DOCUMENT", 0)
                ),
            )
        except Exception as exc:
            # Never let a construction failure silently become "enabled".
            logger.warning(
                "[document_pipeline] Fallback ladder requested but could not be "
                "constructed; continuing deterministic-only: %s",
                exc,
            )
            return None

    def _apply_repairs(self, page: Any, verdict: Any) -> Any:
        """Adopt dual-confirmed repairs into the page text, then re-verify.

        Three representations are kept distinct:

          * ``page.raw_text`` - what extraction read. Written once, never
            overwritten, so the document's own wording stays auditable.
          * ``page.text`` - the published representation, carrying corrections.
          * ``page.applied_repairs`` - provenance for every substitution.

        The gate is re-run over the repaired text rather than assumed correct:
        a repair that does not survive re-verification is not adopted, and
        confidence alone never accepts one (that is `propose_repair`'s dual
        confirmation, upstream of here).
        """
        original_text = page.text or ""
        repaired_text = original_text
        original_tables = [
            [list(row) for row in table] for table in (page.tables or [])
        ]
        repaired_tables = [[list(row) for row in table] for table in original_tables]
        applied = []

        def _substitute_in_tables(before: str, after: str) -> bool:
            """Replace the corrupted cell wherever it appears in the tables.

            The tables are the structured evidence the gate checks. Repairing
            only the flat text would leave re-verification reading the original
            corruption and failing a page that was just corrected.
            """
            changed = False
            for table in repaired_tables:
                for row in table:
                    for index, cell in enumerate(row):
                        if cell == before:
                            row[index] = after
                            changed = True
            return changed

        for repair in verdict.repairs:
            before, after = repair.before, repair.after
            in_tables = bool(before) and after is not None and _substitute_in_tables(
                before, after
            )
            if in_tables and before and before not in repaired_text:
                # Corrected in the structured evidence; the flat text simply
                # does not reproduce the cell verbatim.
                applied.append(
                    {
                        "before": before,
                        "after": after,
                        "applied": True,
                        "scope": "tables",
                        "reason": repair.reason,
                        "method": repair.method,
                        "confirming_checks": list(repair.confirming_checks),
                        "page": repair.page,
                    }
                )
                continue

            if not before or after is None or before not in repaired_text:
                # Nothing to substitute: the corruption is inside a table cell
                # that the flat text does not reproduce verbatim. Record the
                # attempt so the page is not silently reported as clean.
                applied.append(
                    {
                        "before": before,
                        "after": after,
                        "applied": False,
                        "reason": "literal not present in page text",
                        "method": repair.method,
                        "confirming_checks": list(repair.confirming_checks),
                        "page": repair.page,
                    }
                )
                continue

            repaired_text = repaired_text.replace(before, after)
            applied.append(
                {
                    "before": before,
                    "after": after,
                    "applied": True,
                    "scope": "tables+text" if in_tables else "text",
                    "reason": repair.reason,
                    "method": repair.method,
                    "confirming_checks": list(repair.confirming_checks),
                    "page": repair.page,
                }
            )

        if repaired_text == original_text and repaired_tables == original_tables:
            page.applied_repairs = applied
            return verdict

        if page.raw_text is None:
            page.raw_text = original_text
        page.text = repaired_text
        page.tables = repaired_tables
        page.applied_repairs = applied

        # Re-verify. The repaired text is only canonical if the gate agrees.
        return self.quality_gate.assess(page, tables=page.tables or None)

    async def _apply_quality_gate(
        self,
        source: Path,
        extraction: Any,
        *,
        document_id: str,
        fallback_ladder: Any = None,
        page_store: Any = None,
    ) -> list:
        """Assess every page, escalating only what the gate says is wrong.

        Returns the page numbers that still need a human. A page the gate could
        not check is accepted and marked unverified - not escalated - because
        "nothing was verified" is not "something is wrong", and treating it as
        such is what produced 12 paid calls on a correct document.
        """
        needs_review: list = []
        #: Pages this attempt assessed, and therefore may write back.
        assessed: list = []
        # A ladder resolved from configuration wins; self.fallback_ladder is the
        # injected/test path and the default when nothing was resolved.
        ladder = fallback_ladder if fallback_ladder is not None else self.fallback_ladder
        # The cap lives here, not in the ladder: the ladder sees one page at a
        # time and cannot know how many siblings have already spent.
        budget = max(0, int(getattr(self, "fallback_max_pages_per_document", 0)))
        spent = 0

        for page in getattr(extraction, "pages", []) or []:
            if (
                getattr(page, "carried_forward", False)
                # A row with no verdict is an unassessed row, not a clean one:
                # the engine writes pages before any verdict exists. Carrying
                # one past the gate would publish a page nothing ever checked.
                and getattr(page, "quality_verdict", None) is not None
            ):
                # Settled by an earlier attempt of the same run: its verdict,
                # repairs and review flag are already persisted evidence.
                # Reassessing would re-spend the paid fallback on a page this
                # attempt did not touch and overwrite that evidence with a
                # second, differently-derived opinion. Its review state still
                # counts, so the publication barrier is unchanged.
                if getattr(page, "needs_review", False):
                    needs_review.append(page.number)
                continue

            assessed.append(page)
            verdict = self.quality_gate.assess(page, tables=page.tables or None)

            # Adopt deterministic repairs, then re-run the same gate over the
            # repaired text. A repair the gate proposed but nothing applied is
            # the defect this closes: the corrected value has to reach the text
            # that gets persisted and indexed, or the detection was decorative.
            if verdict.repairs:
                verdict = self._apply_repairs(page, verdict)

            page.quality_verdict = verdict.verdict.value
            page.quality_checks = [check.to_record() for check in verdict.checks]

            if not verdict.escalates:
                continue

            outcome = None
            if ladder is not None and spent < budget:
                spent += 1
                resolved = await ladder.resolve(
                    source, page, verdict, document_id=document_id
                )
                outcome = resolved.outcome
                if outcome is FallbackOutcome.RESOLVED:
                    # A reconstruction is text like any other, so it passes the
                    # same publication policy. RESOLVED is the ladder's verdict
                    # on its own work, and OCR_COMPLETED is a status - neither
                    # is evidence that a human can read the result.
                    published, unusable = withhold_unusable(
                        resolved.page.text or ""
                    )
                    if unusable is not None or not published.strip():
                        logger.warning(
                            "[document_pipeline] Fallback reconstruction for "
                            "page %s of %s is unusable text; not adopted",
                            page.number,
                            document_id,
                        )
                    else:
                        # Adopt the reconstruction the ladder already
                        # re-verified. Including the status: a page left
                        # OCR_FAILED while carrying reconstructed text reads as
                        # unresolved to both the checkpoint and the run, so the
                        # next attempt would re-extract it and overwrite the
                        # reconstruction that was just paid for - and the
                        # document could never complete.
                        page.text = published
                        page.source = resolved.page.source
                        page.status = PageStatus.OCR_COMPLETED
                        # The page's own text is published now, so the marks
                        # left by whatever failed before it are no longer true.
                        page.text_withheld = False
                        page.error = None
                        continue
            elif ladder is not None:
                logger.warning(
                    "[document_pipeline] Fallback budget of %s page(s) exhausted "
                    "for %s; page %s goes to human review unattempted",
                    budget,
                    document_id,
                    page.number,
                )

            page.needs_review = True
            needs_review.append(page.number)
            logger.warning(
                "[document_pipeline] Page %s of %s needs human review "
                "(verdict=%s, fallback=%s)",
                page.number,
                document_id,
                verdict.verdict.value,
                outcome.value if outcome else "disabled",
            )

        pages = getattr(extraction, "pages", []) or []

        # The engine froze these before the gate ran. A page the ladder
        # rescued is still listed as failed unless they are recomputed, and
        # the job then reads PARTIAL with nothing outstanding and sends a
        # finished document to a human.
        if pages:
            extraction.ocr_failed_pages = sorted(
                page.number for page in pages if page.status is PageStatus.OCR_FAILED
            )
            extraction.ocr_deferred_pages = sorted(
                page.number for page in pages if page.status is PageStatus.OCR_DEFERRED
            )
            extraction.unrenderable_pages = sorted(
                page.number for page in pages if page.status is PageStatus.UNRENDERABLE
            )
            extraction.withheld_pages = sorted(
                page.number for page in pages if page.text_withheld
            )
            extraction.completeness = (
                Completeness.PARTIAL
                if any(page.status in _UNRESOLVED_PAGE_STATUSES for page in pages)
                else Completeness.COMPLETE
            )

        if needs_review:
            extraction.completeness = Completeness.PARTIAL

        # Persist the assessed evidence. The engine wrote the raw pages before
        # any verdict existed, so without this every persisted quality_verdict
        # stays None and the page evidence cannot be monitored or reviewed.
        # Only the pages this attempt actually assessed. Writing the settled
        # ones back would rewrite rows nothing in this attempt changed, which
        # is how a retry lost an earlier page's text.
        # A carried page this attempt corrected - its unusable text withheld
        # and its status reopened - is written back too. Reporting the
        # correction while the row keeps the unusable text as published text
        # leaves the evidence contradicting the result for ever.
        assessed_numbers = {page.number for page in assessed}
        corrected = [
            page
            for page in pages
            if page.carried_forward
            and page.text_withheld
            and page.status is PageStatus.OCR_PENDING
            and page.number not in assessed_numbers
        ]
        if page_store is not None and (assessed or corrected):
            finalize = getattr(page_store, "finalize_pages", None)
            if finalize is not None:
                await finalize(assessed + corrected)

        # Rebuild the canonical text from the (possibly repaired or
        # reconstructed) pages. The engine froze combined_text before this gate
        # ran, so without this a corrected or ladder-recovered value could
        # never reach persistence, embeddings or retrieval - it would exist
        # only in a log line and a page row. Unconditional: a reconstruction
        # adopted from the ladder leaves no applied_repairs to key on.
        if pages:
            extraction.combined_text = "\n\n".join(page.text or "" for page in pages)

        return needs_review

    async def _extract_legacy(self, input_path: Path) -> LegacyDispatchResult:
        """The pre-Phase-3 extraction path, retained for canary rollback.

        Deliberately minimal and temporary: one document-level `is_pdf_textual`
        decision, no page store, no quality gate, no fallback. It exists so
        `pipeline_version=legacy_v0` means something a tenant can actually be
        rolled back onto without redeploying an image.

        Remove it once the unified pipeline is globally accepted.
        """
        logger.info(
            "[document_pipeline] Legacy extraction path for %s", input_path.name
        )
        processed_path, raw_text = await self.ocr_service.process_pdf(input_path)
        return LegacyDispatchResult(
            raw_text=raw_text, processed_path=processed_path or input_path
        )

    async def _extract_source(
        self,
        input_path: Path,
        *,
        source_mime: Optional[str],
        page_store: Any,
        retry_pages: Optional[Sequence[int]] = None,
    ) -> SourceDispatchResult:
        """Route one upload to the single extractor that can read it.

        There is deliberately no fall-through default. An unrecognised MIME
        raises rather than quietly entering the PDF reader.
        """
        kind = self.source_kind_router.route(source_mime, input_path.name)

        if kind is SourceKind.PDF:
            extraction = await self.ocr_service.process_pdf_pagewise(
                input_path,
                store=page_store,
                document_id=input_path.stem,
                retry_pages=retry_pages,
            )
            return SourceDispatchResult(kind=kind, extraction=extraction)

        if kind is SourceKind.IMAGE:
            extraction = await self._image_extractor(
                input_path,
                store=page_store,
                image_ocr_runner=self.image_ocr_runner,
                language=self.config.ocr_language,
            )
            return SourceDispatchResult(kind=kind, extraction=extraction)

        if kind is SourceKind.TEXT:
            extraction = await self._text_extractor(input_path, store=page_store)
            return SourceDispatchResult(kind=kind, extraction=extraction)

        if kind is SourceKind.ARCHIVE:
            # Stored deliberately without extraction. Never `completed`:
            # nothing was extracted to complete.
            logger.info(
                "[document_pipeline] %s is an archive; stored without extraction",
                input_path.name,
            )
            return SourceDispatchResult(
                kind=kind, processing_state=ProcessingState.STORED_ONLY
            )

        raise UnsupportedSourceKindError(
            f"No extractor for detected content type {source_mime!r} "
            f"({input_path.name})"
        )


    async def process_document(
        self,
        pdf_path: str,
        path_structure: str,
        upload_type: str,
        document_id: Optional[str] = None,
        skip_embeddings: bool = False,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        extraction_run_id: Optional[str] = None,
        retry_pages: Optional[Sequence[int]] = None,
        source_mime: Optional[str] = None,
        pipeline_version: str = LEGACY_PIPELINE,
    ) -> ProcessingResult:
        """
        Main entry point for document processing.

        Args:
            pdf_path: Path to PDF file
            path_structure: Path structure for organization
            upload_type: Type of upload (incoming/outgoing)
            document_id: Optional document ID
            skip_embeddings: Defer vector creation (duplicate-check pending);
                OCR and metadata extraction still run and are persisted.

        Returns:
            ProcessingResult object

        Raises:
            DocumentProcessingError: If processing fails
        """
        start_time = time.time()

        logger.info("[document_pipeline] Starting document processing for %s", pdf_path)

        processed_path: Optional[Path] = None

        try:
            # Validate input file
            input_path = Path(pdf_path)
            logger.info("[document_pipeline] Validating input file %s", input_path)
            if not input_path.exists():
                raise DocumentProcessingError(f"PDF file not found: {pdf_path}")

            file_size = input_path.stat().st_size
            if file_size > self.config.max_file_size_mb * 1024 * 1024:
                raise DocumentProcessingError(f"File too large: {file_size} bytes")

            # Step 1: Page-wise extraction. Pages with a usable text layer keep
            # their native text; only thin pages are OCR'd. This replaces the
            # document-level is_pdf_textual decision, which inspected the first
            # five pages and, on finding any text, skipped OCR for the whole
            # document - leaving scanned pages indexed as empty.
            from .extraction_adapters.document_page_store import DocumentPageStore

            logger.info(
                "[document_pipeline] Starting page-wise extraction for %s",
                input_path.name,
            )
            resolved_document_id = document_id or input_path.stem

            # Routing boundary. A job carries the pipeline it was created for,
            # and that decision - not current configuration - decides which
            # extractor runs. Anything not explicitly unified takes the legacy
            # path, so an unknown value never opts a tenant into newer code.
            extraction = None
            pages_human_review: List[int] = []

            if pipeline_version != UNIFIED_PIPELINE:
                legacy = await self._extract_legacy(input_path)
                processed_path = legacy.processed_path
                raw_ocr_text = legacy.raw_text
                logger.info(
                    "[document_pipeline] Legacy extraction completed for %s "
                    "(chars=%s)",
                    input_path.name,
                    len(raw_ocr_text or ""),
                )
                return await self._extract_and_persist(
                    input_path=input_path,
                    original_path=pdf_path,
                    processed_path=processed_path,
                    raw_ocr_text=raw_ocr_text,
                    extraction=None,
                    pages_human_review=[],
                    path_structure=path_structure,
                    upload_type=upload_type,
                    document_id=document_id,
                    skip_embeddings=skip_embeddings,
                    start_time=start_time,
                )

            run_id = extraction_run_id or str(uuid4())
            db = await self.database_service.get_database()
            page_store = DocumentPageStore(
                db=db,
                document_id=resolved_document_id,
                organization_id=organization_id or "",
                project_id=project_id,
                extraction_run_id=run_id,
            )
            dispatch = await self._extract_source(
                input_path,
                source_mime=source_mime or "application/pdf",
                page_store=page_store,
                retry_pages=retry_pages,
            )
            if dispatch.processing_state is ProcessingState.STORED_ONLY:
                # Archives are stored, never extracted. Reported explicitly so
                # nothing downstream can read this as a completed extraction.
                return ProcessingResult(
                    success=False,
                    document_id=document_id,
                    processed_path=str(input_path),
                    processing_time=time.time() - start_time,
                    source_kind=dispatch.kind.value,
                    processing_state=ProcessingState.STORED_ONLY.value,
                )
            extraction = dispatch.extraction
            if extraction is None:
                # Every kind that reaches here is an extracting kind - STORED_ONLY
                # returned above - so a dispatch with no extraction means the
                # router produced a result no extractor filled in. Say so, rather
                # than reading attributes off None a few lines down and reporting
                # it as an AttributeError from the middle of the pipeline.
                raise DocumentProcessorError(
                    f"source dispatch for {input_path.name} produced no extraction "
                    f"(kind={dispatch.kind.value}, state={dispatch.processing_state})"
                )

            # Step 1b: assess every page. Runs unconditionally - companion
            # evidence measured 9 corruptions in a PDF's own text layer, so
            # there is no page this may skip.
            # The flag's production reader. Resolved from the dispatched source
            # kind so an ineligible kind cannot opt into paid model calls, and
            # so flipping EXTRACTION_FALLBACK_ENABLED changes what actually
            # executes rather than only what the status script reports.
            pages_human_review = await self._apply_quality_gate(
                input_path,
                extraction,
                document_id=resolved_document_id,
                fallback_ladder=self.resolve_fallback_ladder(dispatch.kind, db=db),
                page_store=page_store,
            )

            processed_path = input_path
            raw_ocr_text = extraction.combined_text or None
            ocr_text_len = len(raw_ocr_text) if raw_ocr_text else 0
            logger.info(
                "[document_pipeline] Extraction completed for %s "
                "(chars=%s, ocr_pages=%s, failed=%s, deferred=%s, completeness=%s)",
                input_path.name,
                ocr_text_len,
                extraction.ocr_pages_total,
                extraction.ocr_failed_pages,
                extraction.ocr_deferred_pages,
                extraction.completeness.value,
            )


            return await self._extract_and_persist(
                input_path=input_path,
                original_path=pdf_path,
                processed_path=processed_path,
                raw_ocr_text=raw_ocr_text,
                extraction=extraction,
                pages_human_review=pages_human_review,
                path_structure=path_structure,
                upload_type=upload_type,
                document_id=document_id,
                skip_embeddings=skip_embeddings,
                start_time=start_time,
            )
        except InconsistentExtractionRunError as e:
            # Retrying cannot heal this: the run's page evidence and the
            # checkpoint disagree, and every further attempt would re-derive
            # the same disagreement while burning the retry budget. Report it
            # as terminal so a person sees it, with page numbers only.
            processing_time = time.time() - start_time
            logger.error(
                "[document_pipeline] Extraction run inconsistent for %s: %s",
                pdf_path,
                e,
            )
            return ProcessingResult(
                success=False,
                error=str(e),
                processing_time=processing_time,
                processing_state=ProcessingState.HUMAN_REVIEW_REQUIRED.value,
                pages_human_review=list(e.missing_page_numbers),
                publishable=False,
            )
        except Exception as e:
            processing_time = time.time() - start_time
            logger.error(f"[document_pipeline] Document processing failed for {pdf_path}: {e}")

            return ProcessingResult(
                success=False,
                error=str(e),
                processing_time=processing_time,
                # A failed extraction is never publishable.
                publishable=False,
            )

        finally:
            # Single owner of the database connection, so it is closed exactly
            # once per document and on every path - including a failure that
            # happens before _extract_and_persist is ever reached. OpenAI file
            # cleanup belongs to _extract_and_persist, which owns that handle.
            try:
                await self.database_service.close_connection()
            except Exception as e:
                logger.warning(f"Error closing database connection: {e}")

    def _salvage_incomplete_report(
        self, exc: ModelOutputIncompleteError
    ) -> tuple[str, Optional[List[int]]]:
        """The completed items of a cut-off report, and their item numbers.

        The item the reply was writing when it stopped is dropped with
        everything after it. Returns ``("", None)`` when nothing whole is left.
        """
        trimmed, kept = self.text_service.trim_incomplete_report(exc.partial_output or "")
        if not trimmed.strip():
            return "", None
        return trimmed, kept

    def _settle_body(
        self,
        metadata: ParsedDocumentMetadata,
        *,
        raw_ocr_text: Optional[str],
        extracted_content: str,
        source_complete: bool,
        metadata_source: str,
        salvaged_items: Optional[List[int]],
        used_ocr_fallback: bool = False,
    ) -> ParsedDocumentMetadata:
        """Decide the letter body this run persists as ``full_text``.

        * Complete source text: the source is the body. Item 25 was not asked
          for; anything the parser still labelled "full content" is dropped.
        * Deterministic OCR fallback: the report's Item 25 is the raw OCR text
          verbatim, so the body is that source text, labelled as such.
        * Otherwise Item 25 is the fallback body - from the numbered report
          only, and only when the reply finished writing it.
        """
        has_source = bool(raw_ocr_text and raw_ocr_text.strip())
        update: Dict[str, Any] = {}
        if source_complete or (used_ocr_fallback and has_source):
            update = {
                "full_content": None,
                "body_text": raw_ocr_text,
                "body_text_source": FULL_TEXT_SOURCE_SOURCE,
            }
        elif salvaged_items is not None and 25 not in salvaged_items:
            update = {"full_content": None}
        elif metadata_source == "pydantic_ai" and extracted_content:
            # The agent is never asked for Item 25; the numbered report is
            # the only fallback body.
            update = {
                "full_content": self.text_service.parse_extraction_report(
                    extracted_content
                ).full_content
            }
        if not update:
            return metadata
        if hasattr(metadata, "model_copy"):
            return metadata.model_copy(update=update)
        for key, value in update.items():
            setattr(metadata, key, value)
        return metadata

    def _build_ocr_fallback_report(self, ocr_text: str, *, filename: str) -> str:
        """Build a structured report from OCR text when AI extraction is unavailable.

        Every value comes from a labelled line of the OCR text, or is
        ``null``. The filename is never promoted to a letter number, subject
        or summary: a guessed value stored as an extracted fact looks real,
        yields a false ``letterNoNormalized`` and then false reference links
        (DI-H6). The caller marks this source degraded (metadata_integrity).
        """

        text = (ocr_text or "").strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        first_lines = lines[:80]

        def first_match(patterns: list[str], source: str = text) -> Optional[str]:
            for pattern in patterns:
                match = re.search(pattern, source, flags=re.IGNORECASE | re.MULTILINE)
                if match:
                    value = re.sub(r"\s+", " ", match.group(1)).strip(" :-")
                    if value:
                        return value[:250]
            return None

        # Header fields are read from the start of a line only. Mid-sentence
        # matches ("... our letter no. XYZ/12 dated 01.08.2024") and the first
        # bare date in the text belong to some other letter.
        date = first_match(
            [
                r"^\s*Date\s*[:\-]?\s*([0-3]?\d[./-][01]?\d[./-](?:19|20)?\d{2})",
                r"^\s*Dated?\s*[:\-]?\s*([0-3]?\d[./-][01]?\d[./-](?:19|20)?\d{2})",
            ]
        )
        letter_no = self._fallback_letter_number(lines[:40])
        subject = self._subject_from_lines(first_lines)
        from_company = first_match([r"^\s*From\s*[:\-]\s*(.+)$"])
        to_company = first_match([r"^\s*To\s*[:\-]\s*(.+)$"])
        summary_lines = [line for line in lines if len(line) >= 24][:5]
        summary = "\n".join(f"- {line[:220]}" for line in summary_lines) or None
        keywords = self._keywords_from_text(" ".join(first_lines), letter_no=letter_no, subject=subject)

        def value_or_null(value: Optional[str]) -> str:
            return value if value else "null"

        return "\n".join(
            [
                f"1) Date: {value_or_null(date)}",
                f"2) Letter No.: {value_or_null(letter_no)}",
                f"3) From (Company): {value_or_null(from_company)}",
                f"4) To (Company): {value_or_null(to_company)}",
                f"5) Subject: {value_or_null(subject)}",
                "6) References: null",
                "7) Asset Type: null",
                "8) Location: null",
                "9) Specific Area: null",
                "11) Chainage From: null",
                "12) Chainage To: null",
                "13) Work Type: null",
                "14) Issue Nature: null",
                "15) Claim Category: null",
                "16) Alleged Responsibility: null",
                "17) Priority: null",
                f"18) Key Words: {', '.join(keywords) if keywords else 'null'}",
                "19) Linked Event Suggested: null",
                "20) Reference Chain: null",
                f"21) Additional Key Words: {', '.join(keywords) if keywords else 'null'}",
                f"22) Summary: {value_or_null(summary)}",
                "23) Contractual Clauses: null",
                "24) Key Reply Points - Points to be Addressed While Responding: null",
                f"25) Full Content: {text}",
                "26) extracted_tags: null",
                "27) extracted_subTags: null",
            ]
        )

    #: "Letter No.: X" / "Our Ref: X" / "Ref. No. X" at the start of a line.
    #: The label must end in a delimiter or "No", so "References:" and
    #: "Refund of retention ..." are not labels.
    _LETTER_NO_LABEL = re.compile(
        r"^(?P<label>Letter[ \t]*(?:No\.?|Number)|(?:Our[ \t]+)?Ref(?:erence)?\b\.?(?:[ \t]*No\.?)?)"
        r"[ \t]*[:\-]?[ \t]*(?P<value>\S.*)$",
        flags=re.IGNORECASE,
    )
    _DATED_CLAUSE = re.compile(r"\s+(?:dated|dtd|dt\.?|date)\b", flags=re.IGNORECASE)

    def _fallback_letter_number(self, lines: list[str]) -> Optional[str]:
        """This letter's own number from a labelled header line, or None.

        A candidate must be one identifier-shaped token containing a digit.
        A ``Ref:`` line carrying "dated ..." cites *another* letter and is
        skipped; a ``Letter No.`` line may carry its own date.
        """
        for line in lines:
            match = self._LETTER_NO_LABEL.match(line.strip())
            if not match:
                continue
            is_ref_label = re.sub(r"^our\s+", "", match.group("label").lower()).startswith("ref")
            value = match.group("value").strip()
            parts = self._DATED_CLAUSE.split(value, maxsplit=1)
            if is_ref_label and len(parts) > 1:
                continue
            candidate = parts[0].strip(" :-.,;")[:90]
            # One identifier-shaped token: segments joined by / - _ . with a
            # digit somewhere. Prose ("to your letter no. 12", "GCC Clause
            # 8.4", "2 above is relevant") has spaces and is rejected.
            if re.fullmatch(r"[A-Za-z0-9]+(?:[/_.\-][A-Za-z0-9]+)+", candidate) and re.search(
                r"\d", candidate
            ):
                return candidate
        return None

    def _subject_from_lines(self, lines: list[str]) -> Optional[str]:
        for index, line in enumerate(lines):
            match = re.match(r"^\s*(?:Sub(?:ject)?|Re)\s*[:\-]\s*(.+)$", line, flags=re.IGNORECASE)
            if not match:
                continue
            subject = match.group(1).strip()
            if index + 1 < len(lines):
                next_line = lines[index + 1].strip()
                if next_line and not re.match(r"^(Ref|Date|To|From)\b", next_line, flags=re.IGNORECASE):
                    subject = f"{subject} {next_line}".strip()
            return re.sub(r"\s+", " ", subject)[:250] if subject else None
        return None

    def _keywords_from_text(self, text: str, *, letter_no: Optional[str], subject: Optional[str]) -> list[str]:
        candidates: list[str] = []
        for value in (letter_no, subject):
            if value:
                candidates.extend(part.strip(" ,.;:()[]") for part in re.split(r"[\s,/]+", value))
        candidates.extend(
            match.group(0)
            for match in re.finditer(r"\b[A-Z][A-Za-z0-9/-]{3,}\b", text or "")
        )
        seen = set()
        keywords: list[str] = []
        for candidate in candidates:
            normalized = candidate.strip()
            key = normalized.lower()
            if len(normalized) < 4 or key in seen:
                continue
            seen.add(key)
            keywords.append(normalized)
            if len(keywords) >= 20:
                break
        return keywords

    async def _save_results(
        self,
        extracted_content: str,
        raw_ocr_text: Optional[str],
        original_path: str,
        path_structure: str,
        upload_type: str,
        document_id: Optional[str],
        parsed_metadata: ParsedDocumentMetadata,
        skip_embeddings: bool = False,
        *,
        metadata_quality: Optional[Dict[str, Any]] = None,
        source_text_status: Optional[str] = None,
    ) -> int:
        """Save processing results to file system and database"""
        try:
            # Save summary to file system
            await self.file_service.save_summary(
                extracted_content, original_path, path_structure, upload_type
            )

            # Determine text to use for different purposes. `ocrText` keeps its
            # historical content (the report when there was no source text) and
            # is labelled, so no reader mistakes the report for the letter.
            has_source = bool(raw_ocr_text and raw_ocr_text.strip())
            text_for_db = raw_ocr_text or extracted_content
            # Vectors are the letter: source text, else the report's validated
            # Item 25. Failing both, the report is passed on so the payload
            # guard refuses it visibly (sync `error`) rather than it vanishing.
            text_for_embedding = raw_ocr_text or parsed_metadata.full_content or extracted_content
            source_provenance: Dict[str, Any] = {
                "source_text_status": source_text_status
                or (SOURCE_TEXT_UNVERIFIED if has_source else SOURCE_TEXT_ABSENT),
            }
            if has_source:
                source_provenance["ocr_text_kind"] = OCR_TEXT_KIND_SOURCE
            elif text_for_db:
                source_provenance["ocr_text_kind"] = OCR_TEXT_KIND_REPORT

            # Save to database and create embeddings
            chunks_created = await self.database_service.save_document_data(
                document_id=document_id,
                file_path=original_path,
                parsed_metadata=parsed_metadata,
                full_text=text_for_db,
                embedding_text=text_for_embedding,
                skip_embeddings=skip_embeddings,
                metadata_quality=metadata_quality,
                source_provenance=source_provenance,
            )

            return chunks_created

        except Exception as e:
            logger.error(f"Failed to save results: {e}")
            raise DocumentProcessingError(f"Failed to save results: {str(e)}")

    async def _extract_and_persist(
        self,
        *,
        input_path: Path,
        original_path: str,
        processed_path: Optional[Path],
        raw_ocr_text: Optional[str],
        extraction: Optional[Any],
        pages_human_review: List[int],
        path_structure: str,
        upload_type: str,
        document_id: Optional[str],
        skip_embeddings: bool,
        start_time: float,
    ) -> ProcessingResult:
        """Content extraction, metadata parsing and persistence.

        Shared by both pipelines so there is one implementation, not two. The
        legacy path passes extraction=None and no review pages; everything
        downstream of the text is identical.
        """
        file_id: Optional[str] = None
        try:
            # Step 2: Upload to OpenAI
            partial_failures: Dict[str, Any] = {}
            extracted_content = ""
            metadata_source = "legacy_regex"
            # Complete source text makes report Item 25 redundant: the model
            # would only retype the text it was given. Unknown completeness
            # (legacy_v0) keeps Item 25 requested.
            source_complete = source_text_is_complete(raw_ocr_text, extraction)
            #: Item numbers a cut-off reply finished; None when not cut off.
            salvaged_items: Optional[List[int]] = None
            #: The report is the deterministic one, whose Item 25 is raw OCR.
            used_ocr_fallback = False

            if raw_ocr_text and raw_ocr_text.strip():
                logger.info(
                    "[document_pipeline] Requesting content extraction from OCR text for %s (chars=%s)",
                    input_path.name,
                    len(raw_ocr_text),
                )
                try:
                    # The keyword travels only when Item 25 is dropped, so the
                    # call on every other path is exactly what it always was.
                    omit_item_25: Dict[str, Any] = (
                        {"include_full_content": False} if source_complete else {}
                    )
                    extracted_content = await self.openai_service.process_text(
                        raw_ocr_text,
                        filename=input_path.name,
                        **omit_item_25,
                    )
                    metadata_source = "openai_text_legacy_regex"
                except ModelOutputIncompleteError as exc:
                    # Fail visible, keep what is provably whole: the items the
                    # reply finished before it was cut off. Never the cut item.
                    logger.warning(
                        "[document_pipeline] OCR-text extraction for %s was cut off "
                        "(%s); keeping only the items it completed",
                        input_path.name,
                        exc.reason,
                    )
                    partial_failures["ai_extraction"] = exc.failure_record()
                    extracted_content, salvaged_items = self._salvage_incomplete_report(exc)
                    if extracted_content:
                        metadata_source = "openai_text_legacy_regex"
                    else:
                        extracted_content = self._build_ocr_fallback_report(
                            raw_ocr_text,
                            filename=input_path.name,
                        )
                        metadata_source = "ocr_fallback_regex"
                        salvaged_items = None
                        used_ocr_fallback = True
                except Exception as exc:
                    logger.warning(
                        "[document_pipeline] OpenAI OCR-text extraction failed for %s; "
                        "falling back to deterministic OCR metadata parser: %s",
                        input_path.name,
                        exc,
                    )
                    partial_failures["ai_extraction"] = {
                        "stage": "ocr_text_extraction",
                        "message": str(exc),
                    }
                    extracted_content = self._build_ocr_fallback_report(
                        raw_ocr_text,
                        filename=input_path.name,
                    )
                    metadata_source = "ocr_fallback_regex"
                    used_ocr_fallback = True
            elif extraction is not None and getattr(extraction, "withheld_pages", None):
                # Nothing publishable, because the text layer itself was judged
                # unusable. Uploading the whole PDF would only have it read the
                # same unmapped layer by another route, with nothing checking
                # the reply. The pages stay fail-visible for review instead.
                withheld_pages = list(extraction.withheld_pages)
                logger.warning(
                    "[document_pipeline] %s: %s page(s) had an unusable text layer; "
                    "not sent for whole-file extraction",
                    input_path.name,
                    len(withheld_pages),
                )
                partial_failures["ai_extraction"] = {
                    "stage": "withheld_text_layer",
                    "message": (
                        f"{len(withheld_pages)} page(s) had an unusable text layer "
                        "and were withheld; no text was extracted"
                    ),
                    "pages": withheld_pages,
                }
            else:
                logger.info("[document_pipeline] Uploading %s to OpenAI", processed_path.name if processed_path else input_path.name)
                file_id = await self.openai_service.upload_file(str(processed_path))
                logger.info("[document_pipeline] Uploaded file_id=%s for %s", file_id, input_path.name)

                # Step 3: Extract content using OpenAI
                logger.info("[document_pipeline] Requesting content extraction for %s", input_path.name)
                try:
                    extracted_content = await self.openai_service.process_document(file_id)
                except ModelOutputIncompleteError as exc:
                    # No source text exists here, so a cut-off Item 25 would be
                    # the only body: it is dropped, never stored as complete.
                    logger.warning(
                        "[document_pipeline] Whole-file extraction for %s was cut off "
                        "(%s); keeping only the items it completed",
                        input_path.name,
                        exc.reason,
                    )
                    partial_failures["ai_extraction"] = exc.failure_record()
                    extracted_content, salvaged_items = self._salvage_incomplete_report(exc)
                # The reply is judged by the same canonical policy as every
                # other text surface before it can become the document's text.
                extracted_content, unusable_reply = withhold_unusable(
                    extracted_content or ""
                )
                if unusable_reply is not None:
                    logger.warning(
                        "[document_pipeline] %s: whole-file extraction returned "
                        "unusable (cid:N) text; discarded",
                        input_path.name,
                    )
                    partial_failures["ai_extraction"] = {
                        "stage": "whole_file_extraction",
                        "message": "whole-file extraction returned unusable (cid:N) text",
                    }
            extracted_length = len(extracted_content or "")
            logger.info("[document_pipeline] Content extraction complete for %s (chars=%s)", input_path.name, extracted_length)

            # Step 4: Parse extracted content
            metadata_debug: Optional[Dict[str, Any]] = None
            parsed_metadata: Optional[ParsedDocumentMetadata] = None

            if self.pydantic_ai_service.is_enabled:
                logger.info("[document_pipeline] [%s] Attempting metadata extraction via PydanticAI", input_path.name)
                try:
                    agent_result = await self.pydantic_ai_service.extract_metadata(
                        document_text=raw_ocr_text or extracted_content,
                        context={"filename": input_path.name, "upload_type": upload_type},
                    )
                except PydanticAIMetadataError as exc:
                    if isinstance(exc, ModelOutputIncompleteError):
                        # Visible even though the report parse below takes
                        # over: a truncated agent reply is never silent.
                        partial_failures["pydantic_ai"] = exc.failure_record()
                    logger.warning(f"PydanticAI metadata extraction failed for {input_path}: {exc}")
                    logger.info("[document_pipeline] [%s] Falling back to legacy regex metadata parser", input_path.name)
                else:
                    if agent_result:
                        metadata_source = "pydantic_ai"
                        parsed_metadata = agent_result.metadata
                        metadata_debug = {**agent_result.debug, "raw_result": agent_result.raw_result}
                        logger.info("[document_pipeline] [%s] Metadata extracted successfully via PydanticAI", input_path.name)
                    else:
                        logger.info("[document_pipeline] [%s] PydanticAI returned no metadata; using legacy regex parser", input_path.name)
            else:
                logger.info(
                    "[document_pipeline] [%s] PydanticAI disabled or unavailable (use_pydantic_ai=%s, api_key=%s); using legacy regex parser",
                    input_path.name,
                    getattr(self.config, "use_pydantic_ai", False),
                    "set" if bool(getattr(self.config, "openai_api_key", None)) else "unset",
                )

            if parsed_metadata is None:
                logger.info("[document_pipeline] [%s] Parsing metadata with legacy regex parser", input_path.name)
                parsed_metadata = self.text_service.parse_extraction_report(extracted_content)
                if metadata_source not in {"pydantic_ai", "openai_text_legacy_regex", "ocr_fallback_regex"}:
                    metadata_source = "legacy_regex"



            parsed_metadata = self._settle_body(
                parsed_metadata,
                raw_ocr_text=raw_ocr_text,
                extracted_content=extracted_content,
                source_complete=source_complete,
                metadata_source=metadata_source,
                salvaged_items=salvaged_items,
                used_ocr_fallback=used_ocr_fallback,
            )

            # Degraded extraction must be visible on every path that persists
            # it, including bulk upload, which never reaches DocumentService.
            metadata_quality = assess_metadata_quality(
                parsed_metadata,
                metadata_source=metadata_source,
                partial_failures=partial_failures,
            )

            # The publication barrier. Decided once, here, BEFORE any
            # publishing side effect runs, and carried on the result so every
            # downstream boundary honours the same decision instead of
            # re-deriving it.
            #
            # Previously the embeddings were created first and the review flag
            # written afterwards by DocumentService, so a page the gate had
            # already failed was retrievable and draftable before anyone was
            # told. A status written later is not containment.
            blocked_for_review = bool(pages_human_review)
            publishable = not blocked_for_review
            if blocked_for_review:
                logger.warning(
                    "[document_pipeline] Withholding publication for %s: "
                    "page(s) %s need human review",
                    input_path.name,
                    pages_human_review,
                )

            # Step 5: Save results. Diagnostic evidence is still persisted - a
            # reviewer needs the record - but vectors are withheld.
            logger.info("[document_pipeline] Persisting OCR, metadata, and embeddings for %s", input_path.name)
            chunks_created = await self._save_results(
                extracted_content, raw_ocr_text, original_path, path_structure,
                upload_type, document_id, parsed_metadata,
                skip_embeddings=skip_embeddings or blocked_for_review,
                metadata_quality=metadata_quality,
                source_text_status=(
                    SOURCE_TEXT_COMPLETE
                    if source_complete
                    else SOURCE_TEXT_UNVERIFIED
                    if raw_ocr_text and raw_ocr_text.strip()
                    else SOURCE_TEXT_ABSENT
                ),
            )
            partial_failures.update(dict(getattr(self.database_service, "partial_failures", {}) or {}))

            processing_time = time.time() - start_time

            logger.info("[document_pipeline] Document processing completed successfully for %s in %.2fs", input_path, processing_time)

            return ProcessingResult(
                success=True,
                document_id=document_id,
                processed_path=str(processed_path),
                metadata=parsed_metadata,
                chunks_created=chunks_created,
                processing_time=processing_time,
                metadata_source=metadata_source,
                metadata_debug=metadata_debug,
                partial_failures=partial_failures,
                extraction_result=extraction,
                # None on the legacy path, which produces no page evidence.
                extraction_completeness=(
                    extraction.completeness.value if extraction is not None else None
                ),
                pages_human_review=pages_human_review,
                publishable=publishable,
            )


        except Exception as e:
            processing_time = time.time() - start_time
            logger.error(
                "[document_pipeline] Persistence failed for %s: %s", input_path, e
            )
            return ProcessingResult(
                success=False,
                error=str(e),
                processing_time=processing_time,
                # A failed extraction is never publishable.
                publishable=False,
            )
        finally:
            # This method owns file_id, so it cleans it up. It deliberately does
            # NOT close the database connection: process_document owns that, and
            # closing here as well would double-close every document.
            if file_id:
                try:
                    await self.openai_service.cleanup_file(file_id)
                except Exception as exc:
                    logger.warning("Error during cleanup: %s", exc)


# Factory function
def create_document_processor(config: Optional[DocumentProcessingConfig] = None) -> DocumentProcessor:
    """Create document processor instance"""
    return DocumentProcessor(config)

# Convenience function for backward compatibility
async def process_document(
    pdf_path: str,
    path_structure: str,
    upload_type: str,
    document_id: Optional[str] = None
) -> ProcessingResult:
    """Convenience function for document processing"""
    processor = create_document_processor()
    return await processor.process_document(pdf_path, path_structure, upload_type, document_id)


__all__ = ["DocumentProcessor", "create_document_processor", "process_document", "DocumentProcessorError"]
