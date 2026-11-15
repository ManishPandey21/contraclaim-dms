"""The legacy OCR path must never accept ``(cid:N)`` placeholder text as text.

``PageExtractionEngine`` already routes a page whose native text is unusable
(``services/extraction/text_quality.py``) to OCR. The legacy path -
``OCRService.process_pdf``, reached from ``DocumentProcessor._extract_legacy``
(``pipeline_version=legacy_v0``, the default for every job not on the unified
canary) and from ``MetadataProcessorService`` - decided with
``text.strip()`` alone. A composite (Type0, Identity-H) font without
``/ToUnicode`` extracts as ``(cid:67)(cid:108)...`` in any interpreter, so such a
PDF was classified textual, never OCR'd, and its placeholders were returned as
the document's text.

These tests pin the legacy contract against the same canonical quality policy
the pagewise engine uses:

* unusable native text is not a text layer; it requires OCR, and OCRmyPDF must
  be told to replace it (``force_ocr=True``) rather than refuse it as prior OCR;
* a replacement that fails, or whose output is itself unusable, fails visibly
  with a typed ``DocumentProcessingError`` - it never falls back to the
  unusable original; and
* ordinary textual and scanned PDFs keep their historical behaviour.

OCRmyPDF runs in a child process; tests replace that one seam
(``OCRService._ocrmypdf``) with a fake that records the call and writes, or
fails, the way the real exit codes do. The real import order is covered by
the child-interpreter test at the bottom.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List

import pdfplumber
import pikepdf
import pytest

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.extraction.text_quality import assess_native_text_quality
from rbac_backend.services.ocr_service import (
    OCRService,
    OcrMyPdfEncrypted,
    OcrMyPdfPriorOcrFound,
)
from rbac_backend.services.pipeline_routing import LEGACY_PIPELINE
from rbac_backend.tests.fixtures.child_python import BACKEND_ROOT, run_python_child
from rbac_backend.tests.fixtures.pdf_builders import (
    build_composite_font_pdf,
    build_scanned_only_pdf,
    build_text_pdf,
)
from rbac_backend.utils.exceptions import DocumentProcessingError

LETTER_LINES = [
    "Notice of claim for extension of time",
    "Delay event seven affected the viaduct works",
    "The contractor reserves all rights under clause 44",
]
OCR_TEXT = "Recovered by OCR notice of claim"


# --- helpers -------------------------------------------------------------------


def _cid_pdf(path: Path) -> Path:
    """The ambiguous shape: every glyph extracts as ``(cid:N)``."""
    return build_composite_font_pdf(path, composite_lines=LETTER_LINES)


def _concat(path: Path, *parts: Path) -> Path:
    """One PDF made of every page of `parts`, in order."""
    sources = [pikepdf.open(part) for part in parts]
    try:
        combined = pikepdf.new()
        for source in sources:
            combined.pages.extend(source.pages)
        combined.save(path)
    finally:
        for source in sources:
            source.close()
    return path


def _page_texts(path: Path) -> List[str]:
    with pdfplumber.open(path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# OCRService maps OCRmyPDF's exit codes 6 and 8 onto these.
_PriorOcrFoundError = OcrMyPdfPriorOcrFound
_EncryptedPdfError = OcrMyPdfEncrypted


class FakeOcrmypdf:
    """Records every OCRmyPDF run; `behaviour` decides the outcome.

    `behaviour` receives the call's kwargs and either writes ``output_file``
    or raises.
    """

    def __init__(self, behaviour: Callable[[Dict[str, Any]], None]) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._behaviour = behaviour

    def ocr(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        self._behaviour(kwargs)

    @property
    def force_flags(self) -> List[Any]:
        return [call.get("force_ocr") for call in self.calls]


def _writes(source: Path) -> Callable[[Dict[str, Any]], None]:
    def behaviour(kwargs: Dict[str, Any]) -> None:
        shutil.copyfile(source, kwargs["output_file"])

    return behaviour


def _raises(error: BaseException) -> Callable[[Dict[str, Any]], None]:
    def behaviour(kwargs: Dict[str, Any]) -> None:
        raise error

    return behaviour


def _prior_ocr_unless_forced(source: Path) -> Callable[[Dict[str, Any]], None]:
    """OCRmyPDF's real contract: text anywhere + no force -> PriorOcrFoundError."""

    def behaviour(kwargs: Dict[str, Any]) -> None:
        if not kwargs.get("force_ocr"):
            raise _PriorOcrFoundError("page already has text")
        shutil.copyfile(source, kwargs["output_file"])

    return behaviour


