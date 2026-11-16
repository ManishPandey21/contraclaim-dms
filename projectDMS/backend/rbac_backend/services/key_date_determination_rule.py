"""Which EOT determination is operative, and which governs each milestone (plan D-15, 4B-21).

One rule, used by the key-date register (``KeyDateRevisionService``) to replay
determinations into in-force dates, and by the Claim Bench's Delay Lab to decide
whether a linked determination's granted days count as already granted.

Before 4B-21 the Delay Lab had its own rule: it refused status ``superseded``,
which in the register marks the *superseding, operative* determination; it
counted drafts (not frozen), ``pending`` and ``under_review``; and it summed
every linked determination. D1 granting 10 days superseded by D2 granting 15
read as 10 in the Lab and 15 in the register.

The rule (unchanged from the register's ``_replay_determinations``):

- a determination is operative when it is frozen, its status is final, and no
  frozen determination lists it in ``supersedes_determination_ids``;
- operative determinations apply in contractual order: ``determination_date``,
  then ``frozen_at``, then id;
- for each milestone, the last operative determination whose item for it has an
  effective result (granted or partially granted) and a granted date governs it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set

from ..models.key_date import EOTDeterminationResult, EOTDeterminationStatus
from .key_date_service import _as_dt

FINAL_DETERMINATION_STATUSES = {
    EOTDeterminationStatus.GRANTED.value,
    EOTDeterminationStatus.PARTIALLY_GRANTED.value,
    EOTDeterminationStatus.REJECTED.value,
    EOTDeterminationStatus.NO_EXTENSION.value,
    EOTDeterminationStatus.SUPERSEDED.value,
}
EFFECTIVE_RESULTS = {
    EOTDeterminationResult.GRANTED.value,
    EOTDeterminationResult.PARTIALLY_GRANTED.value,
}


def _value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def superseded_ids(determinations: Sequence[Dict[str, Any]]) -> Set[str]:
    """Ids listed as superseded by a frozen determination."""
    return {
        str(value)
        for row in determinations
        if row.get("frozen_at")
        for value in row.get("supersedes_determination_ids") or []
    }


def superseded_by(determinations: Sequence[Dict[str, Any]], determination_id: str) -> List[str]:
    """The frozen determinations that supersede ``determination_id``, by id."""
    return sorted(
        str(row.get("_id"))
        for row in determinations
        if row.get("frozen_at") and determination_id in {str(item) for item in row.get("supersedes_determination_ids") or []}
    )


def contractual_order_key(row: Dict[str, Any]) -> tuple:
    return (
        _as_dt(row.get("determination_date")) or _as_dt(row.get("frozen_at")) or datetime.min,
        _as_dt(row.get("frozen_at")) or datetime.min,
        str(row.get("_id")),
    )


def operative_determinations(determinations: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Frozen, final, non-superseded determinations, in contractual order."""
    gone = superseded_ids(determinations)
    applicable = [
        row
        for row in determinations
        if row.get("frozen_at")
        and _value(row.get("status")) in FINAL_DETERMINATION_STATUSES
        and str(row.get("_id")) not in gone
    ]
    applicable.sort(key=contractual_order_key)
    return applicable


def is_effective_item(item: Dict[str, Any]) -> bool:
    return _value(item.get("determination_result")) in EFFECTIVE_RESULTS and _as_dt(item.get("eot_granted_date")) is not None


def governing_by_milestone(determinations: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """For each milestone (``key_date_id``), the determination that governs its in-force date."""
    governing: Dict[str, Dict[str, Any]] = {}
    for determination in operative_determinations(determinations):
        for item in determination.get("items") or []:
            if is_effective_item(item):
                governing[str(item.get("key_date_id"))] = determination
    return governing


def governing_determination_id(determinations: Sequence[Dict[str, Any]], key_date_id: Optional[str]) -> Optional[str]:
    if not key_date_id:
        return None
    row = governing_by_milestone(determinations).get(str(key_date_id))
    return str(row.get("_id")) if row else None


__all__ = [
    "EFFECTIVE_RESULTS",
    "FINAL_DETERMINATION_STATUSES",
    "contractual_order_key",
    "governing_by_milestone",
    "governing_determination_id",
    "is_effective_item",
    "operative_determinations",
    "superseded_by",
    "superseded_ids",
]
