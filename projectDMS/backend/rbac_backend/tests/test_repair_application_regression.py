"""G20: a computed repair must reach the text that gets persisted and indexed.

The gate detects the measured split-digit corruptions and computes the correct
value under dual confirmation. That value was then thrown away:

  * `QualityVerdict.repairs` has no consumer outside the quality package;
  * `PageExtractionResult.combined_text` is frozen inside the engine, before the
    gate ever runs, so nothing the gate produces can reach it.

So hard gate (b) - "all 9 detected with correct repairs" - was true at the unit
level and vacuous in the pipeline. The indexed text still read `1 ,900,000`,
which downstream numeric parsing reads as `1`. That is the exact failure mode
the evidence document was written to prevent.

The invariant: raw evidence stays auditable, and the *published* representation
carries the corrected value.
"""

from __future__ import annotations

from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.quality.gate import ExtractionQualityGate

HEADERS = ["S/N", "Description", "Qty", "Rate", "Amount"]

#: 1 x 1,900,000 = 1,900,000, corrupted to `1 ,900,000` in the source's own
#: text layer. Two peers and a stated total give the dual confirmation the
#: repair rule requires.
CORRUPT_ROW = ["1", "Mobilization", "1", "1,900,000", "1 ,900,000"]
PEER_ROW = ["2", "Survey", "1", "100,000", "100,000"]
TOTAL_ROW = ["", "Total", "", "", "2,000,000"]


def _page(text: str) -> ExtractedPage:
    return ExtractedPage(
        number=6,
        text=text,
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=PageClass.TEXT_NATIVE,
            char_count=len(text),
            image_count=0,
            image_coverage=0.0,
            table_count=1,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
    )


def _assess():
    gate = ExtractionQualityGate()
    page = _page("Mobilization and demobilization\n1 ,900,000")
    verdict = gate.assess(page, tables=[[HEADERS, CORRUPT_ROW, PEER_ROW, TOTAL_ROW]])
    return page, verdict


def test_the_repair_is_computed_with_the_correct_value() -> None:
    """Precondition: the detection half genuinely works."""
    _, verdict = _assess()

    assert verdict.repairs, "no repair was proposed for a measured corruption"
    repair = verdict.repairs[0]
    assert str(repair.after).replace(",", "") == "1900000"


def test_the_repair_carries_its_provenance() -> None:
    """A repair that cannot be audited is not usable in an arbitration record."""
    _, verdict = _assess()
    repair = verdict.repairs[0]

    assert repair.before == "1 ,900,000", "original value not retained"
    assert repair.after == "1,900,000"
    assert repair.page == 6, "repair does not identify the page it belongs to"
    assert repair.method == "dual_confirmation"
    assert set(repair.confirming_checks) == {"row_identity", "subtotal_identity"}, (
        "a repair must be corroborated by two independent checks"
    )


# --- Through the production path, not the gate in isolation ------------------


def _processed_page():
    """Drive `_apply_quality_gate`, which is what production actually runs."""
    import asyncio
    from types import SimpleNamespace

    from rbac_backend.models.processing_state import Completeness
    from rbac_backend.services.document_processor import DocumentProcessor

    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.quality_gate = ExtractionQualityGate()
    processor.fallback_ladder = None
    processor.fallback_max_pages_per_document = 0

    page = _page("Mobilization and demobilization\n1 ,900,000")
    page.tables = [[HEADERS, CORRUPT_ROW, PEER_ROW, TOTAL_ROW]]
    extraction = SimpleNamespace(
        pages=[page],
        combined_text=page.text,
        completeness=Completeness.COMPLETE,
    )

    asyncio.run(
        processor._apply_quality_gate(None, extraction, document_id="doc-repair")
    )
    return page, extraction


def test_the_repaired_value_reaches_the_page_text() -> None:
    """The defect. The gate knew the true value; the page text did not."""
    page, _ = _processed_page()

    assert "1,900,000" in page.text, (
        "the corrected value never reached the page text, so the corruption "
        "is what gets persisted, embedded and retrieved"
    )


def test_the_repaired_value_reaches_the_canonical_combined_text() -> None:
    """combined_text becomes raw_ocr_text, which is embedded and indexed."""
    _, extraction = _processed_page()

    assert "1,900,000" in extraction.combined_text
    assert "1 ,900,000" not in extraction.combined_text, (
        "the corrupted literal survived into the text that gets indexed"
    )


def test_the_raw_evidence_is_still_auditable_after_repair() -> None:
    """A repair must not destroy what the document actually said."""
    page, _ = _processed_page()

    assert page.raw_text is not None, "no raw representation retained"
    assert "1 ,900,000" in page.raw_text, "the original corrupted literal was lost"


def test_the_applied_repair_is_recorded_with_provenance() -> None:
    page, _ = _processed_page()

    assert page.applied_repairs, "no provenance recorded for an applied repair"
    record = page.applied_repairs[0]
    assert record["applied"] is True
    assert record["before"] == "1 ,900,000"
    assert record["after"] == "1,900,000"
    assert record["method"] == "dual_confirmation"


def test_the_page_passes_after_re_verification() -> None:
    """The gate re-runs over the repaired text; the page is clean afterwards."""
    page, _ = _processed_page()

    assert page.quality_verdict == "pass"
    assert page.needs_review is False