@pytest.fixture
def install_ocrmypdf(monkeypatch: pytest.MonkeyPatch):
    """Replace the one seam that runs OCRmyPDF (a child process)."""

    def install(behaviour: Callable[[Dict[str, Any]], None]) -> FakeOcrmypdf:
        fake = FakeOcrmypdf(behaviour)

        def run(
            self,
            input_path: Path,
            output_path: Path,
            *,
            force_ocr: bool,
            pages: Any = None,
        ) -> None:
            fake.ocr(
                input_file=str(input_path),
                output_file=str(output_path),
                force_ocr=force_ocr,
                pages=list(pages) if pages else None,
            )

        monkeypatch.setattr(OCRService, "_ocrmypdf", run)
        return fake

    return install


def _service(tmp_path: Path, *, ocr_available: bool = True) -> OCRService:
    config = DocumentProcessingConfig()
    config.process_dir = str(tmp_path / "processed")
    service = OCRService(config)
    # The OCR binaries are absent on the dev host; the fake module stands in.
    service._ocr_available = ocr_available
    return service


@pytest.fixture
def ocr_output(tmp_path: Path) -> Path:
    """What a successful OCR run writes: a readable text layer."""
    return build_text_pdf(tmp_path / "ocr_output.pdf", pages=1, text=OCR_TEXT)


# --- the fixture really is the hazardous shape ----------------------------------


def test_the_composite_font_fixture_extracts_as_unusable_placeholders(
    tmp_path: Path,
) -> None:
    (text,) = _page_texts(_cid_pdf(tmp_path / "cid.pdf"))

    assert "(cid:" in text
    assert assess_native_text_quality(text).unusable


# --- is_pdf_textual (matrix 1-4) ------------------------------------------------


def test_a_valid_native_pdf_is_textual(tmp_path: Path) -> None:
    assert _service(tmp_path).is_pdf_textual(build_text_pdf(tmp_path / "t.pdf")) is True


def test_a_pure_scan_is_not_textual(tmp_path: Path) -> None:
    source = build_scanned_only_pdf(tmp_path / "scan.pdf", pages=2)
    assert _service(tmp_path).is_pdf_textual(source) is False


def test_a_cid_dominated_pdf_is_not_textual(tmp_path: Path) -> None:
    assert _service(tmp_path).is_pdf_textual(_cid_pdf(tmp_path / "cid.pdf")) is False


def test_readable_pages_do_not_vouch_for_an_unusable_one(tmp_path: Path) -> None:
    source = _concat(
        tmp_path / "mixed.pdf",
        build_text_pdf(tmp_path / "t.pdf", pages=2, text="Readable covering letter"),
        _cid_pdf(tmp_path / "cid.pdf"),
    )
    assert _service(tmp_path).is_pdf_textual(source) is False


@pytest.mark.parametrize(
    "composite_lines",
    [
        pytest.param(["X"], id="one-isolated-placeholder"),
        pytest.param(["ABCDEFGHIJ"], id="a-ten-glyph-unreadable-run"),
    ],
)
def test_isolated_placeholders_follow_the_canonical_policy(
    tmp_path: Path, composite_lines: List[str]
) -> None:
    source = build_composite_font_pdf(
        tmp_path / "mostly_text.pdf",
        composite_lines=composite_lines,
        text_lines=LETTER_LINES * 3,
    )
    (text,) = _page_texts(source)
    usable = not assess_native_text_quality(text).unusable

    assert _service(tmp_path).is_pdf_textual(source) is usable


def test_the_isolated_placeholder_case_really_is_usable(tmp_path: Path) -> None:
    """Guards the parametrised test above against passing vacuously."""
    source = build_composite_font_pdf(
        tmp_path / "one.pdf", composite_lines=["X"], text_lines=LETTER_LINES * 3
    )
    (text,) = _page_texts(source)

    assert "(cid:" in text
    assert not assess_native_text_quality(text).unusable
    assert _service(tmp_path).is_pdf_textual(source) is True


# --- process_pdf: force OCR, post-OCR quality (matrix 5, 9, 10) -----------------


async def test_a_cid_pdf_is_force_ocrd_and_returns_the_ocr_text(
    tmp_path: Path, install_ocrmypdf, ocr_output: Path
) -> None:
    fake = install_ocrmypdf(_prior_ocr_unless_forced(ocr_output))
    source = _cid_pdf(tmp_path / "cid.pdf")

    processed, text = await _service(tmp_path).process_pdf(source)

    assert fake.force_flags == [True]
    assert text is not None and OCR_TEXT in text
    assert "(cid:" not in text
    assert "(cid:" not in "".join(_page_texts(processed))


