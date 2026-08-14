"""PageStore adapter for the contract ingestion path.

Everything contract-specific lives here: upload_id, the contract_ocr_batches
and contract_ocr_pages collections, and the record shape those collections
already hold. The engine knows none of it.

The record shape is deliberately frozen to match what contracts_ingest wrote
inline before Phase 1. Changing a key here is a data migration.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..extraction.models import ExtractedPage


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
    }


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
