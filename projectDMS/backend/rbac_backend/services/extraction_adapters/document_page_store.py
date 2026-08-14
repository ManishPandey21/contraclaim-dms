"""PageStore adapter for general documents and enclosures.

Mirrors ContractPageStore but writes to its own collections and keeps per-page
geometry, which the fallback ladder and the photo extractor both need and the
contract record does not carry.

Versioning, not replacement: each extraction run has a stable id and
`record_pages` upserts into that version only. Nothing deletes the previously
visible evidence, so a crash mid-run leaves an unpublished run to reclaim -
never a document whose pages were erased and not rewritten. Readers follow
`document_extraction_heads`, and `publish_run` advances that pointer in one
write, only after the expected page set is actually present.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from pymongo import ReplaceOne

from ..extraction.models import ExtractedPage

DOCUMENT_OCR_PAGES = "document_ocr_pages"
DOCUMENT_OCR_BATCHES = "document_ocr_batches"
DOCUMENT_EXTRACTION_HEADS = "document_extraction_heads"


def to_document_page_record(
    page: ExtractedPage,
    *,
    document_id: str,
    organization_id: str,
    project_id: Optional[str],
    extraction_run_id: str,
) -> Dict[str, Any]:
    text = page.text or ""
    classification = page.classification
    return {
        "document_id": document_id,
        "organization_id": str(organization_id),
        "project_id": str(project_id) if project_id else None,
        "page_number": page.number,
        "extraction_run_id": extraction_run_id,
        "batch_id": page.batch_id,
        "source": page.source.value,
        "status": page.status.value,
        "error": page.error,
        "quality_verdict": page.quality_verdict,
        "quality_checks": list(page.quality_checks),
        "needs_review": page.needs_review,
        "raw_text": text,
        "raw_text_length": len(text),
        "page_class": classification.page_class.value,
        "char_count": classification.char_count,
        "image_count": classification.image_count,
        "image_coverage": classification.image_coverage,
        "table_count": classification.table_count,
        "tables": page.tables,
        "width": classification.width,
        "height": classification.height,
        "rotation": classification.rotation,
        "source_pdf_page_link": f"document:{document_id}#page={page.number}",
    }


class DocumentPageStore:
    def __init__(
        self,
        *,
        db: Any,
        document_id: str,
        organization_id: str,
        project_id: Optional[str],
        extraction_run_id: str,
    ) -> None:
        self.db = db
        self.document_id = document_id
        self.organization_id = organization_id
        self.project_id = project_id
        self.extraction_run_id = extraction_run_id
        self._batch_storage_ids: Dict[str, Any] = {}

    async def begin_batch(self, *, page_start: int, page_end: int, retry: bool) -> str:
        result = await self.db[DOCUMENT_OCR_BATCHES].insert_one(
            {
                "document_id": self.document_id,
                "organization_id": str(self.organization_id),
                "project_id": str(self.project_id) if self.project_id else None,
                "extraction_run_id": self.extraction_run_id,
                "page_start": page_start,
                "page_end": page_end,
                "status": "running",
                "retry": retry,
                "error": None,
                "started_at": datetime.now(timezone.utc),
            }
        )
        public_id = str(result.inserted_id)
        # The public batch id is a string (it is stored on ExtractedPage), but
        # the finishing query needs the real BSON type back.
        self._batch_storage_ids[public_id] = result.inserted_id
        return public_id

    async def finish_batch(
        self, batch_id: str, *, status: str, error: Optional[str] = None
    ) -> None:
        storage_id = self._batch_storage_ids.get(batch_id)
        if storage_id is None:
            # A reclaimed worker may finish a batch created by an earlier
            # process. Convert only strings that are valid ObjectIds; otherwise
            # keep whatever the caller persisted.
            from bson import ObjectId
            from bson.errors import InvalidId

            try:
                storage_id = ObjectId(batch_id)
            except (InvalidId, TypeError):
                storage_id = batch_id

        await self.db[DOCUMENT_OCR_BATCHES].update_one(
            {"document_id": self.document_id, "_id": storage_id},
            {
                "$set": {
                    "status": status,
                    "error": error[:500] if error else None,
                    "finished_at": datetime.now(timezone.utc),
                }
            },
        )

    async def record_pages(self, pages: Sequence[ExtractedPage]) -> None:
        if not pages:
            return

        records: List[Dict[str, Any]] = [
            to_document_page_record(
                page,
                document_id=self.document_id,
                organization_id=self.organization_id,
                project_id=self.project_id,
                extraction_run_id=self.extraction_run_id,
            )
            for page in pages
        ]
        await self.db[DOCUMENT_OCR_PAGES].bulk_write(
            [
                ReplaceOne(
                    {
                        "document_id": self.document_id,
                        "extraction_run_id": self.extraction_run_id,
                        "page_number": record["page_number"],
                    },
                    record,
                    upsert=True,
                )
                for record in records
            ]
        )

    async def publish_run(
        self, *, expected_page_numbers: Sequence[int], session: Any = None
    ) -> None:
        """Make a complete page-record version visible with one pointer write."""
        expected = sorted({int(number) for number in expected_page_numbers})
        count = await self.db[DOCUMENT_OCR_PAGES].count_documents(
            {
                "document_id": self.document_id,
                "extraction_run_id": self.extraction_run_id,
                "page_number": {"$in": expected},
            },
            session=session,
        )
        if count != len(expected):
            raise RuntimeError(
                f"Extraction run {self.extraction_run_id} has "
                f"{count}/{len(expected)} pages"
            )

        await self.db[DOCUMENT_EXTRACTION_HEADS].update_one(
            {"document_id": self.document_id},
            {
                "$set": {
                    "extraction_run_id": self.extraction_run_id,
                    "expected_page_numbers": expected,
                    "published_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
            session=session,
        )
