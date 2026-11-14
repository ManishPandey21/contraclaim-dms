"""Release 1.2: the extraction pipeline generates the grounded summary last.

Drives the real service -> processor -> engine -> page store chain
(``retry_harness``); only OCR, OpenAI and graph publication are stubbed. The
summary must describe the canonical evidence this run published - scanned
pages included - and must never be able to fail or alter the extraction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rbac_backend.services.extraction_adapters.document_page_store import DOCUMENT_EXTRACTION_HEADS
from rbac_backend.services.grounded_summary.service import GroundedSummaryService
from rbac_backend.tests.retry_harness import RetryHarness

COVER_OCR = (
    "We request the Engineer to grant extension of time for the delay caused by the late handover "
    "of the work front at Pier P-114."
)
NATIVE_BODY = "The work front was not made available until 12 August 2024 owing to utility diversion."


async def test_pipeline_summarises_the_published_evidence_including_scanned_pages(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await RetryHarness.create(
        tmp_path, monkeypatch, page_lines=[None, [NATIVE_BODY]], script={1: {1: COVER_OCR}}
    )

    await harness.run_attempt()
    document = await harness.document()
    head = await harness.db[DOCUMENT_EXTRACTION_HEADS].find_one({"document_id": str(harness.document_id)})

    state = document["summary_state"]
    assert state["status"] in {"current", "degraded"}, state
    detailed = document["detailed_summary"]
    assert detailed["generated_from"]["canonical_revision"] == head["canonical_revision"]
    assert detailed["generated_from"]["canonical_sha256"] == head["canonical_sha256"]
    requests = [item for item in detailed["requests_relief"] if "extension of time" in item["text"]]
    # The scanned cover page's request is in the summary, cited to page 1.
    assert requests and requests[0]["page_numbers"] == [1]
    assert any("12 August 2024" in item["text"] for item in detailed["dates_periods"])


async def test_a_summary_failure_never_fails_the_extraction(tmp_path: Path, monkeypatch: Any) -> None:
    async def _boom(self: Any, document_id: str, *, trigger: str) -> Any:
        raise RuntimeError("summary service down")

    monkeypatch.setattr(GroundedSummaryService, "generate", _boom)
    harness = await RetryHarness.create(
        tmp_path, monkeypatch, page_lines=[[NATIVE_BODY]], script={}
    )

    succeeded = await harness.run_attempt()
    document = await harness.document()

    assert succeeded is True
    assert document["processing_status"] in {"metadata_extracted", "completed"}
    assert "detailed_summary" not in document
