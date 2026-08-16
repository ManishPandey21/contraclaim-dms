"""G25: a proper noun is not a reading-order failure.

`check_reading_order` exists to catch a narrative shredded by an adjacent
column's cells - the measured page 5 case, where table header cells were
spliced into a sentence:

    ... AS PER THE Depth Thickness Length Volume
    DW No Area
    ... DIRECTION OF ENGINEER IN CHARGE ... (mtr) (mtr) (mtr) (m3)

The heuristic looked for a trailing run of capitalised non-function words. That
is also exactly what an ordinary contractual sentence looks like when it ends in
a company name, a JV, a drawing title or a station. Five of seven realistic
sentences escalated, and with the fallback ladder disabled every one of those
becomes human review.

The corrected signal: capitalisation alone is not evidence. The trailing run has
to look like *column headers* - measure and quantity nouns - or carry unit
fragments, which is what an interleaved table actually contributes.
"""

from __future__ import annotations

import pytest

from rbac_backend.services.extraction.models import PageClass, PageClassification
from rbac_backend.services.extraction.quality.models import Verdict
from rbac_backend.services.extraction.quality.reading_order import check_reading_order


def _classification(page_class: PageClass = PageClass.TEXT_NATIVE) -> PageClassification:
    return PageClassification(
        page_class=page_class,
        char_count=200,
        image_count=0,
        image_coverage=0.0,
        table_count=0,
        width=595.0,
        height=842.0,
        rotation=0,
    )


#: Ordinary contractual correspondence. Every one of these ends in a capitalised
#: proper noun, and none of them is corrupted.
VALID_CORRESPONDENCE = [
    "We refer to the Engineer Instruction dated 09 March 2021 issued by General Consultant TYPSA ITALFERR JV",
    "This letter is addressed to the Employer Uttar Pradesh Metro Rail Corporation Limited",
    "Kindly arrange to release the payment to Gulermak Sam India Kanpur Metro JV",
    "The works were executed in accordance with the approved General Arrangement Drawing",
    "We request your good office to kindly expedite the approval of the Revised Cost Estimate",
    "Please find enclosed the Monthly Progress Report for the month of March Two Thousand Twenty Three",
    "Attached herewith are the supporting documents namely Measurement Book Site Instruction Register",
    "The reworks were carried out at the station box of Nayaganj Metro Station Kanpur",
    "This claim is submitted under Clause 20 of the General Conditions of Contract",
    "Signed for and on behalf of the Contractor Gulermak Sam India Kanpur Metro JV",
    "The matter was discussed in the meeting chaired by the Chief Project Manager",
    "We enclose the revised Bar Bending Schedule and the Reinforcement Detail Drawing",
]

#: Genuinely destroyed reading order: an adjacent table's header cells spliced
#: into the sentence, which is the measured corruption.
DESTROYED_READING_ORDER = [
    "GUIDE WALL AND D WALL REWORKS COST WITH INCLUDING ALL TOOLS AS PER THE Depth Thickness Length Volume",
    "reworks executed according to the approved specification drawings including materials labour and supervision provided (mtr) (mtr) (mtr) (m3)",
    "The contractor shall provide plant labour materials supervision transport scaffolding described herein Qty Nos Depth Thickness",
]


@pytest.mark.parametrize("line", VALID_CORRESPONDENCE)
def test_ordinary_correspondence_is_not_a_reading_order_failure(line: str) -> None:
    result = check_reading_order(line, _classification())

    assert result.verdict is not Verdict.FAIL, (
        f"valid correspondence escalated as corrupted reading order: {line!r}"
    )


@pytest.mark.parametrize("line", DESTROYED_READING_ORDER)
def test_genuinely_interleaved_text_is_still_caught(line: str) -> None:
    result = check_reading_order(line, _classification())

    assert result.verdict is Verdict.FAIL, (
        f"genuinely shredded reading order was not detected: {line!r}"
    )


def test_the_check_has_not_become_permissive_of_everything() -> None:
    """A fix that passes every input would be worse than the defect."""
    caught = [
        line
        for line in DESTROYED_READING_ORDER
        if check_reading_order(line, _classification()).verdict is Verdict.FAIL
    ]

    assert len(caught) == len(DESTROYED_READING_ORDER)


def test_the_false_positive_rate_on_valid_correspondence_is_zero() -> None:
    failures = [
        line
        for line in VALID_CORRESPONDENCE
        if check_reading_order(line, _classification()).verdict is Verdict.FAIL
    ]

    assert failures == [], f"{len(failures)}/{len(VALID_CORRESPONDENCE)} false positives"
