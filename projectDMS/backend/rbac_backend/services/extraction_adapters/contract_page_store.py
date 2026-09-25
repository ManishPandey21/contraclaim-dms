"""PageStore adapter for the contract ingestion path.

Everything contract-specific lives here: upload_id, the contract_ocr_batches
and contract_ocr_pages collections, and the record shape those collections
already hold. The engine knows none of it.

The record shape is deliberately frozen to match what contracts_ingest wrote
inline before Phase 1. Changing a key here is a data migration. Keys are only
ever added: ``text_withheld``, ``source`` and ``page_class`` let an OCR retry
rebuild the pages an earlier attempt resolved instead of re-extracting them,
and rows written before they existed are read with the defaults below.

A withheld page publishes nothing: ``raw_text`` and ``cleaned_text`` stay empty
and ``text_withheld`` records why. The unusable ``(cid:N)`` text itself is not
kept on the row - every string on a contract page row is read by the clause
agent or shown to a user as page text, so there is no non-published field to
hold it in.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from ..extraction.page_store import InconsistentExtractionRunError
from .document_page_store import MalformedPageRecordError, _coerce


def to_contract_page_record(
    page: ExtractedPage,
    *,
    document_id: str,
    upload_id: str,
    organization_id: str,
    project_id: Optional[str],
    source_pdf_page_link: str,
) -> Dict[str, Any]:
    """Build the contract_ocr_pages record for one page.

    cleaned_text is intentionally empty: the text preprocessor fills it after
    extraction, exactly as before.
    """
    raw_text = page.text or ""
    return {
        "document_id": document_id,
        "upload_id": upload_id,
        "organization_id": str(organization_id),
        "project_id": str(project_id) if project_id else None,
        "page_number": page.number,
        "batch_id": page.batch_id,
        "status": page.status.value,
        "error": page.error,
        "raw_text": raw_text,
        "raw_text_length": len(raw_text),
        "cleaned_text": "",
        "cleaned_text_length": 0,
        "source_pdf_page_link": source_pdf_page_link,
        "text_withheld": bool(page.text_withheld),
        "source": page.source.value,
        "page_class": page.classification.page_class.value,
    }


def from_contract_page_record(record: Dict[str, Any]) -> ExtractedPage:
    """Rebuild a page an earlier attempt of this contract's run recorded.

    Rows written before ``source``/``page_class`` existed get the values their
    status and text imply. An unreadable status is not guessed: it raises, and
    the caller fails the run closed rather than carrying a page it cannot judge.
    """
    number = int(record.get("page_number") or 0)
    text = record.get("raw_text") or ""
    status = _coerce(PageStatus, record.get("status"), page_number=number, field="status")
    default_source = (
        PageSource.OCR
        if status is PageStatus.OCR_COMPLETED
        else PageSource.TEXT_LAYER if text.strip() else PageSource.EMPTY
    )
    default_class = PageClass.TEXT_NATIVE if text.strip() else PageClass.SCANNED_IMAGE
    return ExtractedPage(
        number=number,
        text=text,
        source=_coerce(
            PageSource,
            record.get("source") or default_source.value,
            page_number=number,
            field="source",
        ),
        status=status,
        classification=PageClassification(
            page_class=_coerce(
                PageClass,
                record.get("page_class") or default_class.value,
                page_number=number,
                field="page_class",
            ),
            char_count=len(text.strip()),
            image_count=0,
            image_coverage=0.0,
            table_count=0,
            width=0.0,
            height=0.0,
            rotation=0,
        ),
        batch_id=record.get("batch_id"),
        error=record.get("error"),
        text_withheld=bool(record.get("text_withheld")),
    )


class ContractPageStore:
    def __init__(
        self,
        *,
        db_service: Any,
        document_id: str,
        upload_id: str,
        organization_id: str,
        project_id: Optional[str],
    ) -> None:
        self.db_service = db_service
        self.document_id = document_id
        self.upload_id = upload_id
        self.organization_id = organization_id
        self.project_id = project_id
        self.records: List[Dict[str, Any]] = []
        self._batch_ranges: Dict[str, Tuple[int, int, bool]] = {}

    async def begin_batch(self, *, page_start: int, page_end: int, retry: bool) -> str:
        batch_id = await self.db_service.upsert_ocr_batch(
            document_id=self.document_id,
            upload_id=self.upload_id,
            organization_id=self.organization_id,
            project_id=self.project_id,
            page_start=page_start,
            page_end=page_end,
            status="running",
            retry_count=1 if retry else 0,
        )
        self._batch_ranges[batch_id] = (page_start, page_end, retry)
        return batch_id

    async def finish_batch(
        self, batch_id: str, *, status: str, error: Optional[str] = None
    ) -> None:
        page_start, page_end, retry = self._batch_ranges.get(batch_id, (0, 0, False))
        kwargs: Dict[str, Any] = {
            "document_id": self.document_id,
            "upload_id": self.upload_id,
            "organization_id": self.organization_id,
            "project_id": self.project_id,
            "page_start": page_start,
            "page_end": page_end,
            "status": status,
            "retry_count": 1 if retry else 0,
        }
        if error is not None:
            kwargs["error"] = error[:500]
        await self.db_service.upsert_ocr_batch(**kwargs)

    async def record_pages(self, pages: Sequence[ExtractedPage]) -> None:
        self.records = [
            to_contract_page_record(
                page,
                document_id=self.document_id,
                upload_id=self.upload_id,
                organization_id=self.organization_id,
                project_id=self.project_id,
                source_pdf_page_link=f"contract:{self.document_id}#page={page.number}",
            )
            for page in pages
        ]


class ResumableContractPageStore(ContractPageStore):
    """A contract page store that can hand an OCR retry the run it continues.

    Only an OCR retry of an existing contract uses it. Its presence is what
    makes the engine treat the retry as another attempt of the same run - the
    retry reworks the pages that still owe work and carries every resolved
    page forward, exactly as the document path does (PR #28). Without it the
    retry rewrote every page from a fresh native read, which returns nothing
    for a scanned page an earlier attempt had OCR'd.
    """

    async def load_run_pages(self) -> List[ExtractedPage]:
        cursor = self.db_service.db.contract_ocr_pages.find(
            {"document_id": self.document_id}
        )
        records = sorted(
            [record async for record in cursor],
            key=lambda item: int(item.get("page_number") or 0),
        )
        pages: List[ExtractedPage] = []
        malformed: List[int] = []
        for record in records:
            try:
                pages.append(from_contract_page_record(record))
            except MalformedPageRecordError as exc:
                malformed.append(exc.page_number)
        if malformed:
            raise InconsistentExtractionRunError(
                extraction_run_id=None,
                expected_page_numbers=[int(r.get("page_number") or 0) for r in records],
                persisted_page_numbers=[page.number for page in pages],
                missing_page_numbers=malformed,
                reason=(
                    "contract page records cannot be read; reindex the contract "
                    "to re-extract every page"
                ),
            )
        return pages
