"""Tests for per-document processing-state enrichment of bulk upload status."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import pytest
from bson import ObjectId

from rbac_backend.models.document import BulkUploadStatus, DocumentProcessingResult
from rbac_backend.routers.documents import DocumentController


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    def __aiter__(self):
        async def _gen():
            for row in self._rows:
                yield row

        return _gen()


class _Collection:
    def __init__(self, rows: Iterable[Dict[str, Any]] | None = None) -> None:
        self.rows = [dict(r) for r in rows or []]

    def find(self, filter: Dict[str, Any], _projection: Optional[Dict[str, Any]] = None) -> _Cursor:
        wanted = {str(v) for v in (filter.get("_id") or {}).get("$in", [])}
        return _Cursor([r for r in self.rows if str(r["_id"]) in wanted])


class _Db:
    def __init__(self, documents: Iterable[Dict[str, Any]], jobs: Iterable[Dict[str, Any]]) -> None:
        self.documents = _Collection(documents)
        self.document_processing_jobs = _Collection(jobs)


def _controller() -> DocumentController:
    return DocumentController(
        document_service=SimpleNamespace(),
        file_service=SimpleNamespace(),
        export_service=SimpleNamespace(),
        auth_service=SimpleNamespace(),
        bulk_upload_service=SimpleNamespace(),
    )


def _job_status(results: List[DocumentProcessingResult]) -> BulkUploadStatus:
    return BulkUploadStatus(
        job_id="bulk-1",
        total_files=len(results),
        processed_files=len(results),
        successful_uploads=sum(1 for r in results if r.success),
        failed_uploads=sum(1 for r in results if not r.success),
        status="completed",
        results=results,
    )


@pytest.mark.asyncio
async def test_bulk_status_gains_live_processing_state(monkeypatch) -> None:
    doc_id = ObjectId()
    fake_db = _Db(
        documents=[
            {
                "_id": doc_id,
                "processing_status": "processing",
                "processing_job_id": "pjob-1",
                "duplicate_status": None,
            }
        ],
        jobs=[{"_id": "pjob-1", "stage": "extracting", "status": "processing"}],
    )

    async def _fake_get_database() -> _Db:
        return fake_db

    monkeypatch.setattr("rbac_backend.routers.documents.get_database", _fake_get_database)

    results = [
        DocumentProcessingResult(filename="a.pdf", success=True, document_id=str(doc_id)),
        DocumentProcessingResult(filename="b.pdf", success=False, error="CSV row invalid"),
    ]
    enriched = await _controller()._enrich_bulk_results_with_processing_state(
        _job_status(results)
    )

    first, second = enriched.results
    assert first.processing_status == "processing"
    assert first.processing_stage == "extracting"
    assert second.processing_status is None
    assert second.error == "CSV row invalid"


@pytest.mark.asyncio
async def test_bulk_status_reports_duplicate_state(monkeypatch) -> None:
    doc_id = ObjectId()
    fake_db = _Db(
        documents=[
            {
                "_id": doc_id,
                "processing_status": "completed",
                "processing_job_id": "pjob-9",
                "duplicate_status": "duplicate",
            }
        ],
        jobs=[{"_id": "pjob-9", "stage": "duplicate_detected", "status": "completed"}],
    )

    async def _fake_get_database() -> _Db:
        return fake_db

    monkeypatch.setattr("rbac_backend.routers.documents.get_database", _fake_get_database)

    enriched = await _controller()._enrich_bulk_results_with_processing_state(
        _job_status(
            [DocumentProcessingResult(filename="dup.pdf", success=True, document_id=str(doc_id))]
        )
    )

    assert enriched.results[0].duplicate_status == "duplicate"
    assert enriched.results[0].processing_stage == "duplicate_detected"


@pytest.mark.asyncio
async def test_bulk_status_enrichment_is_best_effort(monkeypatch) -> None:
    async def _boom() -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("rbac_backend.routers.documents.get_database", _boom)

    status = _job_status(
        [DocumentProcessingResult(filename="a.pdf", success=True, document_id=str(ObjectId()))]
    )
    enriched = await _controller()._enrich_bulk_results_with_processing_state(status)

    # The endpoint must still answer with the stored status.
    assert enriched.results[0].filename == "a.pdf"
    assert enriched.results[0].processing_status is None
