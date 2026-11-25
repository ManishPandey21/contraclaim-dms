"""4B-28 (C-16): the submission validator against a bypass corpus and a true-positive corpus.

Plan section 6.6: amounts in words; lakh and crore; borrowed digits; cross-head
swaps; a negated reservation; "Cl." and "Article". Every bypass is rejected;
every faithful rewrite is accepted.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from rbac_backend.services import claim_bench  # noqa: F401  isort: skip  (import order, see test_docx_xml_fuzz)
from rbac_backend.services.claim_submission import validate_section_text
from rbac_backend.services.submission_figures import (
    Figure,
    clauses_in,
    figures_in,
    reserves_rights,
    words_to_number,
)

RECORD = [
    "Head H1 (Delayed handover of Site) claims INR 1,20,00,000.00 and 146 days under Sub-Clause 8.4.",
    "Head H2 (Idle plant) claims INR 45,00,000.00 and 30 days under Clause 2.1.",
    "The event began on 10 March 2026. Notice was given on 11 April 2026.",
]
CITED = {"8.4", "2.1"}

BYPASS = [
    ("amount in words", "quantum", "Head H1 claims twelve crore rupees."),
    ("scaled amount, digits borrowed from a date", "quantum", "Head H1 claims INR 10 lakh."),
    ("two scaled amounts, digits borrowed", "quantum", "The Contractor claims INR 10 lakh and 45 crore."),
    ("day count borrowed from a year", "delay", "Head H1 claims 2026 days."),
    ("day count borrowed from an amount's digits", "delay", "Head H2 claims 45 days."),
    ("cross-head swap of an amount", "quantum", "Head H1 claims INR 45,00,000.00."),
    ("cross-head swap of days", "delay", "Head H2 claims 146 days."),
    ("days in words", "delay", "Head H1 claims one hundred and fifty days."),
    ("uncited clause, Cl.", "contractual_basis", "Under Cl. 13.7 the Contractor is entitled to time."),
    ("uncited clause, Article", "contractual_basis", "Article 20.1 entitles the Contractor to payment."),
    ("negated reservation", "relief_and_reservation", "The Contractor claims 146 days. The Contractor does not reserve its rights."),
    ("no rights reserved", "relief_and_reservation", "The Contractor claims 146 days. No rights are reserved."),
    ("waived reservation", "relief_and_reservation", "The Contractor claims 146 days and waives any reservation."),
]


@pytest.mark.parametrize("name,key,text", BYPASS, ids=[item[0] for item in BYPASS])
def test_every_bypass_is_rejected(name: str, key: str, text: str) -> None:
    assert validate_section_text(key, [text], RECORD, CITED), name


TRUE_POSITIVES = [
    ("same amount in another format", "quantum", "Head H1 claims INR 1.2 crore, being INR 1,20,00,000.00."),
    ("amount in words that matches", "quantum", "Head H1 claims one crore twenty lakh rupees."),
    ("lakh form of the record figure", "quantum", "Head H2 claims INR 45 lakh for idle plant."),
    ("days per head", "delay", "Head H1 claims 146 days; Head H2 claims 30 days."),
    ("dates and plain numbers", "chronology_and_evidence", "The event began on 10 March 2026 and notice followed on 11 April 2026."),
    ("cited clauses in every form", "contractual_basis", "Cl. 8.4 and Clause 2.1 apply; see also Sub-Clause 8.4."),
    ("year is not a clause", "contractual_basis", "The Clause 2026 amendments do not apply; Sub-Clause 8.4 does."),
    ("reservation stated", "relief_and_reservation", "The Contractor claims 146 days. The Contractor reserves all of its rights."),
    ("reservation passive", "relief_and_reservation", "Head H1 claims 146 days. All rights are reserved."),
    ("prose number words are not figures", "executive_summary", "One of the events delayed the works under Sub-Clause 8.4."),
]


@pytest.mark.parametrize("name,key,text", TRUE_POSITIVES, ids=[item[0] for item in TRUE_POSITIVES])
def test_every_faithful_rewrite_is_accepted(name: str, key: str, text: str) -> None:
    assert validate_section_text(key, [text], RECORD, CITED) == [], name


def test_figures_are_typed_and_scaled() -> None:
    assert Figure("money", Decimal(12_000_000)) in figures_in("INR 1.2 crore")
    assert Figure("money", Decimal(1_000_000)) in figures_in("₹ 10 lakh")
    assert Figure("days", Decimal(146)) in figures_in("146 days")
    assert Figure("number", Decimal(2026)) in figures_in("11 April 2026")
    assert figures_in("Head H1 under Sub-Clause 8.4") == set()  # head ids and clause numbers are not figures


@pytest.mark.parametrize(
    "words,value",
    [("forty-five crore", 450_000_000), ("one crore twenty lakh", 12_000_000), ("one hundred and forty six", 146),
     ("ten lakh", 1_000_000), ("two million", 2_000_000)],
)
def test_number_words(words: str, value: int) -> None:
    assert words_to_number(words) == value


def test_clause_and_reservation_readers() -> None:
    assert clauses_in("Cl. 8.4, Clause 2.1(a), Article 20.1, Art. 3 and Clause 2026") == {"8.4", "2.1(a)", "20.1", "3"}
    assert reserves_rights("Rights are reserved.") and not reserves_rights("Rights are not reserved.")
