"""R3: the gate must check the table's real header, and fail truthfully.

Measured on the real claim with PR 2 applied: every one of its 8 tables began
with a blank row, a title row, or both, and `_assess_table` took `rows[0]` as
the header. `map_column_roles` recognises the true headers ("Sr. No |
Description | Nos | QTY | Unit | Rate in INR | Amount in INR") but was never
handed them, so no table was checkable and `check_row`, `check_subtotal` and
`propose_repair` never ran on the document.

Separately, `date_convention` was enough to make a page PASS. Real page 4 has
three tables, none of whose arithmetic was verified, and came out PASS because
a date column parsed consistently.

Policy asserted here:

* The header is the first row, within the first ``HEADER_SEARCH_ROWS`` rows,
  whose roles make the table checkable, and only when every row above it is
  free of numbers. Anything else is not a header: the gate does not guess one
  to force arithmetic.
* A table that carries numbers is *verified* only when at least one arithmetic
  check (row or subtotal identity) ran and passed on it.
* A page with an unverified numeric table is never PASS. It is NOT_CHECKABLE
  unless something actually failed, in which case FAIL/INDETERMINATE stand.
* Each table reports how its header was chosen in a ``table_verification``
  check, so the verdict is explainable from the persisted record alone.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Sequence

import pytest

from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClass,
    PageClassification,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.quality.gate import (
    HEADER_SEARCH_ROWS,
    ExtractionQualityGate,
)
from rbac_backend.services.extraction.quality.models import QualityVerdict, Verdict

HEADER = ["Sr. No", "Description", "Nos", "QTY", "Unit", "Rate in INR", "Amount in INR"]
DATA = [
    ["1", "Providing guide wall", "1", "100", "mtr", "3,200.00", "320,000"],
    ["2", "D-wall excavation", "2", "50", "Sqm", "4,000.00", "400,000"],
]
TOTAL = ["", "Total", "", "", "", "", "720,000"]
BLANK = [""] * len(HEADER)
TITLE = ["Mobilization & Demobilization Charge"] + [""] * (len(HEADER) - 1)


def _page(text: str = "Cost breakdown table") -> ExtractedPage:
    return ExtractedPage(
        number=4,
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


def _assess(*tables: Sequence[Sequence[str]]) -> QualityVerdict:
    return ExtractionQualityGate().assess(_page(), tables=list(tables))


def _verdicts(verdict: QualityVerdict, name: str) -> List[Verdict]:
    return [check.verdict for check in verdict.checks if check.name == name]


def _table_checks(verdict: QualityVerdict) -> List[str]:
    return [
        check.detail for check in verdict.checks if check.name == "table_verification"
    ]


def _header_refs(verdict: QualityVerdict) -> List[str]:
    return [
        ref
        for check in verdict.checks
        if check.name == "table_verification"
        for ref in check.evidence_refs
    ]


# --- A/B/C: the header is found below leading non-header rows -----------------


@pytest.mark.parametrize(
    ("leading", "header_index"),
    [
        pytest.param([BLANK], 1, id="A-blank-row"),
        pytest.param([TITLE], 1, id="B-title-row"),
        pytest.param([BLANK, TITLE], 2, id="C-blank-then-title"),
        pytest.param([BLANK, TITLE, BLANK, BLANK], 4, id="four-leading-rows"),
    ],
)
def test_the_real_header_is_found_below_leading_rows(
    leading: List[List[str]], header_index: int
) -> None:
    verdict = _assess([*leading, HEADER, *DATA, TOTAL])

    assert _verdicts(verdict, "row_identity") == [Verdict.PASS, Verdict.PASS]
    assert _verdicts(verdict, "subtotal_identity") == [Verdict.PASS]
    assert _header_refs(verdict) == [f"table[0].header.row[{header_index}]"]
    assert verdict.verdict is Verdict.PASS


def test_arithmetic_below_a_title_row_can_fail() -> None:
    wrong = [
        ["1", "Providing guide wall", "1", "100", "mtr", "3,200.00", "330,000"],
        DATA[1],
    ]

    verdict = _assess([TITLE, HEADER, *wrong, ["", "Total", "", "", "", "", "730,000"]])

    assert Verdict.FAIL in _verdicts(verdict, "row_identity")
    assert verdict.verdict is Verdict.FAIL
    assert verdict.escalates is True


def test_a_split_digit_repair_is_proposed_below_a_title_row() -> None:
    corrupted = ["1", "Providing guide wall", "1", "100", "mtr", "3,200.00", "3 20,000"]

    verdict = _assess([BLANK, TITLE, HEADER, corrupted, DATA[1], TOTAL])

    assert [(r.before, r.after) for r in verdict.repairs] == [("3 20,000", "320,000")]


# --- D: a first-row header is unchanged ----------------------------------------


def test_a_first_row_header_is_still_used() -> None:
    verdict = _assess([HEADER, *DATA, TOTAL])

    assert _verdicts(verdict, "row_identity") == [Verdict.PASS, Verdict.PASS]
    assert _verdicts(verdict, "subtotal_identity") == [Verdict.PASS]
    assert _header_refs(verdict) == ["table[0].header.row[0]"]
    assert verdict.verdict is Verdict.PASS


# --- E: no header anywhere stays not checkable --------------------------------


def test_no_valid_header_is_never_guessed() -> None:
    computation = [
        ["A", "Grab Idle Days", "", "35", "Days"],
        ["B", "Monthly Rental", "", "2,000,000", "INR"],
        ["D", "per Hrs Cost", "[B/C]", "7,692.31", "INR/Hrs"],
    ]

    verdict = _assess(computation)

    assert _verdicts(verdict, "row_identity") == [Verdict.NOT_CHECKABLE]
    assert "subtotal_identity" not in {check.name for check in verdict.checks}
    assert _verdicts(verdict, "table_verification") == [Verdict.NOT_CHECKABLE]
    assert _header_refs(verdict) == []
    assert verdict.verdict is Verdict.NOT_CHECKABLE
    assert verdict.escalates is False


def test_a_header_beyond_the_search_window_is_not_used() -> None:
    leading = [BLANK] * HEADER_SEARCH_ROWS

    verdict = _assess([*leading, HEADER, *DATA, TOTAL])

    assert _verdicts(verdict, "row_identity") == [Verdict.NOT_CHECKABLE]
    assert verdict.verdict is Verdict.NOT_CHECKABLE


# --- F: ambiguous candidates resolve deterministically or not at all ----------


def test_a_partial_match_is_passed_over_for_the_full_header() -> None:
    partial = ["", "Description", "", "", "", "", "Amount"]

    verdict = _assess([partial, HEADER, *DATA, TOTAL])

    assert _header_refs(verdict) == ["table[0].header.row[1]"]
    assert _verdicts(verdict, "row_identity") == [Verdict.PASS, Verdict.PASS]


def test_two_checkable_rows_resolve_to_the_first() -> None:
    verdict = _assess([TITLE, HEADER, HEADER, *DATA, TOTAL])

    assert _header_refs(verdict) == ["table[0].header.row[1]"]


def test_a_header_shaped_row_below_numeric_data_is_not_a_header() -> None:
    """Rows above a header must be numberless: otherwise data would be skipped
    silently and the table reported verified over rows nobody checked."""
    verdict = _assess([DATA[0], HEADER, DATA[1], TOTAL])

    assert _header_refs(verdict) == []
    assert _verdicts(verdict, "row_identity") == [Verdict.NOT_CHECKABLE]
    assert verdict.verdict is Verdict.NOT_CHECKABLE


# --- G: an unrelated passing check does not certify an unchecked table --------

DATE_LOG = [
    ["Date", "Count", "Status"],
    ["13/10/2022", "1", "Idle"],
    ["14/10/2022", "1", "Idle"],
    ["15/10/2022", "1", "Idle"],
]


def test_a_date_pass_does_not_make_an_unverified_numeric_table_pass() -> None:
    """Real page 4: three unverified tables plus `date_convention: pass` = PASS."""
    verdict = _assess(DATE_LOG)

    assert _verdicts(verdict, "date_convention") == [Verdict.PASS]
    assert verdict.verdict is Verdict.NOT_CHECKABLE
    assert verdict.escalates is False


def test_a_date_only_table_still_passes_on_its_dates() -> None:
    """Outside the numeric case the date check keeps its meaning."""
    dates_only = [["Date", "Status"], ["13/10/2022", "Idle"], ["14/10/2022", "Idle"]]

    verdict = _assess(dates_only)

    assert verdict.verdict is Verdict.PASS


def test_the_unverified_table_is_explained_in_the_record() -> None:
    verdict = _assess(DATE_LOG)

    [detail] = _table_checks(verdict)
    assert "no header row" in detail
    assert "numeric cell" in detail


# --- H/I: checkable tables keep the existing semantics ------------------------


def test_a_verified_table_reports_its_header_and_roles() -> None:
    verdict = _assess([BLANK, TITLE, HEADER, *DATA, TOTAL])

    [detail] = _table_checks(verdict)
    assert "header row 2" in detail
    assert "rate" in detail and "amount" in detail and "quantity" in detail
    assert _verdicts(verdict, "table_verification") == [Verdict.PASS]


def test_a_checkable_table_whose_rows_cannot_be_read_is_not_verified() -> None:
    """Roles alone are not verification: some arithmetic must actually pass."""
    no_operands = [["1", "Lump sum item", "", "", "LS", "", "500,000"]]

    verdict = _assess([HEADER, *no_operands, ["2", "Another", "", "", "LS", "", "10"]])

    assert Verdict.PASS not in _verdicts(verdict, "row_identity")
    assert verdict.verdict is Verdict.NOT_CHECKABLE


# --- J: several tables -------------------------------------------------------


def test_one_unverified_table_keeps_the_page_from_passing() -> None:
    verdict = _assess([TITLE, HEADER, *DATA, TOTAL], DATE_LOG)

    assert _verdicts(verdict, "table_verification") == [
        Verdict.PASS,
        Verdict.NOT_CHECKABLE,
    ]
    assert verdict.verdict is Verdict.NOT_CHECKABLE


def test_all_tables_verified_lets_the_page_pass() -> None:
    verdict = _assess([TITLE, HEADER, *DATA, TOTAL], [BLANK, HEADER, *DATA, TOTAL])

    assert _verdicts(verdict, "table_verification") == [Verdict.PASS, Verdict.PASS]
    assert _header_refs(verdict) == [
        "table[0].header.row[1]",
        "table[1].header.row[1]",
    ]
    assert verdict.verdict is Verdict.PASS


def test_a_failure_still_outranks_an_unverified_table() -> None:
    wrong = [
        ["1", "Providing guide wall", "1", "100", "mtr", "3,200.00", "999"],
        DATA[1],
    ]

    verdict = _assess([HEADER, *wrong], DATE_LOG)

    assert verdict.verdict is Verdict.FAIL


def test_a_table_without_numbers_does_not_block_a_pass() -> None:
    parties = [["Party", "Role"], ["UPMRC", "Employer"], ["Contractor", "EPC"]]

    verdict = _assess([HEADER, *DATA, TOTAL], parties)

    assert verdict.verdict is Verdict.PASS


def test_a_single_row_numeric_table_is_unverified_too() -> None:
    """Too short to hold a header and data, so nothing verified its number."""
    verdict = _assess([HEADER, *DATA, TOTAL], [["Total claim", "34,011,265"]])

    assert _verdicts(verdict, "table_verification") == [
        Verdict.PASS,
        Verdict.NOT_CHECKABLE,
    ]
    assert verdict.verdict is Verdict.NOT_CHECKABLE


# --- Real claim acceptance (customer PDF, never committed) --------------------

_AUDIT_DIR = os.environ.get("EXTRACTION_AUDIT_FIXTURE_DIR")

#: Measured with PR 3 applied. p5 stays FAIL on reading order: that is R4
#: (side-by-side tables) and is deliberately out of scope here.
_REAL_CLAIM_AFTER: Dict[int, Verdict] = {
    2: Verdict.NOT_CHECKABLE,
    3: Verdict.NOT_CHECKABLE,
    4: Verdict.NOT_CHECKABLE,
    5: Verdict.FAIL,
    6: Verdict.PASS,
}


@pytest.mark.skipif(not _AUDIT_DIR, reason="real audit PDFs not provided")
async def test_real_claim_tables_are_checked_against_their_real_headers(
    tmp_path: Path,
) -> None:
    from rbac_backend.config.document_processing_config import (
        DocumentProcessingConfig,
    )
    from rbac_backend.services.extraction.page_store import NullPageStore
    from rbac_backend.services.ocr_service import OCRService

    source = Path(_AUDIT_DIR or "") / "claim.pdf"
    if not source.exists():
        pytest.skip(f"{source} not present")

    class _Runner:
        async def run(self, source: Path, page_numbers: Sequence[int], language: str):
            return {page: "scanned covering letter" for page in page_numbers}

    config = DocumentProcessingConfig()
    config.ocr_enabled = True
    config.contract_ocr_min_text_chars_per_page = 40
    config.process_dir = str(tmp_path / "processed")
    service = OCRService(config)
    service._ocr_available = True
    result = await service.process_pdf_pagewise(
        source, store=NullPageStore(), document_id="audit-claim", ocr_runner=_Runner()
    )

    gate = ExtractionQualityGate()
    verdicts = {
        page.number: gate.assess(page, tables=page.tables or None)
        for page in result.pages
        if page.number in _REAL_CLAIM_AFTER
    }

    assert {number: v.verdict for number, v in verdicts.items()} == _REAL_CLAIM_AFTER
    # The false PASS: p4's tables were never verified.
    assert Verdict.PASS not in _verdicts(verdicts[4], "table_verification")
    # The BOQ tables are now actually checked.
    assert _verdicts(verdicts[6], "row_identity") == [Verdict.PASS, Verdict.PASS]
    assert _verdicts(verdicts[6], "subtotal_identity") == [Verdict.PASS]
    assert _verdicts(verdicts[5], "row_identity").count(Verdict.PASS) == 5
    # R4 remains open and visible.
    assert _verdicts(verdicts[5], "reading_order") == [Verdict.FAIL]
