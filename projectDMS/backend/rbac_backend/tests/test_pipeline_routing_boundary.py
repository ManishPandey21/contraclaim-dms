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
    resolve_pipeline_version,
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


# --- Which pipeline a NEW job is assigned -----------------------------------


def test_a_canary_organisation_is_assigned_the_unified_pipeline() -> None:
    assert (
        resolve_pipeline_version(
            organization_id="org-demo",
            enabled=False,
            canary_org_ids={"org-demo"},
        )
        == UNIFIED_PIPELINE
    )


def test_a_non_canary_organisation_stays_legacy() -> None:
    assert (
        resolve_pipeline_version(
            organization_id="org-other",
            enabled=False,
            canary_org_ids={"org-demo"},
        )
        == LEGACY_PIPELINE
    )


@pytest.mark.parametrize("missing", [None, ""])
def test_a_missing_organisation_stays_legacy(missing) -> None:
    """No scope is not a reason to run newer code."""
    assert (
        resolve_pipeline_version(
            organization_id=missing,
            enabled=False,
            canary_org_ids={"org-demo"},
        )
        == LEGACY_PIPELINE
    )


def test_the_global_flag_promotes_every_organisation() -> None:
    assert (
        resolve_pipeline_version(
            organization_id="org-anything",
            enabled=True,
            canary_org_ids=set(),
        )
        == UNIFIED_PIPELINE
    )


@pytest.mark.parametrize(
    "candidate", ["org-demo-2", "org-demo ", " org-demo", "ORG-DEMO", "org-dem"]
)
def test_the_allowlist_matches_exactly_and_not_by_prefix(candidate: str) -> None:
    """`org-demo` must not drag `org-demo-2` into the canary."""
    assert (
        resolve_pipeline_version(
            organization_id=candidate,
            enabled=False,
            canary_org_ids={"org-demo"},
        )
        == LEGACY_PIPELINE
    )


def test_clearing_the_allowlist_only_affects_new_jobs() -> None:
    """Rollback step one: new work goes legacy again."""
    assert (
        resolve_pipeline_version(
            organization_id="org-demo", enabled=False, canary_org_ids=set()
        )
        == LEGACY_PIPELINE
    )


@pytest.mark.asyncio
async def test_an_in_flight_job_keeps_its_version_when_the_allowlist_empties() -> None:
    """The persisted decision is what a worker honours, not current config.

    If this ever regressed, emptying the allowlist mid-run would silently
    reclassify jobs already in flight.
    """
    db = FakeDB()
    service = DocumentService(db)
    job_id = await _seed(db, UNIFIED_PIPELINE)

    # Rollback happens: allowlist emptied, global flag still off.
    assert (
        resolve_pipeline_version(
            organization_id="org-demo", enabled=False, canary_org_ids=set()
        )
        == LEGACY_PIPELINE
    )

    job = await db.document_processing_jobs.find_one({"_id": job_id})
    assert job["pipeline_version"] == UNIFIED_PIPELINE

    # And it is still claimable only by a unified worker.
    assert await service._claim_next_processing_job(
        pipeline_versions={LEGACY_PIPELINE}
    ) is None
    claimed = await service._claim_next_processing_job(
        pipeline_versions={UNIFIED_PIPELINE}
    )
    assert claimed is not None
    assert claimed["_id"] == job_id


# --- Two workers running at once --------------------------------------------


@pytest.mark.asyncio
async def test_simultaneous_workers_cannot_cross_claim() -> None:
    """One legacy job and one unified job, two restricted workers.

    Each takes exactly its own and neither can take the other's, whichever
    order they run in.
    """
    db = FakeDB()
    service = DocumentService(db)
    legacy_job = await _seed(db, LEGACY_PIPELINE)
    unified_job = await _seed(db, UNIFIED_PIPELINE)

    canary = await service._claim_next_processing_job(
        pipeline_versions={UNIFIED_PIPELINE}
    )
    default = await service._claim_next_processing_job(
        pipeline_versions={LEGACY_PIPELINE}
    )

    assert canary is not None and canary["_id"] == unified_job
    assert default is not None and default["_id"] == legacy_job

    # Queue drained from each worker's perspective; neither sees the other's.
    assert (
        await service._claim_next_processing_job(pipeline_versions={UNIFIED_PIPELINE})
        is None
    )
    assert (
        await service._claim_next_processing_job(pipeline_versions={LEGACY_PIPELINE})
        is None
    )


@pytest.mark.asyncio
async def test_a_legacy_worker_claims_a_historical_job_with_no_version() -> None:
    """Everything queued before this change must still be processed."""
    db = FakeDB()
    service = DocumentService(db)
    job_id = await _seed(db, LEGACY_PIPELINE)
    await db.document_processing_jobs.update_one(
        {"_id": job_id}, {"$unset": {"pipeline_version": ""}}
    )

    claimed = await service._claim_next_processing_job(
        pipeline_versions={LEGACY_PIPELINE}
    )

    assert claimed is not None and claimed["_id"] == job_id


