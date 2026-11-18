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

import hashlib
import json
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


#: JG-5(f): rows the ledger builders add to every pleading's ledger as CONTEXT, not
#: as evidence the drafter selected. Owner decision (conditional approval,
#: 2026-11-18): an uncited context row must not block approval merely because its
#: status is a workflow status rather than an evidence verification - under these
#: controls, all enforced here so the validator, the workflow's source-drift
#: branch and the filing bundle apply one rule:
#:
#: 1. only rows the SERVER classified: ``ledger_context`` is written by the case
#:    analysis and register builders alone (``context.py``; a selected reference's
#:    projection clears it), and must agree with the row's type and origin;
#: 2. a context row the pleading cites by key, or names by its reference number,
#:    is relied upon and is checked as a citation (unverified: it blocks);
#: 3. a negative status (stale, revoked, withdrawn, authority_denied), a blocking
#:    quality flag, revision or content drift, or a scope other than this case's
#:    or project's is never exempt;
#: 4. approved analysis (issues, jurisdiction checks) stays user-supplied in the
#:    ledger: never evidence, never a source of fact;
#: 5. context rows never support an amount, date, clause or name in the text
#:    (``ArbitrationDraftValidator``), and never enter the filed source schedule
#:    (``case_workspace._filing_bundle_drafts``) or the generated annexure list.
#:
#: The policy is versioned and hashed; every version records the policy it was
#: validated under and the rows it exempted. Not final legal sign-off: the
#: authorised legal reviewer approves this filing-validation policy before release.
LEDGER_CONTEXT_CLASSES: Dict[str, Dict[str, Any]] = {
    "case_analysis": {
        "source_types": frozenset({"issue_matrix", "jurisdiction_check"}),
        "source_origins": frozenset({"case_issue_matrix", "case_jurisdiction_matrix"}),
        "scope_key": "case_id",
    },
    "project_register": {
        "source_types": frozenset({"claim", "variation", "payment_event", "bank_guarantee"}),
        "source_origins": frozenset(
            {"claim_register", "variation_register", "ipc_register", "bank_guarantee_register"}
        ),
        "scope_key": "project_id",
    },
}
CASE_ANALYSIS_SOURCE_TYPES = LEDGER_CONTEXT_CLASSES["case_analysis"]["source_types"]
REGISTER_SOURCE_ORIGINS = LEDGER_CONTEXT_CLASSES["project_register"]["source_origins"]
#: Never exempt, whatever the class.
CONTEXT_NEGATIVE_STATUSES = frozenset({"stale", "authority_denied", "revoked", "withdrawn"})
CONTEXT_NON_EXEMPT_FLAGS = frozenset({"authority_denied", "not_verified_for_filing", "unverified_ai_suggestion"})
_REFERENCE_LIKE = re.compile(r"\d")

CONTEXT_EXEMPTION_POLICY: Dict[str, Any] = {
    "name": "pleading-ledger-context-exemption",
    "version": 1,
    "classes": {
        name: {
            "source_types": sorted(spec["source_types"]),
            "source_origins": sorted(spec["source_origins"]),
            "scope_key": spec["scope_key"],
        }
        for name, spec in sorted(LEDGER_CONTEXT_CLASSES.items())
    },
    "never_exempt": {
        "statuses": sorted(CONTEXT_NEGATIVE_STATUSES),
        "quality_flags": sorted(CONTEXT_NON_EXEMPT_FLAGS),
        "drift": ["revision", "content"],
        "scope": "another case or project",
        "relied_upon": "source key cited, or reference number named in the text",
        "user_supplied": "except approved case analysis",
    },
    "case_analysis_requires_status": sorted(CITATION_VERIFIED_STATUSES),
    "context_supports_text": False,
    "context_in_filed_source_schedule": False,
}
CONTEXT_EXEMPTION_POLICY_HASH = hashlib.sha256(
    json.dumps(CONTEXT_EXEMPTION_POLICY, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def context_policy_stamp() -> Dict[str, Any]:
    return {
        "name": CONTEXT_EXEMPTION_POLICY["name"],
        "version": CONTEXT_EXEMPTION_POLICY["version"],
        "hash": CONTEXT_EXEMPTION_POLICY_HASH,
    }


def ledger_context_class(row: Dict[str, Any]) -> Optional[str]:
    """The server's classification of a ledger row as context, or None.

    Requires the server-written ``ledger_context`` AND agreement with the row's
    type and origin, so neither a reference's claimed type nor its origin alone
    can make a row context."""
    name = str(row.get("ledger_context") or "")
    spec = LEDGER_CONTEXT_CLASSES.get(name)
    if not spec:
        return None
    if str(row.get("source_type") or "") not in spec["source_types"]:
        return None
    if str(row.get("source_origin") or "") not in spec["source_origins"]:
        return None
    return name


def is_case_analysis(row: Dict[str, Any]) -> bool:
    """Approved, server-classified analysis (issue or jurisdiction check): context, not evidence."""
    return ledger_context_class(row) == "case_analysis" and is_verified_status(row.get("verification_status"))


def _named_in_text(row: Dict[str, Any], text_lower: str) -> bool:
    citation = str(row.get("citation") or "").strip()
    return len(citation) >= 3 and bool(_REFERENCE_LIKE.search(citation)) and citation.lower() in text_lower


def is_exempt_context_row(row: Dict[str, Any], markdown: str, scope: Optional[Dict[str, Any]]) -> bool:
    """Whether an uncited, untouched, in-scope context row may sit in the ledger without blocking."""
    name = ledger_context_class(row)
    if not name:
        return False
    status_value = str(row.get("verification_status") or "").strip().lower()
    if status_value in CONTEXT_NEGATIVE_STATUSES or row.get("authority_denied"):
        return False
    flags = {str(flag).lower() for flag in row.get("quality_flags") or []}
    if flags & CONTEXT_NON_EXEMPT_FLAGS:
        return False
    if name == "case_analysis":
        if not is_verified_status(status_value):
            return False
    elif row.get("is_user_supplied") or "user_supplied" in flags:
        return False
    for current, recorded in (("current_revision_id", "source_revision_id"), ("current_sha256", "sha256")):
        if row.get(current) and row.get(recorded) and str(row.get(current)) != str(row.get(recorded)):
            return False
    scope_key = LEDGER_CONTEXT_CLASSES[name]["scope_key"]
    expected = str((scope or {}).get(scope_key) or "")
    actual = str((row.get("context_scope") or {}).get(scope_key) or "")
    if not expected or actual != expected:
        return False
    if str(row.get("source_key") or "") in set(source_refs(markdown or "")):
        return False
    if _named_in_text(row, (markdown or "").lower()):
        return False
    return True


def exempt_context_keys(ledger: Any, markdown: str, scope: Optional[Dict[str, Any]]) -> List[str]:
    return sorted(
        str(row.get("source_key"))
        for row in ledger or []
        if row.get("source_key") and is_exempt_context_row(row, markdown, scope)
    )


def _row_exhibit_id(row: Dict[str, Any]) -> Optional[str]:
    raw = row.get("exhibit_id") or (row.get("metadata") or {}).get("exhibit_id")
    match = _EXHIBIT_ID_RE.match(str(raw or ""))
    return normalise_exhibit(*match.groups()) if match else None


def _state_of(row: Optional[Dict[str, Any]], missing: str) -> tuple:
    if row is None:
        return "unresolved", missing
    if ledger_context_class(row) == "case_analysis":
        # Control 4: counsel's analysis is never a source of fact.
        return "unverified", "Case analysis (issues, jurisdiction checks) is not evidence and cannot be cited as a source."
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
