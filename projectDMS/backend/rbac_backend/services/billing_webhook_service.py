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
from .payment_gateway import PaymentGatewayInterface, WebhookEvent, get_payment_gateway

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

        update.update({"updated_at": now, "updated_by": f"system:webhook:{self.provider}"})
        await db.subscriptions.update_one({"_id": subscription["_id"]}, {"$set": update})

        # Billing record (financial audit trail).
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
                "record_status": "paid" if event.event_type == "payment.succeeded" else (
                    "failed" if event.event_type == "payment.failed" else event.event_type
                ),
                "created_at": now,
            }
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
