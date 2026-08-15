"""legacy_v0 must genuinely be the pre-Phase-3 path.

The canary compares unified_v1 against legacy_v0. That comparison only means
something if legacy_v0 really is the behaviour production had before Phase 3 -
otherwise the canary measures new-code-A against new-code-B and proves nothing
about the rollback target.

So this file does not assert hand-written expectations about "what legacy
probably did". It vendors the pre-Phase-3 `process_document` body verbatim from
the last commit before page-wise extraction was introduced:

    ffa844b  feat(uploads): two-stage duplicate-upload detection   <- oracle
    c3840f1  feat: route the general document path through page-wise extraction

`_pre_phase3_process_document` below is that code with `self` renamed to `proc`
and `pdf_path`/`upload_type` threaded in. Both it and the current legacy path
are driven against the SAME fake collaborators, and their observable effects are
compared: which OCR entry point ran, the extracted text, the metadata, the
arguments handed to persistence, and the resulting ProcessingResult.

Generated ids and timings are excluded - they are expected to differ.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.models.document_metadata import (
    ParsedDocumentMetadata,
    ProcessingResult,
)
from rbac_backend.services.document_processor import DocumentProcessor
from rbac_backend.services.pipeline_routing import LEGACY_PIPELINE
from rbac_backend.services.pydantic_ai_service import PydanticAIMetadataError


# --- Fixtures modelling the three source shapes -----------------------------
#
# The OCR/no-OCR decision lives inside OCRService.process_pdf, which the legacy
# path delegates to wholesale. These fakes reproduce its two branches so the
# decision is observable from the outside.

TEXT_NATIVE = "text_native"
OCR_REQUIRED = "ocr_required"
EMPTY_PDF = "empty"

FIXTURE_TEXT = {
    # Text layer present: process_pdf copies the original and extracts a
    # sidecar; no OCR is run.
    TEXT_NATIVE: "Ref: ABC/2026/014\nSubject: Extension of Time\nBody text.",
    # No text layer: process_pdf runs OCR and returns its output.
    OCR_REQUIRED: "SCANNED LETTER\nRef ABC/2026/015",
    # A PDF that yields nothing at all.
    EMPTY_PDF: "",
}


class FakeOcrService:
    """Reproduces OCRService.process_pdf's contract and records the decision."""

    def __init__(self, fixture: str) -> None:
        self.fixture = fixture
        self.process_pdf_calls: List[Path] = []
        self.pagewise_calls = 0
        self.ocr_performed: Optional[bool] = None
        self.dest_dir = Path("/processed")

    async def process_pdf(self, input_path: Path):
        self.process_pdf_calls.append(input_path)
        # Mirrors the real branch: text layer -> no OCR, otherwise OCR.
        self.ocr_performed = self.fixture != TEXT_NATIVE
        dest_path = self.dest_dir / input_path.name
        text = FIXTURE_TEXT[self.fixture]
        return dest_path, (text or None)

    async def process_pdf_pagewise(self, *args: Any, **kwargs: Any):
        self.pagewise_calls += 1
        raise AssertionError(
            "process_pdf_pagewise is the unified extractor and must never run "
            "for a legacy_v0 job"
        )


class FakeOpenAI:
    def __init__(self) -> None:
        self.process_text_calls: List[str] = []
        self.upload_calls: List[str] = []
        self.process_document_calls: List[str] = []

    async def process_text(self, text: str, *, filename: str) -> str:
        self.process_text_calls.append(text)
        return f"REPORT<{filename}>"

    async def upload_file(self, path: str) -> str:
        self.upload_calls.append(path)
        return "file-1"

    async def process_document(self, file_id: str) -> str:
        self.process_document_calls.append(file_id)
        return "REPORT<uploaded>"

    async def cleanup_file(self, file_id: str) -> None:
        return None


class FakeTextService:
    def parse_extraction_report(self, content: str) -> ParsedDocumentMetadata:
        return ParsedDocumentMetadata(subject=f"parsed:{content}")


