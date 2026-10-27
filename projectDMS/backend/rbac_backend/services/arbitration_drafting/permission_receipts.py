"""A rejoinder permission counts only when a committed receipt backs it.

`record_rejoinder_permission` writes the grant to the paragraph response and the
matrix row before it commits the receipt, so a process that dies in between
leaves `permission_obtained=True` behind a `pending` receipt. Every reader
therefore asks `rejoinder_permission_states` instead of trusting the flag. A
permission is verified only when:

- its receipt is committed, approved, not invalidated, and bound to the same
  case, row, draft, permission source, paragraph response, response revision
  and row content;
- the authoritative paragraph response still carries that grant at that
  revision (a response rewritten or revoked before its projection was synced
  does not keep the row's permission);
- the permission source still exists in the case's scope, is still
  authoritative, and holds the bytes and version it held when decided.

Anything else fails closed. Validation is read-only: no reader writes.

Stored grants and receipts are never rewritten to make them count. A legacy
grant (no receipt, or a receipt without this binding) stays on record as
evidence and is refused until a qualified legal reviewer records the permission
again through `record_rejoinder_permission`.

`reconcile_stale_permission_receipts` is the explicit cleanup path. It aborts
`pending` receipts older than `STALE_PENDING_RECEIPT_AGE` with one conditional
write, then withdraws only the grants that still cite an aborted receipt. It
never commits a receipt and never touches a grant that cites another one. It
runs from the permission decision (a write path) and from the maintenance
script `scripts/reconcile_rejoinder_permission_receipts.py`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .approval_policy import REVOKED_REJOINDER_PERMISSION
from .repository import _collect, _jsonable

PERMISSION_RECEIPT_GATE = "rejoinder_permission"

#: Receipts written with the binding below carry this version. Older receipts
#: cannot be checked against the content they covered.
PERMISSION_RECEIPT_VERSION = 2

#: A decision normally commits within one request. A receipt still `pending`
#: after this long belongs to a decision that will never finish.
STALE_PENDING_RECEIPT_AGE = timedelta(minutes=15)

RECONCILER_ACTOR = "system:rejoinder-permission-reconciler"

VERIFIED = "verified"
NOT_GRANTED = "not_granted"
LEGACY_UNBOUND = "legacy_unbound"
RECEIPT_MISSING = "receipt_missing"
RECEIPT_NOT_COMMITTED = "receipt_not_committed"
RECEIPT_MISMATCH = "receipt_mismatch"
SOURCE_UNAVAILABLE = "source_unavailable"

_RECOVERY = "a qualified legal reviewer must record the permission again"
STATE_MESSAGES = {
    LEGACY_UNBOUND: f"its permission was recorded before receipts were bound to content; {_RECOVERY}",
    RECEIPT_MISSING: f"its permission receipt is missing; {_RECOVERY}",
    RECEIPT_NOT_COMMITTED: f"its permission decision did not complete; {_RECOVERY}",
    RECEIPT_MISMATCH: f"the content changed after the permission was decided; {_RECOVERY}",
    SOURCE_UNAVAILABLE: f"its permission source is missing, changed or no longer authoritative; {_RECOVERY}",
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


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(_jsonable(value), sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def permission_content_hash(row: Mapping[str, Any]) -> str:
    """Hash of the rejoinder row content a permission decision covers.

    Ids hash by their string form, so an ObjectId and its hex string agree.
    """
    return _hash({key: value for key, value in row.items() if key not in _UNBOUND_FIELDS})


def permission_source_content_hash(source: Mapping[str, Any]) -> str:
    """The permission source's identity, bytes and version, not its bookkeeping."""
    return _hash({key: source.get(key) for key in ("_id", "sha256", "current_version_id")})


def permission_receipt_binding(row: Mapping[str, Any], source: Mapping[str, Any]) -> Dict[str, Any]:
    """What a new receipt records about the row and source it is decided on."""
    return {
        "receipt_version": PERMISSION_RECEIPT_VERSION,
        "case_id": row.get("case_id"),
        "matrix_row_id": row.get("_id"),
        "draft_id": row.get("draft_id"),
        "source_paragraph_response_id": row.get("source_paragraph_response_id"),
        "source_response_revision": row.get("source_response_revision"),
        "matrix_row_content_hash": permission_content_hash(row),
        "matrix_row_revision": int(row.get("edit_revision") or 0) + 1,
        "permission_source_content_hash": permission_source_content_hash(source),
    }


