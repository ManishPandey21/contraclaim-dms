"""The quality gate: four verdicts, and NOT_CHECKABLE never escalates.

Two hard gates from the companion evidence are asserted here. Phase 7 must not
be enabled until both pass:

  * zero FAIL verdicts across the 12 measured false-positive patterns
  * all 9 measured split-digit corruptions detected with correct repairs
"""

from __future__ import annotations

import json
from pathlib import Path

from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.quality.gate import ExtractionQualityGate
from rbac_backend.services.extraction.quality.models import Verdict
from rbac_backend.services.extraction.quality.numeric_checks import detect_split_digits

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "golden_quality_gate.json").read_text(
        encoding="utf-8"
    )
)


def _page(text: str, page_class: PageClass = PageClass.TEXT_NATIVE) -> ExtractedPage:
    return ExtractedPage(
        number=3,
        text=text,
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=PageClassification(
            page_class=page_class,
            char_count=len(text),
            image_count=0,
            image_coverage=0.0,
            table_count=1,
            width=595.0,
            height=842.0,
            rotation=0,
        ),
    )


def test_all_nine_measured_corruptions_are_detected() -> None:
    corruptions = GOLDEN["split_digit_corruptions"]

    assert (
        len(corruptions)
        == GOLDEN["known_split_digit_corruptions_that_must_be_detected"]
    )
    for corrupted, expected in corruptions.items():
        assert detect_split_digits(corrupted) == expected, corrupted


def test_no_false_positive_case_produces_a_fail() -> None:
    gate = ExtractionQualityGate()
    cases = GOLDEN["false_positive_cases"]

    assert len(cases) == GOLDEN["false_positive_patterns_that_must_not_fail"] == 12

    for case in cases:
        tables = [[case["headers"], case["row"]]]
        verdict = gate.assess(_page("Cost breakdown table"), tables=tables)

        assert verdict.verdict is not Verdict.FAIL, case["why"]


def test_no_false_positive_case_escalates() -> None:
    # The economic point: every one of these would have been a paid call.
    gate = ExtractionQualityGate()

    for case in GOLDEN["false_positive_cases"]:
        verdict = gate.assess(
            _page("Cost breakdown table"), tables=[[case["headers"], case["row"]]]
        )

        assert verdict.escalates is False, case["why"]


def test_clean_page_passes() -> None:
    gate = ExtractionQualityGate()
    tables = [
        [["S/N", "Description", "Qty", "Rate", "Amount"], ["1", "Widget", "2", "50", "100"]]
    ]

    verdict = gate.assess(
        _page("A clean cost table with prose above it."), tables=tables
    )

    assert verdict.verdict is Verdict.PASS
    assert verdict.escalates is False


def test_genuinely_wrong_arithmetic_fails() -> None:
    gate = ExtractionQualityGate()
    tables = [
        [
            ["S/N", "Description", "Qty", "Rate", "Amount"],
            ["1", "Widget", "10", "100", "5,000"],
        ]
    ]

    verdict = gate.assess(_page("A cost table."), tables=tables)

    assert verdict.verdict is Verdict.FAIL
    assert verdict.escalates is True


def test_empty_page_that_should_have_content_fails() -> None:
    gate = ExtractionQualityGate()

    verdict = gate.assess(_page("", PageClass.SCANNED_IMAGE))

    assert verdict.verdict is Verdict.FAIL
    assert verdict.escalates is True


def test_blank_page_is_not_an_escalation() -> None:
    gate = ExtractionQualityGate()

    verdict = gate.assess(_page("", PageClass.BLANK))

    assert verdict.escalates is False


def test_unrenderable_page_is_indeterminate_and_human_review_eligible() -> None:
    gate = ExtractionQualityGate()

    verdict = gate.assess(_page("", PageClass.UNRENDERABLE))

    assert verdict.verdict is Verdict.INDETERMINATE
    assert verdict.escalates is True


def test_unidentifiable_table_is_not_checkable_and_does_not_escalate() -> None:
    gate = ExtractionQualityGate()
    tables = [[["DW No", "Depth", "Thickness", "Volume"], ["DW1", "1.2", "0.8", "4.8"]]]

    verdict = gate.assess(_page("Dimensions table."), tables=tables)

    assert verdict.verdict is Verdict.NOT_CHECKABLE
    assert verdict.escalates is False


def test_ambiguous_dates_are_indeterminate_and_do_escalate() -> None:
    gate = ExtractionQualityGate()
    tables = [[["Date", "Status"], ["3/5/2023", "Idle"], ["5/5/2023", "Idle"]]]

    verdict = gate.assess(_page("Idle register."), tables=tables)

    assert verdict.verdict is Verdict.INDETERMINATE
    assert verdict.escalates is True


def test_mixed_date_conventions_fail() -> None:
    gate = ExtractionQualityGate()
    tables = [
        [
            ["Date", "Status"],
            ["2/26/2023", "Idle"],
            ["1/3/2023", "Idle"],
            ["26/3/2023", "Idle"],
        ]
    ]

    verdict = gate.assess(_page("Idle register."), tables=tables)

    assert verdict.verdict is Verdict.FAIL


def test_repairs_are_recorded_with_full_provenance() -> None:
    gate = ExtractionQualityGate()
    tables = [
        [
            ["S/N", "Description", "Qty", "Rate", "Amount"],
            ["1", "Mobilization", "1", "1,900,000", "1 ,900,000"],
            ["2", "Survey", "1", "100,000", "100,000"],
            ["", "Sub-total", "", "", "2,000,000"],
        ]
    ]

    verdict = gate.assess(_page("Cost table."), tables=tables)

    assert verdict.repairs
    repair = verdict.repairs[0]
    assert repair.before == "1 ,900,000"
    assert repair.after == "1,900,000"
    assert repair.method == "dual_confirmation"
    assert repair.page == 3


def test_a_corruption_without_corroboration_is_not_repaired() -> None:
    # Same corruption, but no subtotal to corroborate it. Flag, never rewrite.
    gate = ExtractionQualityGate()
    tables = [
        [
            ["S/N", "Description", "Qty", "Rate", "Amount"],
            ["1", "Mobilization", "1", "1,900,000", "1 ,900,000"],
        ]
    ]

    verdict = gate.assess(_page("Cost table."), tables=tables)

    assert verdict.repairs == []


def test_verdict_serialises_for_persistence() -> None:
    gate = ExtractionQualityGate()
    tables = [
        [["S/N", "Description", "Qty", "Rate", "Amount"], ["1", "Widget", "2", "50", "100"]]
    ]

    record = gate.assess(_page("A cost table."), tables=tables).to_record()

    assert record["verdict"] == "pass"
    assert isinstance(record["checks"], list)
    assert all("verdict" in check for check in record["checks"])
