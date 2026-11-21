"""Durable subscription operations: gateway work that a database update cannot make atomic
(R6-7 and the gateway/database consistency design, owner decision 2026-11-21).

A cancellation, replacement or lifecycle change touches two systems - the payment gateway and
MongoDB - and no transaction spans them. So the operation is recorded *before* any external
call, every external result is recorded as it arrives, local state is changed only according
to what the gateway confirmed, and anything left unfinished stays visible to an administrator
until it is completed or resolved. Nothing here claims cross-system atomicity.

``subscription_operations`` (one document per operation)::

    _id               "op_<sha256(scope, idempotency key)>" - so a retried request finds the
                      same operation - or "op_<uuid>" when the caller sent no key
    kind              e.g. "plan_settings_replacement", "plan_settings_inherit"
    fingerprint       sha256 of the request: the same key with a different request is refused
    organization_id / project_id / requested_by / created_at / updated_at
    status            in_progress | completed | needs_review | failed (nothing changed anywhere;
                      retryable, not listed for review)
    lease_until       an in-progress operation older than this may be resumed (crash recovery)
    request           what was asked (no secrets, no personal data)
    items             one per affected subscription: subscription_id, status_before,
                      gateway_bound, gateway (not_bound | pending | confirmed | failed),
                      attempts, last_error (exception class only), local
                      (unchanged | cancelled | changed_meanwhile)
    result            e.g. new_subscription_id

``subscription_operation_locks``: one document per scope (``_id`` = scope key) with a lease,
so two different operations never work on one scope at once. Uniqueness comes from ``_id``:
no index migration is needed.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

OPERATIONS = "subscription_operations"
LOCKS = "subscription_operation_locks"
LEASE = timedelta(seconds=60)
OPEN_STATUSES = ("in_progress", "needs_review")
#: Statuses a repeat of the same request answers with as they are (workstream 1 adds the
#: replacement's "awaiting_payment" and "abandoned").
SETTLED_STATUSES = ("completed", "awaiting_payment", "abandoned")


class OperationConflict(Exception):
    """Another operation holds the scope, or this one is still running."""


class IdempotencyKeyReused(Exception):
    """The idempotency key was already used for a different request."""


def scope_key(organization_id: str, project_id: Optional[str]) -> str:
    return f"{organization_id}|{project_id or ''}"


def fingerprint(request: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()


def operation_id(organization_id: str, project_id: Optional[str], idempotency_key: Optional[str], kind: str = "") -> str:
    """Deterministic for a key: scope, kind and key are hashed together, so keys of different
    kinds or scopes never meet (review 7 R7-4)."""
    if not idempotency_key:
        return f"op_{uuid.uuid4().hex}"
    digest = hashlib.sha256(f"{kind}|{scope_key(organization_id, project_id)}|{idempotency_key}".encode()).hexdigest()
    return f"op_{digest[:40]}"


async def begin(db: Any, *, kind: str, organization_id: str, project_id: Optional[str],
                idempotency_key: Optional[str], request: Dict[str, Any], requested_by: Optional[str]) -> Dict[str, Any]:
    """Record the operation intent, or return the existing one for this key.

    A completed operation is returned as it is (the caller answers without repeating it); an
    operation still running under a fresh lease is refused; an unfinished one whose lease has
    lapsed, or that needs review, is returned for the caller to resume.
    """
    now = datetime.utcnow()
    op_id = operation_id(organization_id, project_id, idempotency_key, kind)
    doc: Dict[str, Any] = {
        "_id": op_id, "kind": kind, "fingerprint": fingerprint(request),
        "organization_id": organization_id, "project_id": project_id,
        "requested_by": requested_by, "created_at": now, "updated_at": now,
        "status": "in_progress", "lease_until": now + LEASE, "attempts": 1,
        "request": request, "items": [], "result": {},
        # Fencing token of the current attempt (review 8 C2): the scope lock and every write
        # of this operation are checked against it, so a superseded attempt cannot write.
        "token": uuid.uuid4().hex,
    }
    try:
        await db[OPERATIONS].insert_one(doc)
        return doc
    except DuplicateKeyError:
        existing = await db[OPERATIONS].find_one({"_id": op_id}) or {}
    if existing.get("fingerprint") != doc["fingerprint"]:
        raise IdempotencyKeyReused("This idempotency key was used for a different request")
    if existing.get("status") in SETTLED_STATUSES:
        # Completed, or waiting for something outside this request (a replacement's payment):
        # answered as it is, never started again.
        return existing
    return await resume(db, existing)


async def resume(db: Any, existing: Dict[str, Any]) -> Dict[str, Any]:
    """Take over an unfinished operation with a new attempt token. ``prior_status`` (not
    stored) lets the caller put it back if it cannot proceed (review 8 C1/C4)."""
    now = datetime.utcnow()
    resumed = await db[OPERATIONS].find_one_and_update(
        {"_id": existing["_id"], "$or": [{"status": {"$in": ["needs_review", "failed"]}}, {"lease_until": {"$lt": now}}]},
        {"$set": {"status": "in_progress", "lease_until": now + LEASE, "updated_at": now, "token": uuid.uuid4().hex},
         "$inc": {"attempts": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if resumed is None:
        raise OperationConflict("This operation is already in progress")
    resumed["prior_status"] = existing.get("status")
    return resumed


async def step_back(db: Any, op: Dict[str, Any]) -> None:
    """An attempt that could not start (the scope is held elsewhere) leaves the operation as
    it found it: a resumed operation keeps its earlier status (a review item stays listed),
    a fresh one is ``failed`` (nothing was done)."""
    previous = op.get("prior_status") or "failed"
    if previous in {"in_progress", "completed"}:
        previous = "needs_review" if previous == "in_progress" else previous
    await db[OPERATIONS].update_one(
        {"_id": op["_id"], "token": op.get("token")},
        {"$set": {"status": previous, "lease_until": None, "updated_at": datetime.utcnow()}},
    )


async def lock_scope(db: Any, organization_id: str, project_id: Optional[str], op_id: str) -> None:
    now = datetime.utcnow()
    try:
        await db[LOCKS].find_one_and_update(
            {"_id": scope_key(organization_id, project_id), "$or": [{"lease_until": {"$lt": now}}, {"holder": op_id}]},
            {"$set": {"holder": op_id, "lease_until": now + LEASE, "updated_at": now}},
            upsert=True,
        )
    except DuplicateKeyError as exc:
        raise OperationConflict("Another plan change for this scope is in progress") from exc


class LeaseLost(OperationConflict):
    """Another operation took the scope after this one's lease lapsed."""


