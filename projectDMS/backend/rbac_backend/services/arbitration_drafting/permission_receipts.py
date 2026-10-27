"""A rejoinder permission counts only when a committed receipt backs it.

`record_rejoinder_permission` writes the grant to the paragraph response and the
matrix row before it commits the receipt, so a process that dies in between
leaves `permission_obtained=True` behind a `pending` receipt. Every reader
therefore asks `rejoinder_permission_states` instead of trusting the flag: a
permission is verified only when its receipt is committed, approved, and bound to
the same case, row, draft, permission source, paragraph response revision and
row content. Anything else - no receipt, a pending or aborted one, a mismatch, or
a grant recorded before receipts carried that binding - fails closed.

`reconcile_stale_permission_receipts` aborts `pending` receipts older than
`STALE_PENDING_RECEIPT_AGE` and then withdraws the grants that cite them. It
never commits a receipt, so an interrupted decision is never promoted.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .approval_policy import REVOKED_REJOINDER_PERMISSION
from .repository import _collect, _jsonable

PERMISSION_RECEIPT_GATE = "rejoinder_permission"

#: Receipts written with the content binding below carry this version. Older
#: receipts cannot be checked against the content they covered.
PERMISSION_RECEIPT_VERSION = 2

#: A decision normally commits within one request. A receipt still `pending`
#: after this long belongs to a decision that will never finish.
STALE_PENDING_RECEIPT_AGE = timedelta(minutes=15)

VERIFIED = "verified"
NOT_GRANTED = "not_granted"
LEGACY_UNBOUND = "legacy_unbound"
RECEIPT_MISSING = "receipt_missing"
RECEIPT_NOT_COMMITTED = "receipt_not_committed"
RECEIPT_MISMATCH = "receipt_mismatch"

STATE_MESSAGES = {
    LEGACY_UNBOUND: "its permission was recorded before receipts were bound to content; record the permission again",
    RECEIPT_MISSING: "its permission receipt is missing",
    RECEIPT_NOT_COMMITTED: "its permission decision did not complete",
    RECEIPT_MISMATCH: "its permission receipt does not cover the current content",
}

# Fields that change without the reviewed content changing: identity, review
# and approval state, the permission itself, and write bookkeeping. Everything
# else on the row is the content a permission covers.
_UNBOUND_FIELDS = frozenset(
    {
        "_id",
        "status",
        "edit_revision",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "approved",
        "approved_at",
        "approved_by",
        "approval_log",
        "approval_status",
        "assigned_reviewer_id",
        "human_approval_status",
        "last_material_editor_id",
        "last_review_action",
        "last_reviewed_at",
        "last_reviewed_by",
        "material_contributor_ids",
        "readiness_status",
        "rejected_at",
        "rejected_by",
        "review_assignments",
        "review_comments",
        "review_completed_roles",
        "review_required_roles",
        "review_status",
        "verified",
        "verification_status",
        *REVOKED_REJOINDER_PERMISSION,
        "permission_notes",
        "permission_source_id",
    }
)


def permission_content_hash(row: Mapping[str, Any]) -> str:
    """Hash of the rejoinder row content a permission decision covers."""
    content = {key: value for key, value in row.items() if key not in _UNBOUND_FIELDS}
    return hashlib.sha256(
        json.dumps(_jsonable(content), sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def permission_receipt_binding(row: Mapping[str, Any]) -> Dict[str, Any]:
    """What a new receipt records about the row it is decided on."""
    return {
        "receipt_version": PERMISSION_RECEIPT_VERSION,
        "case_id": row.get("case_id"),
        "matrix_row_id": row.get("_id"),
        "draft_id": row.get("draft_id"),
        "source_paragraph_response_id": row.get("source_paragraph_response_id"),
        "source_response_revision": row.get("source_response_revision"),
        "matrix_row_content_hash": permission_content_hash(row),
        "matrix_row_revision": int(row.get("edit_revision") or 0) + 1,
    }


def _truthy(value: Any) -> bool:
    """The same reading of a flag as `case_workspace._is_positive`."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "yes", "true", "approved", "ready", "verified"}