class FakeFileService:
    def __init__(self) -> None:
        self.summaries: List[tuple] = []

    async def save_summary(self, content, original_path, path_structure, upload_type):
        self.summaries.append((content, original_path, path_structure, upload_type))


class FakeDatabaseService:
    def __init__(self) -> None:
        self.saved: List[Dict[str, Any]] = []
        self.partial_failures: Dict[str, Any] = {}
        self.closed = 0

    async def save_document_data(self, **kwargs: Any) -> int:
        self.saved.append(kwargs)
        return 7

    async def close_connection(self) -> None:
        self.closed += 1


class ForbiddenGate:
    def assess(self, *args: Any, **kwargs: Any):
        raise AssertionError("legacy_v0 must not run the page-wise quality gate")


class ForbiddenLadder:
    async def run(self, *args: Any, **kwargs: Any):
        raise AssertionError("legacy_v0 must not run the fallback ladder")


class RecordingLedger:
    def __init__(self) -> None:
        self.entries: List[Any] = []

    async def record(self, *args: Any, **kwargs: Any):
        self.entries.append((args, kwargs))


def _build_processor(fixture: str, *, pydantic_enabled: bool = False):
    """A DocumentProcessor whose every collaborator is observable."""
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.ocr_service = FakeOcrService(fixture)
    processor.openai_service = FakeOpenAI()
    processor.text_service = FakeTextService()
    processor.file_service = FakeFileService()
    processor.database_service = FakeDatabaseService()
    processor.pydantic_ai_service = SimpleNamespace(
        is_enabled=pydantic_enabled,
        extract_metadata=None,
    )
    processor.quality_gate = ForbiddenGate()
    processor.fallback_ladder = ForbiddenLadder()
    processor.fallback_max_pages_per_document = 0
    processor.intervention_ledger = RecordingLedger()
    processor.config = SimpleNamespace(
        max_file_size_mb=50,
        ocr_language="eng",
        use_pydantic_ai=pydantic_enabled,
        openai_api_key=None,
        process_dir="/processed",
    )
    return processor


def _pdf(tmp_path: Path, name: str = "letter.pdf") -> Path:
    source = tmp_path / name
    source.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return source


# --- The oracle: pre-Phase-3 process_document, vendored from ffa844b --------


async def _pre_phase3_process_document(
    proc: Any,
    pdf_path: str,
    path_structure: str,
    upload_type: str,
    document_id: Optional[str] = None,
    skip_embeddings: bool = False,
) -> ProcessingResult:
    """Verbatim pre-Phase-3 body (git show ffa844b), `self` -> `proc`."""
    start_time = time.time()
    file_id: Optional[str] = None
    processed_path: Optional[Path] = None

    try:
        input_path = Path(pdf_path)
        if not input_path.exists():
            raise RuntimeError(f"PDF file not found: {pdf_path}")

        file_size = input_path.stat().st_size
        if file_size > proc.config.max_file_size_mb * 1024 * 1024:
            raise RuntimeError(f"File too large: {file_size} bytes")

        # Step 1: Process PDF with OCR if needed
        processed_path, raw_ocr_text = await proc.ocr_service.process_pdf(input_path)

        # Step 2: Upload to OpenAI
        partial_failures: Dict[str, Any] = {}
        extracted_content = ""
        metadata_source = "legacy_regex"

        if raw_ocr_text and raw_ocr_text.strip():
            try:
                extracted_content = await proc.openai_service.process_text(
                    raw_ocr_text, filename=input_path.name
                )
                metadata_source = "openai_text_legacy_regex"
            except Exception as exc:
                partial_failures["ai_extraction"] = {
                    "stage": "ocr_text_extraction",
                    "message": str(exc),
                }
                extracted_content = proc._build_ocr_fallback_report(
                    raw_ocr_text, filename=input_path.name
                )
                metadata_source = "ocr_fallback_regex"
        else:
            file_id = await proc.openai_service.upload_file(str(processed_path))
            # Step 3: Extract content using OpenAI
            extracted_content = await proc.openai_service.process_document(file_id)

        # Step 4: Parse extracted content
        metadata_debug: Optional[Dict[str, Any]] = None
        parsed_metadata: Optional[ParsedDocumentMetadata] = None

        if proc.pydantic_ai_service.is_enabled:
            try:
                agent_result = await proc.pydantic_ai_service.extract_metadata(
                    document_text=raw_ocr_text or extracted_content,
                    context={"filename": input_path.name, "upload_type": upload_type},
                )
            except PydanticAIMetadataError:
                pass
            else:
                if agent_result:
                    metadata_source = "pydantic_ai"
                    parsed_metadata = agent_result.metadata
                    metadata_debug = {
                        **agent_result.debug,
                        "raw_result": agent_result.raw_result,
                    }

        if parsed_metadata is None:
            parsed_metadata = proc.text_service.parse_extraction_report(
                extracted_content
            )
            if metadata_source not in {
                "pydantic_ai",
                "openai_text_legacy_regex",
                "ocr_fallback_regex",
            }:
                metadata_source = "legacy_regex"

        # Step 5: Save results
        chunks_created = await proc._save_results(
            extracted_content,
            raw_ocr_text,
            pdf_path,
            path_structure,
            upload_type,
            document_id,
            parsed_metadata,
            skip_embeddings=skip_embeddings,
        )
        partial_failures.update(
            dict(getattr(proc.database_service, "partial_failures", {}) or {})
        )

        return ProcessingResult(
            success=True,
            document_id=document_id,
            processed_path=str(processed_path),
            metadata=parsed_metadata,
            chunks_created=chunks_created,
            processing_time=time.time() - start_time,
            metadata_source=metadata_source,
            metadata_debug=metadata_debug,
            partial_failures=partial_failures,
        )
    except Exception as e:
        return ProcessingResult(
            success=False, error=str(e), processing_time=time.time() - start_time
        )
    finally:
        if file_id:
            await proc.openai_service.cleanup_file(file_id)
        await proc.database_service.close_connection()


