"""Abstract payment gateway interface for future integration.

Provides a plug-in architecture so ContraClaim can integrate with Razorpay,
Stripe, or any other payment processor without changing business logic.

The default ``NoOpPaymentGateway`` records actions locally without contacting
any external provider — suitable for internal / manual billing workflows.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .payment_gateway_reliability import (
    GatewayCallPolicy,
    ProviderSubscriptionState,
    bounded_session,
    call_provider,
    policy_from_settings,
    state_from_entity,
)

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

    #: Whether ``update_subscription`` really changes the plan and period the provider bills
    #: (review 8 A1). An adapter that cannot (Razorpay changes plans by a new subscription)
    #: says so, and an in-place upgrade is refused rather than recorded as done.
    supports_in_place_plan_change: bool = True

    async def fetch_subscription_status(self, *, gateway_subscription_id: str) -> Optional[str]:
        """The provider's own status of a subscription, for reconciliation (review 8 C5);
        ``None`` when the adapter cannot read it."""
        return None

    async def fetch_subscription_state(self, *, gateway_subscription_id: str) -> Optional[ProviderSubscriptionState]:
        """The provider's state of a subscription (status and, where the provider gives them,
        the current cycle's end and when it ended); ``None`` when the adapter cannot read it."""
        status = await self.fetch_subscription_status(gateway_subscription_id=gateway_subscription_id)
        return ProviderSubscriptionState(status=str(status).strip().lower()) if status else None

    async def cancel_with_evidence(
        self, *, gateway_subscription_id: str, immediate: bool = False
    ) -> Optional[ProviderSubscriptionState]:
        """Cancel, returning the provider's answer as evidence (workstream 4): for a cycle-end
        cancel, the effective date is the cycle end the provider reports. ``None`` when the
        adapter gives no state; a failure raises."""
        await self.cancel_subscription(gateway_subscription_id=gateway_subscription_id, immediate=immediate)
        return None

    async def find_subscription_by_reference(
        self, *, reference: str, created_after: Optional[datetime] = None
    ) -> Optional[GatewaySubscription]:
        """Reconciliation after an unknown create (workstream 4): the provider subscription
        whose notes carry ``reference``. ``None`` when none is found or the adapter cannot
        search - the caller must then not assume it was never created."""
        return None

    #: Whether :meth:`find_subscription_by_reference` really searches the provider.
    supports_reference_search: bool = False

    #: Whether ``create_subscription(start_at=...)`` makes the provider start billing only at
    #: that time (workstream 1: a replacement starts when the old plan's paid period ends, so
    #: the two never bill the same days). An adapter that cannot guarantee it says False, and
    #: a replacement checkout is refused rather than risk overlapping charges.
    supports_scheduled_start: bool = False

    @abstractmethod
    async def create_customer(
        self, *, organization_id: str, name: str, email: Optional[str] = None
    ) -> GatewayCustomer:
        ...

    @abstractmethod
    async def create_subscription(
        self, *, gateway_customer_id: str, plan_code: str, billing_period: str, metadata: Optional[Dict[str, Any]] = None,
        start_at: Optional[datetime] = None,
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

    #: Records locally only: a scheduled start is whatever the caller records (no charge).
    supports_scheduled_start = True

    async def create_subscription(
        self, *, gateway_customer_id: str, plan_code: str, billing_period: str, metadata: Optional[Dict[str, Any]] = None,
        start_at: Optional[datetime] = None,
    ) -> GatewaySubscription:
        # Unique per call: one gateway subscription id binds exactly one local subscription
        # (2026-11-20), and two checkouts for the same plan (two projects) are two subscriptions.
        gateway_id = f"noop_sub_{gateway_customer_id}_{plan_code}_{uuid.uuid4().hex[:16]}"
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

    def __init__(self, *, key_id: str, key_secret: str, webhook_secret: str = "",
                 client: Any = None, policy: Optional[GatewayCallPolicy] = None) -> None:
        self.key_id = key_id
        self.key_secret = key_secret
        self.webhook_secret = webhook_secret
        self._injected_client = client
        self.policy = policy

    def _client(self):
        if self._injected_client is not None:
            return self._injected_client
        try:
            import razorpay  # lazy: only needed when this provider is active
        except ImportError as exc:  # pragma: no cover - depends on optional dep
            raise RuntimeError(
                "razorpay package is not installed; add it to run PAYMENT_PROVIDER=razorpay"
            ) from exc
        # Workstream 4: every SDK request carries bounded connect/read timeouts.
        return razorpay.Client(auth=(self.key_id, self.key_secret), session=bounded_session(self._policy()))

    def _policy(self) -> GatewayCallPolicy:
        return self.policy or policy_from_settings()

    async def _call(self, fn: Callable[[], Any], *, operation: str, mutating: bool) -> Any:
        """The SDK is synchronous: run off the event loop, bounded and classified."""
        return await call_provider(fn, operation=operation, mutating=mutating, policy=self._policy())

    # Razorpay cannot swap a subscription's plan in place (see update_subscription).
    supports_in_place_plan_change = False
    supports_reference_search = True
    # Razorpay documents ``start_at`` on subscription creation, but whether the authorisation
    # payment and the first charge then wait for it has not been verified in Razorpay test
    # mode (workstream 1). Until it is, and the change is reviewed, replacements are refused.
    supports_scheduled_start = False

    async def fetch_subscription_status(self, *, gateway_subscription_id: str) -> Optional[str]:
        state = await self.fetch_subscription_state(gateway_subscription_id=gateway_subscription_id)
        return state.status if state else None

    async def fetch_subscription_state(self, *, gateway_subscription_id: str) -> Optional[ProviderSubscriptionState]:
        resp = await self._call(lambda: self._client().subscription.fetch(gateway_subscription_id),
                                operation="subscription.fetch", mutating=False)
        return state_from_entity(resp)

    async def cancel_with_evidence(
        self, *, gateway_subscription_id: str, immediate: bool = False
    ) -> Optional[ProviderSubscriptionState]:
        resp = await self._call(
            lambda: self._client().subscription.cancel(gateway_subscription_id, {"cancel_at_cycle_end": 0 if immediate else 1}),
            operation="subscription.cancel", mutating=True)
        return state_from_entity(resp)

    def _list_subscriptions(self, query: Dict[str, Any]) -> Any:
        return self._client().subscription.all(query)

    async def find_subscription_by_reference(
        self, *, reference: str, created_after: Optional[datetime] = None
    ) -> Optional[GatewaySubscription]:
        """Bounded scan of recent subscriptions for ``notes.checkout_reference`` (5 pages of
        100). Not finding one is not proof it was never created: the caller keeps it listed."""
        since = int(created_after.timestamp()) if created_after else None
        for page in range(5):
            query: Dict[str, Any] = {"count": 100, "skip": page * 100, **({"from": since} if since else {})}
            resp = await self._call(functools.partial(self._list_subscriptions, query),
                                    operation="subscription.all", mutating=False)
            items = (resp or {}).get("items") or []
            for entity in items:
                notes = entity.get("notes") if isinstance(entity.get("notes"), dict) else {}
                if str(notes.get("checkout_reference") or "") == reference:
                    return GatewaySubscription(
                        gateway_subscription_id=str(entity.get("id")), gateway_customer_id=str(entity.get("customer_id") or ""),
                        plan_id=str(entity.get("plan_id") or ""), status=str(entity.get("status") or "created"),
                        metadata={"short_url": entity.get("short_url"), **notes},
                    )
            if len(items) < 100:
                break
        return None

    async def create_customer(
        self, *, organization_id: str, name: str, email: Optional[str] = None
    ) -> GatewayCustomer:
        resp = await self._call(lambda: self._client().customer.create(
            {"name": name, "email": email or "", "fail_existing": 0, "notes": {"organization_id": organization_id}}
        ), operation="customer.create", mutating=True)
        return GatewayCustomer(gateway_customer_id=resp["id"], email=email, name=name, metadata=resp)

    async def create_subscription(
        self, *, gateway_customer_id: str, plan_code: str, billing_period: str, metadata: Optional[Dict[str, Any]] = None,
        start_at: Optional[datetime] = None,
    ) -> GatewaySubscription:
        if start_at is not None and not self.supports_scheduled_start:
            raise ValueError("Scheduled start is not verified for Razorpay; a replacement checkout is refused")
        total_count = {"monthly": 12, "quarterly": 4, "semi_annual": 2, "annual": 1}.get(billing_period, 12)
        resp = await self._call(lambda: self._client().subscription.create(
            {
                "plan_id": plan_code,
                "customer_id": gateway_customer_id,
                "total_count": total_count,
                "customer_notify": 1,
                "notes": metadata or {},
            }
        ), operation="subscription.create", mutating=True)
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
        resp = await self._call(lambda: self._client().subscription.fetch(gateway_subscription_id),
                                operation="subscription.fetch", mutating=False)
        return GatewaySubscription(
            gateway_subscription_id=gateway_subscription_id,
            gateway_customer_id=resp.get("customer_id", ""),
            plan_id=new_plan_code or resp.get("plan_id", ""),
            status=resp.get("status", "active"),
            billing_period=new_billing_period or "monthly",
        )

    async def cancel_subscription(self, *, gateway_subscription_id: str, immediate: bool = False) -> bool:
        await self.cancel_with_evidence(gateway_subscription_id=gateway_subscription_id, immediate=immediate)
        return True

    async def reactivate_subscription(self, *, gateway_subscription_id: str) -> GatewaySubscription:
        await self._call(lambda: self._client().subscription.resume(gateway_subscription_id, {"resume_at": "now"}),
                         operation="subscription.resume", mutating=True)
        resp = await self._call(lambda: self._client().subscription.fetch(gateway_subscription_id),
                                operation="subscription.fetch", mutating=False)
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
        resp = await self._call(lambda: self._client().invoice.create(
            {
                "type": "invoice",
                "subscription_id": gateway_subscription_id,
                "currency": currency,
                "line_items": [
                    {"name": item.get("name", "Charge"), "amount": int(item.get("amount_minor", 0)), "quantity": 1}
                    for item in line_items
                ],
            }
        ), operation="invoice.create", mutating=True)
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

    supports_in_place_plan_change = False

    def __init__(self, *, api_key: str = "", webhook_secret: str = "") -> None:
        self.api_key = api_key
        self.webhook_secret = webhook_secret

    def _not_implemented(self):
        raise NotImplementedError(
            "Stripe adapter is a stub for v1; use PAYMENT_PROVIDER=razorpay or noop"
        )

    async def create_customer(self, *, organization_id, name, email=None):
        self._not_implemented()

    async def create_subscription(self, *, gateway_customer_id, plan_code, billing_period, metadata=None, start_at=None):
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
