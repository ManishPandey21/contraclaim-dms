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


class OperationConflict(Exception):
    """Another operation holds the scope, or this one is still running."""


class IdempotencyKeyReused(Exception):
    """The idempotency key was already used for a different request."""


def scope_key(organization_id: str, project_id: Optional[str]) -> str:
    return f"{organization_id}|{project_id or ''}"


def fingerprint(request: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()


def operation_id(organization_id: str, project_id: Optional[str], idempotency_key: Optional[str]) -> str:
    if not idempotency_key:
        return f"op_{uuid.uuid4().hex}"
    digest = hashlib.sha256(f"{scope_key(organization_id, project_id)}|{idempotency_key}".encode()).hexdigest()
    return f"op_{digest[:40]}"


async def begin(db: Any, *, kind: str, organization_id: str, project_id: Optional[str],
                idempotency_key: Optional[str], request: Dict[str, Any], requested_by: Optional[str]) -> Dict[str, Any]:
    """Record the operation intent, or return the existing one for this key.

    A completed operation is returned as it is (the caller answers without repeating it); an
    operation still running under a fresh lease is refused; an unfinished one whose lease has
    lapsed, or that needs review, is returned for the caller to resume.
    """
    now = datetime.utcnow()
    op_id = operation_id(organization_id, project_id, idempotency_key)
    doc: Dict[str, Any] = {
        "_id": op_id, "kind": kind, "fingerprint": fingerprint(request),
        "organization_id": organization_id, "project_id": project_id,
        "requested_by": requested_by, "created_at": now, "updated_at": now,
        "status": "in_progress", "lease_until": now + LEASE, "attempts": 1,
        "request": request, "items": [], "result": {},
    }
    try:
        await db[OPERATIONS].insert_one(doc)
        return doc
    except DuplicateKeyError:
        existing = await db[OPERATIONS].find_one({"_id": op_id}) or {}
    if existing.get("fingerprint") != doc["fingerprint"]:
        raise IdempotencyKeyReused("This idempotency key was used for a different request")
    if existing.get("status") == "completed":
        return existing
    resumed = await db[OPERATIONS].find_one_and_update(
        {"_id": op_id, "$or": [{"status": {"$in": ["needs_review", "failed"]}}, {"lease_until": {"$lt": now}}]},
        {"$set": {"status": "in_progress", "lease_until": now + LEASE, "updated_at": now}, "$inc": {"attempts": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if resumed is None:
        raise OperationConflict("This operation is already in progress")
    return resumed


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


async def unlock_scope(db: Any, organization_id: str, project_id: Optional[str], op_id: str) -> None:
    await db[LOCKS].delete_one({"_id": scope_key(organization_id, project_id), "holder": op_id})


async def save_items(db: Any, op_id: str, items: List[Dict[str, Any]]) -> None:
    await db[OPERATIONS].update_one({"_id": op_id}, {"$set": {"items": items, "updated_at": datetime.utcnow()}})


async def finish(db: Any, op_id: str, status: str, *, result: Optional[Dict[str, Any]] = None,
                 items: Optional[List[Dict[str, Any]]] = None) -> None:
    update: Dict[str, Any] = {"status": status, "updated_at": datetime.utcnow(), "lease_until": None}
    if result is not None:
        update["result"] = result
    if items is not None:
        update["items"] = items
    await db[OPERATIONS].update_one({"_id": op_id}, {"$set": update})


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
    "IdempotencyKeyReused", "OperationConflict", "begin", "finish", "lock_scope", "open_operations",
    "operation_id", "save_items", "unlock_scope",
]
