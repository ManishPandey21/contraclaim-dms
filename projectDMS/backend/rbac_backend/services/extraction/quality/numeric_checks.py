"""Numeric integrity: detect corruption, repair only on dual confirmation.

Companion 3.1 measured nine split-digit corruptions in a PDF's *own* text
layer - "1 ,900,000" where the value is 1,900,000. Parsed naively that reads as
1 and a claim subtotal lands 2.5 crore light. Tuning pdfplumber's x_tolerance
does not help: 17 such tokens survive identically at every setting from 1.0 to
3.0, because the glyph spacing is in the document. The repair has to be
structural.

The repair rule is deliberately conservative: accept only when two genuinely
*independent* structural checks agree on the same value. Two checks that lean
on a shared operand are one check wearing two hats, and a value the document
renders differently is not something to overwrite on thin evidence.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Sequence

from .column_roles import ColumnRole, is_checkable
from .models import CheckResult, NumericRepair, Verdict

#: Applies only to a displayed quantity against the quantity implied by
#: amount / rate. Companion 3.3: a displayed 58 against a true 57.5 is 0.86%
#: and must pass rather than escalate.
QUANTITY_DISPLAY_REL_TOLERANCE = Decimal("0.01")

#: Applies only to monetary arithmetic, at the source's displayed precision.
#: There is deliberately no blanket monetary *relative* tolerance: a 1% error
#: on a crore is not a rounding artefact.
MONETARY_ABS_TOLERANCE = Decimal("1")

_CANDIDATE_REF = "candidate.amount"
_NUMERIC = re.compile(r"^-?[\d,]*\.?\d+$")
#: A digit, then whitespace, then the rest of a grouped number. The tail must
#: begin with a digit or a comma for this to be a split rather than two values.
_SPLIT_DIGIT = re.compile(r"^(\d)\s+([,\d][\d,]*(?:\.\d+)?)$")


def is_unreadable_amount(raw: Optional[str]) -> bool:
    """True when a cell carries a number we cannot safely interpret.

    This is the line between "the check did not apply" and "the check applied
    and we could not read the value". Getting it wrong in either direction is
    expensive:

      * treating an unreadable amount as NOT_CHECKABLE let a corrupted monetary
        value ride through to page PASS whenever a clean sibling row existed
        (G18) - the gate reported success over a number it never read;
      * treating every unparseable cell as corruption would escalate a repeated
        header row ("Amount") or an empty provisional cell, which is precisely
        the false escalation the gate was built to avoid.

    The discriminator is whether the cell contains digits. A cell with digits
    that will not parse is a damaged number. A cell with no digits is
    structural noise - a header, a label, a blank.
    """
    if raw is None:
        return False
    text = str(raw).strip()
    if not text:
        return False
    if parse_amount(text) is not None:
        return False
    return any(character.isdigit() for character in text)


def parse_amount(raw: Optional[str]) -> Optional[Decimal]:
    """Parse a grouped numeric literal, or None when it is not a number."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or not _NUMERIC.match(text):
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def detect_split_digits(raw: Optional[str]) -> Optional[str]:
    """Return the repaired literal for a split-digit token, else None.

    Only fires on a leading single digit separated from a grouped remainder -
    the measured corruption shape. "52 184615" is two numbers in one cell and
    is deliberately left alone.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    match = _SPLIT_DIGIT.match(text)
    if not match:
        return None

    head, tail = match.group(1), match.group(2)
    # Direct concatenation: the corruption splits a leading digit off an
    # otherwise intact literal, so "5" + "77,188" is "577,188". Inserting a
    # separator would invent a different number.
    repaired = f"{head}{tail}"

    # The repaired literal must be a plausible grouped number.
    if parse_amount(repaired) is None:
        return None
    return repaired


def _column(values: Sequence[str], roles: Sequence[ColumnRole], role: ColumnRole):
    for value, column_role in zip(values, roles):
        if column_role is role:
            return value
    return None


def check_row(values: Sequence[str], roles: Sequence[ColumnRole]) -> CheckResult:
    """Verify amount == qty x rate (x nos) for one row."""
    name = "row_identity"
    if not is_checkable(roles):
        return CheckResult(
            name=name,
            verdict=Verdict.NOT_CHECKABLE,
            detail="column roles not established",
        )

    raw_amount = _column(values, roles, ColumnRole.AMOUNT)

    # An AMOUNT column was established and this row put a damaged number in it.
    # The check applies; we simply cannot read the value. That is a blocking
    # condition, not "nothing to verify" - see is_unreadable_amount.
    if is_unreadable_amount(raw_amount):
        return CheckResult(
            name=name,
            verdict=Verdict.FAIL,
            detail=(
                f"amount {str(raw_amount).strip()!r} could not be interpreted; "
                "the amount column is established, so this value is unverifiable "
                "rather than uncheckable"
            ),
            evidence_refs=[_CANDIDATE_REF],
        )

    amount = parse_amount(raw_amount)
    rate = parse_amount(_column(values, roles, ColumnRole.RATE))
    quantity = parse_amount(_column(values, roles, ColumnRole.QUANTITY))
    nos = parse_amount(_column(values, roles, ColumnRole.NOS))

    # The effective multiplier is every multiplier column the table declares.
    # Companion 3.3: page 5 row ii is 3 x 32.61 x 3,200 = 313,056, and reading
    # Nos as decorative produced a false mismatch.
    multiplier = quantity if quantity is not None else nos
    if quantity is not None and nos is not None:
        multiplier = nos * quantity

    if amount is None or rate is None or multiplier is None:
        return CheckResult(
            name=name,
            verdict=Verdict.NOT_CHECKABLE,
            detail="row operands missing or non-numeric",
        )

    expected = multiplier * rate
    evidence = [_CANDIDATE_REF, "row.rate", "row.multiplier"]

    if rate == 0:
        return CheckResult(
            name=name, verdict=Verdict.NOT_CHECKABLE, detail="rate is zero"
        )

    # Compare through the displayed quantity: the document rounds what it
    # prints, so an implied quantity within tolerance of the printed one is a
    # display artefact rather than an arithmetic error.
    implied_multiplier = amount / rate
    denominator = multiplier if multiplier != 0 else Decimal("1")
    drift = abs(implied_multiplier - multiplier) / abs(denominator)

    if drift <= QUANTITY_DISPLAY_REL_TOLERANCE:
        return CheckResult(
            name=name,
            verdict=Verdict.PASS,
            detail=f"{multiplier} x {rate} ~= {amount}",
            candidate_value=amount,
            evidence_refs=evidence,
        )

    return CheckResult(
        name=name,
        verdict=Verdict.FAIL,
        detail=f"expected {expected:,} from {multiplier} x {rate}, found {amount:,}",
        candidate_value=amount,
        evidence_refs=evidence,
    )


def check_subtotal(
    rows: Sequence[Sequence[str]],
    stated_total: Optional[str],
    roles: Sequence[ColumnRole],
    *,
    candidate_row_index: int,
) -> CheckResult:
    """Verify the amount column sums to the stated total.

    Requires at least two rows: a single row's "sum" is the row itself, which
    corroborates nothing the row check did not already assert.
    """
    name = "subtotal_identity"
    total = parse_amount(stated_total)

    if len(rows) < 2:
        return CheckResult(
            name=name,
            verdict=Verdict.NOT_CHECKABLE,
            detail="a single row is not an independent subtotal",
        )
    if total is None or ColumnRole.AMOUNT not in set(roles):
        return CheckResult(
            name=name,
            verdict=Verdict.NOT_CHECKABLE,
            detail="no stated total or no amount column",
        )

    raw_amounts = [_column(row, roles, ColumnRole.AMOUNT) for row in rows]

    # Without this the corruption disables its own corroborating check: a
    # damaged amount made the subtotal NOT_CHECKABLE, so the one check able to
    # contradict the row went silent exactly when it was needed.
    unreadable = [raw for raw in raw_amounts if is_unreadable_amount(raw)]
    if unreadable:
        return CheckResult(
            name=name,
            verdict=Verdict.FAIL,
            detail=(
                f"{len(unreadable)} row amount(s) could not be interpreted, "
                f"e.g. {str(unreadable[0]).strip()!r}; the subtotal cannot be "
                "verified over an unreadable operand"
            ),
            evidence_refs=[_CANDIDATE_REF, "printed.subtotal"],
        )

    amounts: List[Optional[Decimal]] = [parse_amount(raw) for raw in raw_amounts]
    if any(amount is None for amount in amounts):
        return CheckResult(
            name=name,
            verdict=Verdict.NOT_CHECKABLE,
            detail="one or more row amounts are non-numeric",
        )

    summed = sum(amount for amount in amounts if amount is not None)
    candidate = (
        amounts[candidate_row_index]
        if 0 <= candidate_row_index < len(amounts)
        else None
    )

    evidence = [_CANDIDATE_REF, "printed.subtotal"] + [
        f"peer.row[{index}].amount"
        for index in range(len(rows))
        if index != candidate_row_index
    ]

    if abs(summed - total) <= MONETARY_ABS_TOLERANCE:
        return CheckResult(
            name=name,
            verdict=Verdict.PASS,
            detail=f"sum {summed:,} matches stated {total:,}",
            candidate_value=candidate,
            evidence_refs=evidence,
        )

    return CheckResult(
        name=name,
        verdict=Verdict.FAIL,
        detail=f"sum {summed:,} does not match stated {total:,}",
        candidate_value=candidate,
        evidence_refs=evidence,
    )


def _independent(first: CheckResult, second: CheckResult) -> bool:
    """True when the two checks share nothing but the candidate itself."""
    left = set(first.evidence_refs) - {_CANDIDATE_REF}
    right = set(second.evidence_refs) - {_CANDIDATE_REF}
    return bool(left) and bool(right) and not (left & right)


def propose_repair(
    raw: str, *, row_check: CheckResult, subtotal_check: CheckResult
) -> Optional[NumericRepair]:
    """Repair a corrupted literal only on two independent agreeing checks.

    Returns None on anything less. A value the document renders differently is
    not overwritten on one check, on two checks that disagree, or on two checks
    that lean on the same operand.
    """
    repaired = detect_split_digits(raw)
    if repaired is None:
        return None

    if row_check.verdict is not Verdict.PASS or subtotal_check.verdict is not Verdict.PASS:
        return None

    if (
        row_check.candidate_value is None
        or subtotal_check.candidate_value is None
        or row_check.candidate_value != subtotal_check.candidate_value
    ):
        return None

    if parse_amount(repaired) != row_check.candidate_value:
        return None

    if not _independent(row_check, subtotal_check):
        return None

    return NumericRepair(
        before=raw,
        after=repaired,
        reason=(
            "split-digit corruption confirmed by row identity and an "
            "independent subtotal"
        ),
        method="dual_confirmation",
        confidence=0.99,
        confirming_checks=[row_check.name, subtotal_check.name],
    )
