"""Head-office overhead formulas and money in paise (plan 4B-24, D-14, C-06).

Money is held as integer paise. A rupee figure typed by a person, or sent as a
JSON number, is read through its decimal text (never through binary floating
point) and rounded half-up to the paisa.

The three formulas are computed here, from typed inputs, each input carrying
the record it is taken from. A formula's amount is never typed. The delay
period is the line's own period, counted in days inclusive of both ends, so it
is the same period the delay assessment decided as compensable.

As published (Hudson: Hudson's Building and Engineering Contracts, as stated
at https://en.wikipedia.org/wiki/Hudson_Formula; Emden and Eichleay: as stated
at https://www.designingbuildings.co.uk/wiki/How_to_calculate_head_office_overheads_and_profit,
which cites the SCL Delay and Disruption Protocol):

- **Hudson**: (head-office overhead and profit percentage in the tender / 100)
  x (contract sum / contract period) x period of delay.
- **Emden**: the same, with the contractor's *actual* percentage, taken from its
  accounts: head-office overhead and profit / turnover x 100.
- **Eichleay**: (final contract sum, excluding the overhead claim / total
  revenue over the contract period) x total head-office overhead incurred during
  actual performance = overhead allocable to the contract; divided by the days of
  actual performance = a daily rate; multiplied by the days of compensable delay.

Contract period and actual performance are in days, so the period ratio is
exact whichever unit a source uses for both.

**Eichleay is billings-based** (owner review 2026-11-17): the contract's share is
its billings during the period of actual performance over the company's total
billings for the *same* period, applied to the head-office overhead of that same
period. Every Eichleay figure is labelled with that period, and the figures are
checked for consistency (the contract's billings cannot exceed the company's;
the compensable delay cannot be longer than the performance period). Profit is
not part of Eichleay's overhead pool; Hudson and Emden carry "overhead and
profit" as their published statements do.

**A calculation, not a determination.** What this module returns is arithmetic
from the figures typed. It does not decide that head-office overhead was in fact
unabsorbed, that the delay is compensable under the contract, that the
formula's preconditions are met (for Eichleay, typically standby and inability
to take other work), or that the figures are evidenced. The quantum view marks
every formula line as calculated, and two formula lines over the same days are
locked as double recovery. Worked examples: hand-worked fixtures in
tests/test_quantum_formulas.py; reproducing a published worked example is an
open item in the ticket register (the PMI paper could not be fetched here).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Tuple

PAISA = Decimal("0.01")


def to_paise(value: Any) -> int:
    """Rupees (a number or its decimal text) as integer paise, rounded half-up."""
    if isinstance(value, bool):
        raise ValueError("not an amount")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("not an amount") from exc
    if not amount.is_finite():
        raise ValueError("not an amount")
    return int((amount.quantize(PAISA, rounding=ROUND_HALF_UP) * 100).to_integral_value())


def rupees(paise: int) -> float:
    """The derived rupee figure for display and older readers. Never summed."""
    return float(Decimal(paise) / 100)


@dataclass(frozen=True)
class FormulaInputSpec:
    key: str
    label: str
    kind: str  # "money" (rupees), "percent" or "days"


FORMULA_INPUTS: Dict[str, Tuple[FormulaInputSpec, ...]] = {
    "hudson": (
        FormulaInputSpec("tender_percentage", "Head-office overhead and profit in the tender (%)", "percent"),
        FormulaInputSpec("contract_sum", "Contract sum", "money"),
        FormulaInputSpec("contract_period_days", "Contract period (days)", "days"),
    ),
    "emden": (
        FormulaInputSpec("overhead_and_profit", "Head-office overhead and profit in the accounts", "money"),
        FormulaInputSpec("turnover", "Turnover in the same accounts", "money"),
        FormulaInputSpec("contract_sum", "Contract sum", "money"),
        FormulaInputSpec("contract_period_days", "Contract period (days)", "days"),
    ),
    "eichleay": (
        FormulaInputSpec(
            "contract_billings", "Billings on this contract during actual performance, excluding this overhead claim", "money"
        ),
        FormulaInputSpec("total_billings", "Total company billings for the same period", "money"),
        FormulaInputSpec("total_overhead", "Head-office overhead for the same period, excluding profit", "money"),
        FormulaInputSpec("performance_days", "Days of actual performance (the same period)", "days"),
    ),
}


def missing_inputs(formula: str, inputs: Mapping[str, Any]) -> List[str]:
    """Labels of the inputs a formula needs that are absent or not a positive figure."""
    missing: List[str] = []
    for spec in FORMULA_INPUTS.get(formula, ()):
        entry = inputs.get(spec.key)
        value = getattr(entry, "value", None) if entry is not None and not isinstance(entry, Mapping) else (
            entry.get("value") if isinstance(entry, Mapping) else None
        )
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError):
            number = None
        if number is None or not number.is_finite() or number <= 0:
            missing.append(spec.label)
        elif spec.kind == "percent" and number > 100:
            missing.append(spec.label)
    return missing


def inconsistencies(formula: str, inputs: Mapping[str, Any], delay_days: Optional[int]) -> List[str]:
    """Figures that are each valid but cannot all be true together. Call after ``missing_inputs``."""
    found: List[str] = []
    if formula == "emden" and _value(inputs, "overhead_and_profit") >= _value(inputs, "turnover"):
        found.append("Head-office overhead and profit cannot be as large as the turnover in the same accounts.")
    if formula == "eichleay":
        if _value(inputs, "contract_billings") > _value(inputs, "total_billings"):
            found.append("This contract's billings cannot exceed the company's total billings for the same period.")
        if delay_days is not None and Decimal(delay_days) > _value(inputs, "performance_days"):
            found.append("The period of delay cannot be longer than the period of actual performance it falls within.")
    return found


def _value(inputs: Mapping[str, Any], key: str) -> Decimal:
    entry = inputs[key]
    value = entry.get("value") if isinstance(entry, Mapping) else getattr(entry, "value")
    return Decimal(str(value))


def compute_paise(formula: str, inputs: Mapping[str, Any], delay_days: int) -> int:
    """The formula's amount in paise. ``inputs`` must be complete (see ``missing_inputs``)."""
    delay = Decimal(delay_days)
    if formula == "hudson":
        amount = (_value(inputs, "tender_percentage") / 100) * _value(inputs, "contract_sum") / _value(
            inputs, "contract_period_days"
        ) * delay
    elif formula == "emden":
        percentage = _value(inputs, "overhead_and_profit") / _value(inputs, "turnover")
        amount = percentage * _value(inputs, "contract_sum") / _value(inputs, "contract_period_days") * delay
    elif formula == "eichleay":
        allocable = _value(inputs, "contract_billings") / _value(inputs, "total_billings") * _value(inputs, "total_overhead")
        amount = allocable / _value(inputs, "performance_days") * delay
    else:
        raise ValueError(f"unknown formula: {formula}")
    return to_paise(amount)


def delay_days(start: Optional[Any], end: Optional[Any]) -> Optional[int]:
    """Days in a period, both ends included; None if open or reversed."""
    if start is None or end is None:
        return None
    first = start.date() if hasattr(start, "date") else start
    last = end.date() if hasattr(end, "date") else end
    if last < first:
        return None
    return (last - first).days + 1
