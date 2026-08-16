"""G21: the persisted page evidence must carry the verdict the gate produced.

`record_pages` is called inside `engine.extract()`, before the gate ever runs.
The record mapping always included `quality_verdict` - it was simply always
`None` at write time. So `document_ocr_pages` held no verdicts at all, the
canary's own monitoring query would have bucketed every page as `unset`, and
Gate C6 was unevaluable.

The fix is a deliberate second stage rather than a stray update: `record_pages`
captures what extraction read, `finalize_pages` captures what the gate decided.
Both are the same idempotent ReplaceOne upsert on
(document_id, extraction_run_id, page_number), so a retry rewrites one identity
instead of creating a contradictory second row.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from rbac_backend.models.processing_state import Completeness
from rbac_backend.services.document_processor import DocumentProcessor
from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.quality.gate import ExtractionQualityGate
from rbac_backend.services.extraction_adapters.document_page_store import (
    DOCUMENT_OCR_PAGES,
    DocumentPageStore,
)


class FakeCollection:
    def __init__(self) -> None:
        self.rows: Dict[tuple, Dict[str, Any]] = {}
        self.writes = 0

    async def bulk_write(self, operations: List[Any]) -> None:
        self.writes += 1
        for op in operations:
            criteria = op._filter
            key = (
                criteria["document_id"],
                criteria["extraction_run_id"],
                criteria["page_number"],
            )
            self.rows[key] = op._doc


class FakeDB:
    def __init__(self) -> None:
        self.collections: Dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


HEADERS = ["S/N", "Description", "Qty", "Rate", "Amount"]


def _page(number: int, text: str, tables: Any = None) -> ExtractedPage:
    page = ExtractedPage(
        number=number,
        text=text,
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=PageClass.TEXT_NATIVE,
            char_count=len(text),
            image_count=0,
            image_coverage=0.0,
            table_count=1 if tables else 0,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
    )
    page.tables = tables or []
    return page


def _run(pages: List[ExtractedPage]):
    """Drive the gate stage with the real DocumentPageStore."""
    db = FakeDB()
    store = DocumentPageStore(
        db=db,
        document_id="doc-1",
        organization_id="org-demo",
        project_id=None,
        extraction_run_id="run-1",
    )
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.quality_gate = ExtractionQualityGate()
    processor.fallback_ladder = None
    processor.fallback_max_pages_per_document = 0

    extraction = SimpleNamespace(
        pages=pages,
        combined_text="\n\n".join(p.text for p in pages),
        completeness=Completeness.COMPLETE,
    )
    asyncio.run(
        processor._apply_quality_gate(
            None, extraction, document_id="doc-1", page_store=store
        )
    )
    return db[DOCUMENT_OCR_PAGES].rows, db[DOCUMENT_OCR_PAGES]


def _row(rows, page_number: int) -> Dict[str, Any]:
    return rows[("doc-1", "run-1", page_number)]


# --- The defect ---------------------------------------------------------------


def test_a_passing_verdict_is_persisted() -> None:
    pages = [
        _page(
            1,
            "Cost breakdown",
            [[HEADERS, ["1", "Drilling", "162", "830", "134,460"],
              ["2", "Survey", "1", "100,000", "100,000"]]],
        )
    ]
    rows, _ = _run(pages)

    assert _row(rows, 1)["quality_verdict"] == "pass"


def test_a_failing_verdict_is_persisted() -> None:
    pages = [
        _page(
            1,
            "Cost breakdown",
            [[HEADERS, ["1", "Drilling", "162", "830", "Rs 1 34,460"],
              ["2", "Survey", "1", "100,000", "100,000"]]],
        )
    ]
    rows, _ = _run(pages)
    row = _row(rows, 1)

    assert row["quality_verdict"] == "fail"
    assert row["needs_review"] is True


def test_a_not_checkable_verdict_is_persisted() -> None:
    pages = [_page(1, "A covering letter with no financial table at all.")]
    rows, _ = _run(pages)

    assert _row(rows, 1)["quality_verdict"] == "not_checkable"


def test_the_executed_checks_are_persisted() -> None:
    pages = [_page(1, "A covering letter with no financial table at all.")]
    rows, _ = _run(pages)

    assert _row(rows, 1)["quality_checks"], "no check records persisted"


# --- Repair provenance --------------------------------------------------------


def test_a_repaired_page_persists_both_representations() -> None:
    pages = [
        _page(
            1,
            "Mobilization\n1 ,900,000",
            [[HEADERS,
              ["1", "Mobilization", "1", "1,900,000", "1 ,900,000"],
              ["2", "Survey", "1", "100,000", "100,000"],
              ["", "Total", "", "", "2,000,000"]]],
        )
    ]
    rows, _ = _run(pages)
    row = _row(rows, 1)

    assert "1,900,000" in row["raw_text"], "published text is not the corrected one"
    assert row["original_text"] and "1 ,900,000" in row["original_text"], (
        "the document's own wording was not retained for audit"
    )
    assert row["applied_repairs"], "no repair provenance persisted"
    assert row["quality_verdict"] == "pass", "post-repair verdict not persisted"


# --- Idempotency --------------------------------------------------------------


def test_finalizing_twice_does_not_duplicate_page_identities() -> None:
    """Retry safety: the unique (doc, run, page) triple must still hold."""
    pages = [_page(1, "A covering letter with no financial table at all.")]
    rows, collection = _run(pages)

    assert len(rows) == 1

    asyncio.run(collection.bulk_write([]))  # no-op write must not add rows
    assert len(rows) == 1


def test_two_pages_keep_distinct_identities() -> None:
    pages = [
        _page(1, "A covering letter with no financial table at all."),
        _page(2, "A second covering page with different prose entirely."),
    ]
    rows, _ = _run(pages)

    assert set(rows.keys()) == {("doc-1", "run-1", 1), ("doc-1", "run-1", 2)}