def permission_state(row: Mapping[str, Any], receipt: Optional[Mapping[str, Any]]) -> str:
    """Whether this row's recorded permission is backed by a valid receipt."""
    if not _truthy(row.get("permission_obtained")):
        return NOT_GRANTED
    receipt_id = row.get("permission_approval_receipt_id")
    if not receipt_id:
        return LEGACY_UNBOUND
    if not receipt or str(receipt.get("_id")) != str(receipt_id):
        return RECEIPT_MISSING
    if receipt.get("receipt_status") != "committed" or receipt.get("invalidated_at"):
        return RECEIPT_NOT_COMMITTED
    if receipt.get("receipt_version") != PERMISSION_RECEIPT_VERSION:
        return LEGACY_UNBOUND
    expected = permission_receipt_binding(row)
    expected.pop("matrix_row_revision")  # a later review advances it; content does not change
    bound = (
        receipt.get("gate") == PERMISSION_RECEIPT_GATE
        and receipt.get("decision") == "approved"
        and str(receipt.get("permission_source_id") or "") == str(row.get("permission_source_id") or "")
        and all(_same(receipt.get(key), value) for key, value in expected.items())
    )
    return VERIFIED if bound else RECEIPT_MISMATCH


def _same(left: Any, right: Any) -> bool:
    return (left is None and right is None) or (left is not None and right is not None and str(left) == str(right))


async def rejoinder_permission_states(db: Any, rows: Iterable[Mapping[str, Any]]) -> Dict[str, str]:
    """`permission_state` for every row, keyed by row id, in one receipt query."""
    rows = list(rows)
    receipt_ids = sorted({str(row["permission_approval_receipt_id"]) for row in rows if row.get("permission_approval_receipt_id")})
    receipts: Dict[str, Mapping[str, Any]] = {}
    if receipt_ids:
        cursor = db.arbitration_workflow_approvals.find({"_id": {"$in": receipt_ids}, "gate": PERMISSION_RECEIPT_GATE})
        receipts = {str(receipt.get("_id")): receipt for receipt in await _collect(cursor)}
    return {
        str(row.get("_id")): permission_state(row, receipts.get(str(row.get("permission_approval_receipt_id") or "")))
        for row in rows
    }


def needs_permission(row: Mapping[str, Any]) -> bool:
    return _truthy(row.get("new_matter")) and _truthy(row.get("permission_required"))


async def unverified_new_matter(db: Any, rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Rejoinder rows that need permission and have none a receipt backs."""
    pending = [row for row in rows if needs_permission(row)]
    states = await rejoinder_permission_states(db, pending)
    return [
        {"row": row, "state": states[str(row.get("_id"))]}
        for row in pending
        if states[str(row.get("_id"))] != VERIFIED
    ]


async def reconcile_stale_permission_receipts(
    db: Any,
    *,
    case_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> List[str]:
    """Abort stale `pending` receipts, then withdraw every grant that cites them.

    The abort is a compare-and-set on `pending`, so a decision still in flight
    either commits first (and its receipt is left alone) or finds its commit
    refused and withdraws its own grant. Returns the aborted receipt ids.
    """
    cutoff = (now or datetime.utcnow()) - STALE_PENDING_RECEIPT_AGE
    query: Dict[str, Any] = {"gate": PERMISSION_RECEIPT_GATE, "receipt_status": "pending", "approved_at": {"$lte": cutoff}}
    if case_id:
        query["case_id"] = case_id
    aborted: List[str] = []
    for receipt in await _collect(db.arbitration_workflow_approvals.find(query)):
        result = await db.arbitration_workflow_approvals.update_one(
            {"_id": receipt["_id"], "receipt_status": "pending"},
            {"$set": {"receipt_status": "aborted", "aborted_at": datetime.utcnow(), "abort_reason": "stale_pending_receipt"}},
        )
        if not getattr(result, "modified_count", 0):
            continue
        aborted.append(str(receipt["_id"]))
        revoked = {"$set": {**REVOKED_REJOINDER_PERMISSION, "updated_at": datetime.utcnow()}}
        await db.arbitration_paragraph_responses.update_many({"permission_approval_receipt_id": receipt["_id"]}, revoked)
        await db.arbitration_rejoinder_matrix.update_many({"permission_approval_receipt_id": receipt["_id"]}, revoked)
    return aborted
