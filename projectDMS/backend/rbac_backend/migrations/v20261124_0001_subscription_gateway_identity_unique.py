"""Gateway identity protection (owner decision 2026-11-20): one subscription per gateway id,
and one processing per signed webhook body.

The billing webhook finds the subscription a payment event applies to by
``payment_gateway_subscription_id``. Until 2026-11-20 an ordinary create or update could
write that id, so two subscriptions could carry the same one and a payment event could land
on the wrong one. The application now refuses those writes, checkout refuses an id already
bound, and the webhook refuses an ambiguous binding; this migration adds the database
guarantee behind them:

- ``subscriptions``: a partial unique index on ``payment_gateway_subscription_id`` (non-empty
  strings only; subscriptions with no gateway binding are not covered);
- ``billing_webhook_events``: a partial unique index on (``provider``, ``body_sha256``), so a
  signed body replayed under a new event-id header is refused even under concurrency.

Duplicates: a dry run reports them as warnings (so ``--fail-on-warning`` stops a deploy). An
apply with duplicates present **raises**, so the runner records nothing and the migration
stays pending until a person resolves them (gateway re-binding design, section 15 of
docs/DEF4_BILLING_PERMISSION_SEPARATION.md); it is never marked applied without its indexes
(independent review F3: the runner records any returned result as applied). Nothing is
deleted, merged or rebound here. Not run in any environment by this change.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .runner import MigrationResult
from .v20261114_0001_skill_registry import apply_indexes

VERSION = "20261124_0001"
NAME = "subscription_gateway_identity_unique"
DESCRIPTION = (
    "One subscription per payment gateway subscription id, and one processing per signed billing "
    "webhook body (partial unique indexes). Nothing is changed or rebound."
)

GATEWAY_INDEX: Dict[str, Any] = {
    "collection": "subscriptions",
    "keys": [("payment_gateway_subscription_id", 1)],
    "name": "subscription_gateway_identity_unique",
    "unique": True,
    "partialFilterExpression": {"payment_gateway_subscription_id": {"$type": "string", "$gt": ""}},
}
WEBHOOK_BODY_INDEX: Dict[str, Any] = {
    "collection": "billing_webhook_events",
    "keys": [("provider", 1), ("body_sha256", 1)],
    "name": "billing_webhook_body_unique",
    "unique": True,
    "partialFilterExpression": {"body_sha256": {"$type": "string"}},
}


async def duplicate_groups(db: Any, collection: str, group: Dict[str, Any], match: Dict[str, Any]) -> int:
    rows = await db[collection].aggregate(
        [
            {"$match": match},
            {"$group": {"_id": group, "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}},
            {"$count": "groups"},
        ]
    ).to_list(length=1)
    return int(rows[0]["groups"]) if rows else 0


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    warnings: List[str] = []
    clean: List[Dict[str, Any]] = []
    gateway_duplicates = await duplicate_groups(
        db, "subscriptions", {"id": "$payment_gateway_subscription_id"},
        {"payment_gateway_subscription_id": {"$type": "string", "$gt": ""}},
    )
    if gateway_duplicates:
        warnings.append(
            f"subscriptions: {gateway_duplicates} gateway subscription id(s) are bound to more than one "
            "subscription; the unique index is not created until a person resolves them"
        )
    else:
        clean.append(GATEWAY_INDEX)
    body_duplicates = await duplicate_groups(
        db, "billing_webhook_events", {"provider": "$provider", "body": "$body_sha256"},
        {"body_sha256": {"$type": "string"}},
    )
    if body_duplicates:
        warnings.append(
            f"billing_webhook_events: {body_duplicates} signed body(ies) were processed more than once; "
            "the unique index is not created until they are reviewed"
        )
    else:
        clean.append(WEBHOOK_BODY_INDEX)
    if warnings and not dry_run:
        raise RuntimeError(
            "subscription_gateway_identity_unique: " + "; ".join(warnings)
            + ". Nothing was changed; resolve the duplicates, then apply again."
        )
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=await apply_indexes(db, clean, dry_run),
        warnings=warnings,
        notices=[
            "Subscriptions with no gateway binding, and webhook events recorded before body hashing, are not covered.",
            "No subscription, binding or webhook event is changed, merged or rebound by this migration.",
        ],
    )