# --- Comparison helpers -----------------------------------------------------

#: Fields expected to differ run to run. Everything else must match.
VOLATILE = {"processing_time"}


def _comparable(result: ProcessingResult) -> Dict[str, Any]:
    data = result.model_dump()
    for key in VOLATILE:
        data.pop(key, None)
    # Fields that did not exist pre-Phase-3. They must be inert on the legacy
    # path, which is asserted separately; excluded from the equality compare.
    for key in (
        "extraction_result",
        "extraction_completeness",
        "source_kind",
        "processing_state",
        "pages_human_review",
    ):
        data.pop(key, None)
    return data


def _observed(proc: Any) -> Dict[str, Any]:
    return {
        "ocr_calls": [p.name for p in proc.ocr_service.process_pdf_calls],
        "ocr_performed": proc.ocr_service.ocr_performed,
        "pagewise_calls": proc.ocr_service.pagewise_calls,
        "openai_text": list(proc.openai_service.process_text_calls),
        "openai_uploads": list(proc.openai_service.upload_calls),
        "openai_documents": list(proc.openai_service.process_document_calls),
        "summaries": list(proc.file_service.summaries),
        "saved": list(proc.database_service.saved),
        "db_closed": proc.database_service.closed,
    }


async def _run_both(tmp_path: Path, fixture: str):
    """Run current legacy_v0 and the pre-Phase-3 oracle over one fixture."""
    source = _pdf(tmp_path, f"{fixture}.pdf")

    current = _build_processor(fixture)
    current_result = await current.process_document(
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        document_id="doc-1",
        pipeline_version=LEGACY_PIPELINE,
    )

    oracle = _build_processor(fixture)
    oracle_result = await _pre_phase3_process_document(
        oracle,
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        document_id="doc-1",
    )

    return (current, current_result), (oracle, oracle_result)


# --- Equivalence: the three fixtures ----------------------------------------


@pytest.mark.parametrize("fixture", [TEXT_NATIVE, OCR_REQUIRED, EMPTY_PDF])
@pytest.mark.asyncio
async def test_legacy_result_matches_pre_phase3(tmp_path: Path, fixture: str) -> None:
    """The ProcessingResult is identical to pre-Phase-3, field for field."""
    (_, current_result), (_, oracle_result) = await _run_both(tmp_path, fixture)

    assert oracle_result.success is True, "oracle itself failed; fixture is wrong"
    assert _comparable(current_result) == _comparable(oracle_result)