def _truthy(value: Any) -> bool:
    """The same reading of a flag as `case_workspace._is_positive`."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "yes", "true", "approved", "ready", "verified"}


def _same(left: Any, right: Any) -> bool:
    return (left is None and right is None) or (left is not None and right is not None and str(left) == str(right))


def permission_state(
    row: Mapping[str, Any],
    receipt: Optional[Mapping[str, Any]],
    response: Optional[Mapping[str, Any]] = None,
    source_ok: bool = False,
) -> str:
    """Whether this row's recorded permission is backed by a valid receipt.

    `response` is the authoritative paragraph response the row projects (None
    when the row projects none or it was not found); `source_ok` says whether
    the receipt's permission source still exists, is authoritative and matches
    the receipt's source hash.
    """
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
    expected = permission_receipt_binding(row, {})
    expected.pop("matrix_row_revision")  # a later review advances it; content does not change
    expected.pop("permission_source_content_hash")  # checked against the source itself
    bound = (
        receipt.get("gate") == PERMISSION_RECEIPT_GATE
        and receipt.get("decision") == "approved"
        and _same(receipt.get("permission_source_id"), row.get("permission_source_id"))
        and all(_same(receipt.get(key), value) for key, value in expected.items())
    )
    if bound and row.get("source_paragraph_response_id"):
        # The response is authoritative; the row is a projection of it.
        bound = bool(
            response
            and _truthy(response.get("permission_obtained"))
            and _same(response.get("permission_approval_receipt_id"), receipt_id)
            and _same(response.get("response_revision"), receipt.get("source_response_revision"))
        )
    if not bound:
        return RECEIPT_MISMATCH
    return VERIFIED if source_ok else SOURCE_UNAVAILABLE


async def _source_ok(db: Any, receipt: Mapping[str, Any]) -> bool:
    """The receipt's permission source still exists in scope, is authoritative,
    and holds the bytes and version it held when the permission was decided."""
    from ...core.security import _expand_object_ids
    from ..publication_policy import is_consumable, resolve_document_authority

    source_id = receipt.get("permission_source_id")
    if not source_id or not receipt.get("permission_source_content_hash"):
        return False
    scope: Dict[str, Any] = {"_id": {"$in": _expand_object_ids([str(source_id)])}}
    for key in ("organization_id", "project_id"):
        if receipt.get(key):
            scope[key] = receipt.get(key)
    for collection_name in ("documents", "letters"):
        source = await db[collection_name].find_one(scope)
        if not source:
            continue
        if permission_source_content_hash({**source, "_id": str(source.get("_id"))}) != receipt.get(
            "permission_source_content_hash"
        ):
            return False
        if collection_name == "documents":
            return bool((await resolve_document_authority(db, str(source.get("_id")))).consumable)
        return is_consumable(source)
    return False


async def rejoinder_permission_states(db: Any, rows: Iterable[Mapping[str, Any]]) -> Dict[str, str]:
    """`permission_state` for every row, keyed by row id. Read-only."""
    rows = list(rows)
    granted = [row for row in rows if _truthy(row.get("permission_obtained")) and row.get("permission_approval_receipt_id")]
    receipts: Dict[str, Mapping[str, Any]] = {}
    responses: Dict[str, Mapping[str, Any]] = {}
    sources: Dict[str, bool] = {}
    if granted:
        receipt_ids = sorted({str(row["permission_approval_receipt_id"]) for row in granted})
        cursor = db.arbitration_workflow_approvals.find({"_id": {"$in": receipt_ids}, "gate": PERMISSION_RECEIPT_GATE})
        receipts = {str(receipt.get("_id")): receipt for receipt in await _collect(cursor)}
        response_ids = sorted({str(row["source_paragraph_response_id"]) for row in granted if row.get("source_paragraph_response_id")})
        if response_ids:
            cursor = db.arbitration_paragraph_responses.find({"_id": {"$in": response_ids}})
            responses = {str(response.get("_id")): response for response in await _collect(cursor)}
        for receipt_id, receipt in receipts.items():
            sources[receipt_id] = await _source_ok(db, receipt)
    states: Dict[str, str] = {}
    for row in rows:
        receipt_id = str(row.get("permission_approval_receipt_id") or "")
        states[str(row.get("_id"))] = permission_state(
            row,
            receipts.get(receipt_id),
            responses.get(str(row.get("source_paragraph_response_id") or "")),
            sources.get(receipt_id, False),
        )
    return states


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


async def annotate_permission_states(db: Any, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Copies of rejoinder rows carrying whether their permission counts, and why not.

    The stored grant fields are returned unchanged as the record of what was
    decided; `permission_effective` is what every gate applies.
    """
    states = await rejoinder_permission_states(db, rows)
    annotated = []
    for row in rows:
        state = states[str(row.get("_id"))]
        annotated.append(
            {
                **row,
                "permission_effective": state == VERIFIED,
                "permission_receipt_state": state,
                "permission_receipt_message": STATE_MESSAGES.get(state),
            }
        )
    return annotated


