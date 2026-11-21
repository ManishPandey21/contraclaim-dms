"""Payment amount mismatch policy (workstream 2, owner decision 2026-11-21: approved policy
design; activation and production grace length need separate owner approval).

Only with ``PAYMENT_MISMATCH_POLICY=enforce`` (default ``off``: the webhook behaves as before).

**Initial activation** (a pending or trial subscription becoming paid) requires a verified
payment: the plan's expected charge for the period must be resolvable and non-zero, and the
event's amount and currency must equal it. Otherwise nothing is activated and an ``initial``
discrepancy is recorded for review.

**Renewal discrepancy** (a payment for a subscription already in paid service - active, pilot
or past due - that does not match, or whose expected charge cannot be resolved): the
subscription enters financial review with an auditable reason. Its existing, legitimately paid
entitlement is not revoked at once; nothing is granted from the unverified payment (no status
change - a past-due subscription stays past due - and no plan, period, quantity, add-on or
paid-through change: the webhook writes none of those). A grace period starts at the *first*
open discrepancy (``PAYMENT_MISMATCH_GRACE_DAYS``, owner-approved, 1-30); further mismatching
events are recorded but never move it, so repeated mismatches cannot extend service. When it
lapses with a discrepancy still open, the subscription classifies as ``blocking``
(``subscription_resolution.classify``) - a later correct payment does not lift that; only a
Super Admin settlement does.

**Settlement** (Super Admin, audited): ``settled`` or ``written_off`` closes a discrepancy;
when none is open the review ends and the billing status before it is restored. ``escalated``
keeps it open (and the grace unchanged). Nothing here refunds, charges or calls the provider.

``payment_discrepancies``: ``_id`` = sha256(provider, gateway payment id or event id) - one
record per payment, however many times its event is delivered; ``subscription_id``,
``organization_id``, ``project_id``, ``kind`` (initial | renewal), ``reason``, ``amount_minor``,
``currency``, ``expected_amount_minor``, ``expected_currency``, ``status`` (open | escalated |
settled | written_off), ``detected_at``, ``resolution``.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from pymongo.errors import DuplicateKeyError

COLLECTION = "payment_discrepancies"
OPEN = ("open", "escalated")
RESOLUTIONS = ("settled", "written_off", "escalated")


def enforced() -> bool:
    from ..core.config import settings

    return str(getattr(settings, "PAYMENT_MISMATCH_POLICY", "off") or "off").strip().lower() == "enforce"


def grace() -> timedelta:
    from ..core.config import settings

    return timedelta(days=int(getattr(settings, "PAYMENT_MISMATCH_GRACE_DAYS", 0) or 0))


def discrepancy_id(provider: str, payment_or_event_id: str) -> str:
    return "pd_" + hashlib.sha256(f"{provider}|{payment_or_event_id}".encode()).hexdigest()[:40]


async def record(db: Any, *, subscription: Dict[str, Any], provider: str, kind: str, reason: str,
                 event_id: str, gateway_payment_id: Optional[str], amount_minor: Any, currency: Any,
                 expected: Optional[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    """Record one discrepancy (idempotent per payment) and, for a renewal, open the financial
    review at the first one. Returns ``{"discrepancy_id", "new", "grace_until"}``."""
    doc_id = discrepancy_id(provider, str(gateway_payment_id or event_id))
    doc = {
        "_id": doc_id, "subscription_id": str(subscription["_id"]),
        "organization_id": str(subscription.get("organization_id") or ""), "project_id": subscription.get("project_id"),
        "provider": provider, "kind": kind, "reason": reason, "event_id": event_id,
        "gateway_payment_id": gateway_payment_id, "amount_minor": amount_minor, "currency": currency,
        "expected_amount_minor": (expected or {}).get("amount_minor"), "expected_currency": (expected or {}).get("currency"),
        "status": "open", "detected_at": now,
    }
    try:
        await db[COLLECTION].insert_one(doc)
        new = True
    except DuplicateKeyError:
        new = False
    grace_until = None
    if kind == "renewal":
        # Review 10 R10-10: decided on the stored review, not the caller's snapshot - a review
        # settled meanwhile is reopened for this discrepancy rather than missed.
        counted = await db.subscriptions.find_one_and_update(
            {"_id": subscription["_id"], "financial_review.state": "open"},
            {"$inc": {"financial_review.discrepancies": 1 if new else 0}},
            projection={"financial_review": 1})
        if counted is not None:
            grace_until = (counted.get("financial_review") or {}).get("grace_until")  # anchored: never moved
        else:
            grace_until = now + grace()
            await db.subscriptions.update_one(
                {"_id": subscription["_id"], "financial_review.state": {"$ne": "open"}},
                {"$set": {"financial_review": {"state": "open", "opened_at": now, "grace_until": grace_until,
                                               "reason": reason, "discrepancies": 1,
                                               "billing_status_before": subscription.get("billing_status")},
                          "billing_status": "financial_review", "payment_review_required": True,
                          "updated_at": now}},
            )
    return {"discrepancy_id": doc_id, "new": new, "grace_until": grace_until}


async def list_open(db: Any, organization_id: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {"status": {"$in": list(OPEN)}}
    if organization_id:
        query["organization_id"] = str(organization_id)
    rows = await db[COLLECTION].find(query).sort("detected_at", 1).to_list(length=max(1, min(limit, 1000)))
    return [{**row, "id": row.pop("_id")} for row in rows]


async def resolve(db: Any, doc_id: str, *, outcome: str, reason: str, actor: Optional[str],
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    """Settle, write off or escalate one discrepancy. Settling the last open one of a
    subscription ends its financial review (the billing status before it is restored)."""
    if outcome not in RESOLUTIONS:
        raise ValueError(f"outcome must be one of {', '.join(RESOLUTIONS)}")
    moment = now or datetime.utcnow()
    current = await db[COLLECTION].find_one({"_id": doc_id})
    if not current:
        raise LookupError("Discrepancy not found")
    if current.get("status") not in OPEN:
        raise ValueError("This discrepancy is already resolved")
    resolution = {"outcome": outcome, "reason": reason, "by": actor, "at": moment}
    if outcome == "escalated":
        await db[COLLECTION].update_one({"_id": doc_id, "status": {"$in": list(OPEN)}},
                                        {"$set": {"status": "escalated", "escalation": resolution}})
        return {"id": doc_id, "status": "escalated", "review_closed": False}
    changed = await db[COLLECTION].update_one({"_id": doc_id, "status": {"$in": list(OPEN)}},
                                              {"$set": {"status": outcome, "resolution": resolution}})
    if not changed.matched_count:
        raise ValueError("This discrepancy changed meanwhile")
    closed = False
    if current.get("kind") == "renewal" and not await db[COLLECTION].find_one(
            {"subscription_id": current["subscription_id"], "kind": "renewal", "status": {"$in": list(OPEN)}}, {"_id": 1}):
        from bson import ObjectId

        sid = current["subscription_id"]
        lookup: List[Any] = [sid] + ([ObjectId(sid)] if ObjectId.is_valid(sid) else [])
        sub = await db.subscriptions.find_one({"_id": {"$in": lookup}},
                                              {"financial_review": 1, "status": 1, "billing_status": 1})
        review = (sub or {}).get("financial_review") or {}
        if sub and review.get("state") == "open":
            opened = review.get("opened_at")
            paid_since = bool(await db.billing_records.find_one(
                {"subscription_id": sid, "record_status": "paid",
                 **({"created_at": {"$gte": opened}} if isinstance(opened, datetime) else {})}, {"_id": 1}))
            await db.subscriptions.update_one(
                {"_id": sub["_id"], "financial_review.state": "open"},
                {"$set": {"financial_review.state": "settled", "financial_review.settled_at": moment,
                          "billing_status": _billing_label_after_review(sub, review, paid_since),
                          "payment_review_required": False, "updated_at": moment}})
            closed = True
            # Review 11 (R10-10): a discrepancy recorded between the check above and this close
            # reopens the review, its grace anchored at the earliest still-open discrepancy.
            late = await db[COLLECTION].find_one(
                {"subscription_id": sid, "kind": "renewal", "status": {"$in": list(OPEN)}}, sort=[("detected_at", 1)])
            if late is not None:
                anchor = late.get("detected_at") if isinstance(late.get("detected_at"), datetime) else moment
                await db.subscriptions.update_one(
                    {"_id": sub["_id"], "financial_review.state": "settled"},
                    {"$set": {"financial_review.state": "open", "financial_review.grace_until": anchor + grace(),
                              "billing_status": "financial_review", "payment_review_required": True,
                              "updated_at": moment}})
                closed = False
    return {"id": doc_id, "status": outcome, "review_closed": closed}


def _billing_label_after_review(sub: Dict[str, Any], review: Dict[str, Any], paid_since: bool) -> str:
    """Review 10 R10-9 / review 11: the label follows what happened. A later event that set
    the billing label (``payment.failed``: past due; a cancellation: inactive) is kept; a
    verified payment during the review means active; otherwise the label from before."""
    current = str(sub.get("billing_status") or "")
    if current and current not in {"financial_review", "review"}:
        return current
    if str(sub.get("status") or "").strip().lower() == "cancelled":
        return "inactive"
    if paid_since:
        return "active"
    before = str(review.get("billing_status_before") or "")
    return before if before and before not in {"review", "financial_review"} else "active"


__all__ = ["COLLECTION", "OPEN", "RESOLUTIONS", "discrepancy_id", "enforced", "grace", "list_open", "record", "resolve"]
