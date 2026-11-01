"""R1: mixed-content correspondence loses its scanned pages on `legacy_v0`.

The 2026-10-07 extraction audit ran production's own code on two real letters:

* a scanned two-page letter followed by photo pages with captions - legacy
  kept 150 of 3,555 characters;
* a scanned covering letter followed by native cost sheets - legacy lost the
  whole first page, including the claimed amount.

Both were still reported as processed. The mechanism is one document-level
decision: `OCRService.assess_text_layer` reads the first five pages and, if any
of them carries usable text, the document is "textual" and no page is OCR'd.

`unified_v1` already decides per page. This module pins, on synthetic
stand-ins for both documents (customer PDFs are not committed):

* the legacy defect, as a strict xfail - it is the rollback path and is
  deliberately left unchanged, so the test says what it loses;
* that the unified path recovers the scanned pages, OCRs only the pages that
  need it, keeps native pages native, and is deterministic;
* what neither path recovers yet (a raster inset on a page that also has a
  real text layer), as a strict xfail;
* that a canary organisation's queued work is routed to `unified_v1` and
  everyone else's stays `legacy_v0`;
* that the pipeline that actually ran, and how each page was read, is
  recorded on the document - so a canary can be audited from the record.

`test_mixed_correspondence_live_ocr_acceptance` runs the same documents through
real OCRmyPDF/tesseract when the binaries are present.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Sequence

import pytest
from bson import ObjectId

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.document_processor import DocumentProcessor
from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.extraction.models import (
    Completeness,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.page_store import NullPageStore
from rbac_backend.services.ocr_service import OCRService, TextLayerAssessment
from rbac_backend.services.pipeline_routing import (
    LEGACY_PIPELINE,
    UNIFIED_PIPELINE,
    resolve_pipeline_version,
)
from rbac_backend.tests.fixtures.mixed_correspondence_pdfs import (
    CLAIM_CRITICAL_PHRASES,
    CLAIM_NATIVE_PAGES,
    CLAIM_NATIVE_PAGES_TEXT,
    CLAIM_OCR_TRUTH,
    CLAIM_SCANNED_PAGES,
    GLM_CAPTIONS,
    GLM_CRITICAL_PHRASES,
    GLM_NATIVE_PAGES,
    GLM_OCR_TRUTH,
    GLM_SCANNED_PAGES,
    GLM_THIN_PHOTO_PAGE,
    HYBRID_INSET_LINES,
    HYBRID_INSET_PHRASE,
    build_claim_like_pdf,
    build_glm_like_pdf,
    build_hybrid_page_pdf,
)
from rbac_backend.tests.fixtures.pdf_builders import (
    build_clean_native_pdf,
    build_image_page_pdf,
)
from rbac_backend.tests.test_document_processing_jobs import (
    FakeDB,
    insert_document,
    make_document,
)

MIN_TEXT_CHARS = 40

FIXTURES = {
    "glm_like": SimpleNamespace(
        build=build_glm_like_pdf,
        truth=GLM_OCR_TRUTH,
        critical=GLM_CRITICAL_PHRASES,
        scanned=GLM_SCANNED_PAGES,
        native=GLM_NATIVE_PAGES,
        expected_batches=[[1, 2], [GLM_THIN_PHOTO_PAGE]],
        pages_total=5,
        native_marker="Annexure A - Photograph 1",
    ),
    "claim_like": SimpleNamespace(
        build=build_claim_like_pdf,
        truth=CLAIM_OCR_TRUTH,
        critical=CLAIM_CRITICAL_PHRASES,
        scanned=CLAIM_SCANNED_PAGES,
        native=CLAIM_NATIVE_PAGES,
        expected_batches=[[1]],
        pages_total=4,
        native_marker="Annexure 1 - Basis of claim",
    ),
}


class _TruthOcrRunner:
    """A perfect OCR engine: returns the text drawn into each raster.

    Pages it has no truth for read as empty, which is what tesseract returns
    for a page with no text on it.
    """

    def __init__(self, truth: Dict[int, str]) -> None:
        self.truth = truth
        self.requested: List[List[int]] = []

    async def run(
        self, source: Path, page_numbers: Sequence[int], language: str
    ) -> Dict[int, str]:
        self.requested.append(list(page_numbers))
        return {page: self.truth.get(page, "") for page in page_numbers}


class _FailingOcrRunner:
    async def run(self, source: Path, page_numbers: Sequence[int], language: str):
        raise RuntimeError("tesseract crashed")


def _service(tmp_path: Path) -> OCRService:
    config = DocumentProcessingConfig()
    config.ocr_enabled = True
    config.contract_ocr_min_text_chars_per_page = MIN_TEXT_CHARS
    config.process_dir = str(tmp_path / "processed")
    service = OCRService(config)
    # The OCR binaries are absent on dev hosts and in CI. The page-wise path
    # takes an injected runner; the legacy path is only ever asked to *skip*
    # OCR here, which needs no binary.
    service._ocr_available = True
    return service


def _forbid_legacy_ocr(service: OCRService) -> List[str]:
    """Make any legacy OCR invocation visible instead of silently absent."""
    calls: List[str] = []

    async def _ocr(*args: Any, **kwargs: Any) -> str:
        calls.append("ocr")
        return ""

    service._run_ocr_with_sidecar = _ocr  # type: ignore[method-assign]
    service._replace_unusable_text_layer = _ocr  # type: ignore[method-assign]
    return calls


async def _legacy_text(fixture: SimpleNamespace, tmp_path: Path) -> str:
    service = _service(tmp_path)
    _forbid_legacy_ocr(service)
    _, text = await service.process_pdf(fixture.build(tmp_path / "doc.pdf"))
    return text or ""


async def _unified(fixture: SimpleNamespace, tmp_path: Path, runner: Any = None):
    runner = runner or _TruthOcrRunner(fixture.truth)
    result = await _service(tmp_path).process_pdf_pagewise(
        fixture.build(tmp_path / "doc.pdf"),
        store=NullPageStore(),
        document_id="doc-1",
        ocr_runner=runner,
    )
    return result, runner


# --- A. The production defect on legacy_v0 -----------------------------------


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_legacy_judges_the_whole_mixed_document_textual(
    name: str, tmp_path: Path
) -> None:
    """The document-level decision: a caption or annexure page outvotes the scan."""
    fixture = FIXTURES[name]
    source = fixture.build(tmp_path / "doc.pdf")

    assert _service(tmp_path).assess_text_layer(source) is TextLayerAssessment.TEXTUAL


@pytest.mark.parametrize("name", sorted(FIXTURES))
async def test_legacy_runs_no_ocr_and_loses_the_scanned_letter(
    name: str, tmp_path: Path
) -> None:
    """Reproduces R1 exactly: no OCR, scanned pages absent, native pages kept."""
    fixture = FIXTURES[name]
    service = _service(tmp_path)
    ocr_calls = _forbid_legacy_ocr(service)

    _, text = await service.process_pdf(fixture.build(tmp_path / "doc.pdf"))
    text = text or ""

    assert ocr_calls == [], "legacy must not have OCR'd anything for this shape"
    for phrase in fixture.critical:
        assert phrase not in text
    # GLM measured 150 of 3,555 characters (4%): the native pages survive and
    # the scanned pages contribute nothing at all.
    assert all(fixture.truth[p] not in text for p in fixture.scanned)
    assert fixture.native_marker in text


@pytest.mark.parametrize(
    "pipeline",
    [
        pytest.param(
            LEGACY_PIPELINE,
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "R1: legacy_v0 decides OCR once per document. It is the "
                    "rollback path and is intentionally unchanged."
                ),
            ),
        ),
        UNIFIED_PIPELINE,
    ],
)
@pytest.mark.parametrize("name", sorted(FIXTURES))
async def test_the_letter_subject_reference_and_operative_text_are_extracted(
    name: str, pipeline: str, tmp_path: Path
) -> None:
    """The acceptance criterion, asked of both pipelines."""
    fixture = FIXTURES[name]
    if pipeline == LEGACY_PIPELINE:
        text = await _legacy_text(fixture, tmp_path)
    else:
        result, _ = await _unified(fixture, tmp_path)
        text = result.combined_text

    for phrase in fixture.critical:
        assert phrase in text


# --- B. unified_v1 recovers the pages, page by page ---------------------------


@pytest.mark.parametrize("name", sorted(FIXTURES))
async def test_unified_ocrs_only_the_pages_that_need_it(
    name: str, tmp_path: Path
) -> None:
    fixture = FIXTURES[name]

    result, runner = await _unified(fixture, tmp_path)

    assert runner.requested == fixture.expected_batches
    assert result.ocr_pages_total == sum(len(b) for b in fixture.expected_batches)
    assert len(result.pages) == fixture.pages_total


@pytest.mark.parametrize("name", sorted(FIXTURES))
async def test_unified_recovers_the_scanned_pages(name: str, tmp_path: Path) -> None:
    fixture = FIXTURES[name]

    result, _ = await _unified(fixture, tmp_path)
    by_number = {page.number: page for page in result.pages}

    for number in fixture.scanned:
        page = by_number[number]
        assert page.source is PageSource.OCR
        assert page.status is PageStatus.OCR_COMPLETED
        assert page.text.strip() == fixture.truth[number]
    assert result.completeness is Completeness.COMPLETE


async def test_unified_keeps_the_glm_caption_pages_native(tmp_path: Path) -> None:
    result, _ = await _unified(FIXTURES["glm_like"], tmp_path)
    by_number = {page.number: page for page in result.pages}

    for number in GLM_NATIVE_PAGES:
        page = by_number[number]
        assert page.source is PageSource.TEXT_LAYER
        assert page.status is PageStatus.TEXT_LAYER
        for line in GLM_CAPTIONS[number]:
            assert line in page.text


async def test_unified_keeps_the_claim_annexures_native(tmp_path: Path) -> None:
    result, _ = await _unified(FIXTURES["claim_like"], tmp_path)
    by_number = {page.number: page for page in result.pages}

    for number in CLAIM_NATIVE_PAGES:
        page = by_number[number]
        assert page.source is PageSource.TEXT_LAYER
        # Native text is read word for word; table columns are not modelled
        # here (that is R4), so compare words rather than spacing.
        for line in CLAIM_NATIVE_PAGES_TEXT[number]:
            assert line.split() == [w for w in line.split() if w in page.text.split()]


@pytest.mark.parametrize("name", sorted(FIXTURES))
async def test_unified_is_no_longer_a_small_fraction_of_the_source(
    name: str, tmp_path: Path
) -> None:
    """GLM legacy kept ~4% of the text. Unified must keep all of the letter."""
    fixture = FIXTURES[name]
    legacy = await _legacy_text(fixture, tmp_path / "legacy")
    result, _ = await _unified(fixture, tmp_path / "unified")

    scanned_truth = "".join(fixture.truth[p] for p in fixture.scanned)
    assert len(result.combined_text) > len(legacy) + len(scanned_truth) * 0.9


# --- C. Hybrid pages -------------------------------------------------------------


async def test_a_thin_caption_over_a_photo_is_still_ocrd(tmp_path: Path) -> None:
    """Text elsewhere in the document never suppresses OCR on a thin page.

    GLM page 5 carries a nine-character caption over a photograph. Legacy
    skipped it with the rest; unified OCRs it on its own merits.
    """
    result, runner = await _unified(FIXTURES["glm_like"], tmp_path)
    page = {p.number: p for p in result.pages}[GLM_THIN_PHOTO_PAGE]

    assert [GLM_THIN_PHOTO_PAGE] in runner.requested
    assert page.source is PageSource.OCR


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Not supported by the existing engine: a page whose text layer clears "
        "the native threshold is never OCR'd, so text inside a raster inset on "
        "that page is not read. Region-level OCR is a follow-up, not PR 1."
    ),
)
async def test_a_raster_inset_on_a_native_page_is_read(tmp_path: Path) -> None:
    truth = {1: "\n".join(HYBRID_INSET_LINES)}
    result = await _service(tmp_path).process_pdf_pagewise(
        build_hybrid_page_pdf(tmp_path / "hybrid.pdf"),
        store=NullPageStore(),
        document_id="doc-1",
        ocr_runner=_TruthOcrRunner(truth),
    )

    assert HYBRID_INSET_PHRASE in result.combined_text


async def test_a_raster_inset_on_a_native_page_keeps_its_native_text(
    tmp_path: Path,
) -> None:
    """The supported half of the hybrid case: nothing native is lost."""
    runner = _TruthOcrRunner({})
    result = await _service(tmp_path).process_pdf_pagewise(
        build_hybrid_page_pdf(tmp_path / "hybrid.pdf"),
        store=NullPageStore(),
        document_id="doc-1",
        ocr_runner=runner,
    )
    page = result.pages[0]

    assert page.source is PageSource.TEXT_LAYER
    assert "native header carried by the PDF text layer" in page.text
    assert runner.requested == []


# --- D. Determinism ------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(FIXTURES))
async def test_unified_selection_is_deterministic(name: str, tmp_path: Path) -> None:
    fixture = FIXTURES[name]

    first, first_runner = await _unified(fixture, tmp_path / "a")
    second, second_runner = await _unified(fixture, tmp_path / "b")

    assert first.combined_text == second.combined_text
    assert first_runner.requested == second_runner.requested
    assert [(p.number, p.source, p.status) for p in first.pages] == [
        (p.number, p.source, p.status) for p in second.pages
    ]


# --- E. No regression on the pure shapes ---------------------------------------


async def test_a_pure_native_pdf_stays_native(tmp_path: Path) -> None:
    runner = _TruthOcrRunner({})
    result = await _service(tmp_path).process_pdf_pagewise(
        build_clean_native_pdf(tmp_path / "native.pdf"),
        store=NullPageStore(),
        document_id="doc-1",
        ocr_runner=runner,
    )

    assert runner.requested == []
    assert all(page.source is PageSource.TEXT_LAYER for page in result.pages)


async def test_a_pure_scan_is_still_ocrd(tmp_path: Path) -> None:
    runner = _TruthOcrRunner({1: "Scanned page text read by OCR"})
    result = await _service(tmp_path).process_pdf_pagewise(
        build_image_page_pdf(tmp_path / "scan.pdf"),
        store=NullPageStore(),
        document_id="doc-1",
        ocr_runner=runner,
    )

    assert runner.requested == [[1]]
    assert result.pages[0].source is PageSource.OCR
    assert result.pages[0].status is PageStatus.OCR_COMPLETED


async def test_legacy_still_ocrs_a_pure_scan(tmp_path: Path) -> None:
    """The legacy path is unchanged: a document with no text at all is OCR'd."""
    service = _service(tmp_path)
    calls = _forbid_legacy_ocr(service)

    await service.process_pdf(build_image_page_pdf(tmp_path / "scan.pdf"))

    assert calls == ["ocr"]


