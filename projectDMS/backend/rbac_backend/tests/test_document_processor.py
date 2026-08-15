from types import SimpleNamespace

import pytest

from rbac_backend.services.document_processor import DocumentProcessor
from rbac_backend.services.extraction.models import (
    Completeness,
    ExtractedPage,
    PageClass,
    PageClassification,
    PageExtractionResult,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.quality.gate import ExtractionQualityGate
from rbac_backend.services.text_processing_service import TextProcessingService
from rbac_backend.utils.exceptions import DocumentProcessingError


class FakeOCRService:
    """Stands in for OCRService's page-wise extraction seam.

    The processor consumes a PageExtractionResult now rather than a
    (path, text) tuple, so this fake returns one carrying the same text.
    """

    def __init__(self, text: str):
        self.text = text

    async def process_pdf_pagewise(self, input_path, *, store, document_id, **_kwargs):
        page = ExtractedPage(
            number=1,
            text=self.text,
            source=PageSource.OCR,
            status=PageStatus.OCR_COMPLETED,
            classification=PageClassification(
                page_class=PageClass.SCANNED_IMAGE,
                char_count=len(self.text),
                image_count=1,
                image_coverage=1.0,
                table_count=0,
                width=595.0,
                height=842.0,
                rotation=0,
            ),
        )
        await store.record_pages([page])
        return PageExtractionResult(
            pages=[page],
            combined_text=self.text,
            ocr_pages_total=1,
            completeness=Completeness.COMPLETE,
        )


class FakeOpenAIService:
    def __init__(self, *, text_response: str = "", fail_text: bool = False):
        self.text_response = text_response
        self.fail_text = fail_text
        self.process_text_calls = 0
        self.upload_file_calls = 0

    async def process_text(self, document_text: str, *, filename=None):
        self.process_text_calls += 1
        if self.fail_text:
            raise DocumentProcessingError("OpenAI unavailable")
        return self.text_response

    async def upload_file(self, file_path: str):
        self.upload_file_calls += 1
        return "file-test"

    async def process_document(self, file_id: str):
        return self.text_response

    async def cleanup_file(self, file_id: str):
        return None


def make_processor(ocr_text: str, openai_service: FakeOpenAIService):
    async def close_connection():
        return None

    async def get_database():
        # The page store is exercised by its own suite; here it only needs a
        # collection-shaped object that accepts writes.
        class _Collection:
            async def bulk_write(self, requests):
                return None

            async def insert_one(self, document):
                return SimpleNamespace(inserted_id="batch-1")

            async def update_one(self, *args, **kwargs):
                return None

        class _Db:
            def __getitem__(self, name):
                return _Collection()

        return _Db()

    from rbac_backend.services.extraction.source_kind import SourceKindRouter

    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.config = SimpleNamespace(
        max_file_size_mb=100, chunk_size=3000, chunk_overlap=200, ocr_language="eng"
    )
    # __new__ skips __init__, so the dispatch seams must be supplied here.
    processor.source_kind_router = SourceKindRouter
    processor.image_ocr_runner = object()
    processor._image_extractor = None
    processor._text_extractor = None
    processor.quality_gate = ExtractionQualityGate()
    processor.fallback_ladder = None  # off by default; this test spends nothing
    processor.ocr_service = FakeOCRService(ocr_text)
    processor.openai_service = openai_service
    processor.text_service = TextProcessingService(processor.config)
    processor.database_service = SimpleNamespace(
        partial_failures={},
        close_connection=close_connection,
        get_database=get_database,
    )
    processor.file_service = SimpleNamespace()
    processor.pydantic_ai_service = SimpleNamespace(is_enabled=False)
    return processor


@pytest.mark.asyncio
async def test_processor_uses_ocr_text_without_openai_file_upload(tmp_path, monkeypatch):
    pdf_path = tmp_path / "ABC-001.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    report = "\n".join(
        [
            "1) Date: 09-07-2026",
            "2) Letter No.: ABC-001",
            "3) From (Company): Contractor",
            "4) To (Company): Employer",
            "5) Subject: Test OCR metadata",
            "22) Summary: - OCR metadata extracted",
            "25) Full Content: Full text",
        ]
    )
    openai_service = FakeOpenAIService(text_response=report)
    processor = make_processor("OCR text from scanned PDF", openai_service)
    captured = {}

    async def fake_save(extracted_content, raw_ocr_text, *args, **_kwargs):
        parsed_metadata = args[-1]
        captured["extracted_content"] = extracted_content
        captured["raw_ocr_text"] = raw_ocr_text
        captured["metadata"] = parsed_metadata
        return 0

    monkeypatch.setattr(processor, "_save_results", fake_save)

    result = await processor.process_document(str(pdf_path), "org/project", "incoming", "doc-1")

    assert result.success is True
    assert result.metadata_source == "openai_text_legacy_regex"
    assert result.metadata.letter_no == "ABC-001"
    assert captured["raw_ocr_text"] == "OCR text from scanned PDF"
    assert openai_service.process_text_calls == 1
    assert openai_service.upload_file_calls == 0


@pytest.mark.asyncio
async def test_processor_saves_ocr_fallback_when_ai_text_extraction_fails(tmp_path, monkeypatch):
    pdf_path = tmp_path / "AFC-PM-KNPCC-06-4930.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    ocr_text = "Date: 09-07-2026\nSub: Borewell execution delay\nBody text from OCR"
    openai_service = FakeOpenAIService(fail_text=True)
    processor = make_processor(ocr_text, openai_service)
    captured = {}

    async def fake_save(extracted_content, raw_ocr_text, *args, **_kwargs):
        parsed_metadata = args[-1]
        captured["extracted_content"] = extracted_content
        captured["raw_ocr_text"] = raw_ocr_text
        captured["metadata"] = parsed_metadata
        return 0

    monkeypatch.setattr(processor, "_save_results", fake_save)

    result = await processor.process_document(str(pdf_path), "org/project", "incoming", "doc-1")

    assert result.success is True
    assert result.metadata_source == "ocr_fallback_regex"
    assert result.metadata.letter_no == "AFC-PM-KNPCC-06-4930"
    assert result.metadata.subject == "Borewell execution delay Body text from OCR"
    assert captured["raw_ocr_text"] == ocr_text
    assert "ai_extraction" in result.partial_failures
    assert openai_service.process_text_calls == 1
    assert openai_service.upload_file_calls == 0