async def commit_permission_receipt(db: Any, receipt_id: Any) -> bool:
    """Commit a decision's receipt only while it is still `pending`.

    False means the reconciler aborted it first: the caller must withdraw its grant.
    """
    result = await db.arbitration_workflow_approvals.update_one(
        {"_id": receipt_id, "gate": PERMISSION_RECEIPT_GATE, "receipt_status": "pending"},
        {"$set": {"receipt_status": "committed", "committed_at": datetime.utcnow()}},
    )
    return bool(getattr(result, "modified_count", 0))


async def reconcile_stale_permission_receipts(
    db: Any,
    *,
    case_id: Optional[str] = None,
    now: Optional[datetime] = None,
    dry_run: bool = False,
) -> Dict[str, List[str]]:
    """Abort stale `pending` receipts, then withdraw every grant still citing an aborted one.

    The abort is one conditional write - still `pending` and older than the
    cutoff - so a decision that commits first keeps its receipt, and one that
    commits later finds its commit refused and withdraws its own grant. Grants
    are withdrawn only where they still cite that receipt, so a newer grant
    under another receipt is never touched. Withdrawal also sweeps grants left
    behind by an earlier abort whose withdrawal failed. A failure on one
    receipt does not stop the others; the first error is raised at the end.
    """
    cutoff = (now or datetime.utcnow()) - STALE_PENDING_RECEIPT_AGE
    scope: Dict[str, Any] = {"gate": PERMISSION_RECEIPT_GATE}
    if case_id:
        scope["case_id"] = case_id
    stale = {"receipt_status": "pending", "approved_at": {"$lte": cutoff}}
    candidates = await _collect(db.arbitration_workflow_approvals.find({**scope, **stale}))
    report: Dict[str, List[str]] = {"aborted": [], "withdrawn_from": [], "errors": []}
    if dry_run:
        report["aborted"] = [str(receipt["_id"]) for receipt in candidates]
        return report
    first_error: Optional[BaseException] = None
    for receipt in candidates:
        try:
            result = await db.arbitration_workflow_approvals.update_one(
                {"_id": receipt["_id"], **scope, **stale},
                {
                    "$set": {
                        "receipt_status": "aborted",
                        "aborted_at": datetime.utcnow(),
                        "aborted_by": RECONCILER_ACTOR,
                        "abort_reason": "stale_pending_receipt",
                    }
                },
            )
            if getattr(result, "modified_count", 0):
                report["aborted"].append(str(receipt["_id"]))
        except Exception as exc:  # keep going; surfaced below
            report["errors"].append(f"{receipt['_id']}: {exc}")
            first_error = first_error or exc
    # Every aborted receipt in scope, including ones aborted earlier whose
    # withdrawal did not finish.
    aborted = await _collect(db.arbitration_workflow_approvals.find({**scope, "receipt_status": "aborted"}))
    for receipt in aborted:
        cited = {"permission_approval_receipt_id": receipt["_id"], "permission_obtained": True}
        revoked = {"$set": {**REVOKED_REJOINDER_PERMISSION, "updated_at": datetime.utcnow(), "updated_by": RECONCILER_ACTOR}}
        try:
            responses = await db.arbitration_paragraph_responses.update_many(cited, revoked)
            rows = await db.arbitration_rejoinder_matrix.update_many(cited, revoked)
            if getattr(responses, "modified_count", 0) or getattr(rows, "modified_count", 0):
                report["withdrawn_from"].append(str(receipt["_id"]))
        except Exception as exc:
            report["errors"].append(f"{receipt['_id']}: {exc}")
            first_error = first_error or exc
    if first_error is not None:
        raise first_error
    return report
