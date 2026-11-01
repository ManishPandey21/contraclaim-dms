"""Numeric integrity: detect corruption, repair only on dual confirmation.

Companion document 3.1 measured 9 split-digit corruptions in a PDF's own text
layer - "1 ,900,000" for 1,900,000. Parsed naively that reads as 1, and a claim
subtotal lands 2.5 crore light. pdfplumber x_tolerance tuning does NOT fix it
(17 tokens survive every setting from 1.0 to 3.0), so the repair is structural.
"""

from __future__ import annotations

from decimal import Decimal

from rbac_backend.services.extraction.quality.column_roles import ColumnRole
from rbac_backend.services.extraction.quality.models import CheckResult, Verdict
from rbac_backend.services.extraction.quality.numeric_checks import (
    MONETARY_ABS_TOLERANCE,
    QUANTITY_DISPLAY_REL_TOLERANCE,
    check_row,
    check_subtotal,
    detect_split_digits,
    parse_amount,
    propose_repair,
)

ROLES = [
    ColumnRole.SERIAL,
    ColumnRole.DESCRIPTION,
    ColumnRole.QUANTITY,
    ColumnRole.RATE,
    ColumnRole.AMOUNT,
]


def test_parses_indian_grouped_amounts() -> None:
    assert parse_amount("22,140,168") == Decimal("22140168")
    assert parse_amount("110,000.00") == Decimal("110000.00")
    assert parse_amount("") is None
    assert parse_amount("n/a") is None


def test_detects_the_measured_split_digit_corruptions() -> None:
    # All nine, verbatim from companion document 3.1.
    measured = {
        "1 ,900,000": "1,900,000",
        "5 77,188": "577,188",
        "9 50,000": "950,000",
        "7 0,500": "70,500",
        "1 34,460": "134,460",
        "4 42,728": "442,728",
        "1 10,000": "110,000",
        "1 15,000": "115,000",
        "4 8,960": "48,960",
    }

    for corrupted, expected in measured.items():
        assert detect_split_digits(corrupted) == expected, corrupted


def test_leaves_clean_numbers_alone() -> None:
    for clean in ("1,900,000", "22,140,168", "598.20", "0", "3,071.88"):
        assert detect_split_digits(clean) is None


def test_does_not_join_genuinely_separate_numbers() -> None:
    # Two columns collapsed into one cell must not become one number.
    assert detect_split_digits("52 184615") is None


def test_row_identity_passes_within_tolerance() -> None:
    # Companion 3.3: a displayed qty of 58 against a true 57.5 is a 0.86%
    # discrepancy that must pass, not escalate.
    result = check_row(["1", "Ground improvement", "58", "1,215", "70,500"], ROLES)

    assert result.verdict is Verdict.PASS


def test_row_identity_fails_outside_tolerance() -> None:
    result = check_row(["1", "Widget", "10", "100", "5,000"], ROLES)

    assert result.verdict is Verdict.FAIL
    assert "1,000" in result.detail or "1000" in result.detail


def test_nos_multiplier_is_honoured() -> None:
    # Page 5 row ii: 3 x 32.61 x 3,200 = 313,056. Ignoring Nos gives a false fail.
    roles = [
        ColumnRole.DESCRIPTION,
        ColumnRole.NOS,
        ColumnRole.QUANTITY,
        ColumnRole.RATE,
        ColumnRole.AMOUNT,
    ]
    result = check_row(["Guide wall", "3", "32.61", "3,200", "313,056"], roles)

    assert result.verdict is Verdict.PASS


def test_unidentified_roles_are_not_checkable_not_failed() -> None:
    roles = [ColumnRole.UNKNOWN] * 4
    result = check_row(["DW1", "1.2", "3.4", "5.6"], roles)

    assert result.verdict is Verdict.NOT_CHECKABLE


def test_missing_values_are_not_checkable() -> None:
    result = check_row(["1", "Widget", "", "100", "5,000"], ROLES)

    assert result.verdict is Verdict.NOT_CHECKABLE


def test_subtotal_matches_stated_total() -> None:
    rows = [
        ["1", "a", "1", "1", "9,600,000"],
        ["2", "b", "1", "1", "5,684,775"],
        ["3", "c", "1", "1", "185,441"],
    ]
    result = check_subtotal(rows, "15,470,216", ROLES, candidate_row_index=1)

    assert result.verdict is Verdict.PASS


def test_monetary_subtotal_absorbs_only_one_display_unit() -> None:
    # Companion 3.2: p3 line items sum to 18,450,139 against a stated 18,450,140.
    rows = [
        ["1", "a", "1", "1", "18,000,000"],
        ["2", "b", "1", "1", "450,139"],
    ]
    result = check_subtotal(rows, "18,450,140", ROLES, candidate_row_index=0)

    assert result.verdict is Verdict.PASS