@pytest.mark.parametrize("fixture", [TEXT_NATIVE, OCR_REQUIRED, EMPTY_PDF])
@pytest.mark.asyncio
async def test_legacy_side_effects_match_pre_phase3(
    tmp_path: Path, fixture: str
) -> None:
    """Same calls, same order, same arguments to every collaborator.

    This is what proves extracted text, the OCR decision, and the persisted
    downstream fields are unchanged: they are the arguments recorded here.
    """
    (current, _), (oracle, _) = await _run_both(tmp_path, fixture)

    assert _observed(current) == _observed(oracle)


@pytest.mark.parametrize("fixture", [TEXT_NATIVE, OCR_REQUIRED, EMPTY_PDF])
@pytest.mark.asyncio
async def test_legacy_persists_the_original_path_not_a_normalised_one(
    tmp_path: Path, fixture: str
) -> None:
    """`file_path` must be the caller's string, as pre-Phase-3 persisted it."""
    source = _pdf(tmp_path, f"{fixture}.pdf")
    proc = _build_processor(fixture)

    await proc.process_document(
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        document_id="doc-1",
        pipeline_version=LEGACY_PIPELINE,
    )

    assert proc.database_service.saved[0]["file_path"] == str(source)


# --- The OCR / no-OCR decision ---------------------------------------------


@pytest.mark.asyncio
async def test_text_native_pdf_skips_ocr(tmp_path: Path) -> None:
    (current, _), (oracle, _) = await _run_both(tmp_path, TEXT_NATIVE)

    assert current.ocr_service.ocr_performed is False
    assert oracle.ocr_service.ocr_performed is False


@pytest.mark.asyncio
async def test_ocr_required_pdf_runs_ocr(tmp_path: Path) -> None:
    (current, _), (oracle, _) = await _run_both(tmp_path, OCR_REQUIRED)

    assert current.ocr_service.ocr_performed is True
    assert oracle.ocr_service.ocr_performed is True


@pytest.mark.asyncio
async def test_the_decision_is_made_once_for_the_whole_document(
    tmp_path: Path,
) -> None:
    """Pre-Phase-3 asked once per document, not once per page."""
    (current, _), _ = await _run_both(tmp_path, OCR_REQUIRED)

    assert len(current.ocr_service.process_pdf_calls) == 1


@pytest.mark.asyncio
async def test_empty_pdf_falls_back_to_the_upload_path(tmp_path: Path) -> None:
    """No usable text -> upload the file, exactly as pre-Phase-3 did."""
    (current, current_result), (oracle, oracle_result) = await _run_both(
        tmp_path, EMPTY_PDF
    )

    assert current.openai_service.upload_calls == oracle.openai_service.upload_calls
    assert current.openai_service.upload_calls != []
    assert current.openai_service.process_text_calls == []
    assert current_result.success is True


# --- Extracted text and processing state ------------------------------------


@pytest.mark.parametrize("fixture", [TEXT_NATIVE, OCR_REQUIRED])
@pytest.mark.asyncio
async def test_extracted_text_is_the_ocr_text(tmp_path: Path, fixture: str) -> None:
    (current, _), _ = await _run_both(tmp_path, fixture)

    saved = current.database_service.saved[0]
    assert saved["full_text"] == FIXTURE_TEXT[fixture]
    assert saved["embedding_text"] == FIXTURE_TEXT[fixture]


@pytest.mark.parametrize("fixture", [TEXT_NATIVE, OCR_REQUIRED, EMPTY_PDF])
@pytest.mark.asyncio
async def test_legacy_reports_no_unified_processing_state(
    tmp_path: Path, fixture: str
) -> None:
    """The Phase-3+ fields stay inert; nothing downstream can read page state."""
    (_, result), _ = await _run_both(tmp_path, fixture)

    assert result.extraction_result is None
    assert result.extraction_completeness is None
    assert result.pages_human_review == []
    assert result.processing_state is None


