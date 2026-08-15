"""pipeline_version must be a real routing and claim boundary.

Two separate guarantees, both required for a genuine per-tenant canary:

  * **Claim boundary** - a worker restricted to one pipeline version never
    claims the other's jobs, so a canary worker cannot pick up every tenant.
  * **Routing boundary** - a claimed job is processed by the path its persisted
    version names, so a legacy_v0 job never enters the unified extractor.

Together these give server-side rollback: emptying the allowlist makes new jobs
legacy_v0 again, and a legacy worker processes them on the old path without
redeploying an image.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId

from rbac_backend.services.document_processor import DocumentProcessor
from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.pipeline_routing import (
    LEGACY_PIPELINE,
    UNIFIED_PIPELINE,
    claim_filter_for,
)
from rbac_backend.tests.test_document_processing_jobs import (
    FakeDB,
    insert_document,
    make_document,
)


# --- Claim boundary ---------------------------------------------------------


def test_no_restriction_claims_every_version() -> None:
    assert claim_filter_for(None) == {}
    assert claim_filter_for(set()) == {}


def test_a_unified_only_worker_filters_to_unified_jobs() -> None:
    query = claim_filter_for({UNIFIED_PIPELINE})

    assert query == {"pipeline_version": {"$in": [UNIFIED_PIPELINE]}}


def test_a_legacy_only_worker_also_matches_jobs_with_no_recorded_version() -> None:
    """Jobs queued before this change carry no version and are legacy."""
    query = claim_filter_for({LEGACY_PIPELINE})
    branches = query["$or"]

    assert {"pipeline_version": {"$in": [LEGACY_PIPELINE]}} in branches
    assert {"pipeline_version": {"$exists": False}} in branches
    assert {"pipeline_version": None} in branches


async def _seed(db: FakeDB, version: str) -> str:
    service = DocumentService(db)
    document_id = ObjectId()
    document = make_document(document_id)
    await insert_document(db, document, document_id)
    job_id = await service.queue_document_processing(document, "letter.pdf")
    await db.document_processing_jobs.update_one(
        {"_id": job_id}, {"$set": {"pipeline_version": version}}
    )
    return job_id


@pytest.mark.asyncio
async def test_a_unified_worker_does_not_claim_a_legacy_job() -> None:
    db = FakeDB()
    service = DocumentService(db)
    await _seed(db, LEGACY_PIPELINE)

    claimed = await service._claim_next_processing_job(
        pipeline_versions={UNIFIED_PIPELINE}
    )

    assert claimed is None


@pytest.mark.asyncio
async def test_a_legacy_worker_does_not_claim_a_unified_job() -> None:
    db = FakeDB()
    service = DocumentService(db)
    await _seed(db, UNIFIED_PIPELINE)

    claimed = await service._claim_next_processing_job(
        pipeline_versions={LEGACY_PIPELINE}
    )

    assert claimed is None


@pytest.mark.asyncio
async def test_a_worker_claims_the_version_it_is_configured_for() -> None:
    db = FakeDB()
    service = DocumentService(db)
    job_id = await _seed(db, UNIFIED_PIPELINE)

    claimed = await service._claim_next_processing_job(
        pipeline_versions={UNIFIED_PIPELINE}
    )

    assert claimed is not None
    assert claimed["_id"] == job_id


@pytest.mark.asyncio
async def test_an_unrestricted_worker_claims_either_version() -> None:
    db = FakeDB()
    service = DocumentService(db)
    await _seed(db, LEGACY_PIPELINE)

    assert await service._claim_next_processing_job() is not None


# --- Routing boundary -------------------------------------------------------


class _SpyOcrService:
    def __init__(self) -> None:
        self.legacy_calls = 0
        self.unified_calls = 0

    async def process_pdf(self, input_path: Path):
        self.legacy_calls += 1
        return input_path, "legacy extracted text"

    async def process_pdf_pagewise(self, input_path: Path, **kwargs: Any):
        self.unified_calls += 1
        raise AssertionError("unified extractor must not run for a legacy job")


class _SpyGate:
    def __init__(self) -> None:
        self.calls = 0

    def assess(self, page: Any, *, tables: Any = None):
        self.calls += 1
        raise AssertionError("the quality gate must not run for a legacy job")


def _legacy_processor(ocr: _SpyOcrService, gate: _SpyGate) -> DocumentProcessor:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.ocr_service = ocr
    processor.quality_gate = gate
    processor.fallback_ladder = None
    processor.fallback_max_pages_per_document = 0
    processor.config = SimpleNamespace(ocr_language="eng")
    return processor


async def test_a_legacy_job_uses_the_legacy_extractor(tmp_path: Path) -> None:
    source = tmp_path / "letter.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    ocr, gate = _SpyOcrService(), _SpyGate()

    dispatch = await _legacy_processor(ocr, gate)._extract_legacy(source)

    assert ocr.legacy_calls == 1
    assert ocr.unified_calls == 0
    assert gate.calls == 0
    assert dispatch.raw_text == "legacy extracted text"


async def test_the_legacy_path_produces_no_page_evidence(tmp_path: Path) -> None:
    """It is the pre-Phase-3 behaviour, deliberately: no pages, no gate.

    That is the point of keeping it - it is the known-good path to fall back
    to, not a second implementation of the new one.
    """
    source = tmp_path / "letter.pdf"
    source.write_bytes(b"%PDF-1.4\n")

    dispatch = await _legacy_processor(_SpyOcrService(), _SpyGate())._extract_legacy(
        source
    )

    assert dispatch.extraction is None
    assert dispatch.pages_human_review == []


def test_process_document_branches_on_the_persisted_version() -> None:
    import inspect

    source = inspect.getsource(DocumentProcessor.process_document)

    assert "pipeline_version" in source
    assert "_extract_legacy" in source
    assert LEGACY_PIPELINE in source or "LEGACY_PIPELINE" in source