# --- Failure visibility ---------------------------------------------------------


async def test_a_failed_ocr_page_is_distinguishable_from_a_native_page(
    tmp_path: Path,
) -> None:
    result, _ = await _unified(
        FIXTURES["glm_like"], tmp_path, runner=_FailingOcrRunner()
    )
    by_number = {page.number: page for page in result.pages}

    for number in GLM_SCANNED_PAGES:
        assert by_number[number].status is PageStatus.OCR_FAILED
        assert by_number[number].source is not PageSource.TEXT_LAYER
    for number in GLM_NATIVE_PAGES:
        assert by_number[number].status is PageStatus.TEXT_LAYER
    assert result.completeness is Completeness.PARTIAL
    assert set(GLM_SCANNED_PAGES) <= set(result.ocr_failed_pages)


class _ProcessorOcr:
    """Records which extractor the processor reached."""

    def __init__(self, *, unified_error: Exception | None = None) -> None:
        self.legacy_calls = 0
        self.unified_calls = 0
        self.unified_error = unified_error

    async def process_pdf(self, input_path: Path):
        self.legacy_calls += 1
        return input_path, "legacy text"

    async def process_pdf_pagewise(self, input_path: Path, **kwargs: Any):
        self.unified_calls += 1
        raise self.unified_error or RuntimeError("unified extractor failed")


