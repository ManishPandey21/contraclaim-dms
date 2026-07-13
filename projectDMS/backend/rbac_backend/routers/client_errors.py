"""Public client-error telemetry sink.

The browser POSTs caught render / chunk-load / runtime errors here so that
production failures are visible server-side. Previously ``logError`` on the
client only wrote to the browser console, so an error was invisible unless a
user happened to have devtools open (that is how the stale-deploy chunk-load
failure went unnoticed).

Design:
- Unauthenticated on purpose: an error in the app shell or on the login page
  (before any token exists) must still be reportable.
- IP rate-limited and size-capped so it cannot be abused as a write amplifier.
- Logged, not persisted: this stays a pure telemetry surface and never becomes
  an unbounded or PII-heavy collection. Point log shipping at it downstream.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger("client_errors")
router = APIRouter(prefix="/client-errors", tags=["client-errors"])
# Errors can legitimately burst (a bad deploy hits many users at once), so the
# window is generous; the cap only exists to blunt deliberate abuse.
rate_limiter = RateLimiter(max_requests=60, window_seconds=60, scope="client_errors")


class ClientErrorReport(BaseModel):
    """A single client-side error beacon. All fields are length-capped so a
    malformed or hostile payload cannot bloat the logs."""

    message: str = Field(..., max_length=2000)
    scope: Optional[str] = Field(default=None, max_length=120)
    action: Optional[str] = Field(default=None, max_length=120)
    stack: Optional[str] = Field(default=None, max_length=8000)
    component_stack: Optional[str] = Field(default=None, max_length=8000)
    url: Optional[str] = Field(default=None, max_length=2000)
    user_agent: Optional[str] = Field(default=None, max_length=500)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def report_client_error(payload: ClientErrorReport, request: Request) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    # Rate-limit abusive senders. On excess this raises 429, which the client
    # beacon swallows — reporting is best-effort and must never surface to users.
    await rate_limiter.check_ip_limit(
        client_ip, cost=1, window_seconds=60, max_requests=60
    )

    logger.warning(
        "client_error scope=%s action=%s url=%s ip=%s message=%s",
        payload.scope or "unknown",
        payload.action or "unknown",
        payload.url or "unknown",
        client_ip,
        payload.message,
        extra={
            "client_error": {
                "scope": payload.scope,
                "action": payload.action,
                "url": payload.url,
                "user_agent": payload.user_agent,
                "component_stack": payload.component_stack,
                "stack": payload.stack,
                "client_ip": client_ip,
            }
        },
    )
    return {"status": "recorded"}