@pytest.mark.asyncio
async def test_a_unified_worker_ignores_a_historical_job_with_no_version() -> None:
    db = FakeDB()
    service = DocumentService(db)
    job_id = await _seed(db, LEGACY_PIPELINE)
    await db.document_processing_jobs.update_one(
        {"_id": job_id}, {"$unset": {"pipeline_version": ""}}
    )

    assert (
        await service._claim_next_processing_job(pipeline_versions={UNIFIED_PIPELINE})
        is None
    )


@pytest.mark.asyncio
async def test_the_unrestricted_claim_query_is_unchanged_outside_canary_mode() -> None:
    """Outside a canary there is one worker and no restriction, so the query
    must be exactly what it was before any of this existed.
    """
    assert claim_filter_for(None) == {}

    db = FakeDB()
    service = DocumentService(db)
    legacy_job = await _seed(db, LEGACY_PIPELINE)
    unified_job = await _seed(db, UNIFIED_PIPELINE)

    claimed = [
        await service._claim_next_processing_job(),
        await service._claim_next_processing_job(),
    ]

    assert {c["_id"] for c in claimed if c} == {legacy_job, unified_job}


# --- Rollback preserves evidence identities ---------------------------------


@pytest.mark.asyncio
async def test_rollback_does_not_rewrite_run_or_evidence_identities() -> None:
    """Emptying the allowlist is a routing change, not a data change.

    A paused canary job must keep its extraction_run_id, its attempts, and its
    persisted version so its page evidence stays addressable.
    """
    db = FakeDB()
    job_id = await _seed(db, UNIFIED_PIPELINE)
    await db.document_processing_jobs.update_one(
        {"_id": job_id},
        {"$set": {"extraction_run_id": "run-abc", "attempts": 2}},
    )
    before = await db.document_processing_jobs.find_one({"_id": job_id})

    # Rollback: the allowlist empties. Nothing touches existing jobs.
    assert (
        resolve_pipeline_version(
            organization_id="org-demo", enabled=False, canary_org_ids=set()
        )
        == LEGACY_PIPELINE
    )

    after = await db.document_processing_jobs.find_one({"_id": job_id})
    assert after["extraction_run_id"] == before["extraction_run_id"] == "run-abc"
    assert after["attempts"] == before["attempts"] == 2
    assert after["pipeline_version"] == UNIFIED_PIPELINE


# --- The dispatcher actually invokes different implementations --------------


class _DispatchSpy:
    """Records which extractor ran, without either one raising."""

    def __init__(self) -> None:
        self.legacy_calls = 0
        self.pagewise_calls = 0

    async def process_pdf(self, input_path: Path):
        self.legacy_calls += 1
        return input_path, "legacy text"

    async def process_pdf_pagewise(self, input_path: Path, **kwargs: Any):
        self.pagewise_calls += 1
        raise RuntimeError("unified extractor reached")


def _dispatch_processor(spy: _DispatchSpy) -> DocumentProcessor:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.ocr_service = spy
    processor.quality_gate = _SpyGate()
    processor.fallback_ladder = None
    processor.fallback_max_pages_per_document = 0
    processor.config = SimpleNamespace(max_file_size_mb=50, ocr_language="eng")
    return processor


@pytest.mark.asyncio
async def test_the_dispatcher_runs_the_legacy_extractor_for_a_legacy_job(
    tmp_path: Path,
) -> None:
    """Real execution through process_document, not a source-string check."""
    source = tmp_path / "letter.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    spy = _DispatchSpy()
    processor = _dispatch_processor(spy)
    # Persistence is out of scope here; make it fail loudly and inspect which
    # extractor ran before that point.
    processor._extract_and_persist = None  # type: ignore[assignment]

    await processor.process_document(
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        pipeline_version=LEGACY_PIPELINE,
    )

    assert spy.legacy_calls == 1
    assert spy.pagewise_calls == 0


@pytest.mark.asyncio
async def test_the_dispatcher_runs_the_unified_extractor_for_a_unified_job(
    tmp_path: Path,
) -> None:
    """The mirror image: a unified job must NOT reach the legacy extractor."""
    source = tmp_path / "letter.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    spy = _DispatchSpy()
    processor = _dispatch_processor(spy)
    processor.database_service = SimpleNamespace(
        get_database=lambda: (_ for _ in ()).throw(RuntimeError("unified reached")),
        close_connection=_anoop,
    )

    result = await processor.process_document(
        str(source),
        path_structure="org/proj",
        upload_type="incoming",
        pipeline_version=UNIFIED_PIPELINE,
    )

    assert result.success is False
    assert spy.legacy_calls == 0, "a unified job must never run the legacy extractor"


async def _anoop() -> None:
    return None


@pytest.mark.asyncio
async def test_both_routes_converge_on_one_persistence_implementation() -> None:
    """There must be exactly one `_extract_and_persist` call site per route and
    no second copy of the persistence logic.
    """
    import inspect

    source = inspect.getsource(DocumentProcessor.process_document)

    assert source.count("_extract_and_persist(") == 2
    # The downstream steps must live only in the shared method.
    persist = inspect.getsource(DocumentProcessor._extract_and_persist)
    assert "_save_results(" in persist
    assert "_save_results(" not in source