async def _anoop() -> None:
    return None


def _processor(ocr: _ProcessorOcr) -> DocumentProcessor:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.ocr_service = ocr
    processor.quality_gate = None
    processor.fallback_ladder = None
    processor.fallback_max_pages_per_document = 0
    processor.config = SimpleNamespace(max_file_size_mb=50, ocr_language="eng")
    processor.database_service = SimpleNamespace(
        get_database=_db_none, close_connection=_anoop
    )
    processor.source_kind_router = SimpleNamespace(
        route=lambda mime, name: __import__(
            "rbac_backend.services.extraction.models", fromlist=["SourceKind"]
        ).SourceKind.PDF
    )
    return processor


async def _db_none() -> None:
    return None


async def test_a_failed_unified_run_never_falls_back_to_legacy(tmp_path: Path) -> None:
    """A unified failure is a failure - not quietly legacy output."""
    source = tmp_path / "letter.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    ocr = _ProcessorOcr()

    result = await _processor(ocr).process_document(
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        pipeline_version=UNIFIED_PIPELINE,
    )

    assert ocr.unified_calls == 1
    assert ocr.legacy_calls == 0
    assert result.success is False
    assert result.publishable is False


# --- Observability: which pipeline ran ------------------------------------------


async def test_the_processor_reports_the_unified_pipeline_even_when_it_fails(
    tmp_path: Path,
) -> None:
    source = tmp_path / "letter.pdf"
    source.write_bytes(b"%PDF-1.4\n")

    result = await _processor(_ProcessorOcr()).process_document(
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        pipeline_version=UNIFIED_PIPELINE,
    )

    assert result.pipeline_version == UNIFIED_PIPELINE