# --- Absence of the Phase 3-7 machinery -------------------------------------


@pytest.mark.parametrize("fixture", [TEXT_NATIVE, OCR_REQUIRED, EMPTY_PDF])
@pytest.mark.asyncio
async def test_legacy_runs_no_page_gate_no_ladder_no_page_store(
    tmp_path: Path, fixture: str
) -> None:
    """The collaborators raise on any use; reaching success proves absence."""
    source = _pdf(tmp_path, f"{fixture}.pdf")
    proc = _build_processor(fixture)

    result = await proc.process_document(
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        document_id="doc-1",
        pipeline_version=LEGACY_PIPELINE,
    )

    assert result.success is True
    assert proc.ocr_service.pagewise_calls == 0


@pytest.mark.parametrize("fixture", [TEXT_NATIVE, OCR_REQUIRED, EMPTY_PDF])
@pytest.mark.asyncio
async def test_legacy_writes_no_intervention_ledger_entries(
    tmp_path: Path, fixture: str
) -> None:
    source = _pdf(tmp_path, f"{fixture}.pdf")
    proc = _build_processor(fixture)

    await proc.process_document(
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        document_id="doc-1",
        pipeline_version=LEGACY_PIPELINE,
    )

    assert proc.intervention_ledger.entries == []


@pytest.mark.asyncio
async def test_an_unknown_pipeline_version_takes_the_legacy_path(
    tmp_path: Path,
) -> None:
    """Anything not exactly unified_v1 must execute legacy, not newer code."""
    source = _pdf(tmp_path, "unknown.pdf")
    proc = _build_processor(TEXT_NATIVE)

    result = await proc.process_document(
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        document_id="doc-1",
        pipeline_version="some_future_version_v9",
    )

    assert result.success is True
    assert len(proc.ocr_service.process_pdf_calls) == 1
    assert proc.ocr_service.pagewise_calls == 0


@pytest.mark.asyncio
async def test_the_default_pipeline_version_is_legacy(tmp_path: Path) -> None:
    """A caller that passes nothing gets the old behaviour, not the new one."""
    source = _pdf(tmp_path, "default.pdf")
    proc = _build_processor(TEXT_NATIVE)

    result = await proc.process_document(
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        document_id="doc-1",
    )

    assert result.success is True
    assert len(proc.ocr_service.process_pdf_calls) == 1


@pytest.mark.parametrize("fixture", [TEXT_NATIVE, OCR_REQUIRED, EMPTY_PDF])
@pytest.mark.asyncio
async def test_the_database_connection_is_closed_exactly_once(
    tmp_path: Path, fixture: str
) -> None:
    """Extracting _extract_and_persist once left two `finally` blocks closing
    the same connection, so every document double-closed. One owner only.
    """
    source = _pdf(tmp_path, f"{fixture}.pdf")
    proc = _build_processor(fixture)

    await proc.process_document(
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        document_id="doc-1",
        pipeline_version=LEGACY_PIPELINE,
    )

    assert proc.database_service.closed == 1


@pytest.mark.asyncio
async def test_the_connection_is_still_closed_when_extraction_fails(
    tmp_path: Path,
) -> None:
    """The failure path must not leak the connection either."""
    proc = _build_processor(TEXT_NATIVE)

    result = await proc.process_document(
        str(tmp_path / "does_not_exist.pdf"),
        path_structure="org/proj",
        upload_type="incoming",
        document_id="doc-1",
        pipeline_version=LEGACY_PIPELINE,
    )

    assert result.success is False
    assert proc.database_service.closed == 1


def test_the_oracle_is_pinned_to_a_real_pre_phase3_commit() -> None:
    """Guards the provenance claim in this module's docstring.

    If someone re-points the oracle at a different commit without saying so,
    the equivalence evidence silently changes meaning.
    """
    import subprocess

    repo_root = Path(__file__).resolve().parents[4]
    result = subprocess.run(
        ["git", "-C", str(repo_root), "cat-file", "-t", "ffa844b"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("git history unavailable in this environment")

    assert result.stdout.strip() == "commit"
