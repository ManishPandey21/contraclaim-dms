"""Abstract payment gateway interface for future integration.

Provides a plug-in architecture so ContraClaim can integrate with Razorpay,
Stripe, or any other payment processor without changing business logic.

The default ``NoOpPaymentGateway`` records actions locally without contacting
any external provider — suitable for internal / manual billing workflows.
"""

from __future__ import annotations

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
# Factory
# ---------------------------------------------------------------------------

def get_payment_gateway(provider: str = "noop") -> PaymentGatewayInterface:
    """Return a payment gateway instance based on the configured provider.

    Extend this factory when integrating real payment gateways:
        if provider == "razorpay":
            return RazorpayGateway(...)
        if provider == "stripe":
            return StripeGateway(...)
    """
    if provider == "noop":
        return NoOpPaymentGateway()
    raise ValueError(f"Unknown payment gateway provider: {provider}")
