"""Confirming or changing a claim's type (owner decision 2026-11-18).

A claim started from three questions has a provisional type ("other"). The notice
period recorded for a type is not the contract's period for this claim until a
person has chosen the type (``services/claims_notice.assess_notice``).

Confirming records who chose it, when, and the notice assessment as it stood.
Changing it also withdraws the notice decisions people made on the claim's heads
under the old type, keeping each in the element's history, and marks the heads'
tests stale (reopening a locked board), so the notice is assessed and decided
again for the new type. Nothing earlier is deleted.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

#: Attempts at the versioned board write before giving up (another write won the race).
_BOARD_ATTEMPTS = 3


def _type_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "other")


async def record_claim_type(
    db: Any,
    claim: Dict[str, Any],
    new_type: Any,
    actor_id: Optional[str],
    *,
    via: str,
    reason: Optional[str] = None,
    notice_before: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Confirm ``new_type`` for ``claim`` (changing it if different). Returns the stored claim."""
    old_type = _type_value(claim.get("type"))
    new_value = _type_value(new_type)
    now = datetime.utcnow()
    entry = {
        "from": old_type,
        "to": new_value,
        "changed": new_value != old_type,
        "confirmed_by": actor_id,
        "confirmed_at": now,
        "via": via,
        "reason": (reason or "").strip() or None,
        "previously_confirmed": claim.get("type_confirmed") is not False,
        "notice_before": notice_before,
    }
    updated = await db.claims.find_one_and_update(
        {"_id": claim["_id"]},
        {
            "$set": {"type": new_value, "type_confirmed": True, "updated_at": now, "updated_by": actor_id},
            "$push": {"type_history": entry},
        },
        return_document=True,
    )
    if updated is not None and entry["changed"]:
        await _withdraw_notice_decisions(db, updated, old_type, new_value, actor_id, now)
        updated = await db.claims.find_one({"_id": claim["_id"]}) or updated
    return updated or claim


async def _withdraw_notice_decisions(
    db: Any, claim: Dict[str, Any], old_type: str, new_type: str, actor_id: Optional[str], now: datetime
) -> None:
    """Move each head's notice decision into its history; the head must be decided again."""
    for _ in range(_BOARD_ATTEMPTS):
        current = await db.claims.find_one({"_id": claim["_id"]})
        bench = (current or {}).get("bench")
        if not isinstance(bench, dict):
            return
        heads = list(bench.get("heads") or [])
        touched = False
        for head in heads:
            element = (head.get("elements") or {}).get("notice")
            if not isinstance(element, dict) or not element.get("state"):
                continue
            history = list(element.get("history") or [])
            history.append(
                {
                    "state": element.get("state"),
                    "note": element.get("note"),
                    "decided_by": element.get("decided_by"),
                    "decided_at": element.get("decided_at"),
                    "withdrawn_at": now,
                    "withdrawn_by": actor_id,
                    "reason": f"claim type changed from {old_type} to {new_type}",
                }
            )
            element.update({"state": None, "note": None, "decided_by": None, "decided_at": None, "history": history})
            touched = True
        if not touched:
            return
        version = bench.get("board_version")
        result = await db.claims.update_one(
            {"_id": claim["_id"], "bench.board_version": version},
            {
                "$set": {"bench.heads": heads, "bench.stage": "heads", "bench.locked_at": None, "bench.locked_by": None},
                "$inc": {"bench.board_version": 1},
            },
        )
        if getattr(result, "modified_count", 0):
            return
    raise RuntimeError("the claim's board changed while its notice decisions were being withdrawn")


__all__ = ["record_claim_type"]
