"""Map table headers onto the roles a numeric check needs.

This is the prerequisite the companion evidence identified: a rule that assumed
"the last three numeric columns are qty x rate = amount" produced 12 false
mismatches on a correct document, because

* a separate `Nos` column multiplied the row (3 x 32.61 x 3,200 = 313,056),
* two tables declared their own formulas in the header (Area A=[h*l]), and
* an `S/N` serial was read as a quantity.

So roles are established first, and a layout whose roles cannot be established
is **not checkable** - never failed.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional, Sequence


class ColumnRole(str, Enum):
    SERIAL = "serial"
    DESCRIPTION = "description"
    NOS = "nos"
    QUANTITY = "quantity"
    UNIT = "unit"
    RATE = "rate"
    AMOUNT = "amount"
    UNKNOWN = "unknown"


# Ordered: the first pattern that matches wins, so the more specific
# discriminators (serial, nos) are tried before the general ones.
_PATTERNS: list[tuple[ColumnRole, re.Pattern[str]]] = [
    (
        ColumnRole.SERIAL,
        re.compile(
            r"^(s[/.\s-]*n[o.]*|sr[.\s-]*no[.]*|sl[.\s-]*(no[.]*)?|item\s*no[.]*|#)$"
        ),
    ),
    (ColumnRole.NOS, re.compile(r"^(nos?[.]*|number\s*of\s*\w*|count)$")),
    (ColumnRole.DESCRIPTION, re.compile(r"(description|particular|item|work|scope)")),
    (ColumnRole.QUANTITY, re.compile(r"^(qty|quantity|qnty)\b|\bquantity\b")),
    (ColumnRole.UNIT, re.compile(r"^(unit|uom|units)$|unit\s*of\s*measure")),
    (ColumnRole.RATE, re.compile(r"\brate\b|\bprice\b")),
    (ColumnRole.AMOUNT, re.compile(r"\bamount\b|\bvalue\b|\btotal\b")),
]


def _normalize(header: Optional[str]) -> str:
    if not header:
        return ""
    # Header cells wrap across lines and carry unit suffixes like "(INR)".
    text = re.sub(r"\s+", " ", str(header)).strip().lower()
    return text


def map_column_roles(headers: Sequence[Optional[str]]) -> list[ColumnRole]:
    """Return one role per header cell, UNKNOWN where nothing matched."""
    roles: list[ColumnRole] = []
    for header in headers:
        normalized = _normalize(header)
        if not normalized:
            roles.append(ColumnRole.UNKNOWN)
            continue

        matched = ColumnRole.UNKNOWN
        for role, pattern in _PATTERNS:
            if pattern.search(normalized):
                matched = role
                break
        roles.append(matched)
    return roles


def is_checkable(roles: Sequence[ColumnRole]) -> bool:
    """True only when a row-level qty x rate = amount check is meaningful.

    Requires RATE and AMOUNT plus at least one multiplier column. SERIAL
    deliberately does not satisfy the multiplier requirement - treating it as
    one is exactly the measured false positive.
    """
    present = set(roles)
    has_multiplier = bool(present & {ColumnRole.QUANTITY, ColumnRole.NOS})
    return (
        ColumnRole.RATE in present
        and ColumnRole.AMOUNT in present
        and has_multiplier
    )
