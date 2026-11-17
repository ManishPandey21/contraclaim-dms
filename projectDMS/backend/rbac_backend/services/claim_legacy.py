"""Reading a claim record that predates today's rules (plan D-10, 4B-19, C-23).

A legacy record may hold its amount as text, its days as a float, or a status
that is no longer in the list. These functions say what can be taken from such
a value without guessing. Nothing here writes: the board and the register use
them when they read.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from bson.decimal128 import Decimal128

from ..models.claim import ClaimStatus

# ASCII digits only: ``\d`` would also accept digits of other scripts.
_PLAIN_AMOUNT = re.compile(r"[0-9]{1,15}(\.[0-9]{1,2})?")
_PLAIN_DAYS = re.compile(r"[0-9]{1,6}")
_KNOWN_STATUSES = {item.value for item in ClaimStatus}


def _recorded(value: Any) -> str:
    return str(value).strip()[:80]


def coerce_legacy_amount(value: Any) -> Tuple[Optional[float], Optional[str]]:
    """``(amount, recorded)`` for a claim record's amount (plan D-10, 4B-19).

    A number is taken as it is. Text is taken only when it is digits with an
    optional decimal point. Anything else (separators, a currency, words, a
    negative or non-finite number) is not interpreted: "12,50,000" and
    "1.250,00" read differently by convention, and a guess would be a wrong
    claim figure. Then the amount is None and ``recorded`` is the text a person
    re-enters from.
    """
    if value is None:
        return None, None
    if isinstance(value, Decimal128):
        value = value.to_decimal()
    if isinstance(value, Decimal):
        value = float(value) if value.is_finite() else str(value)
    if isinstance(value, bool):
        return None, _recorded(value)
    if isinstance(value, (int, float)):
        # Amounts are kept to the paisa (4B-24). A finer figure would be rounded
        # on the first save, so it is re-entered rather than changed silently.
        if math.isfinite(value) and value >= 0 and round(value, 2) == value:
            return float(value), None
        return None, _recorded(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, None
        if _PLAIN_AMOUNT.fullmatch(text):
            return float(text), None
    return None, _recorded(value)


def coerce_legacy_days(value: Any) -> Tuple[Optional[int], Optional[str]]:
    """``(days, recorded)`` for a claim record's EOT days. Whole days only: a
    whole number stored as a float or as plain digits is taken; a part-day is
    not rounded."""
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, _recorded(value)
    if isinstance(value, int):
        return (value, None) if 0 <= value <= 999_999 else (None, _recorded(value))
    if isinstance(value, float):
        if (
            math.isfinite(value)
            and value >= 0
            and value.is_integer()
            and value <= 999_999
        ):
            return int(value), None
        return None, _recorded(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, None
        if _PLAIN_DAYS.fullmatch(text):
            return int(text), None
    return None, _recorded(value)


def unrecognised_status(claim: Dict[str, Any]) -> Optional[str]:
    """The claim's status when it is not one the application knows. A claim with
    no status recorded is a draft, as it always has been."""
    raw = claim.get("status")
    value = str(getattr(raw, "value", raw) or "").strip()
    return value if value and value not in _KNOWN_STATUSES else None
