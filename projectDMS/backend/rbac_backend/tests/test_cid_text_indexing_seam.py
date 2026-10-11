"""Unusable (cid:N) text must never reach the embedding/indexing seam.

Drives ``DocumentProcessor.process_document`` on the unified pipeline with the
real ``OCRService``, the real ``PageExtractionEngine`` and the real quality
gate over a PDF whose text layer is a composite font with no ``/ToUnicode``.
Only OCR itself (a stub runner) and persistence are faked. What is asserted is
the text handed to ``DatabaseService.save_document_data`` - the call that
writes ``full_text`` and creates the embeddings - and to the OpenAI text
extraction that feeds metadata and summaries.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.models.document_metadata import ParsedDocumentMetadata
from rbac_backend.services.document_processor import DocumentProcessor
from rbac_backend.services.extraction.quality.gate import ExtractionQualityGate
from rbac_backend.services.extraction.source_kind import SourceKindRouter
from rbac_backend.services.ocr_service import OCRService
from rbac_backend.services.pipeline_routing import UNIFIED_PIPELINE
from rbac_backend.tests.fixtures.pdf_builders import build_composite_font_pdf

_BODY = [
    "The Contractor hereby gives notice of a claim for additional payment",
    "arising from the suspension of the works instructed by the Engineer",
    "under Sub-Clause 8.8, with particulars to follow within 28 days.",
]


class _Runner:
    def __init__(self, *, text: str | None = None, fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.requested: List[List[int]] = []

    async def run(
        self, source: Path, page_numbers: Sequence[int], language: str
    ) -> Dict[int, str]:
        self.requested.append(list(page_numbers))
        if self.fail:
            raise RuntimeError("OCRmyPDF batch failed (exit=2)")
        return {page: self.text or "" for page in page_numbers}


class _PagewiseOcr:
    """The real OCRService, with OCR availability and the runner pinned."""

    def __init__(self, *, available: bool, runner: _Runner) -> None:
        config = DocumentProcessingConfig()
        config.ocr_enabled = True
        config.contract_ocr_min_text_chars_per_page = 40
        self.service = OCRService.__new__(OCRService)
        self.service.config = config
        self.service._ocr_available = available
        self.runner = runner

    async def process_pdf_pagewise(self, path: Path, **kwargs: Any):
        kwargs["ocr_runner"] = self.runner
        return await self.service.process_pdf_pagewise(path, **kwargs)


class _Cursor:
    """Just enough of a Motor cursor for the store's run-scoped read."""

    def __init__(self, records: List[Dict[str, Any]]) -> None:
        self._records = records

    def sort(self, field: str, direction: int) -> "_Cursor":
        return _Cursor(
            sorted(self._records, key=lambda record: record.get(field) or 0)
        )

    async def to_list(self, length: Any = None) -> List[Dict[str, Any]]:
        return list(self._records)


