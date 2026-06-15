"""Abstract payment gateway interface for future integration.

Provides a plug-in architecture so ContraClaim can integrate with Razorpay,
Stripe, or any other payment processor without changing business logic.

The default ``NoOpPaymentGateway`` records actions locally without contacting
any external provider — suitable for internal / manual billing workflows.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class GatewayCustomer:
    """Represents a customer on the payment gateway side."""
    gateway_customer_id: str
    email: Optional[str] = None
    name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewaySubscription:
    """Represents a subscription on the payment gateway side."""
    gateway_subscription_id: str
    gateway_customer_id: str
    plan_id: str
    status: str = "active"
    billing_period: str = "monthly"
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayInvoice:
    """Represents an invoice on the payment gateway side."""
    gateway_invoice_id: str
    gateway_subscription_id: str
    amount_minor: int = 0
    currency: str = "INR"
    status: str = "draft"
    line_items: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayPaymentResult:
    """Result of a payment attempt."""
    success: bool
    gateway_payment_id: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WebhookEvent:
    """Provider-agnostic normalization of an inbound billing webhook event.

    ``event_type`` is normalized to one of:
    ``payment.succeeded``, ``payment.failed``, ``subscription.activated``,
    ``subscription.cancelled``, ``subscription.halted``, or ``ignored``.
    ``event_id`` is the provider's idempotency key.
    """
    event_id: str
    event_type: str
    gateway_subscription_id: Optional[str] = None
    gateway_payment_id: Optional[str] = None
    gateway_customer_id: Optional[str] = None
    amount_minor: int = 0
    currency: str = "INR"
    status: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class PaymentGatewayInterface(ABC):
    """Contract that all payment gateway adapters must implement."""

    @abstractmethod
    async def create_customer(
        self, *, organization_id: str, name: str, email: Optional[str] = None
    ) -> GatewayCustomer:
        ...

    @abstractmethod
    async def create_subscription(
        self, *, gateway_customer_id: str, plan_code: str, billing_period: str, metadata: Optional[Dict[str, Any]] = None
    ) -> GatewaySubscription:
        ...

    @abstractmethod
    async def update_subscription(
        self, *, gateway_subscription_id: str, new_plan_code: Optional[str] = None, new_billing_period: Optional[str] = None
    ) -> GatewaySubscription:
        ...

    @abstractmethod
    async def cancel_subscription(self, *, gateway_subscription_id: str, immediate: bool = False) -> bool:
        ...

    @abstractmethod
    async def reactivate_subscription(self, *, gateway_subscription_id: str) -> GatewaySubscription:
        ...

    @abstractmethod
    async def create_invoice(
        self, *, gateway_subscription_id: str, line_items: List[Dict[str, Any]], currency: str = "INR"
    ) -> GatewayInvoice:
        ...

    @abstractmethod
    async def charge_invoice(self, *, gateway_invoice_id: str) -> GatewayPaymentResult:
        ...

    # -- Webhook handling (concrete; override in real adapters) --------------

    def verify_webhook_signature(self, *, payload: bytes, signature: Optional[str]) -> bool:
        """Verify an inbound webhook signature. Deny-by-default."""
        return False

    def parse_webhook_event(
        self, *, payload: Dict[str, Any], event_id: Optional[str] = None
    ) -> Optional[WebhookEvent]:
        """Normalize a provider webhook body into a WebhookEvent, or None to ignore."""
        return None


# ---------------------------------------------------------------------------
# No-op implementation (internal / manual billing)
# ---------------------------------------------------------------------------

class NoOpPaymentGateway(PaymentGatewayInterface):
    """Default gateway that records actions locally without external calls.

    Suitable for:
    - Development / testing environments
    - Manual invoicing workflows
    - Deployments where billing is handled outside the DMS
    """

    async def create_customer(
        self, *, organization_id: str, name: str, email: Optional[str] = None
    ) -> GatewayCustomer:
        gateway_id = f"noop_cust_{organization_id}"
        logger.info("NoOpPaymentGateway: create_customer(%s)", gateway_id)
        return GatewayCustomer(
            gateway_customer_id=gateway_id, email=email, name=name
        )

    async def create_subscription(
        self, *, gateway_customer_id: str, plan_code: str, billing_period: str, metadata: Optional[Dict[str, Any]] = None
    ) -> GatewaySubscription:
        gateway_id = f"noop_sub_{gateway_customer_id}_{plan_code}"
        logger.info("NoOpPaymentGateway: create_subscription(%s)", gateway_id)
        return GatewaySubscription(
            gateway_subscription_id=gateway_id,
            gateway_customer_id=gateway_customer_id,
            plan_id=plan_code,
            billing_period=billing_period,
            metadata=metadata or {},
        )

    async def update_subscription(
        self, *, gateway_subscription_id: str, new_plan_code: Optional[str] = None, new_billing_period: Optional[str] = None
    ) -> GatewaySubscription:
        logger.info("NoOpPaymentGateway: update_subscription(%s)", gateway_subscription_id)
        return GatewaySubscription(
            gateway_subscription_id=gateway_subscription_id,
            gateway_customer_id="",
            plan_id=new_plan_code or "",
            billing_period=new_billing_period or "monthly",
        )

    async def cancel_subscription(self, *, gateway_subscription_id: str, immediate: bool = False) -> bool:
        logger.info("NoOpPaymentGateway: cancel_subscription(%s, immediate=%s)", gateway_subscription_id, immediate)
        return True

    async def reactivate_subscription(self, *, gateway_subscription_id: str) -> GatewaySubscription:
        logger.info("NoOpPaymentGateway: reactivate_subscription(%s)", gateway_subscription_id)
        return GatewaySubscription(
            gateway_subscription_id=gateway_subscription_id,
            gateway_customer_id="",
            plan_id="",
            status="active",
        )

    async def create_invoice(
        self, *, gateway_subscription_id: str, line_items: List[Dict[str, Any]], currency: str = "INR"
    ) -> GatewayInvoice:
        gateway_id = f"noop_inv_{gateway_subscription_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        total = sum(item.get("amount_minor", 0) for item in line_items)
        logger.info("NoOpPaymentGateway: create_invoice(%s, total=%d)", gateway_id, total)
        return GatewayInvoice(
            gateway_invoice_id=gateway_id,
            gateway_subscription_id=gateway_subscription_id,
            amount_minor=total,
            currency=currency,
            status="draft",
            line_items=line_items,
        )

    async def charge_invoice(self, *, gateway_invoice_id: str) -> GatewayPaymentResult:
        logger.info("NoOpPaymentGateway: charge_invoice(%s) — auto-approved", gateway_invoice_id)
        return GatewayPaymentResult(
            success=True,
            gateway_payment_id=f"noop_pay_{gateway_invoice_id}",
        )


# ---------------------------------------------------------------------------
# Razorpay (primary integration)
# ---------------------------------------------------------------------------

class RazorpayGateway(PaymentGatewayInterface):
    """Razorpay adapter. SDK is imported lazily so the package is only required
    when ``PAYMENT_PROVIDER=razorpay`` is actually configured."""

    # event-name -> normalized event_type
    _EVENT_MAP = {
        "subscription.charged": "payment.succeeded",
        "payment.captured": "payment.succeeded",
        "order.paid": "payment.succeeded",
        "subscription.activated": "subscription.activated",
        "subscription.authenticated": "subscription.activated",
        "subscription.cancelled": "subscription.cancelled",
        "subscription.completed": "subscription.cancelled",
        "subscription.halted": "subscription.halted",
        "subscription.pending": "subscription.halted",
        "payment.failed": "payment.failed",
    }

    def __init__(self, *, key_id: str, key_secret: str, webhook_secret: str = "") -> None:
        self.key_id = key_id
        self.key_secret = key_secret
        self.webhook_secret = webhook_secret

    def _client(self):
        try:
            import razorpay  # lazy: only needed when this provider is active
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise RuntimeError(
                "razorpay package is not installed; add it to run PAYMENT_PROVIDER=razorpay"
            ) from exc
        return razorpay.Client(auth=(self.key_id, self.key_secret))

    async def create_customer(
        self, *, organization_id: str, name: str, email: Optional[str] = None
    ) -> GatewayCustomer:
        resp = self._client().customer.create(
            {"name": name, "email": email or "", "fail_existing": 0, "notes": {"organization_id": organization_id}}
        )
        return GatewayCustomer(gateway_customer_id=resp["id"], email=email, name=name, metadata=resp)

    async def create_subscription(
        self, *, gateway_customer_id: str, plan_code: str, billing_period: str, metadata: Optional[Dict[str, Any]] = None
    ) -> GatewaySubscription:
        total_count = {"monthly": 12, "quarterly": 4, "semi_annual": 2, "annual": 1}.get(billing_period, 12)
        resp = self._client().subscription.create(
            {
                "plan_id": plan_code,
                "customer_id": gateway_customer_id,
                "total_count": total_count,
                "customer_notify": 1,
                "notes": metadata or {},
            }
        )
        return GatewaySubscription(
            gateway_subscription_id=resp["id"],
            gateway_customer_id=gateway_customer_id,
            plan_id=plan_code,
            status=resp.get("status", "created"),
            billing_period=billing_period,
            metadata={"short_url": resp.get("short_url"), **(metadata or {})},
        )

    async def update_subscription(
        self, *, gateway_subscription_id: str, new_plan_code: Optional[str] = None, new_billing_period: Optional[str] = None
    ) -> GatewaySubscription:
        # Razorpay does not support in-place plan swaps; plan changes are handled
        # at the service layer via cancel + re-create. Return current state.
        resp = self._client().subscription.fetch(gateway_subscription_id)
        return GatewaySubscription(
            gateway_subscription_id=gateway_subscription_id,
            gateway_customer_id=resp.get("customer_id", ""),
            plan_id=new_plan_code or resp.get("plan_id", ""),
            status=resp.get("status", "active"),
            billing_period=new_billing_period or "monthly",
        )

    async def cancel_subscription(self, *, gateway_subscription_id: str, immediate: bool = False) -> bool:
        self._client().subscription.cancel(
            gateway_subscription_id, {"cancel_at_cycle_end": 0 if immediate else 1}
        )
        return True

    async def reactivate_subscription(self, *, gateway_subscription_id: str) -> GatewaySubscription:
        self._client().subscription.resume(gateway_subscription_id, {"resume_at": "now"})
        resp = self._client().subscription.fetch(gateway_subscription_id)
        return GatewaySubscription(
            gateway_subscription_id=gateway_subscription_id,
            gateway_customer_id=resp.get("customer_id", ""),
            plan_id=resp.get("plan_id", ""),
            status=resp.get("status", "active"),
        )

    async def create_invoice(
        self, *, gateway_subscription_id: str, line_items: List[Dict[str, Any]], currency: str = "INR"
    ) -> GatewayInvoice:
        total = sum(int(item.get("amount_minor", 0)) for item in line_items)
        resp = self._client().invoice.create(
            {
                "type": "invoice",
                "subscription_id": gateway_subscription_id,
                "currency": currency,
                "line_items": [
                    {"name": item.get("name", "Charge"), "amount": int(item.get("amount_minor", 0)), "quantity": 1}
                    for item in line_items
                ],
            }
        )
        return GatewayInvoice(
            gateway_invoice_id=resp["id"],
            gateway_subscription_id=gateway_subscription_id,
            amount_minor=total,
            currency=currency,
            status=resp.get("status", "issued"),
            line_items=line_items,
        )

    async def charge_invoice(self, *, gateway_invoice_id: str) -> GatewayPaymentResult:
        # Razorpay subscriptions auto-charge on their schedule; explicit charge is
        # surfaced via webhooks rather than a synchronous call here.
        return GatewayPaymentResult(
            success=False,
            error_message="Razorpay charges are processed asynchronously via subscription webhooks",
        )

    def verify_webhook_signature(self, *, payload: bytes, signature: Optional[str]) -> bool:
        if not self.webhook_secret or not signature:
            return False
        expected = hmac.new(self.webhook_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        try:
            return hmac.compare_digest(expected, str(signature))
        except Exception:  # pragma: no cover - defensive
            return False

    def parse_webhook_event(
        self, *, payload: Dict[str, Any], event_id: Optional[str] = None
    ) -> Optional[WebhookEvent]:
        event = str(payload.get("event") or "")
        body = payload.get("payload") or {}
        sub_entity = (body.get("subscription") or {}).get("entity") or {}
        pay_entity = (body.get("payment") or {}).get("entity") or {}
        gateway_subscription_id = sub_entity.get("id") or pay_entity.get("subscription_id")
        gateway_payment_id = pay_entity.get("id")
        gateway_customer_id = sub_entity.get("customer_id") or pay_entity.get("customer_id")
        normalized = self._EVENT_MAP.get(event, "ignored")
        eid = event_id or payload.get("id") or f"{event}:{gateway_payment_id or gateway_subscription_id or ''}"
        return WebhookEvent(
            event_id=str(eid),
            event_type=normalized,
            gateway_subscription_id=gateway_subscription_id,
            gateway_payment_id=gateway_payment_id,
            gateway_customer_id=gateway_customer_id,
            amount_minor=int(pay_entity.get("amount") or 0),
            currency=pay_entity.get("currency") or "INR",
            status=sub_entity.get("status") or pay_entity.get("status"),
            raw=payload,
        )


# ---------------------------------------------------------------------------
# Stripe (documented stub — kept behind the factory for a future v2)
# ---------------------------------------------------------------------------

class StripeGateway(PaymentGatewayInterface):
    """Stub kept behind the factory. Implement the SDK calls when Stripe goes live."""

    def __init__(self, *, api_key: str = "", webhook_secret: str = "") -> None:
        self.api_key = api_key
        self.webhook_secret = webhook_secret

    def _not_implemented(self):
        raise NotImplementedError(
            "Stripe adapter is a stub for v1; use PAYMENT_PROVIDER=razorpay or noop"
        )

    async def create_customer(self, *, organization_id, name, email=None):
        self._not_implemented()

    async def create_subscription(self, *, gateway_customer_id, plan_code, billing_period, metadata=None):
        self._not_implemented()

    async def update_subscription(self, *, gateway_subscription_id, new_plan_code=None, new_billing_period=None):
        self._not_implemented()

    async def cancel_subscription(self, *, gateway_subscription_id, immediate=False):
        self._not_implemented()

    async def reactivate_subscription(self, *, gateway_subscription_id):
        self._not_implemented()

    async def create_invoice(self, *, gateway_subscription_id, line_items, currency="USD"):
        self._not_implemented()

    async def charge_invoice(self, *, gateway_invoice_id):
        self._not_implemented()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_payment_gateway(provider: Optional[str] = None) -> PaymentGatewayInterface:
    """Return a payment gateway instance based on the configured provider.

    Defaults to the ``PAYMENT_PROVIDER`` setting when no provider is passed.
    """
    from ..core.config import settings

    resolved = str(provider or settings.PAYMENT_PROVIDER or "noop").lower()
    if resolved == "noop":
        return NoOpPaymentGateway()
    if resolved == "razorpay":
        return RazorpayGateway(
            key_id=settings.RAZORPAY_KEY_ID,
            key_secret=settings.RAZORPAY_KEY_SECRET,
            webhook_secret=settings.RAZORPAY_WEBHOOK_SECRET,
        )
    if resolved == "stripe":
        return StripeGateway(
            api_key=settings.STRIPE_API_KEY,
            webhook_secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    raise ValueError(f"Unknown payment gateway provider: {resolved}")
