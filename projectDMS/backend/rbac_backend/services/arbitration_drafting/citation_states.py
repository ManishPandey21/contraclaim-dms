"""Citation states and pleaded-head provenance for a pleading draft.

Two deterministic checks, shared by the draft validator (which turns them into
approval blockers) and by the screens (which show them), so a count on screen
is always the count the gate used.

**Citation states.** A draft cites ledger sources as ``[S3]``, ``[S3: ...]`` or
``[SRC-4]``, and case exhibits as "Ex. C-12", "Exhibit R/3", "CW-1/4" or
"Annexure A-7" (one parser, ``citation_refs``; D-17). Each citation is exactly
one of:

- ``verified``: in the draft's source ledger, and its status is in the
  allow-list ``CITATION_VERIFIED_STATUSES``;
- ``unverified``: in the ledger, but user-supplied, or any other status,
  including a missing or unknown one;
- ``unresolved``: not in the ledger at all (for an exhibit: no ledger row
  carries that exhibit id).

**Pleaded heads.** In a Statement of Claim every head must carry the
contractual provision relied on, the event, the causal link, quantified
relief, and the notice given; a head that pleads an amount must also carry the
records that support it. Each missing item is named.

Neither check reads anything but its arguments. Neither calls a model.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .citation_refs import (
    CITATION_VERIFIED_STATUSES,
    SOURCE_REF_RE,
    exhibit_refs,
    is_verified_status,
    normalise_exhibit,
    source_refs,
)

#: Kept for importers; the parser is ``citation_refs.SOURCE_REF_RE``.
SOURCE_CITATION_RE = SOURCE_REF_RE
STATES = ("verified", "unverified", "unresolved")
_EXHIBIT_ID_RE = re.compile(r"^\s*([A-Za-z]{1,3})\s*[-/]\s*(\d{1,4})(?:\s*/\s*(\d{1,4}))?\s*$")

_HEAD_ELEMENTS = (
    ("clause_ids", "the contractual provision relied on"),
    ("facts", "the event"),
    ("causation", "the causal link from the event to the loss or delay"),
)


def is_unverified_source(row: Dict[str, Any]) -> bool:
    """A ledger row that is not admissible for filing as it stands: user-supplied, or any
    status outside the allow-list (a missing or unknown status included)."""
    return bool(row.get("is_user_supplied")) or not is_verified_status(row.get("verification_status"))


def _row_exhibit_id(row: Dict[str, Any]) -> Optional[str]:
    raw = row.get("exhibit_id") or (row.get("metadata") or {}).get("exhibit_id")
    match = _EXHIBIT_ID_RE.match(str(raw or ""))
    return normalise_exhibit(*match.groups()) if match else None


def _state_of(row: Optional[Dict[str, Any]], missing: str) -> tuple:
    if row is None:
        return "unresolved", missing
    if row.get("is_user_supplied"):
        return "unverified", "User-supplied evidence that has not been verified."
    if not is_verified_status(row.get("verification_status")):
        status = str(row.get("verification_status") or "").strip().lower() or "not recorded"
        return "unverified", f"Source status is {status}."
    return "verified", None


def _key_order(key: str) -> tuple:
    digits = key[1:]
    return (int(digits) if digits.isdigit() else 0, key)


def classify_citations(markdown: str, source_ledger: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Every source key cited in ``markdown``, with its state against ``source_ledger``."""
    ledger = {str(row.get("source_key")): row for row in source_ledger or [] if row.get("source_key")}
    exhibits: Dict[str, Dict[str, Any]] = {}
    for row in source_ledger or []:
        exhibit_id = _row_exhibit_id(row)
        if exhibit_id and exhibit_id not in exhibits:
            exhibits[exhibit_id] = row
    items: List[Dict[str, Any]] = []
    uses = source_refs(markdown)
    for key in sorted(uses, key=_key_order):
        row = ledger.get(key)
        state, reason = _state_of(row, "Not in this draft's source ledger.")
        items.append(
            {
                "kind": "source",
                "source_key": key,
                "state": state,
                "uses": uses[key],
                "label": (row or {}).get("label") or (row or {}).get("citation"),
                "source_origin": (row or {}).get("source_origin"),
                "reason": reason,
            }
        )
    for exhibit_id, count in sorted(exhibit_refs(markdown).items()):
        row = exhibits.get(exhibit_id)
        state, reason = _state_of(row, f"Exhibit {exhibit_id} is not in this case's exhibit list for this draft.")
        items.append(
            {
                "kind": "exhibit",
                # The name the blockers and screens show: "Ex. C-12", never a database id.
                "source_key": f"Ex. {exhibit_id}",
                "exhibit_id": exhibit_id,
                "ledger_key": (row or {}).get("source_key"),
                "state": state,
                "uses": count,
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


NO_APPROVED_HEADS = (
    "A Statement of Claim needs at least one approved claim head in this case's claim matrix, "
    "and none is approved."
)


def awaiting_heads_blocker(count: int) -> str:
    return (
        f"{count} claim head{'' if count == 1 else 's'} in this case "
        f"{'is' if count == 1 else 'are'} still awaiting approval. Approve or remove "
        f"{'it' if count == 1 else 'them'} before this Statement of Claim can be approved."
    )


def claim_head_set_blockers(matrix_context: Dict[str, Any], counts: Optional[Dict[str, int]]) -> List[str]:
    """A Statement of Claim pleads the case's approved heads: there must be some, and none left in review.

    ``counts`` comes from the context builder ({"approved": n, "awaiting_approval": m}).
    When it is absent (an older caller), the approved heads are read from the
    matrix context and nothing is known to be awaiting approval.
    """
    approved = len((matrix_context or {}).get("claims") or [])
    awaiting = 0
    if counts:
        approved = int(counts.get("approved", approved) or 0)
        awaiting = int(counts.get("awaiting_approval", 0) or 0)
    blockers: List[str] = []
    if approved == 0:
        blockers.append(NO_APPROVED_HEADS)
    if awaiting:
        blockers.append(awaiting_heads_blocker(awaiting))
    return blockers


def is_pleaded_head_blocker(message: str) -> bool:
    """Whether a blocker comes from the pleaded-head checks above.

    The legal-review gate classifies blockers by keyword; these messages name
    provisions, causal links and approval, not its keywords, so it asks here
    instead of guessing.
    """
    text = str(message or "")
    return (
        (text.startswith("Claim ") and " does not carry: " in text)
        or text == NO_APPROVED_HEADS
        or ("claim head" in text and "still awaiting approval" in text)
    )


__all__ = [
    "CITATION_VERIFIED_STATUSES",
    "SOURCE_CITATION_RE",
    "NO_APPROVED_HEADS",
    "citation_blockers",
    "claim_head_set_blockers",
    "classify_citations",
    "is_pleaded_head_blocker",
    "is_unverified_source",
    "pleaded_head_blockers",
    "pleaded_head_gaps",
]
