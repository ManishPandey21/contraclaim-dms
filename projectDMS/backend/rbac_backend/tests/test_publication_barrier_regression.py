"""G23: content must not be published before the quality decision is complete.

The ordering was inverted. `_extract_and_persist` ran `_save_results` ->
`save_document_data(...)`, which upserts the canonical text and creates Qdrant
embeddings, and returned `success=True`. Only afterwards did
`document_service._checkpoint_extraction_attempt` derive the state and set
`human_review_required`.

So a page the gate had already failed was embedded, retrievable and usable for
drafting *before* anyone was told it needed review. "Fail closed" was a status
label written late, not a containment boundary.

There are two publication boundaries, not one:

  * `save_document_data` - canonical Mongo text plus Qdrant vectors;
  * `graph_ingestion.ingest_document` / `evidence_graph` in DocumentService,
    already guarded by `duplicate_pending`.

The invariant: one publishability decision, computed once from the quality
outcome, honoured at every boundary. A blocked document may keep raw evidence
and diagnostics; it may not become authoritative or retrievable.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from rbac_backend.models.document_metadata import ParsedDocumentMetadata
from rbac_backend.models.processing_state import Completeness
from rbac_backend.services.document_processor import DocumentProcessor


class RecordingDatabaseService:
    """Stands in for the real persistence + embedding boundary."""

    def __init__(self) -> None:
        self.saved: List[Dict[str, Any]] = []
        self.embedding_calls: List[Dict[str, Any]] = []
        self.partial_failures: Dict[str, Any] = {}
        self.closed = 0

    async def save_document_data(self, **kwargs: Any) -> int:
        self.saved.append(kwargs)
        # Mirrors the real service: vectors are created unless deferred.
        if not kwargs.get("skip_embeddings"):
            self.embedding_calls.append(kwargs)
        return 3

    async def close_connection(self) -> None:
        self.closed += 1


class _FileService:
    def __init__(self) -> None:
        self.summaries: List[tuple] = []

    async def save_summary(self, *args: Any) -> None:
        self.summaries.append(args)


class _OpenAI:
    async def process_text(self, text: str, *, filename: str, include_full_content: bool = True) -> str:
        return "REPORT"

    async def upload_file(self, path: str) -> str:
        return "file-1"

    async def process_document(self, file_id: str) -> str:
        return "REPORT"

    async def cleanup_file(self, file_id: str) -> None:
        return None


class _TextService:
    def parse_extraction_report(self, content: str) -> ParsedDocumentMetadata:
        return ParsedDocumentMetadata(subject="parsed")


def _processor() -> DocumentProcessor:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.openai_service = _OpenAI()
    processor.text_service = _TextService()
    processor.file_service = _FileService()
    processor.database_service = RecordingDatabaseService()
    processor.pydantic_ai_service = SimpleNamespace(
        is_enabled=False, extract_metadata=None
    )
    processor.config = SimpleNamespace(
        max_file_size_mb=50,
        ocr_language="eng",
        use_pydantic_ai=False,
        openai_api_key=None,
    )
    return processor


def _persist(tmp_path: Path, *, pages_human_review: List[int]):
    """Drive `_extract_and_persist` exactly as the unified path does."""
    processor = _processor()
    source = tmp_path / "claim.pdf"
    source.write_bytes(b"%PDF-1.4\n")

    extraction = SimpleNamespace(
        pages=[],
        combined_text="Mobilization 1,900,000",
        completeness=(
            Completeness.PARTIAL if pages_human_review else Completeness.COMPLETE
        ),
    )

    result = asyncio.run(
        processor._extract_and_persist(
            input_path=source,
            original_path=str(source),
            processed_path=source,
            raw_ocr_text="Mobilization 1,900,000",
            extraction=extraction,
            pages_human_review=pages_human_review,
            path_structure="org/proj",
            upload_type="incoming",
            document_id="doc-1",
            skip_embeddings=False,
            start_time=0.0,
        )
    )
    return processor, result


# --- The defect ---------------------------------------------------------------


def test_a_page_needing_review_does_not_get_embedded(tmp_path: Path) -> None:
    """The headline containment failure."""
    processor, _ = _persist(tmp_path, pages_human_review=[3])

    assert processor.database_service.embedding_calls == [], (
        "a document with an unresolved blocking page was embedded into the "
        "vector store and is therefore retrievable before any human saw it"
    )


def test_a_blocked_document_is_not_reported_publishable(tmp_path: Path) -> None:
    """`success=True` must not be overloaded to mean 'safe to publish'."""
    _, result = _persist(tmp_path, pages_human_review=[3])

    assert getattr(result, "publishable", None) is False, (
        "the result carries no publishability decision, so every downstream "
        "boundary has to re-derive it and one of them will forget"
    )


def test_a_blocked_document_still_persists_its_diagnostic_evidence(
    tmp_path: Path,
) -> None:
    """Containment is not deletion: review needs the raw evidence."""
    processor, _ = _persist(tmp_path, pages_human_review=[3])

    assert processor.database_service.saved, (
        "blocking publication must not also discard the document record that "
        "a reviewer needs"
    )


def test_a_blocked_document_records_why_vectors_were_withheld(
    tmp_path: Path,
) -> None:
    processor, _ = _persist(tmp_path, pages_human_review=[3])
    saved = processor.database_service.saved[0]

    assert saved.get("skip_embeddings") is True


# --- The other half: clean documents must still publish -----------------------


def test_a_clean_document_is_still_embedded(tmp_path: Path) -> None:
    processor, result = _persist(tmp_path, pages_human_review=[])

    assert processor.database_service.embedding_calls, (
        "the barrier must not block documents that passed"
    )
    assert getattr(result, "publishable", None) is True


def test_a_clean_document_reports_success(tmp_path: Path) -> None:
    _, result = _persist(tmp_path, pages_human_review=[])

    assert result.success is True
    assert result.chunks_created == 3


def test_an_explicit_deferral_still_wins(tmp_path: Path) -> None:
    """Duplicate-pending deferral must keep working alongside the new barrier."""
    processor = _processor()
    source = tmp_path / "dup.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    extraction = SimpleNamespace(
        pages=[], combined_text="text", completeness=Completeness.COMPLETE
    )

    asyncio.run(
        processor._extract_and_persist(
            input_path=source,
            original_path=str(source),
            processed_path=source,
            raw_ocr_text="text",
            extraction=extraction,
            pages_human_review=[],
            path_structure="org/proj",
            upload_type="incoming",
            document_id="doc-2",
            skip_embeddings=True,
            start_time=0.0,
        )
    )

    assert processor.database_service.embedding_calls == []