class _Collection:
    """Records what the store writes, so a run-scoped read can find it.

    The engine assembles a cumulative run: it reads back the pages this run
    already holds before deciding what an attempt may change. A fake that
    cannot answer that read makes every attempt look like a first attempt.
    """

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []

    async def bulk_write(self, operations: Any) -> None:
        for operation in operations or []:
            # ReplaceOne carries the replacement document itself; an update
            # operation carries a $set. Both shapes are accepted so this fake
            # cannot silently record nothing.
            document = getattr(operation, "_doc", None)
            if not isinstance(document, dict):
                continue
            record = document.get("$set") if "$set" in document else document
            if isinstance(record, dict) and "page_number" in record:
                self.records = [
                    existing
                    for existing in self.records
                    if existing.get("page_number") != record["page_number"]
                ]
                self.records.append(dict(record))
        return None

    async def update_one(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def insert_one(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(inserted_id="batch-1")

    def find(self, query: Dict[str, Any]) -> _Cursor:
        return _Cursor(
            [
                record
                for record in self.records
                if all(record.get(key) == value for key, value in (query or {}).items())
            ]
        )

    async def find_one(self, query: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # Publishing canonical evidence reads the previous head for its revision.
        return None


class _Db:
    def __init__(self) -> None:
        self._collections: Dict[str, _Collection] = {}

    def _collection(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._collection(name)

    def __getitem__(self, name: str) -> _Collection:
        return self._collection(name)


class _Database:
    def __init__(self) -> None:
        self.saved: List[Dict[str, Any]] = []
        self._db: Any = None

    async def get_database(self) -> _Db:
        if self._db is None:
            self._db = _Db()
        return self._db

    async def save_document_data(self, **kwargs: Any) -> int:
        self.saved.append(kwargs)
        return 1

    async def close_connection(self) -> None:
        return None


class _OpenAI:
    def __init__(self, *, upload_reply: str = "REPORT<uploaded>") -> None:
        self.texts: List[str] = []
        self.uploads: List[str] = []
        self.upload_reply = upload_reply

    async def process_text(self, text: str, *, filename: str, include_full_content: bool = True) -> str:
        self.texts.append(text)
        return "REPORT"

    async def upload_file(self, path: str) -> str:
        self.uploads.append(path)
        return "file-1"

    async def process_document(self, file_id: str) -> str:
        return self.upload_reply

    async def cleanup_file(self, file_id: str) -> None:
        return None


def _processor(ocr: _PagewiseOcr, openai: _OpenAI | None = None) -> DocumentProcessor:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.ocr_service = ocr
    processor.openai_service = openai or _OpenAI()
    processor.text_service = SimpleNamespace(
        parse_extraction_report=lambda content: ParsedDocumentMetadata(
            subject="parsed"
        )
    )
    processor.file_service = SimpleNamespace(
        save_summary=_async_none,
    )
    processor.database_service = _Database()
    processor.pydantic_ai_service = SimpleNamespace(is_enabled=False)
    processor.quality_gate = ExtractionQualityGate()
    processor.fallback_ladder = None
    processor.fallback_max_pages_per_document = 0
    processor.intervention_ledger = None
    processor.source_kind_router = SourceKindRouter
    processor.image_ocr_runner = object()
    processor.resolve_fallback_ladder = lambda kind, db=None: None
    processor.config = SimpleNamespace(
        max_file_size_mb=50,
        ocr_language="eng",
        use_pydantic_ai=False,
        openai_api_key=None,
        process_dir="/processed",
    )
    return processor


async def _async_none(*args: Any, **kwargs: Any) -> None:
    return None


async def _run(
    tmp_path: Path,
    ocr: _PagewiseOcr,
    openai: _OpenAI | None = None,
    source: Path | None = None,
) -> DocumentProcessor:
    source = source or build_composite_font_pdf(
        tmp_path / "notice.pdf", composite_lines=_BODY
    )
    processor = _processor(ocr, openai)
    await processor.process_document(
        pdf_path=str(source),
        path_structure="org/proj",
        upload_type="incoming",
        document_id="doc-1",
        organization_id="org-1",
        source_mime="application/pdf",
        pipeline_version=UNIFIED_PIPELINE,
    )
    return processor


def _indexed_text(processor: DocumentProcessor) -> str:
    database: _Database = processor.database_service  # type: ignore[assignment]
    openai: _OpenAI = processor.openai_service  # type: ignore[assignment]
    # Positive control: persistence was really reached. Without it an early
    # failure would make every "no placeholders" assertion pass vacuously.
    assert len(database.saved) == 1
    saved = [
        str(call.get(key) or "")
        for call in database.saved
        for key in ("full_text", "embedding_text")
    ]
    saved += [str(call.get("parsed_metadata")) for call in database.saved]
    return "\n".join(saved + openai.texts)


async def test_ocr_unavailable_puts_no_placeholders_into_the_index(
    tmp_path: Path,
) -> None:
    processor = await _run(
        tmp_path, _PagewiseOcr(available=False, runner=_Runner(text="unused"))
    )

    assert "(cid:" not in _indexed_text(processor)


async def test_failed_ocr_puts_no_placeholders_into_the_index(tmp_path: Path) -> None:
    runner = _Runner(fail=True)
    processor = await _run(tmp_path, _PagewiseOcr(available=True, runner=runner))

    assert runner.requested == [[1]]
    assert "(cid:" not in _indexed_text(processor)


async def test_unusable_ocr_output_puts_no_placeholders_into_the_index(
    tmp_path: Path,
) -> None:
    garbage = "".join(f"(cid:{ord(character)})" for character in _BODY[0])
    processor = await _run(
        tmp_path, _PagewiseOcr(available=True, runner=_Runner(text=garbage))
    )

    assert "(cid:" not in _indexed_text(processor)


async def test_successful_ocr_text_is_what_gets_indexed(tmp_path: Path) -> None:
    recovered = "The Contractor hereby gives notice of a claim for additional payment"
    processor = await _run(
        tmp_path, _PagewiseOcr(available=True, runner=_Runner(text=recovered))
    )

    indexed = _indexed_text(processor)
    assert recovered in indexed
    assert "(cid:" not in indexed


_GARBAGE_REPLY = "Subject: " + "".join(f"(cid:{ord(c)})" for c in _BODY[1])


async def test_a_withheld_text_layer_is_not_sent_to_openai_as_a_whole_pdf(
    tmp_path: Path,
) -> None:
    """Found in review: with every page withheld, combined_text was empty and
    the processor uploaded the original PDF to OpenAI instead - whose reply is
    read from the same unmapped text layer and was stored unchecked."""
    openai = _OpenAI(upload_reply=_GARBAGE_REPLY)
    processor = await _run(
        tmp_path, _PagewiseOcr(available=False, runner=_Runner()), openai=openai
    )

    assert openai.uploads == []
    assert "(cid:" not in _indexed_text(processor)


async def test_an_unusable_whole_pdf_reply_is_not_persisted(tmp_path: Path) -> None:
    # A textless scan with OCR unavailable still takes the whole-PDF route (no
    # page was withheld); its reply must pass the same canonical check.
    from rbac_backend.tests.fixtures.pdf_builders import build_scanned_only_pdf

    openai = _OpenAI(upload_reply=_GARBAGE_REPLY)
    processor = await _run(
        tmp_path,
        _PagewiseOcr(available=False, runner=_Runner()),
        openai=openai,
        source=build_scanned_only_pdf(tmp_path / "scan.pdf", pages=1),
    )

    assert len(openai.uploads) == 1
    assert "(cid:" not in _indexed_text(processor)
