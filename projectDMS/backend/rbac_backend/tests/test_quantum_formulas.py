"""4B-24 (D-14, C-06): money in paise; Hudson, Emden and Eichleay computed in code.

The formulas are the published statements cited in services/quantum_formulas.py.
No published numeric worked example was found for any of the three (searched
2026-11-17: the Hudson Formula article, Designing Buildings' head-office
overhead page and the FTI Consulting paper carry formulas only, or are gone).
So each fixture below is worked by hand, step by step, in its comment, and the
code must reproduce the hand result to the paisa.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from rbac_backend.models.claim import ClaimHead
from rbac_backend.models.claim_delay import FormulaInput, QuantumLine, QuantumRecord
from rbac_backend.services.claim_quantum import build_quantum_view, line_lock_reasons
from rbac_backend.services.quantum_formulas import compute_paise, delay_days, missing_inputs, rupees, to_paise


def _inputs(**values: str) -> dict:
    return {key: FormulaInput(value=value, document_id="acct") for key, value in values.items()}


def test_hudson_by_hand() -> None:
    # 5% tender overhead and profit; contract sum Rs 1,00,00,000; 365-day contract; 30 days' delay.
    # 0.05 x 1,00,00,000 = 5,00,000 a contract period; / 365 = 1,369.86301369... a day;
    # x 30 = 41,095.890410958... -> Rs 41,095.89 (next digit 0: round down).
    inputs = _inputs(tender_percentage="5", contract_sum="10000000", contract_period_days="365")
    assert compute_paise("hudson", inputs, 30) == 4_109_589


def test_emden_by_hand() -> None:
    # Accounts: overhead and profit Rs 12 crore on turnover Rs 150 crore = 8%.
    # Contract sum Rs 50 crore over 730 days; 61 days' delay.
    # 0.08 x 50,00,00,000 = 4,00,00,000; x 61 = 2,44,00,00,000; / 730 = 33,42,465.7534246... -> Rs 33,42,465.75.
    inputs = _inputs(overhead_and_profit="120000000", turnover="1500000000", contract_sum="500000000", contract_period_days="730")
    assert compute_paise("emden", inputs, 61) == 334_246_575


def test_eichleay_by_hand() -> None:
    # Contract billings Rs 30 crore of total billings Rs 200 crore = 15%; head-office overhead
    # in the period Rs 16 crore -> allocable Rs 2.4 crore; / 500 days = Rs 48,000 a day; x 45 = Rs 21,60,000.00.
    inputs = _inputs(contract_billings="300000000", total_billings="2000000000", total_overhead="160000000", performance_days="500")
    assert compute_paise("eichleay", inputs, 45) == 216_000_000


def test_rounding_is_half_up_to_the_paisa() -> None:
    # Hudson giving exactly half a paisa: 1% x Rs 1,000.50 / 1 day x 1 day = Rs 10.005 -> Rs 10.01.
    inputs = _inputs(tender_percentage="1", contract_sum="1000.50", contract_period_days="1")
    assert compute_paise("hudson", inputs, 1) == 1001
    assert to_paise("0.125") == 13 and to_paise("0.124") == 12
    # A binary float is read through its decimal text: 0.1 + 0.2 is 0.30000000000000004, i.e. 30 paise.
    assert to_paise(0.1 + 0.2) == 30
    assert to_paise("1,20,000.10") == 12_000_010


@pytest.mark.parametrize("bad", ["", "abc", "NaN", "Infinity", True])
def test_what_is_not_an_amount_is_refused(bad) -> None:
    with pytest.raises(ValueError):
        to_paise(bad)


def test_the_period_is_counted_with_both_ends_included() -> None:
    assert delay_days(datetime(2026, 3, 1), datetime(2026, 3, 30)) == 30
    assert delay_days(datetime(2026, 3, 1), datetime(2026, 3, 1)) == 1
    assert delay_days(datetime(2026, 3, 2), datetime(2026, 3, 1)) is None
    assert delay_days(None, datetime(2026, 3, 1)) is None


def test_missing_or_impossible_inputs_are_named() -> None:
    assert missing_inputs("hudson", _inputs(tender_percentage="5", contract_sum="0")) == [
        "Contract sum", "Contract period (days)",
    ]
    assert missing_inputs("hudson", _inputs(tender_percentage="120", contract_sum="1", contract_period_days="1")) == [
        "Head-office overhead and profit in the tender (%)",
    ]


def test_totals_are_exact_at_ten_thousand_crore() -> None:
    # Rs 10,000 crore = Rs 1,00,00,00,00,000 = 10^13 paise, built from three lines with paise.
    parts = ["33333333333.33", "33333333333.33", "33333333333.34"]
    head = ClaimHead(
        head_id="H1", title="Prolongation",
        quantum_lines=[
            QuantumLine(line_id=f"Q{i}", category="direct", description="Cost", amount_paise=to_paise(part),
                        records=[QuantumRecord(document_id="d")])
            for i, part in enumerate(parts)
        ],
    )
    view = build_quantum_view(head, set(), linked={"d"})
    assert view.claimed_total_paise == 10_000_000_000_000
    assert view.supported_total_paise == 10_000_000_000_000 and view.locked_total_paise == 0
    assert Decimal(repr(view.claimed_total)) == Decimal("100000000000")
    assert rupees(1) == 0.01 and to_paise(repr(rupees(10_000_000_000_007))) == 10_000_000_000_007


def test_a_stored_line_from_before_paise_is_read_through_its_decimal_text() -> None:
    line = QuantumLine(line_id="Q1", category="direct", description="Old line", amount=125000.1)
    assert line.amount_paise == 12_500_010 and line.amount == 125000.1


def test_a_record_no_longer_linked_locks_the_line_and_says_which() -> None:
    line = QuantumLine(line_id="Q1", category="direct", description="Plant", amount_paise=100,
                       records=[QuantumRecord(document_id="kept"), QuantumRecord(document_id="gone")])
    assert line_lock_reasons(line, set(), linked={"kept", "gone"}) == []
    reasons = line_lock_reasons(line, set(), linked={"kept"})
    assert reasons == [
        "Record 2 on this line (supporting record) is no longer linked to this claim. "
        "Link it to the claim again, or remove it from the line."
    ]


def test_a_formula_input_without_its_record_or_unlinked_locks_the_line() -> None:
    compensable = {date(2026, 3, day) for day in range(1, 31)}
    line = QuantumLine(
        line_id="Q1", category="overhead_formula", description="HO overhead", amount_paise=1, formula="hudson",
        period_start=datetime(2026, 3, 1), period_end=datetime(2026, 3, 30),
        formula_inputs={
            "tender_percentage": FormulaInput(value="5", document_id="tender"),
            "contract_sum": FormulaInput(value="10000000"),
            "contract_period_days": FormulaInput(value="365", document_id="contract"),
        },
        records=[QuantumRecord(document_id="accounts", role="actual_loss")],
    )
    reasons = line_lock_reasons(line, compensable, linked={"tender", "accounts"})
    assert any('"Contract sum" is taken from' in reason for reason in reasons)
    assert any('record for "Contract period (days)" is no longer linked' in reason for reason in reasons)
    line.formula_inputs["contract_sum"] = FormulaInput(value="10000000", document_id="contract")
    assert line_lock_reasons(line, compensable, linked={"tender", "accounts", "contract"}) == []


def test_totals_stay_exact_where_a_float_cannot_hold_paise() -> None:
    # Rs 90,00,00,00,00,00,000.01 has 17 significant digits; a binary float holds about 15.
    # Three such lines total Rs 2,70,00,00,00,00,00,000.03, i.e. 270,000,000,000,000,003 paise.
    head = ClaimHead(
        head_id="H1", title="Large",
        quantum_lines=[
            QuantumLine(line_id=f"Q{i}", category="direct", description="Cost", amount_paise=90_000_000_000_000_001,
                        records=[QuantumRecord(document_id="d")])
            for i in range(3)
        ],
    )
    view = build_quantum_view(head, set(), linked={"d"})
    assert view.claimed_total_paise == 270_000_000_000_000_003
    assert sum(line.amount for line in head.quantum_lines) * 100 != 270_000_000_000_000_003  # what floats would give


def test_the_client_names_each_figure_exactly_as_the_server_does() -> None:
    # The Quantum tab asks for the figures by these labels (client/src/lib/delay-lab-logic.ts).
    from pathlib import Path

    from rbac_backend.services.quantum_formulas import FORMULA_INPUTS

    client = (Path(__file__).resolve().parents[3] / "client/src/lib/delay-lab-logic.ts").read_text(encoding="utf-8")
    for specs in FORMULA_INPUTS.values():
        for spec in specs:
            assert f'{{ key: "{spec.key}", label: "{spec.label}" }}' in client, spec


def test_eichleay_figures_are_billings_for_one_period_and_checked_together() -> None:
    from rbac_backend.services.quantum_formulas import FORMULA_INPUTS, inconsistencies

    labels = [spec.label for spec in FORMULA_INPUTS["eichleay"]]
    assert labels[0].startswith("Billings on this contract during actual performance")
    assert all("same period" in label for label in labels[1:])
    assert "excluding profit" in labels[2]
    sound = _inputs(contract_billings="300000000", total_billings="2000000000", total_overhead="160000000", performance_days="500")
    assert inconsistencies("eichleay", sound, 45) == []
    assert inconsistencies("eichleay", sound, 501) == [
        "The period of delay cannot be longer than the period of actual performance it falls within."
    ]
    larger = _inputs(contract_billings="2000000001", total_billings="2000000000", total_overhead="160000000", performance_days="500")
    assert inconsistencies("eichleay", larger, 45) == [
        "This contract's billings cannot exceed the company's total billings for the same period."
    ]
    emden = _inputs(overhead_and_profit="150", turnover="150", contract_sum="1", contract_period_days="1")
    assert inconsistencies("emden", emden, 1) == [
        "Head-office overhead and profit cannot be as large as the turnover in the same accounts."
    ]


def test_a_formula_line_is_marked_as_a_calculation_and_overlapping_formula_lines_lock() -> None:
    def hudson(line_id: str, start: int, end: int) -> QuantumLine:
        return QuantumLine(
            line_id=line_id, category="overhead_formula", description="HO", amount_paise=1000, formula="hudson",
            period_start=datetime(2026, 3, start), period_end=datetime(2026, 3, end),
            formula_inputs={key: FormulaInput(value="1", document_id="d") for key in
                            ("tender_percentage", "contract_sum", "contract_period_days")},
            records=[QuantumRecord(document_id="d", role="actual_loss")],
        )

    compensable = {date(2026, 3, day) for day in range(1, 32)}
    direct = QuantumLine(line_id="Q9", category="direct", description="Plant", amount_paise=500, records=[QuantumRecord(document_id="d")])
    head = ClaimHead(head_id="H1", title="Overheads", quantum_lines=[hudson("Q1", 1, 10), hudson("Q2", 20, 31), direct])
    view = build_quantum_view(head, compensable, linked={"d"})
    assert [line.calculated for line in view.lines] == [True, True, False]
    assert view.lines[0].calculation_note and "not a finding that this amount is recoverable" in view.lines[0].calculation_note
    assert view.lines[2].calculation_note is None
    assert view.calculated_total_paise == 2000 and view.locked_total_paise == 0

    # Q2 now starts on 8 March: 8, 9 and 10 March are claimed twice.
    head.quantum_lines[1] = hudson("Q2", 8, 31)
    view = build_quantum_view(head, compensable, linked={"d"})
    assert view.lines[0].locked and view.lines[1].locked and not view.lines[2].locked
    assert view.lines[0].lock_reasons == [
        "Overlaps line 2 (Hudson) for 3 days: head-office overhead would be recovered twice for those days. "
        "Change one period or remove one line."
    ]
    assert "Overlaps line 1 (Hudson) for 3 days" in view.lines[1].lock_reasons[0]
    assert view.supported_total_paise == 500 and view.locked_total_paise == 2000

    # Sharing a single day (10 March, both ends counted) is still recovering it twice; adjacent periods are not.
    head.quantum_lines[1] = hudson("Q2", 10, 31)
    assert "for 1 day:" in build_quantum_view(head, compensable, linked={"d"}).lines[0].lock_reasons[0]
    head.quantum_lines[1] = hudson("Q2", 11, 31)
    assert not build_quantum_view(head, compensable, linked={"d"}).lines[0].locked


def test_the_export_states_amounts_from_paise_exactly() -> None:
    from rbac_backend.services.claim_submission import format_amount, format_paise

    assert format_paise(4_109_589) == "INR 41,095.89"
    assert format_paise(270_000_000_000_000_003) == "INR 2,70,00,00,00,00,00,000.03"
    assert format_paise(-1001) == "INR -10.01"
    # A rupee figure is read through its decimal text: 1.005 is Rs 1.01, as typed, not 1.00 as binary would give.
    assert f"{1.005:.2f}" == "1.00" and format_amount(1.005) == "INR 1.01"
    assert format_amount(125000.5) == format_paise(12_500_050)


def test_each_formula_line_carries_the_courts_caution_for_that_formula() -> None:
    # docs/QUANTUM_FORMULA_SOURCES.md: McDermott International v. Burn Standard (2006).
    from rbac_backend.services.quantum_formulas import FORMULA_CAUTIONS

    assert set(FORMULA_CAUTIONS) == set(FORMULA_INPUTS_KEYS)
    assert "little or no relation" in FORMULA_CAUTIONS["hudson"]
    assert "loss of opportunity cannot be proved" in FORMULA_CAUTIONS["eichleay"]
    line = QuantumLine(
        line_id="Q1", category="overhead_formula", description="HO", amount_paise=1, formula="emden",
        formula_inputs={}, records=[QuantumRecord(document_id="d", role="actual_loss")],
    )
    note = build_quantum_view(ClaimHead(head_id="H1", title="T", quantum_lines=[line]), set(), linked={"d"}).lines[0].calculation_note
    assert note.endswith(FORMULA_CAUTIONS["emden"]) and "may insist on proof of actual loss" in note


FORMULA_INPUTS_KEYS = ("hudson", "emden", "eichleay")


# --------------------------------------------------------------------------- #
# Published worked example: J. G. Zack, "Calculation and recovery of home office
# overhead", PMI Annual Seminars & Symposium, Houston (2000). Owner-supplied PDF,
# read 2026-11-17. One hypothetical case, worked by several formulas. Figures are
# US dollars; the formulas are currency-neutral, so they are entered as given.
#
# The paper's convention, recovered from its own figures: the share (billings or
# turnover ratio) is rounded to six decimal places, every money step and the daily
# rate to whole dollars (half-up), and the amount is the rounded rate times the days.
# Under it every Eichleay figure printed below is reproduced exactly. Without the
# six-place ratio, step 1 comes to $5,832,784, not the printed $5,832,787.
# --------------------------------------------------------------------------- #
from rbac_backend.services.quantum_formulas import Rounding, steps  # noqa: E402

PMI = Rounding(ratio_places=6, step_unit=Decimal("1"))
PMI_DELAY_DAYS = 235  # "Owner-caused Delay 235 cd's"


def test_pmi_eichleay_example_reproduced_with_its_rounding() -> None:
    # "($76,866,128 ÷ $381,095,333) x $28,918,417 = $5,832,787"
    # "($5,832,787 ÷ 655 cd's) = $8,905/cd"
    # "$8,905 x 235 cd's = $2,092,675"
    inputs = _inputs(contract_billings="76866128", total_billings="381095333", total_overhead="28918417", performance_days="655")
    worked = steps("eichleay", inputs, PMI_DELAY_DAYS, PMI)
    assert worked["share"] == Decimal("0.201698")
    assert worked["allocable_overhead"] == Decimal("5832787")
    assert worked["daily_rate"] == Decimal("8905")
    assert worked["amount"] == Decimal("2092675")

    # The product does not round on the way: the same inputs give $2,092,678.21.
    assert compute_paise("eichleay", inputs, PMI_DELAY_DAYS) == 209_267_821
    assert to_paise(steps("eichleay", inputs, PMI_DELAY_DAYS)["amount"]) == 209_267_821
    assert steps("eichleay", inputs, PMI_DELAY_DAYS, Rounding(step_unit=Decimal("1")))["allocable_overhead"] == Decimal("5832784")


@pytest.mark.parametrize(
    ("label", "total_billings", "total_overhead", "days", "allocable", "rate", "amount"),
    [
        # Modified Eichleay, variation 1: original-period revenue and overhead, planned 365 days.
        ("variation 1", "247711967", "16265000", "365", "5047095", "13828", "3249580"),
        # Variation 2: "($76,866,128 ÷ $ 254,824,241) x $16,265,000 = $4,906,240"; "$13,442/cd"; "$3,158,870".
        ("variation 2", "254824241", "16265000", "365", "4906240", "13442", "3158870"),
    ],
)
def test_pmi_modified_eichleay_variations_reproduced(label, total_billings, total_overhead, days, allocable, rate, amount) -> None:
    inputs = _inputs(contract_billings="76866128", total_billings=total_billings, total_overhead=total_overhead, performance_days=days)
    worked = steps("eichleay", inputs, PMI_DELAY_DAYS, PMI)
    assert (worked["allocable_overhead"], worked["daily_rate"], worked["amount"]) == (
        Decimal(allocable), Decimal(rate), Decimal(amount)
    ), label


def test_pmi_hudson_and_emden_reproduced() -> None:
    # Hudson: 7.0% bid overhead and profit, original contract $68,500,000 over 365 days.
    # Printed "7.0% x ($68,500,000 x 365 cd's) = $13,137/cd" - the "x 365" is a misprint for
    # "÷ 365" (the paper's own formula divides by the original contract period, and only
    # division gives $13,137). "$13,137 x 235 cd's = $3,087,195".
    hudson = steps("hudson", _inputs(tender_percentage="7.0", contract_sum="68500000", contract_period_days="365"), PMI_DELAY_DAYS, PMI)
    assert (hudson["daily_rate"], hudson["amount"]) == (Decimal("13137"), Decimal("3087195"))
    # Emden: total overhead and profit $37,156,795 on total revenue $381,095,333 (actual period);
    # "($37,156,795/$381,095,333 ÷ 100) x ($68,500,000 ÷ 365 cd's) = $18,298/cd"; "$4,300,030".
    emden = steps(
        "emden",
        _inputs(overhead_and_profit="37156795", turnover="381095333", contract_sum="68500000", contract_period_days="365"),
        PMI_DELAY_DAYS, PMI,
    )
    assert (emden["daily_rate"], emden["amount"]) == (Decimal("18298"), Decimal("4300030"))


def test_steps_without_rounding_agree_with_the_product_figure() -> None:
    for formula, inputs in (
        ("hudson", _inputs(tender_percentage="5", contract_sum="10000000", contract_period_days="365")),
        ("emden", _inputs(overhead_and_profit="120000000", turnover="1500000000", contract_sum="500000000", contract_period_days="730")),
        ("eichleay", _inputs(contract_billings="300000000", total_billings="2000000000", total_overhead="160000000", performance_days="500")),
    ):
        assert to_paise(steps(formula, inputs, 61)["amount"]) == compute_paise(formula, inputs, 61), formula