async def test_ocr_output_that_is_itself_unusable_fails_visibly(
    tmp_path: Path, install_ocrmypdf
) -> None:
    still_cid = _cid_pdf(tmp_path / "still_cid.pdf")
    install_ocrmypdf(_writes(still_cid))
    source = _cid_pdf(tmp_path / "cid.pdf")
    service = _service(tmp_path)

    with pytest.raises(DocumentProcessingError, match="unusable"):
        await service.process_pdf(source)

    processed_dir = Path(service.config.process_dir)
    assert not (processed_dir / source.name).exists()
    assert not (processed_dir / source.with_suffix(".txt").name).exists()


async def test_a_scanned_pdf_keeps_the_unforced_ocr_run(
    tmp_path: Path, install_ocrmypdf, ocr_output: Path
) -> None:
    fake = install_ocrmypdf(_writes(ocr_output))
    source = build_scanned_only_pdf(tmp_path / "scan.pdf", pages=2)

    _, text = await _service(tmp_path).process_pdf(source)

    assert fake.force_flags == [False]
    assert text is not None and OCR_TEXT in text


async def test_a_textual_pdf_is_not_ocrd(tmp_path: Path, install_ocrmypdf) -> None:
    fake = install_ocrmypdf(_raises(AssertionError("OCR must not run")))
    source = build_text_pdf(tmp_path / "t.pdf", pages=2, text="Native letter body")

    _, text = await _service(tmp_path).process_pdf(source)

    assert fake.calls == []
    assert text is not None and "Native letter body" in text


async def test_an_unusable_page_past_the_inspected_pages_forces_ocr(
    tmp_path: Path, install_ocrmypdf, ocr_output: Path
) -> None:
    """The legacy decision inspects five pages; the sidecar reads them all."""
    fake = install_ocrmypdf(_prior_ocr_unless_forced(ocr_output))
    source = _concat(
        tmp_path / "long.pdf",
        build_text_pdf(tmp_path / "t.pdf", pages=5, text="Readable body page"),
        _cid_pdf(tmp_path / "cid.pdf"),
    )

    _, text = await _service(tmp_path).process_pdf(source)

    assert fake.force_flags == [True]
    assert text is not None and "(cid:" not in text


async def test_prior_text_on_a_scan_escalates_to_forced_ocr_when_it_is_unusable(
    tmp_path: Path, install_ocrmypdf, ocr_output: Path
) -> None:
    """Five scanned pages, then a CID page: unforced OCR refuses the text layer."""
    fake = install_ocrmypdf(_prior_ocr_unless_forced(ocr_output))
    source = _concat(
        tmp_path / "scan_then_cid.pdf",
        build_scanned_only_pdf(tmp_path / "scan.pdf", pages=5),
        _cid_pdf(tmp_path / "cid.pdf"),
    )

    _, text = await _service(tmp_path).process_pdf(source)

    assert fake.force_flags == [False, True]
    assert text is not None and "(cid:" not in text


async def test_prior_text_on_a_scan_still_uses_a_usable_original(
    tmp_path: Path, install_ocrmypdf, ocr_output: Path
) -> None:
    """Historical behaviour kept: prior *usable* text -> the original is used."""
    fake = install_ocrmypdf(_prior_ocr_unless_forced(ocr_output))
    source = _concat(
        tmp_path / "scan_then_text.pdf",
        build_scanned_only_pdf(tmp_path / "scan.pdf", pages=5),
        build_text_pdf(tmp_path / "t.pdf", pages=1, text="Typed annex page"),
    )

    _, text = await _service(tmp_path).process_pdf(source)

    assert fake.force_flags == [False]
    assert text is not None and "Typed annex page" in text


# --- failure semantics (matrix 6, 7, 11) ----------------------------------------


async def test_prior_ocr_found_on_the_cid_path_fails_visibly(
    tmp_path: Path, install_ocrmypdf
) -> None:
    install_ocrmypdf(_raises(_PriorOcrFoundError("page already has text")))
    source = _cid_pdf(tmp_path / "cid.pdf")
    before = _digest(source)
    service = _service(tmp_path)

    with pytest.raises(DocumentProcessingError, match="unusable"):
        await service.process_pdf(source)

    assert _digest(source) == before
    assert not (Path(service.config.process_dir) / source.name).exists()


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(RuntimeError("tesseract crashed"), id="generic"),
        pytest.param(_EncryptedPdfError("encrypted"), id="encrypted"),
        pytest.param(DocumentProcessingError("typed"), id="typed-processing-error"),
    ],
)
async def test_an_ocr_failure_on_the_cid_path_never_falls_back_to_the_original(
    tmp_path: Path, install_ocrmypdf, error: BaseException
) -> None:
    install_ocrmypdf(_raises(error))
    source = _cid_pdf(tmp_path / "cid.pdf")
    before = _digest(source)
    service = _service(tmp_path)

    with pytest.raises(DocumentProcessingError, match="unusable"):
        await service.process_pdf(source)

    assert _digest(source) == before
    assert not (Path(service.config.process_dir) / source.name).exists()


