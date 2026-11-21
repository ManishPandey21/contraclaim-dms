"""Idempotent billing webhook processing (Week 3.2).

Receives provider webhooks, verifies the signature, normalizes the event, and
reconciles the subscription state. Entitlements are *derived* from subscription
state (see EntitlementService), so flipping ``status``/``billing_status`` is the
entitlement reconciliation.

Idempotency: every event is recorded in ``billing_webhook_events`` keyed by the
provider's event id (unique index). A replayed event is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from ..core.database import get_database
from .audit_event_service import AuditEventService
from . import payment_discrepancy_service as payment_discrepancies
from .payment_gateway import PaymentGatewayInterface, WebhookEvent, get_payment_gateway

logger = logging.getLogger(__name__)


def _event_time(event: WebhookEvent) -> Optional[float]:
    """The provider's own event time (Razorpay ``created_at``, seconds), when it sent one."""
    raw = event.raw if isinstance(event.raw, dict) else {}
    value = raw.get("created_at")
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _event_notes(event: WebhookEvent) -> Dict[str, Any]:
    """The scope checkout put in the gateway subscription's notes, when the event carries it."""
    raw = event.raw if isinstance(event.raw, dict) else {}
    body = raw.get("payload") or {}
    entity = (body.get("subscription") or {}).get("entity") or {}
    notes = entity.get("notes")
    return notes if isinstance(notes, dict) else {}


#: A webhook may move a subscription to "active" only from these statuses (re-review R2).
WEBHOOK_ACTIVATABLE_STATUSES = frozenset({"pending", "trial", "active", "past_due"})


def _trial_lookup(value: Any) -> Any:
    """A stored trial id as stored: an ObjectId when it reads as one, else the string."""
    from bson import ObjectId

    text = str(value)
    return {"$in": [text, ObjectId(text)]} if ObjectId.is_valid(text) else text

#: Entity fields kept from a refused event's payload: identity, amount, scope. Contact
#: details, card and VPA data are dropped (re-review R4).
_KEPT_ENTITY_FIELDS = ("id", "subscription_id", "customer_id", "plan_id", "status", "amount", "currency", "notes", "order_id")