async def renew(db: Any, organization_id: str, project_id: Optional[str], op_id: str, token: Optional[str] = None) -> None:
    """Extend this attempt's scope lease and the operation's lease, fenced on the attempt's
    token (review 7 R7-1, review 8 C2): an attempt that lost the scope - to another operation
    or to a later attempt of the same one - stops before its next external call."""
    now = datetime.utcnow()
    holder = token or op_id
    held = await db[LOCKS].update_one(
        {"_id": scope_key(organization_id, project_id), "holder": holder},
        {"$set": {"lease_until": now + LEASE, "updated_at": now}},
    )
    mine = await db[OPERATIONS].update_one(
        {"_id": op_id, **({"token": token} if token else {})},
        {"$set": {"lease_until": now + LEASE, "updated_at": now}},
    )
    if held.matched_count == 0 or mine.matched_count == 0:
        raise LeaseLost("This attempt lost its scope; it stopped before its next gateway call")


async def unlock_scope(db: Any, organization_id: str, project_id: Optional[str], op_id: str) -> None:
    await db[LOCKS].delete_one({"_id": scope_key(organization_id, project_id), "holder": op_id})


async def save_items(db: Any, op_id: str, items: List[Dict[str, Any]], token: Optional[str] = None) -> None:
    saved = await db[OPERATIONS].update_one({"_id": op_id, **({"token": token} if token else {})},
                                            {"$set": {"items": items, "updated_at": datetime.utcnow()}})
    if token and saved.matched_count == 0:
        raise LeaseLost("A later attempt took over this operation; this attempt stopped")


async def finish(db: Any, op_id: str, status: str, *, result: Optional[Dict[str, Any]] = None,
                 items: Optional[List[Dict[str, Any]]] = None, token: Optional[str] = None) -> None:
    update: Dict[str, Any] = {"status": status, "updated_at": datetime.utcnow(), "lease_until": None}
    if result is not None:
        update["result"] = result
    if items is not None:
        update["items"] = items
    done = await db[OPERATIONS].update_one({"_id": op_id, **({"token": token} if token else {})}, {"$set": update})
    if token and done.matched_count == 0:
        raise LeaseLost("A later attempt took over this operation; this attempt's outcome was not recorded")


async def open_operations(db: Any, organization_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """Unfinished operations for an administrator: needing review, or in progress past their lease."""
    now = datetime.utcnow()
    query: Dict[str, Any] = {"$or": [{"status": "needs_review"},
                                     {"status": "in_progress", "lease_until": {"$lt": now}}]}
    if organization_id:
        query["organization_id"] = organization_id
    rows = await db[OPERATIONS].find(query).sort("updated_at", -1).to_list(length=limit)
    return [{**row, "id": row.pop("_id")} for row in rows]


__all__ = [
    "IdempotencyKeyReused", "LeaseLost", "OperationConflict", "begin", "finish", "lock_scope", "open_operations",
    "operation_id", "renew", "resume", "save_items", "step_back", "unlock_scope",
]