@pytest.mark.parametrize("requested", [LEGACY_PIPELINE, None, "unified_v2", ""])
async def test_the_processor_reports_legacy_for_anything_not_unified(
    requested: Any, tmp_path: Path
) -> None:
    """What is recorded is what ran: an unknown value ran legacy, so says legacy."""
    source = tmp_path / "letter.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    processor = _processor(_ProcessorOcr())

    async def _persist(**kwargs: Any):
        from rbac_backend.models.document_metadata import ProcessingResult

        return ProcessingResult(success=True)

    processor._extract_and_persist = _persist  # type: ignore[method-assign]

    result = await processor.process_document(
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        pipeline_version=requested,
    )

    assert result.pipeline_version == LEGACY_PIPELINE


class _RecordingProcessor:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: List[Dict[str, Any]] = []

    async def process_document(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.result


def _page(number: int, source: PageSource, status: PageStatus) -> Any:
    return SimpleNamespace(number=number, source=source, status=status)


def _unified_result() -> Any:
    pages = [
        _page(1, PageSource.OCR, PageStatus.OCR_COMPLETED),
        _page(2, PageSource.EMPTY, PageStatus.OCR_FAILED),
        _page(3, PageSource.TEXT_LAYER, PageStatus.TEXT_LAYER),
    ]
    return SimpleNamespace(
        success=True,
        metadata=None,
        processing_time=0.1,
        chunks_created=0,
        processed_path=None,
        metadata_source="legacy_regex",
        partial_failures={},
        publishable=False,
        pages_human_review=[2],
        pipeline_version=UNIFIED_PIPELINE,
        extraction_result=SimpleNamespace(pages=pages),
    )


def _legacy_result() -> Any:
    return SimpleNamespace(
        success=True,
        metadata=None,
        processing_time=0.1,
        chunks_created=0,
        processed_path=None,
        metadata_source="legacy_regex",
        partial_failures={},
        publishable=True,
        pages_human_review=[],
        pipeline_version=LEGACY_PIPELINE,
        extraction_result=None,
    )


async def _run_jobless_with(
    monkeypatch, tmp_path: Path, result: Any
) -> "tuple[Dict[str, Any], _RecordingProcessor]":
    db = FakeDB()
    service = DocumentService(db)
    document_id = ObjectId()
    await insert_document(db, make_document(document_id), document_id)
    processor = _RecordingProcessor(result)
    monkeypatch.setattr(
        "rbac_backend.services.document_service.create_document_processor",
        lambda: processor,
    )
    source = tmp_path / "letter.pdf"
    source.write_bytes(b"%PDF-1.4\n")

    await service.process_document_async(
        str(document_id),
        file_path=str(source),
        organization_id="org-1",
        project_id="proj-1",
        upload_type="incoming",
    )
    return await db.documents.find_one({"_id": document_id}), processor


async def _run_jobless(monkeypatch, tmp_path: Path, result: Any) -> Dict[str, Any]:
    stored, _ = await _run_jobless_with(monkeypatch, tmp_path, result)
    return stored


async def test_the_document_records_the_unified_pipeline_and_each_pages_method(
    monkeypatch, tmp_path: Path
) -> None:
    stored = await _run_jobless(monkeypatch, tmp_path, _unified_result())
    meta = stored["processing_metadata"]

    assert meta["pipeline_version"] == UNIFIED_PIPELINE
    # A failed OCR page reads differently from a native page and an OCR'd one.
    assert meta["page_extraction"] == {
        "1": {"source": "ocr", "status": "ocr_completed"},
        "2": {"source": "empty", "status": "ocr_failed"},
        "3": {"source": "text_layer", "status": "text_layer"},
    }


async def test_the_document_records_legacy_without_inventing_page_methods(
    monkeypatch, tmp_path: Path
) -> None:
    """Legacy decides per document; it has no per-page record to report."""
    stored = await _run_jobless(monkeypatch, tmp_path, _legacy_result())
    meta = stored["processing_metadata"]

    assert meta["pipeline_version"] == LEGACY_PIPELINE
    assert meta["page_extraction"] is None


async def test_a_failed_run_still_records_which_pipeline_failed(
    monkeypatch, tmp_path: Path
) -> None:
    failed = SimpleNamespace(
        success=False,
        error="unified extractor failed",
        pipeline_version=UNIFIED_PIPELINE,
        publishable=False,
    )

    stored = await _run_jobless(monkeypatch, tmp_path, failed)

    assert stored["processing_error"]["pipeline_version"] == UNIFIED_PIPELINE


# --- Canary routing, end to end through the job record ------------------------


async def _queue_and_route(monkeypatch, *, organization_id: str, canary: str) -> str:
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "UNIFIED_EXTRACTION_ENABLED", False)
    monkeypatch.setattr(settings, "UNIFIED_EXTRACTION_CANARY_ORG_IDS", canary)
    db = FakeDB()
    service = DocumentService(db)
    document_id = ObjectId()
    document = make_document(document_id).model_copy(
        update={"organization_id": organization_id}
    )
    await insert_document(db, document, document_id)
    job_id = await service.queue_document_processing(document, "/tmp/letter.pdf")
    job = await db.document_processing_jobs.find_one({"_id": job_id})
    return job["pipeline_version"]


