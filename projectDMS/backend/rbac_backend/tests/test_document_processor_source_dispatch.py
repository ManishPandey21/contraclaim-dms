"""Every admitted source kind must reach the extractor that can read it.

The old general path sent every admitted MIME into OCRService.process_pdf, so
a PNG entered a PDF reader and produced nothing while the document was marked
processed. These tests pin one dispatch point with no fall-through to PDF.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from rbac_backend.models.processing_state import ProcessingState
from rbac_backend.services.document_processor import (
    DocumentProcessor,
    UnsupportedSourceKindError,
)
from rbac_backend.services.extraction.models import (
    Completeness,
    ExtractedPage,
    PageClass,
    PageClassification,
    PageExtractionResult,
    PageSource,
    PageStatus,
    SourceKind,
)


def _result(text: str) -> PageExtractionResult:
    page = ExtractedPage(
        number=1,
        text=text,
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=PageClass.TEXT_NATIVE,
            char_count=len(text),
            image_count=0,
            image_coverage=0.0,
            table_count=0,
            width=0.0,
            height=0.0,
            rotation=0,
        ),
    )
    return PageExtractionResult(
        pages=[page],
        combined_text=text,
        ocr_pages_total=0,
        completeness=Completeness.COMPLETE,
    )


class _Counters:
    def __init__(self) -> None:
        self.pdf = 0
        self.image = 0
        self.text = 0

    @property
    def total(self) -> int:
        return self.pdf + self.image + self.text


def _processor(counters: _Counters) -> DocumentProcessor:
    processor = DocumentProcessor.__new__(DocumentProcessor)

    class _FakeOcrService:
        async def process_pdf_pagewise(self, path, **kwargs):
            counters.pdf += 1
            return _result("pdf text")

    async def fake_image(source, *, store, image_ocr_runner, language):
        counters.image += 1
        return _result("image text")

    async def fake_text(source, *, store):
        counters.text += 1
        return _result("plain text")

    from rbac_backend.services.extraction.source_kind import SourceKindRouter

    processor.ocr_service = _FakeOcrService()
    processor.source_kind_router = SourceKindRouter
    processor.image_ocr_runner = object()
    processor._image_extractor = fake_image
    processor._text_extractor = fake_text
    processor.config = SimpleNamespace(ocr_language="eng")
    return processor


class _NullStore:
    async def begin_batch(self, **kwargs):
        return "b1"

    async def finish_batch(self, batch_id, **kwargs):
        return None

    async def record_pages(self, pages):
        return None


@pytest.mark.parametrize(
    ("mime", "expected_kind", "called"),
    [
        ("application/pdf", SourceKind.PDF, "pdf"),
        ("image/png", SourceKind.IMAGE, "image"),
        ("image/jpeg", SourceKind.IMAGE, "image"),
        ("text/plain", SourceKind.TEXT, "text"),
    ],
)
async def test_dispatches_exactly_one_source_extractor(
    tmp_path: Path, mime: str, expected_kind: SourceKind, called: str
) -> None:
    counters = _Counters()
    processor = _processor(counters)
    source = tmp_path / "upload.bin"
    source.write_bytes(b"x")

    dispatch = await processor._extract_source(
        source, source_mime=mime, page_store=_NullStore(), retry_pages=None
    )

    assert dispatch.kind is expected_kind
    assert counters.total == 1
    assert getattr(counters, called) == 1


async def test_archive_is_stored_only_and_touches_no_extractor(
    tmp_path: Path,
) -> None:
    counters = _Counters()
    processor = _processor(counters)
    source = tmp_path / "bundle.zip"
    source.write_bytes(b"PK\x03\x04")

    dispatch = await processor._extract_source(
        source,
        source_mime="application/zip",
        page_store=_NullStore(),
        retry_pages=None,
    )

    assert dispatch.kind is SourceKind.ARCHIVE
    assert dispatch.processing_state is ProcessingState.STORED_ONLY
    assert dispatch.extraction is None
    assert counters.total == 0


async def test_rar_is_also_stored_only(tmp_path: Path) -> None:
    counters = _Counters()
    processor = _processor(counters)
    source = tmp_path / "bundle.rar"
    source.write_bytes(b"Rar!\x1a\x07\x00")

    dispatch = await processor._extract_source(
        source,
        source_mime="application/vnd.rar",
        page_store=_NullStore(),
        retry_pages=None,
    )

    assert dispatch.processing_state is ProcessingState.STORED_ONLY
    assert counters.total == 0


async def test_unsupported_mime_fails_before_any_extractor_runs(
    tmp_path: Path,
) -> None:
    counters = _Counters()
    processor = _processor(counters)
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"\xd0\xcf")

    with pytest.raises(UnsupportedSourceKindError):
        await processor._extract_source(
            source,
            source_mime="application/msword",
            page_store=_NullStore(),
            retry_pages=None,
        )

    assert counters.total == 0


async def test_missing_mime_is_unsupported_not_assumed_pdf(tmp_path: Path) -> None:
    # The old path's implicit default. It must not survive.
    counters = _Counters()
    processor = _processor(counters)
    source = tmp_path / "unknown.bin"
    source.write_bytes(b"x")

    with pytest.raises(UnsupportedSourceKindError):
        await processor._extract_source(
            source, source_mime=None, page_store=_NullStore(), retry_pages=None
        )

    assert counters.pdf == 0


async def test_detected_mime_beats_a_misleading_extension(tmp_path: Path) -> None:
    counters = _Counters()
    processor = _processor(counters)
    source = tmp_path / "invoice.pdf"  # named .pdf, sniffed as PNG
    source.write_bytes(b"\x89PNG\r\n\x1a\n")

    dispatch = await processor._extract_source(
        source, source_mime="image/png", page_store=_NullStore(), retry_pages=None
    )

    assert dispatch.kind is SourceKind.IMAGE
    assert counters.image == 1
    assert counters.pdf == 0


async def test_retry_pages_reach_only_the_pdf_extractor(tmp_path: Path) -> None:
    seen: dict[str, object] = {}
    counters = _Counters()
    processor = _processor(counters)

    class _RecordingOcr:
        async def process_pdf_pagewise(self, path, **kwargs):
            seen.update(kwargs)
            counters.pdf += 1
            return _result("pdf text")

    processor.ocr_service = _RecordingOcr()
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF-")

    await processor._extract_source(
        source,
        source_mime="application/pdf",
        page_store=_NullStore(),
        retry_pages=[3, 4],
    )

    assert seen["retry_pages"] == [3, 4]