async def test_an_ocr_failure_on_a_scan_keeps_the_historical_fallback(
    tmp_path: Path, install_ocrmypdf
) -> None:
    """Not every OCR failure becomes a hard failure: scans keep copy + None."""
    install_ocrmypdf(_raises(RuntimeError("tesseract crashed")))
    source = build_scanned_only_pdf(tmp_path / "scan.pdf", pages=2)
    service = _service(tmp_path)

    processed, text = await service.process_pdf(source)

    assert text is None
    assert _digest(processed) == _digest(source)


async def test_ocr_unavailable_does_not_pass_a_cid_pdf_through(
    tmp_path: Path, install_ocrmypdf
) -> None:
    fake = install_ocrmypdf(_raises(AssertionError("OCR must not run")))
    source = _cid_pdf(tmp_path / "cid.pdf")

    with pytest.raises(DocumentProcessingError, match="unusable"):
        await _service(tmp_path, ocr_available=False).process_pdf(source)

    assert fake.calls == []


async def test_ocr_unavailable_checks_every_page_not_just_the_first_five(
    tmp_path: Path, install_ocrmypdf
) -> None:
    """Found in review: five readable pages hid a sixth unusable one.

    With OCR available the sidecar reads every page and escalates. Without OCR
    there is no sidecar, so the copy-and-return-None branch must judge the
    whole document itself - otherwise the original, CID page and all, is
    handed on as a processed PDF.
    """
    fake = install_ocrmypdf(_raises(AssertionError("OCR must not run")))
    source = _concat(
        tmp_path / "long.pdf",
        build_text_pdf(tmp_path / "t.pdf", pages=5, text="Readable body page"),
        _cid_pdf(tmp_path / "cid.pdf"),
    )

    with pytest.raises(DocumentProcessingError, match="unusable"):
        await _service(tmp_path, ocr_available=False).process_pdf(source)

    assert fake.calls == []


async def test_ocr_unavailable_still_copies_an_ordinary_pdf(tmp_path: Path) -> None:
    source = build_text_pdf(tmp_path / "t.pdf")

    processed, text = await _service(tmp_path, ocr_available=False).process_pdf(source)

    assert text is None
    assert _digest(processed) == _digest(source)


# --- the sidecar extractor (matrix 8, phase 9) ----------------------------------


async def test_the_sidecar_rejects_placeholder_text(tmp_path: Path) -> None:
    source = _cid_pdf(tmp_path / "cid.pdf")
    sidecar = tmp_path / "cid.txt"

    with pytest.raises(DocumentProcessingError, match="unusable"):
        await _service(tmp_path)._extract_sidecar_text(source, sidecar)

    assert not sidecar.exists()


async def test_the_sidecar_keeps_readable_text(tmp_path: Path) -> None:
    source = build_text_pdf(tmp_path / "t.pdf", pages=2, text="Readable sidecar")
    sidecar = tmp_path / "t.txt"

    text = await _service(tmp_path)._extract_sidecar_text(source, sidecar)

    assert text is not None and "Readable sidecar" in text
    assert "Readable sidecar" in sidecar.read_text(encoding="utf-8")


async def test_no_customer_text_reaches_the_failure_message_or_logs(
    tmp_path: Path, install_ocrmypdf, caplog: pytest.LogCaptureFixture
) -> None:
    install_ocrmypdf(_raises(RuntimeError("tesseract crashed")))
    source = _cid_pdf(tmp_path / "cid.pdf")

    with caplog.at_level("DEBUG"):
        with pytest.raises(DocumentProcessingError) as raised:
            await _service(tmp_path).process_pdf(source)

    everything = str(raised.value) + "\n".join(r.getMessage() for r in caplog.records)
    assert "(cid:" not in everything
    for line in LETTER_LINES:
        assert line not in everything


# --- legacy callers (matrix 12-14) -----------------------------------------------


def _legacy_processor(service: OCRService):
    from rbac_backend.tests.test_legacy_pipeline_equivalence import _build_processor

    processor = _build_processor("text_native")
    processor.ocr_service = service
    return processor


