"""The seam between the extraction engine and caller-specific persistence.

The engine never writes to Mongo. It calls begin_batch/finish_batch as OCR
batches progress and returns page records; the caller's adapter decides where
those land - contract_ocr_pages for contracts, document_ocr_pages for general
documents. Contract-specific state (upload_id, job status, usage metering)
stays entirely on the caller's side of this seam.

Two adapters exist from day one - ContractPageStore (Task 1.7) and
DocumentPageStore (Phase 3) - so this is a real seam, not a hypothetical one.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, runtime_checkable

from .models import ExtractedPage


@runtime_checkable
class PageStore(Protocol):
    """Where a caller persists batch lifecycle and page records."""

    async def begin_batch(self, *, page_start: int, page_end: int, retry: bool) -> str:
        ...

    async def finish_batch(
        self, batch_id: str, *, status: str, error: Optional[str] = None
    ) -> None:
        ...

    async def record_pages(self, pages: Sequence[ExtractedPage]) -> None:
        ...


@runtime_checkable
class ResumablePageStore(Protocol):
    """A PageStore that can hand back everything a run has already recorded.

    Deliberately separate from PageStore: only a store with a durable,
    run-scoped identity (document_id + extraction_run_id) can answer this, and
    forcing the contract adapter or the in-memory stores to fake one would
    invent run semantics they do not have. A retry asks for this capability by
    feature test; a store without it keeps the pre-existing behaviour.
    """

    async def load_run_pages(self) -> List[ExtractedPage]:
        """Every page recorded for this run so far, in page-number order."""
        ...


class InconsistentExtractionRunError(RuntimeError):
    """A retry expected earlier pages that the extraction run does not hold.

    Fail-closed on purpose. Re-reading those pages natively would silently
    republish a scanned page as empty text - exactly the corruption the
    cumulative run exists to prevent - so the attempt stops visibly instead.
    Carries page numbers only; never page text.
    """

    def __init__(
        self,
        *,
        extraction_run_id: Optional[str],
        expected_page_numbers: Sequence[int],
        persisted_page_numbers: Sequence[int],
        missing_page_numbers: Sequence[int],
        reason: str = "pages this retry was told were resolved are absent",
    ) -> None:
        self.extraction_run_id = extraction_run_id
        self.expected_page_numbers = list(expected_page_numbers)
        self.persisted_page_numbers = list(persisted_page_numbers)
        self.missing_page_numbers = list(missing_page_numbers)
        self.reason = reason
        super().__init__(
            f"Extraction run {extraction_run_id!r} cannot be assembled: {reason}. "
            f"missing={self.missing_page_numbers} "
            f"expected={self.expected_page_numbers} "
            f"persisted={self.persisted_page_numbers}"
        )


@runtime_checkable
class OcrRunner(Protocol):
    """Runs OCR over selected pages of a PDF.

    PDF-only: implementations may count pages and map source pages to output
    indexes. Returns {page_number: text} for the pages it processed.
    """

    async def run(
        self, source_pdf: Path, page_numbers: Sequence[int], language: str
    ) -> Dict[int, str]:
        ...


@runtime_checkable
class ImageOcrRunner(Protocol):
    """Runs OCR over a single standalone image.

    Deliberately separate from OcrRunner: an image has no page numbers and no
    PDF page-mapping contract, so sharing the interface would force every
    implementation to fake one.
    """

    async def run(self, source_image: Path, language: str) -> str:
        ...


@runtime_checkable
class MeterCallback(Protocol):
    """Records billable OCR pages before the work is done."""

    async def __call__(
        self, *, page_count: int, page_numbers: Sequence[int], retry: bool
    ) -> None:
        ...


class NullPageStore:
    """In-memory PageStore for tests and for callers that do not persist."""

    def __init__(self) -> None:
        self.batches: List[Dict[str, Any]] = []
        self.recorded_pages: List[ExtractedPage] = []

    async def begin_batch(self, *, page_start: int, page_end: int, retry: bool) -> str:
        batch_id = str(uuid.uuid4())
        self.batches.append(
            {
                "batch_id": batch_id,
                "page_start": page_start,
                "page_end": page_end,
                "retry": retry,
                "status": "running",
                "error": None,
            }
        )
        return batch_id

    async def finish_batch(
        self, batch_id: str, *, status: str, error: Optional[str] = None
    ) -> None:
        for batch in self.batches:
            if batch["batch_id"] == batch_id:
                batch["status"] = status
                batch["error"] = error
                return
        # An unknown batch id is not an error here: the engine must be able to
        # report a batch failure even if begin_batch never completed, rather
        # than raising and masking the original failure.

    async def record_pages(self, pages: Sequence[ExtractedPage]) -> None:
        self.recorded_pages.extend(pages)