def _redacted(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    body = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    kept: Dict[str, Any] = {}
    for name, wrapper in body.items():
        entity = (wrapper or {}).get("entity") if isinstance(wrapper, dict) else None
        if isinstance(entity, dict):
            kept[str(name)] = {key: entity[key] for key in _KEPT_ENTITY_FIELDS if key in entity}
    return {"event": raw.get("event"), "created_at": raw.get("created_at"), "entities": kept}


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
        # The provider signs the body, not the event-id header, so a captured signed body
        # replayed under a new event id used to be processed again (an old
        # payment.succeeded re-activating a cancelled subscription). The body's own hash is
        # a second idempotency key (unique index: migration 20261124_0001).
        now = datetime.utcnow()
        body_sha256 = hashlib.sha256(raw_body).hexdigest()
        if await db.billing_webhook_events.find_one({"provider": self.provider, "body_sha256": body_sha256}, {"_id": 1}):
            logger.info("Replayed billing webhook body ignored: %s", event.event_id)
            return {"status": "duplicate", "event_id": event.event_id}
        try:
            await db.billing_webhook_events.insert_one(
                {
                    "event_id": event.event_id,
                    "provider": self.provider,
                    "body_sha256": body_sha256,
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
            # Identity before state: the gateway id must name exactly one subscription, bound
            # by checkout for this provider. Two bindings (possible before 2026-11-20, when an
            # ordinary create or update could write the id) are ambiguous: nothing is changed
            # and the refusal is audited for a person to resolve.
            bound = await db.subscriptions.find(
                {"payment_gateway_subscription_id": event.gateway_subscription_id}
            ).to_list(length=2)
            if len(bound) > 1:
                return await self._refuse_binding(db, event, "ambiguous_gateway_binding", bound)
            subscription = bound[0] if bound else None
            if subscription is not None:
                provider = str(subscription.get("payment_provider") or "").lower()
                if provider and provider != str(self.provider).lower():
                    return await self._refuse_binding(db, event, "gateway_provider_mismatch", [subscription])
                if subscription.get("payment_gateway_bound_by") not in (None, "checkout"):
                    return await self._refuse_binding(db, event, "gateway_binding_not_from_checkout", [subscription])
                # Ownership (independent review F4): the event's customer and the scope the
                # checkout sent in its notes must agree with the subscription, where both exist.
                stored_customer = str(subscription.get("payment_gateway_customer_id") or "")
                if event.gateway_customer_id and stored_customer and str(event.gateway_customer_id) != stored_customer:
                    return await self._refuse_binding(db, event, "gateway_customer_mismatch", [subscription])
                notes = _event_notes(event)
                for key in ("organization_id", "project_id"):
                    noted = str(notes.get(key) or "").strip()
                    if noted and noted != str(subscription.get(key) or "").strip():
                        return await self._refuse_binding(db, event, "gateway_notes_scope_mismatch", [subscription])
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
        if event.event_type == "subscription.authenticated":
            return await self._authenticated(db, subscription, event, now)
        update = dict(transitions.get(event.event_type) or {})
        if not update:
            return {"matched": True, "changed": False}

        # Order and freshness (independent review F2; re-review R1-R3). These guard the
        # **status change** only: a real payment is always recorded in billing_records (as
        # refused, for admin review, when its status change is refused), so the financial
        # trail is never lost.
        # - An event older than the last one applied to this subscription changes nothing.
        #   With no high-water mark yet (no event since deploy), the last processed event of
        #   this gateway id stands for it, so an old signed body cannot be replayed once.
        # - A webhook may set "active" only from pending, trial, active or past_due: never
        #   from cancelled, paused, offboarding, archive or an unknown state (an
        #   administrator's suspension or offboarding is not undone by the next charge).
        # One payment, one effect, whatever event id or body carried it.
        if event.gateway_payment_id and await db.billing_records.find_one(
            {"provider": self.provider, "gateway_payment_id": event.gateway_payment_id, "event_type": event.event_type},
            {"_id": 1},
        ):
            return {"matched": True, "changed": False, "duplicate_payment": True}
        event_at = _event_time(event)
        last_at = subscription.get("payment_last_event_at")
        if not isinstance(last_at, (int, float)):
            last_at = await self._last_processed_at(db, event)
        refusal: Optional[str] = None
        if event_at is not None and isinstance(last_at, (int, float)) and event_at < last_at:
            refusal = "stale_event"
        elif update.get("status") == "active" and str(subscription.get("status") or "").lower() not in WEBHOOK_ACTIVATABLE_STATUSES:
            refusal = "status_not_activatable_by_webhook"
        if refusal:
            if event.gateway_payment_id or event.event_type in {"payment.succeeded", "payment.failed"}:
                if not await self._insert_billing_record(db, subscription, event, f"refused_{refusal}", now, requires_review=True):
                    return {"matched": True, "changed": False, "duplicate_payment": True}
            return await self._refuse_binding(db, event, refusal, [subscription])
        if event_at is not None:
            update["payment_last_event_at"] = max(event_at, last_at) if isinstance(last_at, (int, float)) else event_at

        # Money-safety: an activating event must carry the amount/currency the
        # plan was priced at. A mismatch never enables entitlement (entitlement
        # derives from status == "active"); it is flagged for admin review.
        activates = update.get("status") == "active" or update.get("billing_status") == "active"
        if activates and payment_discrepancies.enforced():
            # Workstream 2 (PAYMENT_MISMATCH_POLICY=enforce): initial activation needs a verified
            # amount and currency; a renewal discrepancy enters financial review with a grace
            # anchored at the first open discrepancy, and grants nothing.
            decided = await self._enforce_payment_policy(db, subscription, event, update, now)
            if decided is not None:
                return decided
        elif activates and event.amount_minor is not None:
            expected = await self._expected_charge(db, subscription)
            if expected is not None and not expected["amount_minor"]:
                # Review 9 R9-2: a plan not sold for a price is never "paid" into service.
                return await self._record_mismatch(db, subscription, event, "plan is not sold for a price", now)
            if expected and expected["amount_minor"]:
                bad: Optional[str] = None
                if int(event.amount_minor) != expected["amount_minor"]:
                    bad = f"amount {event.amount_minor} != expected {expected['amount_minor']}"
                elif event.currency and str(event.currency).upper() != expected["currency"].upper():
                    bad = f"currency {event.currency} != expected {expected['currency']}"
                if bad:
                    return await self._record_mismatch(db, subscription, event, bad, now)

        from .subscription_resolution import GOVERNING_STATUSES

        becomes_governing = (
            str(update.get("status") or "").lower() in GOVERNING_STATUSES
            and str(subscription.get("status") or "").strip().lower() not in GOVERNING_STATUSES
        )
        if becomes_governing:
            update["governing_since"] = now
        update.update({"updated_at": now, "updated_by": f"system:webhook:{self.provider}"})
        await db.subscriptions.update_one({"_id": subscription["_id"]}, {"$set": update})
        if becomes_governing and subscription.get("converts_trial_id"):
            await self._close_converted_trial(db, subscription, event, now)
        if becomes_governing and subscription.get("replacement_operation_id"):
            # Workstream 1: the target's payment is the authoritative confirmation; the source is
            # now cancelled at the end of its paid period. A failure there is recorded on the
            # operation for review and never undoes this payment's activation.
            try:
                from .monetization_service import MonetizationService

                await MonetizationService(db).complete_replacement(
                    str(subscription["replacement_operation_id"]), actor=f"system:webhook:{self.provider}")
            except Exception:
                logger.exception("Replacement %s could not be completed from the webhook",
                                 subscription.get("replacement_operation_id"))

        # Billing record (financial audit trail).
        record_status = "paid" if event.event_type == "payment.succeeded" else (
            "failed" if event.event_type == "payment.failed" else event.event_type
        )
        await self._insert_billing_record(db, subscription, event, record_status, now)
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

    async def _authenticated(self, db: Any, subscription: Dict[str, Any], event: WebhookEvent,
                             now: datetime) -> Dict[str, Any]:
        """The customer authorised a subscription's mandate (review 10 R10-3). Nothing is paid
        and nothing is activated. For a scheduled replacement target this is the provider's
        authoritative confirmation: the source is cancelled at its cycle end now, before it can
        renew (workstream 1)."""
        if not subscription.get("replacement_operation_id") or str(subscription.get("status") or "") != "pending":
            return {"matched": True, "changed": False, "authenticated": True}
        marked = await db.subscriptions.update_one(
            {"_id": subscription["_id"], "status": "pending", "replacement_authorised_at": {"$exists": False}},
            {"$set": {"replacement_authorised_at": now, "updated_at": now,
                      "updated_by": f"system:webhook:{self.provider}"}})
        try:
            from .monetization_service import MonetizationService

            outcome = await MonetizationService(db).complete_replacement(
                str(subscription["replacement_operation_id"]), actor=f"system:webhook:{self.provider}")
        except Exception:
            logger.exception("Replacement %s could not be completed from the webhook",
                             subscription.get("replacement_operation_id"))
            outcome = {"status": "error"}
        return {"matched": True, "changed": bool(marked.modified_count), "authenticated": True,
                "replacement": outcome.get("status")}

    #: Statuses of a subscription already in paid service: a payment for it is a renewal.
    PAID_SERVICE_STATUSES = frozenset({"active", "pilot", "past_due"})

    async def _enforce_payment_policy(self, db: Any, subscription: Dict[str, Any], event: WebhookEvent,
                                      update: Dict[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
        """``None`` when the payment is verified (the caller applies it); otherwise the
        recorded refusal. With an open financial review a verified payment never clears it -
        only a Super Admin settlement does - so its billing status label is kept."""
        renewal = str(subscription.get("status") or "").strip().lower() in self.PAID_SERVICE_STATUSES
        if renewal and not event.gateway_payment_id:
            # A lifecycle event that carries no payment for a subscription already in paid
            # service: nothing to verify, nothing granted, nothing recorded.
            return {"matched": True, "changed": False, "validation": "no_payment_in_event"}
        expected = await self._expected_charge(db, subscription)
        problem: Optional[str] = None
        if not event.gateway_payment_id:
            problem = "the event carries no payment"
        elif expected is None:
            problem = "expected charge cannot be resolved for the plan and period"
        elif not expected["amount_minor"]:
            problem = "plan is not sold for a price"
        elif event.amount_minor is None or int(event.amount_minor) != expected["amount_minor"]:
            problem = f"amount {event.amount_minor} != expected {expected['amount_minor']}"
        elif not event.currency or str(event.currency).upper() != str(expected["currency"]).upper():
            problem = f"currency {event.currency} != expected {expected['currency']}"
        review = subscription.get("financial_review") if isinstance(subscription.get("financial_review"), dict) else {}
        if problem is None:
            if review.get("state") == "open":
                update.pop("billing_status", None)
            return None
        kind = "renewal" if renewal else "initial"
        outcome = await payment_discrepancies.record(
            db, subscription=subscription, provider=self.provider, kind=kind, reason=problem, event_id=event.event_id,
            gateway_payment_id=event.gateway_payment_id, amount_minor=event.amount_minor, currency=event.currency,
            expected=expected, now=now)
        if renewal:
            if not await self._insert_billing_record(db, subscription, event, "amount_mismatch", now, requires_review=True):
                return {"matched": True, "changed": False, "duplicate_payment": True}
            await AuditEventService(db).emit(
                action="billing.payment_discrepancy_renewal", actor_id=f"system:webhook:{self.provider}",
                resource_type="subscription", resource_id=str(subscription["_id"]),
                organization_id=str(subscription.get("organization_id", "")), project_id=subscription.get("project_id"),
                result="failure", reason="renewal_payment_discrepancy",
                metadata={"requires_admin_review": True, "discrepancy_id": outcome["discrepancy_id"],
                          "validation_error": problem, "event_id": event.event_id,
                          "grace_until": outcome["grace_until"].isoformat() if isinstance(outcome["grace_until"], datetime) else None})
            return {"matched": True, "changed": False, "validation": "financial_review", "detail": problem,
                    "subscription_id": str(subscription["_id"]), "discrepancy_id": outcome["discrepancy_id"]}
        result = await self._record_mismatch(db, subscription, event, problem, now)
        return {**result, "discrepancy_id": outcome["discrepancy_id"]}

    async def _close_converted_trial(self, db: Any, subscription: Dict[str, Any], event: WebhookEvent, now: datetime) -> None:
        """R6-5: payment for a trial conversion confirmed. The paid subscription now governs
        (latest activation); the trial it converts is closed (compare-and-set on it still
        being a trial of the same scope, so a replay or a later event changes nothing)."""
        trial = await db.subscriptions.find_one_and_update(
            {
                "_id": _trial_lookup(subscription["converts_trial_id"]),
                "status": "trial",
                "organization_id": subscription.get("organization_id"),
                "project_id": subscription.get("project_id"),
                # Review 8 B4: the conversion may never have recorded this checkout (an attempt
                # that crashed after creating it): matched on the checkout's converts_trial_id.
                "$or": [{"conversion.subscription_id": str(subscription["_id"])},
                        {"conversion.state": {"$in": ["starting", "failed"]}}],
            },
            {"$set": {"status": "cancelled", "billing_status": "inactive", "cancelled_at": now,
                      "cancellation_reason": "converted_to_paid", "conversion.state": "paid",
                      "conversion.paid_at": now, "updated_at": now,
                      "updated_by": f"system:webhook:{self.provider}"}},
        )
        if trial is None:
            return
        await db.subscription_history.insert_one({
            "subscription_id": str(trial["_id"]),
            "organization_id": str(trial.get("organization_id", "")),
            "project_id": trial.get("project_id"),
            "change_type": "trial_convert",
            "from_status": "trial",
            "to_status": "cancelled",
            "metadata": {"paid_subscription_id": str(subscription["_id"]), "event_id": event.event_id},
            "changed_by": f"system:webhook:{self.provider}",
            "changed_at": now,
        })
        await AuditEventService(db).emit(
            action="subscription.trial_conversion_paid",
            actor_id=f"system:webhook:{self.provider}",
            resource_type="subscription",
            resource_id=str(trial["_id"]),
            organization_id=str(trial.get("organization_id", "")),
            project_id=trial.get("project_id"),
            metadata={"paid_subscription_id": str(subscription["_id"]), "event_id": event.event_id},
        )

    async def _insert_billing_record(
        self, db: Any, subscription: Dict[str, Any], event: WebhookEvent, record_status: str, now: datetime,
        *, requires_review: bool = False,
    ) -> bool:
        """One billing record per payment; False when the payment was already recorded (the
        unique index of migration 20261124_0001 makes the dedupe hold under concurrency)."""
        try:
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
                    "requires_admin_review": requires_review,
                    "created_at": now,
                }
            )
        except DuplicateKeyError:
            return False
        return True

    async def _last_processed_at(self, db: Any, event: WebhookEvent) -> Optional[float]:
        """The receipt time of the last event processed for this gateway id (seconds), the
        order baseline for a subscription that has no high-water mark yet."""
        if not event.gateway_subscription_id:
            return None
        row = await db.billing_webhook_events.find_one(
            {"gateway_subscription_id": event.gateway_subscription_id, "processed": True,
             "event_id": {"$ne": event.event_id}},
            sort=[("received_at", -1)],
        )
        received = (row or {}).get("received_at")
        if isinstance(received, datetime):
            return received.replace(tzinfo=timezone.utc).timestamp() if received.tzinfo is None else received.timestamp()
        return None

    async def _refuse_binding(self, db: Any, event: WebhookEvent, reason: str, subscriptions: Any) -> Dict[str, Any]:
        """A payment event whose gateway binding cannot be trusted changes nothing; the refusal
        is audited (per subscription named) for admin review."""
        logger.error("Billing webhook %s refused: %s (gateway subscription %s)", event.event_id, reason, event.gateway_subscription_id)
        audit = AuditEventService(db)
        for subscription in subscriptions:
            await audit.emit(
                action="billing.webhook.binding_refused",
                actor_id=f"system:webhook:{self.provider}",
                resource_type="subscription",
                resource_id=str(subscription.get("_id")),
                organization_id=str(subscription.get("organization_id", "")),
                project_id=subscription.get("project_id"),
                result="failure",
                reason=reason,
                metadata={
                    "requires_admin_review": True,
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "gateway_subscription_id": event.gateway_subscription_id,
                },
            )
        # Kept for the admin who resolves it: the event is acknowledged (a provider retry would
        # be refused the same way), so its payload is the only copy (review F6).
        await db.billing_webhook_events.update_one(
            {"event_id": event.event_id},
            {"$set": {"refused": reason, "refused_payload": _redacted(event.raw)}},
        )
        return {"matched": False, "refused": reason}

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
