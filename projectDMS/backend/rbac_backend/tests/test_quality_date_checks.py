"""Mixed date conventions in one column - the highest contractual risk.

Companion document 3.4 measured page 4's 52-row idle register mixing M/D/YYYY
and D/M/YYYY in the same column. Those dates establish the claim period behind
9,600,000 of idling charges. An unresolved ambiguity must stay unresolved.
"""

from __future__ import annotations

from rbac_backend.services.extraction.quality.date_checks import (
    DateConvention,
    check_date_column,
    detect_convention,
)
from rbac_backend.services.extraction.quality.models import Verdict


def test_unambiguous_month_first_is_detected() -> None:
    # Day > 12 in the second position proves month-first.
    values = ["2/26/2023", "2/27/2023", "2/28/2023"]

    assert detect_convention(values) is DateConvention.MONTH_FIRST


def test_unambiguous_day_first_is_detected() -> None:
    values = ["26/2/2023", "27/2/2023", "28/2/2023"]

    assert detect_convention(values) is DateConvention.DAY_FIRST


def test_the_measured_mixed_column_is_flagged_mixed() -> None:
    values = [
        "2/26/2023",
        "2/27/2023",
        "2/28/2023",  # must be M/D
        "1/3/2023",
        "2/3/2023",
        "12/3/2023",  # must be D/M (1-12 March)
        "3/13/2023",
        "3/24/2023",  # must be M/D
    ]

    assert detect_convention(values) is DateConvention.MIXED


def test_a_column_proving_both_conventions_is_mixed() -> None:
    # 26 in second position proves month-first; 26 in first proves day-first.
    values = ["2/26/2023", "26/2/2023"]

    assert detect_convention(values) is DateConvention.MIXED


def test_ambiguous_values_without_unique_sequence_evidence_stay_ambiguous() -> None:
    assert (
        detect_convention(["1/3/2023", "2/3/2023", "3/3/2023"])
        is DateConvention.AMBIGUOUS
    )


def test_all_ambiguous_values_stay_ambiguous() -> None:
    # 3/5 and 5/5 are valid under both readings.
    assert detect_convention(["3/5/2023", "5/5/2023"]) is DateConvention.AMBIGUOUS


def test_empty_or_unparseable_input_is_unknown() -> None:
    assert detect_convention([]) is DateConvention.UNKNOWN
    assert detect_convention(["not a date", ""]) is DateConvention.UNKNOWN


def test_mixed_column_fails_the_check() -> None:
    result = check_date_column(["2/26/2023", "1/3/2023", "26/3/2023"])

    assert result.verdict is Verdict.FAIL
    assert "mixed" in result.detail.lower()


def test_ambiguous_column_is_indeterminate_not_failed() -> None:
    result = check_date_column(["3/5/2023", "5/5/2023"])

    assert result.verdict is Verdict.INDETERMINATE


def test_consistent_column_passes() -> None:
    result = check_date_column(["26/2/2023", "27/2/2023", "28/2/2023"])

    assert result.verdict is Verdict.PASS


def test_month_first_column_also_passes() -> None:
    result = check_date_column(["2/26/2023", "2/27/2023"])

    assert result.verdict is Verdict.PASS


def test_column_without_dates_is_not_checkable() -> None:
    result = check_date_column(["Idle", "Idle", "Working"])

    assert result.verdict is Verdict.NOT_CHECKABLE


def test_iso_dates_are_unambiguous_and_pass() -> None:
    result = check_date_column(["2023-02-26", "2023-02-27"])

    assert result.verdict is Verdict.PASS


def test_impossible_dates_do_not_prove_a_convention() -> None:
    # 13/13 is neither reading; it must not be counted as evidence.
    assert detect_convention(["13/13/2023"]) is DateConvention.UNKNOWN


def test_ambiguity_is_never_silently_resolved_to_the_repo_default() -> None:
    """CLAUDE.md records a move to day-first DD-MM-YYYY parsing.

    That default must not leak into this check: a day-first parse of 2/26/2023
    yields month 26 and fails, and silently falling back to month-first
    mis-dates the March entries. Ambiguous stays ambiguous.
    """
    result = check_date_column(["3/5/2023", "5/5/2023"])

    assert result.verdict is not Verdict.PASS
    assert result.verdict is not Verdict.FAIL