def test_repair_requires_row_identity_and_independent_multirow_subtotal() -> None:
    repaired_row = ["1", "Mobilization", "1", "1,900,000", "1,900,000"]
    rows = [
        repaired_row,
        ["2", "Survey", "1", "100,000", "100,000"],
    ]
    row_check = check_row(repaired_row, ROLES)
    subtotal_check = check_subtotal(rows, "2,000,000", ROLES, candidate_row_index=0)

    repair = propose_repair(
        "1 ,900,000", row_check=row_check, subtotal_check=subtotal_check
    )

    assert repair is not None
    assert repair.before == "1 ,900,000"
    assert repair.after == "1,900,000"
    assert repair.method == "dual_confirmation"
    assert repair.confidence >= 0.99


def test_single_agreeing_check_never_repairs() -> None:
    row_check = check_row(["1", "Mobilization", "1", "1,900,000", "1,900,000"], ROLES)
    not_checkable = check_row(["1", "x", "", "", ""], ROLES)

    assert (
        propose_repair(
            "1 ,900,000", row_check=row_check, subtotal_check=not_checkable
        )
        is None
    )


def test_disagreeing_checks_never_repair() -> None:
    row_check = check_row(["1", "Widget", "10", "100", "1,000"], ROLES)
    contradicting = check_subtotal(
        [["1", "Widget", "10", "100", "1,000"], ["2", "Peer", "1", "1", "1"]],
        "9,999",
        ROLES,
        candidate_row_index=0,
    )

    assert (
        propose_repair(
            "1 ,000", row_check=row_check, subtotal_check=contradicting
        )
        is None
    )


def test_quantity_and_money_tolerances_are_separate() -> None:
    assert QUANTITY_DISPLAY_REL_TOLERANCE == Decimal("0.01")
    assert MONETARY_ABS_TOLERANCE == Decimal("1")


def test_one_percent_monetary_error_does_not_pass() -> None:
    rows = [["1", "a", "1", "1", "990"], ["2", "b", "1", "1", "10"]]

    result = check_subtotal(rows, "1,010", ROLES, candidate_row_index=0)

    assert result.verdict is Verdict.FAIL


def test_single_row_total_is_not_independent_corroboration() -> None:
    row = ["1", "Mobilization", "1", "1,900,000", "1,900,000"]
    row_check = check_row(row, ROLES)
    same_row_total = check_subtotal([row], "1,900,000", ROLES, candidate_row_index=0)

    assert same_row_total.verdict is Verdict.NOT_CHECKABLE
    assert (
        propose_repair(
            "1 ,900,000", row_check=row_check, subtotal_check=same_row_total
        )
        is None
    )


def test_overlapping_structural_support_is_not_independent() -> None:
    candidate = Decimal("1900000")
    row_check = CheckResult(
        name="row_identity",
        verdict=Verdict.PASS,
        candidate_value=candidate,
        evidence_refs=["candidate.amount", "shared.operand"],
    )
    subtotal_check = CheckResult(
        name="subtotal_identity",
        verdict=Verdict.PASS,
        candidate_value=candidate,
        evidence_refs=["candidate.amount", "shared.operand", "printed.subtotal"],
    )

    assert (
        propose_repair(
            "1 ,900,000", row_check=row_check, subtotal_check=subtotal_check
        )
        is None
    )


def test_repair_disagreeing_on_the_candidate_value_is_refused() -> None:
    row_check = CheckResult(
        name="row_identity",
        verdict=Verdict.PASS,
        candidate_value=Decimal("1900000"),
        evidence_refs=["candidate.amount", "row.qty"],
    )
    subtotal_check = CheckResult(
        name="subtotal_identity",
        verdict=Verdict.PASS,
        candidate_value=Decimal("1900001"),
        evidence_refs=["candidate.amount", "printed.subtotal"],
    )

    assert (
        propose_repair(
            "1 ,900,000", row_check=row_check, subtotal_check=subtotal_check
        )
        is None
    )


def test_repair_records_the_full_audit_trail() -> None:
    repaired_row = ["1", "Mobilization", "1", "1,900,000", "1,900,000"]
    rows = [repaired_row, ["2", "Survey", "1", "100,000", "100,000"]]

    repair = propose_repair(
        "1 ,900,000",
        row_check=check_row(repaired_row, ROLES),
        subtotal_check=check_subtotal(
            rows, "2,000,000", ROLES, candidate_row_index=0
        ),
    )

    assert repair is not None
    assert repair.reason
    assert repair.confirming_checks == ["row_identity", "subtotal_identity"]


def test_repair_is_refused_when_the_literal_is_not_corrupt() -> None:
    repaired_row = ["1", "Mobilization", "1", "1,900,000", "1,900,000"]
    rows = [repaired_row, ["2", "Survey", "1", "100,000", "100,000"]]

    assert (
        propose_repair(
            "1,900,000",
            row_check=check_row(repaired_row, ROLES),
            subtotal_check=check_subtotal(
                rows, "2,000,000", ROLES, candidate_row_index=0
            ),
        )
        is None
    )
