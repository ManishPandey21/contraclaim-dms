"""End-to-end acceptance for unified page extraction.

This is the release gate for Phases 3-7. It exercises the real extraction
engine, source dispatch, quality gate and fallback decision against real
fixtures, and proves the property the whole programme rests on:

    a correct document costs nothing.

Requires a Mongo replica set for the durable-job half, so it skips unless
RUN_UNIFIED_EXTRACTION_E2E=1. With that variable set, an unreachable database
is a failure rather than a skip - a silently skipped release gate is worse than
no gate at all.

Run inside the backend container:

    docker compose --env-file .env -f docker-compose.prod.yml \\
      -f docker-compose.mongo-replicaset.yml \\
      run --rm -e RUN_UNIFIED_EXTRACTION_E2E=1 backend \\
      python -m pytest rbac_backend/tests/integration/test_unified_extraction_e2e.py -q
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Sequence

import pytest

from rbac_backend.services.extraction.engine import PageExtractionEngine
from rbac_backend.services.extraction.fallback.ladder import ExtractionFallbackLadder
from rbac_backend.services.extraction.fallback.ledger import InterventionLedger
from rbac_backend.services.extraction.fallback.models import (
    FallbackOutcome,
    Reconstruction,
    Tier,
)
from rbac_backend.services.extraction.models import (
    Completeness,
    PageExtractionPolicy,
    PageSource,
)
from rbac_backend.services.extraction.page_store import NullPageStore
from rbac_backend.services.extraction.quality.gate import ExtractionQualityGate
from rbac_backend.services.extraction.rasterizer import PageRasterizer
from rbac_backend.tests.fixtures.pdf_builders import (
    CLEAN_NATIVE_PAGE_COUNT,
    build_clean_native_pdf,
    build_mixed_pdf,
    build_scanned_only_pdf,
)

REQUIRES_DB = pytest.mark.skipif(
    os.environ.get("RUN_UNIFIED_EXTRACTION_E2E") != "1",
    reason="set RUN_UNIFIED_EXTRACTION_E2E=1 with a Mongo replica set to run the "
    "durable-job half of the acceptance gate",
)


class _FailIfCalledOcrRunner:
    async def run(self, source: Path, page_numbers: Sequence[int], language: str):
        raise AssertionError(
            f"OCR must not run on a clean native fixture (pages {list(page_numbers)})"
        )


class _CountingReconstructionModel:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.calls = 0

    async def reconstruct(self, evidence: Any, tier: Tier) -> Reconstruction:
        self.calls += 1
        return Reconstruction(
            text=self.text,
            confidence=0.99,
            model="counting-stub",
            model_version="1",
            prompt_version="v1",
        )


class _FakeCollection:
    def __init__(self) -> None:
        self.inserted: list[Dict[str, Any]] = []

    async def insert_one(self, document: Dict[str, Any]) -> Any:
        self.inserted.append(document)
        return type("R", (), {"inserted_id": "oid"})()


class _FakeDb:
    def __init__(self) -> None:
        self.collections: Dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection())


def _policy() -> PageExtractionPolicy:
    return PageExtractionPolicy(
        ocr_enabled=True,
        min_text_chars_per_page=40,
        batch_size=25,
        max_ocr_pages_per_attempt=0,
        ocr_language="eng",
    )


async def _extract(source: Path, ocr_runner: Any):
    engine = PageExtractionEngine(
        policy=_policy(), ocr_runner=ocr_runner, store=NullPageStore()
    )
    return await engine.extract(source)


# --- The zero-cost gate -----------------------------------------------------


async def test_clean_document_needs_no_ocr(tmp_path: Path) -> None:
    source = build_clean_native_pdf(tmp_path / "clean.pdf")

    # _FailIfCalledOcrRunner raises if the engine routes any page to OCR.
    result = await _extract(source, _FailIfCalledOcrRunner())

    assert len(result.pages) == CLEAN_NATIVE_PAGE_COUNT
    assert result.ocr_pages_total == 0
    assert result.completeness is Completeness.COMPLETE
    assert all(page.source is PageSource.TEXT_LAYER for page in result.pages)


async def test_clean_document_costs_zero_model_calls(tmp_path: Path) -> None:
    """The property the whole programme rests on: a correct document is free."""
    source = build_clean_native_pdf(tmp_path / "clean.pdf")
    db = _FakeDb()
    tier1, tier2 = _CountingReconstructionModel(), _CountingReconstructionModel()
    gate = ExtractionQualityGate()
    ladder = ExtractionFallbackLadder(
        gate=gate,
        rasterizer=PageRasterizer(dpi=72),
        ledger=InterventionLedger(db=db),
        tier1=tier1,
        tier2=tier2,
    )

    result = await _extract(source, _FailIfCalledOcrRunner())
    for page in result.pages:
        verdict = gate.assess(page, tables=page.tables or None)
        resolved = await ladder.resolve(
            source, page, verdict, document_id="doc-clean"
        )
        assert resolved.outcome is FallbackOutcome.UNCHANGED

    assert tier1.calls == 0
    assert tier2.calls == 0
    assert db[InterventionLedger.COLLECTION].inserted == []


async def test_clean_document_escalates_nothing(tmp_path: Path) -> None:
    """No page escalates.

    Note the verdict is NOT_CHECKABLE rather than PASS: a text-only page with
    no ruled table gives `extract_tables()` nothing to find, so no substantive
    check can run. That is the correct outcome - nothing was verified, so
    nothing is claimed as verified - and crucially it does not escalate.
    """
    source = build_clean_native_pdf(tmp_path / "clean.pdf")
    gate = ExtractionQualityGate()

    result = await _extract(source, _FailIfCalledOcrRunner())
    verdicts = [gate.assess(page, tables=page.tables or None) for page in result.pages]

    assert all(not verdict.escalates for verdict in verdicts)
    assert all(verdict.repairs == [] for verdict in verdicts)


# --- The failure paths ------------------------------------------------------


async def test_scanned_document_routes_every_page_to_ocr(tmp_path: Path) -> None:
    source = build_scanned_only_pdf(tmp_path / "scan.pdf", pages=3)
    requested: list[list[int]] = []

    class _Recording:
        async def run(self, src, page_numbers, language):
            requested.append(list(page_numbers))
            return {page: f"RECOVERED {page}" for page in page_numbers}

    result = await _extract(source, _Recording())

    assert requested == [[1, 2, 3]]
    assert result.ocr_pages_total == 3


async def test_failed_ocr_page_ends_needing_review_not_completed(
    tmp_path: Path,
) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    class _Failing:
        async def run(self, src, page_numbers, language):
            raise RuntimeError("ocr unavailable")

    result = await _extract(source, _Failing())

    assert result.ocr_failed_pages == [1, 2]
    assert result.completeness is Completeness.PARTIAL


async def test_a_genuinely_blank_page_costs_nothing(tmp_path: Path) -> None:
    """An empty page that is *supposed* to be empty must not escalate.

    Fixture limitation worth recording: pages 1-2 of build_mixed_pdf carry no
    content objects at all, so the classifier calls them BLANK rather than
    SCANNED_IMAGE. A real scanned page carries a full-page raster and would
    classify SCANNED_IMAGE, whose empty text density DOES fail and escalate -
    that path is covered by the gate and ladder unit suites. Building a
    fixture with a real page raster belongs with the Phase 9 asset work.
    """
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    db = _FakeDb()
    gate = ExtractionQualityGate()
    tier1 = _CountingReconstructionModel(text="")
    ladder = ExtractionFallbackLadder(
        gate=gate,
        rasterizer=PageRasterizer(dpi=72),
        ledger=InterventionLedger(db=db),
        tier1=tier1,
    )

    class _Empty:
        async def run(self, src, page_numbers, language):
            return {page: "" for page in page_numbers}

    result = await _extract(source, _Empty())
    blank_page = next(page for page in result.pages if page.number == 1)
    verdict = gate.assess(blank_page, tables=None)

    resolved = await ladder.resolve(source, blank_page, verdict, document_id="doc-x")

    assert verdict.escalates is False
    assert resolved.outcome is FallbackOutcome.UNCHANGED
    assert tier1.calls == 0
    assert db[InterventionLedger.COLLECTION].inserted == []


# --- The durable-job half, which needs a real replica set -------------------


@REQUIRES_DB
async def test_document_job_clean_fixture_is_gated_persisted_and_costs_zero() -> None:
    pytest.fail(
        "Not implemented: this half of the acceptance gate requires the "
        "Mongo-backed DocumentService job path with an injectable processor "
        "factory. It has not been written or executed - see the Phase 7 report."
    )
