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

from ..extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from ..extraction.page_store import InconsistentExtractionRunError

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
        # What extraction originally read, when a deterministic repair changed
        # the published text. None means text and original are the same.
        # Kept separate so a repaired page stays auditable: `raw_text` above is
        # the published representation, this is the document's own wording.
        "original_text": page.raw_text,
        "applied_repairs": list(page.applied_repairs),
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


class MalformedPageRecordError(ValueError):
    """A persisted page row cannot be rebuilt into a page.

    Distinct from a missing row and deliberately not defaulted away: a row
    whose status, source or class is absent or unrecognised carries no usable
    claim about whether that page is resolved, and guessing one would be the
    same silent corruption as overwriting it.
    """

    def __init__(self, *, page_number: int, field: str, value: Any) -> None:
        self.page_number = page_number
        self.field = field
        super().__init__(
            f"page {page_number} has an unusable {field} value {value!r}"
        )


def _coerce(enum_type: Any, value: Any, *, page_number: int, field: str) -> Any:
    try:
        return enum_type(value)
    except (ValueError, TypeError) as exc:
        raise MalformedPageRecordError(
            page_number=page_number, field=field, value=value
        ) from exc


def from_document_page_record(record: Dict[str, Any]) -> ExtractedPage:
    """Rebuild the page an earlier attempt recorded, with its evidence intact.

    The inverse of `to_document_page_record`, and it has to be exact: a page
    rehydrated as native text would lose the OCR status, the batch that ran it
    and the repairs that were adopted - which is the same corruption as
    overwriting the row. `raw_text` on the record is the published text;
    `original_text` is what extraction first read, and maps back to the page's
    own `raw_text`.
    """
    number = int(record.get("page_number") or 0)
    classification = PageClassification(
        page_class=_coerce(
            PageClass,
            record.get("page_class"),
            page_number=number,
            field="page_class",
        ),
        char_count=int(record.get("char_count") or 0),
        image_count=int(record.get("image_count") or 0),
        image_coverage=float(record.get("image_coverage") or 0.0),
        table_count=int(record.get("table_count") or 0),
        width=float(record.get("width") or 0.0),
        height=float(record.get("height") or 0.0),
        rotation=int(record.get("rotation") or 0),
    )
    return ExtractedPage(
        number=number,
        text=record.get("raw_text") or "",
        source=_coerce(
            PageSource, record.get("source"), page_number=number, field="source"
        ),
        status=_coerce(
            PageStatus, record.get("status"), page_number=number, field="status"
        ),
        classification=classification,
        # Copied, not aliased: a carried page must not share structure with the
        # record it was read from, or a later in-place edit would silently
        # rewrite evidence nobody meant to touch.
        tables=[[list(row) for row in table] for table in (record.get("tables") or [])],
        batch_id=record.get("batch_id"),
        error=record.get("error"),
        quality_verdict=record.get("quality_verdict"),
        quality_checks=[dict(check) for check in (record.get("quality_checks") or [])],
        needs_review=bool(record.get("needs_review")),
        raw_text=record.get("original_text"),
        applied_repairs=[
            dict(repair) for repair in (record.get("applied_repairs") or [])
        ],
    )


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

    async def finalize_pages(self, pages: Sequence[ExtractedPage]) -> None:
        """Persist the assessed page evidence, after the gate and any repair.

        A deliberate second stage, not an afterthought. `record_pages` runs
        inside the engine and captures what extraction read; at that moment no
        verdict exists, so `quality_verdict` was always written as None and the
        canary's own monitoring query would have reported every page as unset.

        Idempotent by construction: the same ReplaceOne upsert on
        (document_id, extraction_run_id, page_number), so a retry rewrites the
        same identity rather than creating a second contradictory row, and the
        unique index stays valid.
        """
        await self.record_pages(pages)

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

    async def load_run_pages(self) -> List[ExtractedPage]:
        """Every page this run has recorded so far, in page-number order.

        Scoped to (document_id, extraction_run_id) and nothing else. It
        deliberately does not consult `document_extraction_heads`: the head is
        the last *published* run, while a retry is assembling the in-progress
        one, and following the head would rebuild a different run's pages.
        """
        cursor = self.db[DOCUMENT_OCR_PAGES].find(
            {
                "document_id": self.document_id,
                "extraction_run_id": self.extraction_run_id,
            }
        )
        records = sorted(
            await cursor.sort("page_number", 1).to_list(length=None),
            key=lambda item: item.get("page_number") or 0,
        )

        pages: List[ExtractedPage] = []
        malformed: List[int] = []
        for record in records:
            try:
                pages.append(from_document_page_record(record))
            except MalformedPageRecordError as exc:
                malformed.append(exc.page_number)
        if malformed:
            # Fail closed with the same typed error a missing row raises: a row
            # that cannot be read is not a page that can be carried, and
            # dropping it would silently shorten the run.
            raise InconsistentExtractionRunError(
                extraction_run_id=self.extraction_run_id,
                expected_page_numbers=[
                    int(record.get("page_number") or 0) for record in records
                ],
                persisted_page_numbers=[page.number for page in pages],
                missing_page_numbers=malformed,
                reason="page records in this run cannot be read",
            )
        return pages

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
