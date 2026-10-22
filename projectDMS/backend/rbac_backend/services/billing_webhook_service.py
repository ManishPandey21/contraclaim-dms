"""Idempotent billing webhook processing (Week 3.2).

Receives provider webhooks, verifies the signature, normalizes the event, and
reconciles the subscription state. Entitlements are *derived* from subscription
state (see EntitlementService), so flipping ``status``/``billing_status`` is the
entitlement reconciliation.

Idempotency: every event is recorded in ``billing_webhook_events`` keyed by the
provider's event id (unique index). A replayed event is a no-op.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from ..core.database import get_database
from .audit_event_service import AuditEventService
from .payment_gateway import PaymentGatewayInterface, WebhookEvent, get_payment_gateway
from .subscription_scope_key import current_subscription_scope_key

logger = logging.getLogger(__name__)


class BillingWebhookService:
    def __init__(self, db: Any = None, gateway: Optional[PaymentGatewayInterface] = None, provider: str = "noop") -> None:
        self.db = db
        self.provider = provider
        self.gateway = gateway or get_payment_gateway(provider)

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    async def process(
        self,
        *,
        raw_body: bytes,
        signature: Optional[str],
        event_id_header: Optional[str] = None,
    ) -> Dict[str, Any]:
        # 1) Signature verification — never trust the body alone.
        if not self.gateway.verify_webhook_signature(payload=raw_body, signature=signature):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook signature")

        # 2) Parse + normalize.
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed webhook body") from exc

        event = self.gateway.parse_webhook_event(payload=payload, event_id=event_id_header)
        if event is None or event.event_type == "ignored":
            return {"status": "ignored"}

        db = await self._get_db()

        # 3) Idempotency — unique insert on event_id; replays are no-ops.
        now = datetime.utcnow()
        try:
            await db.billing_webhook_events.insert_one(
                {
                    "event_id": event.event_id,
                    "provider": self.provider,
                    "event_type": event.event_type,
                    "gateway_subscription_id": event.gateway_subscription_id,
                    "received_at": now,
                    "processed": False,
                }
            )
        except DuplicateKeyError:
            logger.info("Duplicate billing webhook event ignored: %s", event.event_id)
            return {"status": "duplicate", "event_id": event.event_id}

        # 4) Reconcile subscription state.
        result = await self._reconcile(event)

        await db.billing_webhook_events.update_one(
            {"event_id": event.event_id},
            {"$set": {"processed": True, "processed_at": datetime.utcnow(), "result": result}},
        )
        return {"status": "processed", "event_id": event.event_id, **result}

    async def _reconcile(self, event: WebhookEvent) -> Dict[str, Any]:
        db = await self._get_db()
        now = datetime.utcnow()

        subscription = None
        if event.gateway_subscription_id:
            subscription = await db.subscriptions.find_one(
                {"payment_gateway_subscription_id": event.gateway_subscription_id}
            )
        if not subscription:
            logger.warning(
                "Webhook %s references unknown gateway subscription %s",
                event.event_id,
                event.gateway_subscription_id,
            )
            return {"matched": False}

        transitions = {
            "payment.succeeded": {"status": "active", "billing_status": "active"},
            "subscription.activated": {"status": "active", "billing_status": "active"},
            "payment.failed": {"billing_status": "past_due"},
            "subscription.halted": {"billing_status": "past_due"},
            "subscription.cancelled": {"status": "cancelled", "billing_status": "inactive"},
        }
        update = dict(transitions.get(event.event_type) or {})
        if not update:
            return {"matched": True, "changed": False}

        # Money-safety: an activating event must carry the amount/currency the
        # plan was priced at. A mismatch never enables entitlement (entitlement
        # derives from status == "active"); it is flagged for admin review.
        activates = update.get("status") == "active" or update.get("billing_status") == "active"
        if activates and event.amount_minor is not None:
            expected = await self._expected_charge(db, subscription)
            if expected and expected["amount_minor"]:
                bad: Optional[str] = None
                if int(event.amount_minor) != expected["amount_minor"]:
                    bad = f"amount {event.amount_minor} != expected {expected['amount_minor']}"
                elif event.currency and str(event.currency).upper() != expected["currency"].upper():
                    bad = f"currency {event.currency} != expected {expected['currency']}"
                if bad:
                    return await self._record_mismatch(db, subscription, event, bad, now)

        update.update({"updated_at": now, "updated_by": f"system:webhook:{self.provider}"})
        prospective = {**subscription, **update}
        current_key = current_subscription_scope_key(prospective)
        if current_key:
            update["current_scope_key"] = current_key
            operation: Dict[str, Any] = {"$set": update}
        else:
            operation = {"$set": update, "$unset": {"current_scope_key": ""}}
        try:
            await db.subscriptions.update_one({"_id": subscription["_id"]}, operation)
        except DuplicateKeyError:
            await db.subscriptions.update_one(
                {"_id": subscription["_id"]},
                {
                    "$set": {
                        "billing_status": "review",
                        "subscription_conflict_review_required": True,
                        "updated_at": now,
                        "updated_by": f"system:webhook:{self.provider}",
                    },
                    "$unset": {"current_scope_key": ""},
                },
            )
            await AuditEventService(db).emit(
                action="billing.webhook.subscription_scope_conflict",
                actor_id=f"system:webhook:{self.provider}",
                resource_type="subscription",
                resource_id=str(subscription["_id"]),
                organization_id=str(subscription.get("organization_id", "")),
                project_id=subscription.get("project_id"),
                result="failure",
                reason="current_subscription_scope_conflict",
                metadata={"event_id": event.event_id, "requires_admin_review": True},
            )
            return {
                "matched": True,
                "changed": False,
                "subscription_id": str(subscription["_id"]),
                "requires_admin_review": True,
                "reason": "current_subscription_scope_conflict",
            }

        # Billing record (financial audit trail).
        record_status = "paid" if event.event_type == "payment.succeeded" else (
            "failed" if event.event_type == "payment.failed" else event.event_type
        )
        await db.billing_records.insert_one(
            {
                "subscription_id": str(subscription["_id"]),
                "organization_id": str(subscription.get("organization_id", "")),
                "project_id": subscription.get("project_id"),
                "provider": self.provider,
                "event_id": event.event_id,
                "event_type": event.event_type,
                "gateway_payment_id": event.gateway_payment_id,
                "gateway_subscription_id": event.gateway_subscription_id,
                "amount_minor": event.amount_minor,
                "currency": event.currency,
                "record_status": record_status,
                "created_at": now,
            }
        )
        if record_status == "failed":
            await AuditEventService(db).emit(
                action="billing.webhook.payment_failed",
                actor_id=f"system:webhook:{self.provider}",
                resource_type="subscription",
                resource_id=str(subscription["_id"]),
                organization_id=str(subscription.get("organization_id", "")),
                project_id=subscription.get("project_id"),
                result="failure",
                reason="payment_failed",
                metadata={
                    "requires_admin_review": True,
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "gateway_payment_id": event.gateway_payment_id,
                    "gateway_subscription_id": event.gateway_subscription_id,
                },
            )

        # Subscription history for the lifecycle audit.
        await db.subscription_history.insert_one(
            {
                "subscription_id": str(subscription["_id"]),
                "organization_id": str(subscription.get("organization_id", "")),
                "project_id": subscription.get("project_id"),
                "change_type": "webhook",
                "from_status": str(subscription.get("status", "")),
                "to_status": str(update.get("status", subscription.get("status", ""))),
                "metadata": {"event_type": event.event_type, "event_id": event.event_id},
                "changed_by": f"system:webhook:{self.provider}",
                "changed_at": now,
            }
        )

        return {
            "matched": True,
            "changed": True,
            "subscription_id": str(subscription["_id"]),
            "applied": {k: v for k, v in update.items() if k in {"status", "billing_status"}},
        }

    async def _expected_charge(self, db: Any, subscription: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Authoritative expected charge for the subscription's plan + period.

        Resolves the plan the subscription was created against and returns
        ``{amount_minor, currency}``. Returns ``None`` when it can't be resolved
        (plan not seeded / no price); the caller then skips amount validation and
        logs, rather than blocking a legitimately-priced activation on a config gap.
        """
        plan_code = subscription.get("plan_code")
        if not plan_code:
            return None
        try:
            plan = await db.plans.find_one({"code": plan_code})
        except Exception:  # pragma: no cover - defensive (collection absent in some test DBs)
            plan = None
        if not plan:
            return None
        period = subscription.get("billing_period") or "monthly"
        tiers = plan.get("pricing_tiers") or {}
        amount = tiers.get(period)
        if amount is None:
            amount = plan.get("base_price_minor")
        if amount is None:
            return None
        return {"amount_minor": int(amount), "currency": str(plan.get("currency") or "INR")}

    async def _record_mismatch(self, db: Any, subscription: Dict[str, Any], event: WebhookEvent,
                               detail: str, now: datetime) -> Dict[str, Any]:
        """Flag an amount/currency mismatch without enabling entitlement.

        The subscription is left un-activated (status unchanged) and marked for
        admin review; a billing record captures the discrepancy for the queue.
        """
        logger.warning(
            "Billing webhook %s rejected for subscription %s: %s",
            event.event_id, subscription["_id"], detail,
        )
        await db.subscriptions.update_one(
            {"_id": subscription["_id"]},
            {"$set": {
                "billing_status": "review",
                "payment_review_required": True,
                "updated_at": now,
                "updated_by": f"system:webhook:{self.provider}",
            }},
        )
        await db.billing_records.insert_one({
            "subscription_id": str(subscription["_id"]),
            "organization_id": str(subscription.get("organization_id", "")),
            "project_id": subscription.get("project_id"),
            "provider": self.provider,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "gateway_payment_id": event.gateway_payment_id,
            "gateway_subscription_id": event.gateway_subscription_id,
            "amount_minor": event.amount_minor,
            "currency": event.currency,
            "record_status": "amount_mismatch",
            "validation_error": detail,
            "created_at": now,
        })
        await AuditEventService(db).emit(
            action="billing.webhook.amount_mismatch",
            actor_id=f"system:webhook:{self.provider}",
            resource_type="subscription",
            resource_id=str(subscription["_id"]),
            organization_id=str(subscription.get("organization_id", "")),
            project_id=subscription.get("project_id"),
            result="failure",
            reason="amount_mismatch",
            metadata={
                "requires_admin_review": True,
                "event_id": event.event_id,
                "event_type": event.event_type,
                "gateway_payment_id": event.gateway_payment_id,
                "gateway_subscription_id": event.gateway_subscription_id,
                "validation_error": detail,
            },
        )
        await db.subscription_history.insert_one({
            "subscription_id": str(subscription["_id"]),
            "organization_id": str(subscription.get("organization_id", "")),
            "project_id": subscription.get("project_id"),
            "change_type": "webhook_rejected",
            "from_status": str(subscription.get("status", "")),
            "to_status": str(subscription.get("status", "")),
            "metadata": {"event_type": event.event_type, "event_id": event.event_id,
                         "validation_error": detail},
            "changed_by": f"system:webhook:{self.provider}",
            "changed_at": now,
        })
        return {
            "matched": True,
            "changed": False,
            "validation": "amount_mismatch",
            "detail": detail,
            "subscription_id": str(subscription["_id"]),
        }