async def test_the_legacy_document_pipeline_reports_failure_not_placeholders(
    tmp_path: Path, install_ocrmypdf
) -> None:
    install_ocrmypdf(_raises(RuntimeError("tesseract crashed")))
    source = _cid_pdf(tmp_path / "cid.pdf")
    processor = _legacy_processor(_service(tmp_path))

    result = await processor.process_document(
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        document_id="doc-1",
        pipeline_version=LEGACY_PIPELINE,
    )

    assert result.success is False
    assert result.publishable is False
    assert "unusable" in (result.error or "")
    assert processor.database_service.saved == []
    assert processor.openai_service.process_text_calls == []
    assert processor.openai_service.upload_calls == []


async def test_the_legacy_document_pipeline_indexes_ocr_text_for_a_cid_pdf(
    tmp_path: Path, install_ocrmypdf, ocr_output: Path
) -> None:
    install_ocrmypdf(_prior_ocr_unless_forced(ocr_output))
    source = _cid_pdf(tmp_path / "cid.pdf")
    processor = _legacy_processor(_service(tmp_path))

    result = await processor.process_document(
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        document_id="doc-1",
        pipeline_version=LEGACY_PIPELINE,
    )

    assert result.success is True
    (saved,) = processor.database_service.saved
    assert OCR_TEXT in saved["full_text"] and OCR_TEXT in saved["embedding_text"]
    assert "(cid:" not in saved["full_text"] + saved["embedding_text"]
    assert all(
        "(cid:" not in text for text in processor.openai_service.process_text_calls
    )


class _FakeOpenAIService:
    uploads: List[Path] = []

    def __init__(self, config: Any) -> None:
        pass

    def upload_file(self, path: Any) -> str:
        _FakeOpenAIService.uploads.append(Path(path))
        return "file-1"

    def extract_document_metadata(self, file_id: str) -> str:
        return "REPORT"

    def cleanup_file(self, file_id: str) -> None:
        return None


class _FakeTextService:
    def __init__(self, config: Any) -> None:
        pass

    def parse_extraction_report(self, content: str) -> Any:
        return SimpleNamespace(
            date=None,
            subject=None,
            letter_no=None,
            from_company=None,
            to_company=None,
            references=None,
            summary=None,
            keywords=None,
            contractual_clauses=None,
            full_content=None,
        )


def _metadata_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from rbac_backend.services import openai_service, text_processing_service
    from rbac_backend.services.metadata_processor_service import (
        MetadataProcessorService,
    )

    _FakeOpenAIService.uploads = []
    monkeypatch.setattr(openai_service, "OpenAIService", _FakeOpenAIService)
    monkeypatch.setattr(
        text_processing_service, "TextProcessingService", _FakeTextService
    )
    monkeypatch.setattr(OCRService, "_check_ocr_availability", lambda self: True)

    service = MetadataProcessorService.__new__(MetadataProcessorService)
    config = DocumentProcessingConfig()
    config.process_dir = str(tmp_path / "processed")
    service.config = config
    return service


