"""G22: certification that proves the check ran, not merely that nothing failed.

The previous hard-gate suite asserted "zero FAIL across 12 false-positive
patterns" and counted a case as validated when the gate declined to examine it.
Measured: 9 of 12 resolved to NOT_CHECKABLE, and because every case was built as
a SINGLE-row table, `_split_total_row` bailed and `subtotal_identity` executed in
**0 of 12**. Case 6 - the only one carrying a stated total, and the one whose
purpose was to prove the monetary tolerance - passed on the tautology
`1 x 18,450,139 = 18,450,139`.

So this file records, for every case, which check was *expected* to run, which
checks *actually* ran, the verdict and the escalation. A case whose expected
check did not execute is a failure here even when the verdict looks acceptable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import pytest

from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.quality.gate import ExtractionQualityGate
from rbac_backend.services.extraction.quality.models import Verdict

HEADERS = ["S/N", "Description", "Qty", "Rate", "Amount"]


def _page(text: str = "Cost breakdown table") -> ExtractedPage:
    return ExtractedPage(
        number=3,
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


def _assess(table: Sequence[Sequence[str]]) -> Dict[str, Any]:
    """Run the gate and report what actually executed."""
    verdict = ExtractionQualityGate().assess(_page(), tables=[table])
    executed = [check.name for check in verdict.checks]
    return {
        "verdict": verdict.verdict,
        "escalates": verdict.escalates,
        "executed": executed,
        "checks": {check.name: check.verdict for check in verdict.checks},
        "repairs": verdict.repairs,
    }


# ---------------------------------------------------------------------------
# Subtotal hard gate: the check that never ran
# ---------------------------------------------------------------------------

DETAIL_ROWS = [
    ["1", "Item one", "1", "100", "100"],
    ["2", "Item two", "1", "200", "200"],
    ["3", "Item three", "1", "300", "300"],
]


def _with_total(total: str, rows: Optional[List[List[str]]] = None):
    return [HEADERS, *(rows or DETAIL_ROWS), ["", "Subtotal", "", "", total]]


def test_subtotal_identity_actually_executes() -> None:
    """The precondition the old suite never met."""
    result = _assess(_with_total("600"))

    assert "subtotal_identity" in result["executed"], (
        "subtotal_identity did not run; a single-row table cannot certify it"
    )


def test_a_correct_subtotal_passes() -> None:
    result = _assess(_with_total("600"))

    assert result["checks"]["subtotal_identity"] is Verdict.PASS
    assert result["verdict"] is Verdict.PASS
    assert result["escalates"] is False


def test_a_subtotal_within_rounding_tolerance_passes() -> None:
    """The measured 1-unit case: 18,450,139 summed vs 18,450,140 stated."""
    result = _assess(_with_total("601"))

    assert "subtotal_identity" in result["executed"]
    assert result["checks"]["subtotal_identity"] is Verdict.PASS


def test_an_incorrect_subtotal_fails() -> None:
    result = _assess(_with_total("900"))

    assert result["checks"]["subtotal_identity"] is Verdict.FAIL
    assert result["escalates"] is True


def test_a_corrupted_detail_row_is_caught_by_the_subtotal() -> None:
    rows = [
        ["1", "Item one", "1", "100", "100"],
        ["2", "Item two", "1", "200", "20"],  # dropped a zero
        ["3", "Item three", "1", "300", "300"],
    ]
    result = _assess(_with_total("600", rows))

    assert result["verdict"] is Verdict.FAIL
    assert result["escalates"] is True


def test_a_corrupted_subtotal_is_caught() -> None:
    result = _assess(_with_total("6 00"))

    assert result["verdict"] is not Verdict.PASS
    assert result["escalates"] is True


def test_an_unparseable_detail_amount_does_not_pass() -> None:
    rows = [
        ["1", "Item one", "1", "100", "Rs 1 00"],
        ["2", "Item two", "1", "200", "200"],
        ["3", "Item three", "1", "300", "300"],
    ]
    result = _assess(_with_total("600", rows))

    assert result["verdict"] is not Verdict.PASS
    assert result["escalates"] is True


def test_a_missing_detail_amount_is_not_a_failure() -> None:
    """Absent is uncheckable; it must not be treated as corruption."""
    rows = [
        ["1", "Item one", "1", "100", ""],
        ["2", "Item two", "1", "200", "200"],
        ["3", "Item three", "1", "300", "300"],
    ]
    result = _assess(_with_total("600", rows))

    assert result["verdict"] is not Verdict.FAIL
    assert result["escalates"] is False


# ---------------------------------------------------------------------------
# False-positive suite, rebuilt on realistic multi-row structures
# ---------------------------------------------------------------------------

#: Each case states the check whose execution the acceptance claim depends on.
#: `expected_check=None` means the case legitimately has nothing to check and is
#: recorded as such rather than counted as a validated pass.
FALSE_POSITIVE_CASES: List[Dict[str, Any]] = [
    {
        "why": "Nos multiplier applies: 3 x 32.61 x 3,200",
        "expected_check": "row_identity",
        "table": [
            ["S/N", "Description", "Nos", "Qty", "Rate", "Amount"],
            ["1", "Guide wall", "3", "32.61", "3,200", "313,056"],
            ["2", "Survey", "1", "1", "100,000", "100,000"],
        ],
    },
    {
        "why": "0.86% display rounding: printed qty 58 against a true 57.5",
        "expected_check": "row_identity",
        "table": [
            HEADERS,
            ["1", "Ground improvement", "58", "1,216", "70,500"],
            ["2", "Survey", "1", "100,000", "100,000"],
        ],
    },
    {
        "why": "serial column must not be read as a quantity",
        "expected_check": "row_identity",
        "table": [
            HEADERS,
            ["1", "Mobilization", "1", "950,000", "950,000"],
            ["2", "Survey", "1", "100,000", "100,000"],
        ],
    },
    {
        "why": "1-unit rounding across a genuine multi-row subtotal",
        "expected_check": "subtotal_identity",
        "table": _with_total("601"),
    },
    {
        "why": "repeated header row inside a continued table",
        "expected_check": None,
        "table": [HEADERS, HEADERS, ["2", "Survey", "1", "100,000", "100,000"]],
    },
    {
        "why": "lump-sum row carries an amount but no quantity",
        "expected_check": None,
        "table": [
            HEADERS,
            ["1", "Lump sum provision", "", "500,000", "500,000"],
            ["2", "Survey", "1", "100,000", "100,000"],
        ],
    },
    {
        "why": "negative credit has no multiplication operand",
        "expected_check": None,
        "table": [
            HEADERS,
            ["1", "Credit note", "", "", "-25,000"],
            ["2", "Survey", "1", "100,000", "100,000"],
        ],
    },
    {
        "why": "declared in-table formula, not a qty x rate identity",
        "expected_check": None,
        "table": [
            ["DW No", "Depth", "Thickness", "Length", "Volume"],
            ["1", "20.00", "0.80", "3.00", "48.00"],
            ["2", "20.00", "0.80", "3.00", "48.00"],
        ],
    },
    {
        "why": "missing amount is uncheckable rather than false",
        "expected_check": None,
        "table": [
            HEADERS,
            ["1", "Provisional", "1", "5,000", ""],
            ["2", "Survey", "1", "100,000", "100,000"],
        ],
    },
    {
        "why": "percentage item is not a quantity-rate identity",
        "expected_check": None,
        "table": [
            ["S/N", "Description", "Percent", "Base", "Amount"],
            ["1", "Overheads @ 20%", "20", "18,450,140", "3,690,028"],
            ["2", "Survey", "1", "100,000", "100,000"],
        ],
    },
    {
        "why": "correspondence prose above a financial table",
        "expected_check": "row_identity",
        "table": [
            HEADERS,
            ["1", "Drilling", "162", "830", "134,460"],
            ["2", "Survey", "1", "100,000", "100,000"],
        ],
    },
    {
        "why": "Indian grouping in a correct amount",
        "expected_check": "row_identity",
        "table": [
            HEADERS,
            ["1", "Drilling", "162", "830", "134,460"],
            ["2", "Rebaring", "286", "1,548", "442,728"],
        ],
    },
]


@pytest.mark.parametrize(
    "case", FALSE_POSITIVE_CASES, ids=[c["why"][:40] for c in FALSE_POSITIVE_CASES]
)
def test_no_valid_case_escalates(case: Dict[str, Any]) -> None:
    result = _assess(case["table"])

    assert result["verdict"] is not Verdict.FAIL, case["why"]
    assert result["escalates"] is False, case["why"]


@pytest.mark.parametrize(
    "case",
    [c for c in FALSE_POSITIVE_CASES if c["expected_check"]],
    ids=[c["why"][:40] for c in FALSE_POSITIVE_CASES if c["expected_check"]],
)
def test_the_expected_check_actually_executed(case: Dict[str, Any]) -> None:
    """The heart of G22: a declined check is not a validated one."""
    result = _assess(case["table"])

    assert case["expected_check"] in result["executed"], (
        f"{case['why']}: {case['expected_check']} never ran, so this case "
        "certifies nothing"
    )
    assert result["checks"][case["expected_check"]] is Verdict.PASS, case["why"]


def test_the_suite_actually_exercises_its_substantive_checks() -> None:
    """Guards against the whole suite regressing to declined checks again."""
    executed = set()
    for case in FALSE_POSITIVE_CASES:
        executed.update(_assess(case["table"])["executed"])

    assert "row_identity" in executed
    assert "subtotal_identity" in executed


# ---------------------------------------------------------------------------
# Corruption suite, in realistic multi-row context
# ---------------------------------------------------------------------------

#: The nine measured split-digit corruptions with their true values.
MEASURED_CORRUPTIONS = [
    ("1 ,900,000", "1,900,000"),
    ("5 77,188", "577,188"),
    ("9 50,000", "950,000"),
    ("7 0,500", "70,500"),
    ("1 34,460", "134,460"),
    ("4 42,728", "442,728"),
    ("1 10,000", "110,000"),
    ("1 15,000", "115,000"),
    ("4 8,960", "48,960"),
]


@pytest.mark.parametrize("corrupt,true_value", MEASURED_CORRUPTIONS)
def test_a_measured_corruption_cannot_pass_beside_a_clean_row(
    corrupt: str, true_value: str
) -> None:
    """No corruption may ride through on a clean sibling's verdict."""
    table = [
        HEADERS,
        ["1", "Item", "1", true_value, corrupt],
        ["2", "Survey", "1", "100,000", "100,000"],
    ]
    result = _assess(table)

    assert result["verdict"] is not Verdict.PASS, corrupt


@pytest.mark.parametrize("corrupt,true_value", MEASURED_CORRUPTIONS)
def test_a_measured_corruption_is_repaired_to_its_true_value(
    corrupt: str, true_value: str
) -> None:
    """With dual confirmation available, the correct value is recovered."""
    peer = "100,000"
    total = f"{int(true_value.replace(',', '')) + 100000:,}"
    table = [
        HEADERS,
        ["1", "Item", "1", true_value, corrupt],
        ["2", "Survey", "1", peer, peer],
        ["", "Total", "", "", total],
    ]
    result = _assess(table)

    assert result["repairs"], f"{corrupt} was not repaired"
    assert result["repairs"][0].after == true_value
    assert result["repairs"][0].method == "dual_confirmation"