async def test_a_canary_organisations_upload_is_routed_to_unified(monkeypatch) -> None:
    version = await _queue_and_route(
        monkeypatch, organization_id="org-canary", canary="org-canary"
    )

    assert version == UNIFIED_PIPELINE


async def test_every_other_organisation_stays_legacy(monkeypatch) -> None:
    version = await _queue_and_route(
        monkeypatch, organization_id="org-other", canary="org-canary"
    )

    assert version == LEGACY_PIPELINE


def test_the_global_flag_is_not_enabled_by_default() -> None:
    from rbac_backend.core.config import Settings

    field = Settings.model_fields["UNIFIED_EXTRACTION_ENABLED"]
    assert field.default is False
    assert (
        resolve_pipeline_version(
            organization_id="org-any", enabled=False, canary_org_ids=set()
        )
        == LEGACY_PIPELINE
    )


async def test_a_jobless_reprocess_is_deliberately_legacy(
    monkeypatch, tmp_path: Path
) -> None:
    """Reprocess and bulk OCR have no durable job, so no checkpoint to resume.

    The unified path relies on that checkpoint to keep a partial extraction out
    of `completed`, so a jobless run stays on legacy even for a canary
    organisation - and says so on the record.
    """
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "UNIFIED_EXTRACTION_ENABLED", False)
    monkeypatch.setattr(settings, "UNIFIED_EXTRACTION_CANARY_ORG_IDS", "org-1")

    _, processor = await _run_jobless_with(monkeypatch, tmp_path, _legacy_result())

    assert [call["pipeline_version"] for call in processor.calls] == [LEGACY_PIPELINE]