async def test_metadata_extraction_does_not_return_placeholder_text(
    tmp_path: Path, install_ocrmypdf, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_ocrmypdf(_raises(RuntimeError("tesseract crashed")))
    source = _cid_pdf(tmp_path / "cid.pdf")

    result = await _metadata_service(tmp_path, monkeypatch).extract_metadata_only(
        str(source)
    )

    assert result["success"] is False
    assert "unusable" in result["error"]
    assert "(cid:" not in repr(result)
    assert _FakeOpenAIService.uploads == []


async def test_metadata_extraction_uses_the_ocr_text_for_a_cid_pdf(
    tmp_path: Path, install_ocrmypdf, monkeypatch: pytest.MonkeyPatch, ocr_output: Path
) -> None:
    install_ocrmypdf(_prior_ocr_unless_forced(ocr_output))
    source = _cid_pdf(tmp_path / "cid.pdf")

    result = await _metadata_service(tmp_path, monkeypatch).extract_metadata_only(
        str(source)
    )

    assert result["success"] is True
    assert OCR_TEXT in result["ocr_text"]
    assert "(cid:" not in result["ocr_text"]


async def test_metadata_validation_reports_a_cid_pdf_as_needing_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _cid_pdf(tmp_path / "cid.pdf")

    result = await _metadata_service(
        tmp_path, monkeypatch
    ).validate_document_processability(str(source))

    assert result["valid"] is True
    assert result["has_text_layer"] is False
    assert result["needs_ocr"] is True


async def test_metadata_validation_still_reports_a_textual_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = build_text_pdf(tmp_path / "t.pdf")

    result = await _metadata_service(
        tmp_path, monkeypatch
    ).validate_document_processability(str(source))

    assert result["has_text_layer"] is True
    assert result["needs_ocr"] is False


async def test_bulk_upload_does_not_mark_a_cid_pdf_extracted(
    tmp_path: Path, install_ocrmypdf, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bulk path: MetadataProcessorService.process_document -> legacy_v0."""
    from rbac_backend.services import metadata_processor_service
    from rbac_backend.services.bulk_upload_service import BulkUploadService
    from rbac_backend.services.metadata_processor_service import (
        MetadataProcessorService,
    )

    # `backoff` is imported inside the method but is not in requirements.txt;
    # a pass-through stand-in lets the test reach the metadata step.
    fake_backoff = types.ModuleType("backoff")
    fake_backoff.expo = object()  # type: ignore[attr-defined]
    fake_backoff.on_exception = lambda *a, **k: (lambda fn: fn)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backoff", fake_backoff)

    install_ocrmypdf(_raises(RuntimeError("tesseract crashed")))
    source = _cid_pdf(tmp_path / "cid.pdf")
    processor = _legacy_processor(_service(tmp_path))
    monkeypatch.setattr(
        metadata_processor_service,
        "create_document_processor",
        lambda config: processor,
    )

    class _DocumentService:
        async def create_document(self, **kwargs: Any) -> Any:
            return SimpleNamespace(id="doc-1")

    bulk = BulkUploadService.__new__(BulkUploadService)
    metadata = MetadataProcessorService.__new__(MetadataProcessorService)
    metadata.config = DocumentProcessingConfig()
    bulk.metadata_service = metadata
    bulk.document_service = _DocumentService()

    async def _get_document_service() -> Any:
        return bulk.document_service

    bulk._get_document_service = _get_document_service  # type: ignore[method-assign]

    result = await bulk._async_process_single_document_with_metadata(
        {"filename": source.name, "file_path": str(source), "ocr_enabled": True},
        "org-1",
        "proj-1",
    )

    assert result.metadata_extracted is False
    assert result.ocr_completed is False
    assert processor.database_service.saved == []


# --- import order (matrix 15) ----------------------------------------------------

#: ``argv[1]`` output dir, ``argv[2]`` ``clean`` or ``ocrmypdf-first``.
_IMPORT_ORDER_CHILD = r"""
import json, sys
from pathlib import Path

out_dir, mode = Path(sys.argv[1]), sys.argv[2]
if mode == "ocrmypdf-first":
    import ocrmypdf  # noqa: F401 - the import is the point

from pdfminer.pdffont import PDFSimpleFont
from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.ocr_service import OCRService
from rbac_backend.tests.fixtures.pdf_builders import (
    build_composite_font_pdf, build_scanned_only_pdf, build_text_pdf,
)

out_dir.mkdir(parents=True, exist_ok=True)
service = OCRService(DocumentProcessingConfig())
print(json.dumps({
    "patched": PDFSimpleFont.__init__.__module__,
    "cid": service.assess_text_layer(build_composite_font_pdf(
        out_dir / "cid.pdf", composite_lines=["Notice of claim for extension"])).value,
    "text": service.assess_text_layer(build_text_pdf(out_dir / "t.pdf", text="Letter body")).value,
    "scan": service.assess_text_layer(build_scanned_only_pdf(out_dir / "s.pdf")).value,
}))
"""


@pytest.mark.parametrize("mode", ["clean", "ocrmypdf-first"])
def test_the_legacy_assessment_holds_in_both_import_orders(
    tmp_path: Path, mode: str
) -> None:
    pytest.importorskip("ocrmypdf")
    verdict = run_python_child(
        _IMPORT_ORDER_CHILD, str(tmp_path), mode, cwd=BACKEND_ROOT
    )

    if mode == "ocrmypdf-first":
        # Control: the pdfminer patch really is installed in this child.
        assert verdict["patched"].startswith("ocrmypdf")
    else:
        assert verdict["patched"].startswith("pdfminer")
    assert verdict["cid"] == "ocr_required_unusable_text"
    assert verdict["text"] == "textual"
    assert verdict["scan"] == "ocr_required_empty"


# --- every copy-the-original exit checks the whole document (review MEDIUMs) ---


def _text_then_cid(
    tmp_path: Path, *, leading_pages: int = 5, scanned: bool = False
) -> Path:
    lead = (
        build_scanned_only_pdf(tmp_path / "lead.pdf", pages=leading_pages)
        if scanned
        else build_text_pdf(
            tmp_path / "lead.pdf", pages=leading_pages, text="Readable body page"
        )
    )
    return _concat(tmp_path / "text_then_cid.pdf", lead, _cid_pdf(tmp_path / "cid.pdf"))


async def test_ocr_unavailable_checks_every_page_of_a_scan_too(tmp_path: Path) -> None:
    """Five scanned pages read as EMPTY; the sixth, unusable, must still be seen."""
    source = _text_then_cid(tmp_path, scanned=True)

    with pytest.raises(DocumentProcessingError, match="unusable"):
        await _service(tmp_path, ocr_available=False).process_pdf(source)


async def test_a_failed_unforced_ocr_does_not_copy_an_unusable_original(
    tmp_path: Path, install_ocrmypdf, ocr_output: Path
) -> None:
    """Five scanned pages then a CID page; unforced OCR dies of something else."""

    def behaviour(kwargs: Dict[str, Any]) -> None:
        if not kwargs.get("force_ocr"):
            raise RuntimeError("tesseract crashed on page 2")
        shutil.copyfile(ocr_output, kwargs["output_file"])

    fake = install_ocrmypdf(behaviour)
    source = _text_then_cid(tmp_path, scanned=True)

    _, text = await _service(tmp_path).process_pdf(source)

    assert fake.force_flags == [False, True]
    assert text is not None and "(cid:" not in text


async def test_a_failed_unforced_ocr_with_an_unusable_original_fails_visibly(
    tmp_path: Path, install_ocrmypdf
) -> None:
    install_ocrmypdf(_raises(RuntimeError("tesseract crashed")))
    source = _text_then_cid(tmp_path, scanned=True)
    before = _digest(source)
    service = _service(tmp_path)

    with pytest.raises(DocumentProcessingError, match="unusable"):
        await service.process_pdf(source)

    assert _digest(source) == before
    assert not (Path(service.config.process_dir) / source.name).exists()


async def test_a_sidecar_read_error_does_not_hide_an_unusable_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later page that pdfplumber cannot read must not discard the verdict."""
    source = _concat(
        tmp_path / "cid_then_broken.pdf",
        build_text_pdf(tmp_path / "t.pdf", pages=2, text="Readable body page"),
        _cid_pdf(tmp_path / "cid.pdf"),
        build_text_pdf(tmp_path / "t2.pdf", pages=1, text="Unreadable later page"),
    )
    from pdfplumber.page import Page

    real_extract = Page.extract_text

    def extract_text(self: Any, *args: Any, **kwargs: Any) -> Any:
        if self.page_number == 4:
            raise ValueError("malformed content stream")
        return real_extract(self, *args, **kwargs)

    monkeypatch.setattr(Page, "extract_text", extract_text)

    with pytest.raises(DocumentProcessingError, match="unusable"):
        await _service(tmp_path)._extract_sidecar_text(source, tmp_path / "x.txt")


async def test_a_sidecar_read_error_on_a_usable_pdf_keeps_the_old_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = build_text_pdf(tmp_path / "t.pdf", pages=3, text="Readable body page")
    from pdfplumber.page import Page

    real_extract = Page.extract_text

    def extract_text(self: Any, *args: Any, **kwargs: Any) -> Any:
        if self.page_number == 3:
            raise ValueError("malformed content stream")
        return real_extract(self, *args, **kwargs)

    monkeypatch.setattr(Page, "extract_text", extract_text)

    assert (
        await _service(tmp_path)._extract_sidecar_text(source, tmp_path / "x.txt")
        is None
    )


# --- a failed run removes only what it wrote itself (owner decision 8) ---------


async def _processed_letter(
    tmp_path: Path, service: OCRService
) -> tuple[Path, bytes, str]:
    first = tmp_path / "a"
    first.mkdir()
    source = build_text_pdf(first / "letter.pdf", pages=1, text="Document A body")
    processed, _ = await service.process_pdf(source)
    return (
        processed,
        processed.read_bytes(),
        processed.with_suffix(".txt").read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize(
    "fails_after_writing",
    [
        pytest.param(False, id="ocr-fails-before-writing"),
        pytest.param(True, id="ocr-writes-unusable-output"),
    ],
)
async def test_a_failed_run_keeps_an_earlier_documents_output_of_the_same_name(
    tmp_path: Path, install_ocrmypdf, fails_after_writing: bool
) -> None:
    service = _service(tmp_path)
    processed_a, a_pdf, a_txt = await _processed_letter(tmp_path, service)

    install_ocrmypdf(
        _writes(_cid_pdf(tmp_path / "still_cid.pdf"))
        if fails_after_writing
        else _raises(RuntimeError("tesseract crashed"))
    )
    second = tmp_path / "b"
    second.mkdir()
    document_b = _cid_pdf(second / "letter.pdf")

    with pytest.raises(DocumentProcessingError, match="unusable"):
        await service.process_pdf(document_b)

    assert processed_a.read_bytes() == a_pdf
    assert processed_a.with_suffix(".txt").read_text(encoding="utf-8") == a_txt
    assert sorted(p.name for p in processed_a.parent.iterdir()) == [
        "letter.pdf",
        "letter.txt",
    ]


async def test_no_staging_file_survives_a_successful_run(
    tmp_path: Path, install_ocrmypdf, ocr_output: Path
) -> None:
    install_ocrmypdf(_prior_ocr_unless_forced(ocr_output))
    service = _service(tmp_path)

    processed, text = await service.process_pdf(_cid_pdf(tmp_path / "cid.pdf"))

    assert text is not None and OCR_TEXT in text
    assert sorted(p.name for p in processed.parent.iterdir()) == ["cid.pdf", "cid.txt"]


# --- an encrypted scan is a refusal, not a document with no text ---------------


async def test_an_encrypted_scan_is_not_reported_as_processed(
    tmp_path: Path, install_ocrmypdf
) -> None:
    """The refusal used to be caught by its own function's `except Exception`.

    OCRmyPDF's exit 8 became DocumentProcessingError inside the outer try, the
    generic handler copied the original, and process_pdf returned it with no
    text at all - a document marked processed that nobody could read.
    """
    install_ocrmypdf(_raises(_EncryptedPdfError("exit=8")))
    source = build_scanned_only_pdf(tmp_path / "scan.pdf", pages=2)
    service = _service(tmp_path)

    with pytest.raises(DocumentProcessingError, match="encrypted"):
        await service.process_pdf(source)

    assert list(Path(service.config.process_dir).glob("**/*.pdf")) == []


# --- one bad page does not rasterise the whole document -----------------------


async def test_only_the_unusable_pages_are_sent_for_forced_ocr(
    tmp_path: Path, install_ocrmypdf, ocr_output: Path
) -> None:
    """Forcing the whole document would destroy the native text of good pages."""
    fake = install_ocrmypdf(_prior_ocr_unless_forced(ocr_output))
    source = _concat(
        tmp_path / "long.pdf",
        build_text_pdf(tmp_path / "t.pdf", pages=5, text="Readable body page"),
        _cid_pdf(tmp_path / "cid.pdf"),
    )

    await _service(tmp_path).process_pdf(source)

    forced = [call for call in fake.calls if call.get("force_ocr")]
    assert [call.get("pages") for call in forced] == [[6]]


# --- run-scoped outputs: documents cannot reach each other's files ------------


async def test_two_documents_of_the_same_name_keep_separate_outputs(
    tmp_path: Path, install_ocrmypdf, ocr_output: Path
) -> None:
    install_ocrmypdf(_prior_ocr_unless_forced(ocr_output))
    service = _service(tmp_path)
    first = tmp_path / "a"
    first.mkdir()
    second = tmp_path / "b"
    second.mkdir()

    processed_a, text_a = await service.process_pdf(
        build_text_pdf(first / "letter.pdf", pages=1, text="Document A body")
    )
    a_bytes = processed_a.read_bytes()
    processed_b, text_b = await service.process_pdf(
        build_text_pdf(second / "letter.pdf", pages=1, text="Document B body")
    )

    assert processed_a != processed_b
    assert processed_a.read_bytes() == a_bytes
    assert text_a is not None and "Document A body" in text_a
    assert text_b is not None and "Document B body" in text_b
    assert (
        processed_a.with_suffix(".txt").read_text(encoding="utf-8").count("Document B")
        == 0
    )


async def test_an_abandoned_run_directory_is_swept_but_a_fresh_one_is_kept(
    tmp_path: Path,
) -> None:
    """A killed worker skips the cleanup; process_dir is a backed-up volume."""
    service = _service(tmp_path)
    process_dir = Path(service.config.process_dir)
    process_dir.mkdir(parents=True, exist_ok=True)
    abandoned = process_dir / "run-deadbeef"
    abandoned.mkdir()
    (abandoned / "letter.pdf").write_bytes(b"%PDF-1.4\n")
    old = time.time() - (service.OCR_TIMEOUT_SECONDS * 2 + 7200)
    os.utime(abandoned, (old, old))
    fresh = process_dir / "run-cafebabe"
    fresh.mkdir()

    await service.process_pdf(build_text_pdf(tmp_path / "t.pdf", text="Body"))

    assert not abandoned.exists()
    assert fresh.exists()
