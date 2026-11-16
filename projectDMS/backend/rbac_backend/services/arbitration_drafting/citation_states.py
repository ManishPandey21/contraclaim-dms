"""Citation states and pleaded-head provenance for a pleading draft.

Two deterministic checks, shared by the draft validator (which turns them into
approval blockers) and by the screens (which show them), so a count on screen
is always the count the gate used.

**Citation states.** A draft cites sources as ``[S3: ...]``. Each cited key is
exactly one of:

- ``verified``: in the draft's source ledger and admissible as it stands;
- ``unverified``: in the ledger, but user-supplied or not yet verified;
- ``unresolved``: not in the ledger at all.

**Pleaded heads.** In a Statement of Claim every head must carry the
contractual provision relied on, the event, the causal link, quantified
relief, and the notice given; a head that pleads an amount must also carry the
records that support it. Each missing item is named.

Neither check reads anything but its arguments. Neither calls a model.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

SOURCE_CITATION_RE = re.compile(r"\[(S\d+):[^\]]+\]")
UNVERIFIED_SOURCE_STATUSES = {"draft", "needs_review", "pending", "selected", "unverified"}
STATES = ("verified", "unverified", "unresolved")

_HEAD_ELEMENTS = (
    ("clause_ids", "the contractual provision relied on"),
    ("facts", "the event"),
    ("causation", "the causal link from the event to the loss or delay"),
)


def is_unverified_source(row: Dict[str, Any]) -> bool:
    """A ledger row that is not admissible for filing as it stands."""
    return bool(row.get("is_user_supplied")) or (
        str(row.get("verification_status") or "").lower() in UNVERIFIED_SOURCE_STATUSES
    )


def _key_order(key: str) -> tuple:
    digits = key[1:]
    return (int(digits) if digits.isdigit() else 0, key)


def classify_citations(markdown: str, source_ledger: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Every source key cited in ``markdown``, with its state against ``source_ledger``."""
    ledger = {str(row.get("source_key")): row for row in source_ledger or [] if row.get("source_key")}
    uses: Dict[str, int] = {}
    for match in SOURCE_CITATION_RE.finditer(markdown or ""):
        uses[match.group(1)] = uses.get(match.group(1), 0) + 1
    items: List[Dict[str, Any]] = []
    for key in sorted(uses, key=_key_order):
        row = ledger.get(key)
        if row is None:
            state, reason = "unresolved", "Not in this draft's source ledger."
        elif row.get("is_user_supplied"):
            state, reason = "unverified", "User-supplied evidence that has not been verified."
        elif is_unverified_source(row):
            state, reason = "unverified", f"Source status is {str(row.get('verification_status')).lower()}."
        else:
            state, reason = "verified", None
        items.append(
            {
                "source_key": key,
                "state": state,
                "uses": uses[key],
                "label": (row or {}).get("label") or (row or {}).get("citation"),
                "source_origin": (row or {}).get("source_origin"),
                "reason": reason,
            }
        )
    counts = {state: sum(1 for item in items if item["state"] == state) for state in STATES}
    return {"counts": {**counts, "cited": len(items), "ledger": len(ledger)}, "items": items}


def citation_blockers(classification: Dict[str, Any]) -> List[str]:
    blockers: List[str] = []
    for state, wording in (
        ("unresolved", "that are not in the source ledger"),
        ("unverified", "that are not yet verified"),
    ):
        keys = [item["source_key"] for item in classification.get("items") or [] if item["state"] == state]
        if keys:
            blockers.append(f"Draft cites sources {wording}: {', '.join(keys)}.")
    return blockers


def _present(value: Any) -> bool:
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return bool(str(value or "").strip())


def pleaded_head_gaps(matrix_context: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What each pleaded claim head is missing. Empty when every head is complete."""
    gaps: List[Dict[str, Any]] = []
    for row in (matrix_context or {}).get("claims") or []:
        metadata = row.get("metadata") or {}
        missing = [label for field, label in _HEAD_ELEMENTS if not _present(metadata.get(field))]
        pleads_figure = _present(metadata.get("amount_or_days"))
        if not pleads_figure and not _present(metadata.get("relief")):
            missing.append("quantified relief")
        if not _present(metadata.get("notice_ids")):
            missing.append("a notice record")
        if pleads_figure and not _present(metadata.get("evidence_ids")):
            missing.append("records supporting the amount or days pleaded")
        if missing:
            gaps.append({"claim": str(row.get("citation") or row.get("source_id") or "claim"), "missing": missing})
    return gaps


def pleaded_head_blockers(matrix_context: Dict[str, Any]) -> List[str]:
    return [f"Claim {gap['claim']} does not carry: {'; '.join(gap['missing'])}." for gap in pleaded_head_gaps(matrix_context)]


__all__ = [
    "SOURCE_CITATION_RE",
    "UNVERIFIED_SOURCE_STATUSES",
    "citation_blockers",
    "classify_citations",
    "is_unverified_source",
    "pleaded_head_blockers",
    "pleaded_head_gaps",
]
