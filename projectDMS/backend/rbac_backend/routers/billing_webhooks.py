"""Billing webhook endpoint (Week 3.2).

POST /api/billing/webhooks/{provider}

Intentionally unauthenticated — it is called by the payment provider, not a
logged-in user — but every request is signature-verified against the provider's
webhook secret before any state changes. Processing is idempotent.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from ..core.config import settings
from ..core.database import get_db
from ..services.billing_webhook_service import BillingWebhookService
from ..services.payment_gateway import get_payment_gateway
from ..services.upload_streaming import read_request_body_within_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])

# Provider-specific signature / idempotency headers.
_SIGNATURE_HEADERS = {
    "razorpay": "x-razorpay-signature",
    "stripe": "stripe-signature",
}
_EVENT_ID_HEADERS = {
    "razorpay": "x-razorpay-event-id",
    "stripe": "stripe-id",
}


@router.post("/webhooks/{provider}")
async def handle_billing_webhook(provider: str, request: Request, db=Depends(get_db)):
    # Bounded, not `await request.body()`. This endpoint is unauthenticated by
    # design, so an anonymous caller decided how much the process held; the only
    # limit was the gateway's `client_max_body_size 200m`. The Gate 5 upload
    # guard could not see it because it watched `UploadFile` parameters and this
    # route has none (R-A8U, F-A8T2-5/6 class).
    raw_body = await read_request_body_within_limit(
        request, max_size_bytes=int(settings.WEBHOOK_MAX_BODY_SIZE_KB) * 1024
    )
    signature = request.headers.get(_SIGNATURE_HEADERS.get(provider, "x-webhook-signature"))
    event_id = request.headers.get(_EVENT_ID_HEADERS.get(provider, "x-webhook-event-id"))

    service = BillingWebhookService(db=db, gateway=get_payment_gateway(provider), provider=provider)
    result = await service.process(raw_body=raw_body, signature=signature, event_id_header=event_id)
    logger.info("billing webhook provider=%s result=%s", provider, result.get("status"))
    return result
