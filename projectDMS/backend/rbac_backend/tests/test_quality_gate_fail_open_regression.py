"""G18: a corrupt amount beside a clean peer row must not yield page PASS.

The defect these tests pin was invisible for one reason: every existing case
assessed a SINGLE-row table. Real BOQ pages have many rows, and with a clean
peer row present the gate promoted the whole page to PASS while one row's
monetary value was unreadable.

Two mechanisms combined:

  * `parse_amount` returns None for any non-bare-numeric literal, so `check_row`
    reported NOT_CHECKABLE - "we could not verify" - for an AMOUNT column that
    was mapped and populated and simply could not be read;
  * `_aggregate` promotes on `any(... PASS ...)`, so the clean sibling's PASS
    became the page verdict.

The corroborating subtotal check was disabled by the same corruption
("one or more row amounts are non-numeric"), so nothing caught it.

The invariant: NOT_CHECKABLE means the check did not apply. It must never mean
"the check applied and we could not read the number".
"""

from __future__ import annotations

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

#: A correct line item: 1 x 100,000 = 100,000. Present in every table below so
#: the gate always has something it can legitimately pass.
CLEAN_PEER = ["2", "Survey", "1", "100,000", "100,000"]

#: Forms that stand in for a true 134,460 (162 x 830) but cannot be parsed.
#: The last entry is the corruption actually measured in the source document.
UNREADABLE_AMOUNTS = [
    "Rs 1 34,460",
    "(1 34,460)",
    "-1 34,460",
    "1 900 000",
    "12 3,456",
    "1 ,900,000",
]


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


@pytest.mark.parametrize("corrupt_amount", UNREADABLE_AMOUNTS)
def test_a_corrupt_amount_beside_a_clean_row_does_not_pass(
    corrupt_amount: str,
) -> None:
    """The headline defect: page PASS while a monetary value is unreadable."""
    gate = ExtractionQualityGate()
    tables = [[HEADERS, ["1", "Drilling", "162", "830", corrupt_amount], CLEAN_PEER]]

    verdict = gate.assess(_page(), tables=tables)

    assert verdict.verdict is not Verdict.PASS, (
        f"page passed while amount {corrupt_amount!r} could not be read"
    )


@pytest.mark.parametrize("corrupt_amount", UNREADABLE_AMOUNTS)
def test_a_corrupt_amount_beside_a_clean_row_escalates(corrupt_amount: str) -> None:
    """Not passing is not enough - it has to reach a human."""
    gate = ExtractionQualityGate()
    tables = [[HEADERS, ["1", "Drilling", "162", "830", corrupt_amount], CLEAN_PEER]]

    verdict = gate.assess(_page(), tables=tables)

    assert verdict.escalates is True, (
        f"amount {corrupt_amount!r} was unreadable but nothing escalated"
    )


def test_the_measured_corruption_does_not_pass_in_a_multi_row_table() -> None:
    """`1 ,900,000` is one of the nine corruptions the gate exists to catch."""
    gate = ExtractionQualityGate()
    tables = [
        [
            HEADERS,
            ["1", "Mobilization", "1", "1,900,000", "1 ,900,000"],
            CLEAN_PEER,
        ]
    ]

    verdict = gate.assess(_page(), tables=tables)

    assert verdict.verdict is not Verdict.PASS
    assert verdict.escalates is True


def test_several_corrupt_rows_still_do_not_pass() -> None:
    gate = ExtractionQualityGate()
    tables = [
        [
            HEADERS,
            ["1", "Drilling", "162", "830", "Rs 1 34,460"],
            ["2", "Mobilization", "1", "1,900,000", "1 ,900,000"],
            CLEAN_PEER,
        ]
    ]

    verdict = gate.assess(_page(), tables=tables)

    assert verdict.verdict is not Verdict.PASS
    assert verdict.escalates is True


# --- The other half of the invariant: legitimate cases must stay unescalated --


def test_a_repeated_header_row_is_still_not_a_failure() -> None:
    """Case 10 of the false-positive set: a continued table repeats its header.

    `Amount` is unparseable but carries no digits, so it is structural noise,
    not an unreadable number. It must not be treated as corruption - that is
    exactly the false escalation the gate was built to avoid.
    """
    gate = ExtractionQualityGate()
    tables = [[HEADERS, ["S/N", "Description", "Qty", "Rate", "Amount"], CLEAN_PEER]]

    verdict = gate.assess(_page(), tables=tables)

    assert verdict.verdict is not Verdict.FAIL
    assert verdict.escalates is False


def test_a_missing_amount_is_still_not_a_failure() -> None:
    """Case 11: an empty amount cell is genuinely uncheckable."""
    gate = ExtractionQualityGate()
    tables = [[HEADERS, ["1", "Provisional item", "1", "5,000", ""], CLEAN_PEER]]

    verdict = gate.assess(_page(), tables=tables)

    assert verdict.verdict is not Verdict.FAIL
    assert verdict.escalates is False


def test_a_clean_multi_row_table_still_passes() -> None:
    """The fix must not make ordinary correct tables escalate."""
    gate = ExtractionQualityGate()
    tables = [
        [
            HEADERS,
            ["1", "Drilling", "162", "830", "134,460"],
            CLEAN_PEER,
        ]
    ]

    verdict = gate.assess(_page(), tables=tables)

    assert verdict.verdict is Verdict.PASS
    assert verdict.escalates is False
