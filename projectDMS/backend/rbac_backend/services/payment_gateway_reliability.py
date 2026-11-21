"""Bounded, outcome-aware calls to a payment provider (workstream 4, owner decision 2026-11-21).

Before this the Razorpay adapter called the synchronous SDK straight from async code with no
timeout: a slow provider held the event loop and the request for as long as the socket
stayed open, and every failure - a refused connection, a ``400`` refusal, a read timeout -
looked the same to the service.

Every provider call now runs in a worker thread with a bounded connect and read timeout and
an overall deadline, and its failure is classified by what it proves about the provider:

``not_sent``  the request provably never reached the provider (connect timeout, refused or
              unresolvable connection). Nothing changed there; safe to retry.
``rejected``  the provider answered and refused (``4xx``). Nothing changed there; not retried.
``unknown``   the request may have been applied (read timeout, connection lost after send,
              ``5xx``, the overall deadline). **Never retried for a mutating call**: the
              caller records ``outcome_unknown`` and reconciles against the provider's own
              state, reusing the same operation - it never starts a second create or charge
              under a new key because a response timed out.

Reads (``mutating=False``) are retried a bounded number of times with backoff; a mutating
call is retried only when the failure proves the request was not sent.

Razorpay cycle-end cancellation (review 9 R9-11). Razorpay's documentation says a
subscription cancelled at the end of its billing cycle keeps its status until the cycle ends,
and documents no field that marks such a cancellation as scheduled. So ``active`` before the
effective date is not evidence of a failed cancellation: :func:`interpret_cancellation`
decides from our own durable record of the confirmed request and its effective date, and
reports an unconfirmed or overdue cancellation for review instead of guessing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

NOT_SENT = "not_sent"
REJECTED = "rejected"
UNKNOWN = "unknown"


class GatewayCallError(Exception):
    """A provider call failed; ``outcome`` says what that proves (see the module docstring)."""

    outcome = UNKNOWN

    def __init__(self, message: str, *, cause_type: Optional[str] = None) -> None:
        super().__init__(message)
        self.cause_type = cause_type


class GatewayCallNotSent(GatewayCallError):
    outcome = NOT_SENT


class GatewayRejected(GatewayCallError):
    outcome = REJECTED


class GatewayOutcomeUnknown(GatewayCallError):
    outcome = UNKNOWN


class GatewayUnavailable(GatewayCallError):
    """A read could not be answered (after its bounded retries). Nothing was changed by it."""

    outcome = NOT_SENT


@dataclass(frozen=True)
class GatewayCallPolicy:
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    read_retries: int = 2
    not_sent_retries: int = 1
    backoff: float = 0.5

    @property
    def deadline(self) -> float:
        """Overall bound of one attempt: the socket timeouts plus scheduling slack."""
        return self.connect_timeout + self.read_timeout + 2.0


def policy_from_settings() -> GatewayCallPolicy:
    from ..core.config import settings

    def number(name: str, default: float, low: float, high: float) -> float:
        try:
            value = float(getattr(settings, name, default))
        except (TypeError, ValueError):
            value = default
        return min(max(value, low), high)  # bounded whatever is configured

    return GatewayCallPolicy(
        connect_timeout=number("PAYMENT_GATEWAY_CONNECT_TIMEOUT_SECONDS", 5.0, 0.1, 30.0),
        read_timeout=number("PAYMENT_GATEWAY_READ_TIMEOUT_SECONDS", 15.0, 0.1, 60.0),
        read_retries=int(number("PAYMENT_GATEWAY_READ_RETRIES", 2, 0, 5)),
        not_sent_retries=int(number("PAYMENT_GATEWAY_NOT_SENT_RETRIES", 1, 0, 3)),
        backoff=number("PAYMENT_GATEWAY_RETRY_BACKOFF_SECONDS", 0.5, 0.0, 10.0),
    )


def bounded_session(policy: GatewayCallPolicy) -> Any:
    """A ``requests.Session`` whose every request carries the policy's timeouts unless the
    caller passes its own (the Razorpay SDK accepts an injected session)."""
    import requests

    class _BoundedSession(requests.Session):
        def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            if kwargs.get("timeout") is None:
                kwargs["timeout"] = (policy.connect_timeout, policy.read_timeout)
            return super().request(method, url, *args, **kwargs)

    return _BoundedSession()


def _http_status(exc: BaseException) -> Optional[int]:
    for holder in (exc, getattr(exc, "response", None)):
        for name in ("status_code", "status"):
            value = getattr(holder, name, None)
            if isinstance(value, int):
                return value
    return None


def classify(exc: BaseException) -> str:
    """What a provider-call failure proves. Unrecognised failures are ``unknown``: the safe
    reading is that the request may have been applied."""
    if isinstance(exc, GatewayCallError):
        return exc.outcome
    if isinstance(exc, ConnectionRefusedError):
        return NOT_SENT
    try:
        import requests
    except ImportError:  # pragma: no cover - requests is a base dependency
        requests = None  # type: ignore[assignment]
    if requests is not None:
        if isinstance(exc, requests.exceptions.ConnectTimeout):
            return NOT_SENT
        if isinstance(exc, requests.exceptions.ConnectionError) and not isinstance(exc, requests.exceptions.ReadTimeout):
            reason = getattr(exc.args[0], "reason", None) if exc.args else None
            name = type(reason).__name__ if reason is not None else ""
            # urllib3 raises these before a single byte is written.
            if name in {"NewConnectionError", "NameResolutionError", "ConnectTimeoutError"}:
                return NOT_SENT
            return UNKNOWN
        if isinstance(exc, requests.exceptions.Timeout):
            return UNKNOWN  # read timeout: the request was sent
    # Razorpay SDK errors, matched by name (the SDK is optional): BadRequestError is a 4xx.
    if type(exc).__name__ == "BadRequestError":
        return REJECTED
    status = _http_status(exc)
    if status is not None and 400 <= status < 500 and status not in {408, 409, 425, 429}:
        return REJECTED
    return UNKNOWN


async def call_provider(
    fn: Callable[[], T],
    *,
    operation: str,
    mutating: bool,
    policy: Optional[GatewayCallPolicy] = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> T:
    """Run one synchronous provider call off the event loop, bounded, classified and retried
    per the module rules. Raises a :class:`GatewayCallError` subclass on failure."""
    policy = policy or policy_from_settings()
    retries_left = policy.not_sent_retries if mutating else policy.read_retries
    attempt = 0
    while True:
        attempt += 1
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout=policy.deadline)
        except asyncio.TimeoutError as exc:
            # The worker thread may still complete the request: never "not sent".
            outcome, cause = UNKNOWN, "DeadlineExceeded"
            error: BaseException = exc
        except Exception as exc:  # classified below; nothing is swallowed
            outcome, cause, error = classify(exc), type(exc).__name__, exc
        retryable = outcome == NOT_SENT or (not mutating and outcome == UNKNOWN)
        logger.warning("payment provider %s attempt %d failed: %s (%s)", operation, attempt, outcome, cause)
        if retryable and retries_left > 0:
            retries_left -= 1
            await sleep(policy.backoff * attempt)
            continue
        if not mutating and outcome != REJECTED:
            raise GatewayUnavailable(f"The payment provider did not answer {operation}", cause_type=cause) from error
        kind = {NOT_SENT: GatewayCallNotSent, REJECTED: GatewayRejected}.get(outcome, GatewayOutcomeUnknown)
        raise kind(f"The payment provider {operation}: {outcome}", cause_type=cause) from error


# ---------------------------------------------------------------------------
# Provider subscription state and cancellation interpretation
# ---------------------------------------------------------------------------

#: Provider statuses that end billing.
PROVIDER_ENDED = frozenset({"cancelled", "completed", "expired"})
#: Provider statuses under which a subscription may still be charged.
PROVIDER_BILLABLE = frozenset({"created", "authenticated", "active", "pending", "halted", "paused"})


@dataclass(frozen=True)
class ProviderSubscriptionState:
    """The provider's view of a subscription, kept to what reconciliation needs."""

    status: Optional[str]
    current_end: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    plan_id: Optional[str] = None
    has_scheduled_changes: Optional[bool] = None

    def evidence(self) -> Dict[str, Any]:
        """Stored with the operation as financial evidence (no personal or card data)."""
        return {"status": self.status, "current_end": self.current_end, "ended_at": self.ended_at,
                "plan_id": self.plan_id, "has_scheduled_changes": self.has_scheduled_changes}


def epoch(value: Any) -> Optional[datetime]:
    if value in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def state_from_entity(entity: Any) -> Optional[ProviderSubscriptionState]:
    if not isinstance(entity, dict):
        return None
    status = str(entity.get("status") or "").strip().lower() or None
    scheduled = entity.get("has_scheduled_changes")
    return ProviderSubscriptionState(
        status=status, current_end=epoch(entity.get("current_end")), ended_at=epoch(entity.get("ended_at")),
        plan_id=str(entity.get("plan_id") or "") or None,
        has_scheduled_changes=scheduled if isinstance(scheduled, bool) else None,
    )


#: Interpretations of a cancellation against the provider's state.
CANCELLED = "cancelled"                  # the provider ended it
SCHEDULED = "scheduled"                  # confirmed cycle-end request, effective date not yet passed
OVERDUE = "still_billable_after_effective_date"   # should have ended; provider may still bill
NOT_APPLIED = "not_applied"              # an immediate cancel the provider does not show
UNCONFIRMED = "unconfirmed"              # no confirmed request on record; provider still billable
UNREADABLE = "unreadable"                # provider state could not be read
UNRECOGNISED = "unrecognised"            # a provider status this code does not know

DEFAULT_EFFECTIVE_TOLERANCE = timedelta(hours=24)


def interpret_cancellation(
    state: Optional[ProviderSubscriptionState],
    *,
    requested_mode: Optional[str],
    request_confirmed: bool,
    effective_at: Optional[datetime],
    now: datetime,
    tolerance: timedelta = DEFAULT_EFFECTIVE_TOLERANCE,
) -> str:
    """Read the provider's state of a cancellation without guessing (review 9 R9-11).

    ``requested_mode`` is ``"cycle_end"`` or ``"immediate"``; ``request_confirmed`` is True
    only when the provider answered the cancel request successfully (recorded durably). An
    active subscription is a *scheduled* cancellation only with a confirmed cycle-end request
    and an effective date not yet passed (plus ``tolerance`` for the provider's own batch).
    """
    if state is None or state.status is None:
        return UNREADABLE
    status = state.status
    if status in PROVIDER_ENDED:
        return CANCELLED
    if status not in PROVIDER_BILLABLE:
        return UNRECOGNISED
    if not request_confirmed:
        return UNCONFIRMED
    if requested_mode == "immediate":
        return NOT_APPLIED
    if effective_at is None:
        return UNCONFIRMED  # a confirmed request with no effective date cannot be judged
    if now <= effective_at + tolerance:
        return SCHEDULED
    return OVERDUE


__all__ = [
    "CANCELLED", "DEFAULT_EFFECTIVE_TOLERANCE", "GatewayCallError", "GatewayCallNotSent", "GatewayCallPolicy",
    "GatewayOutcomeUnknown", "GatewayRejected", "GatewayUnavailable", "NOT_APPLIED", "NOT_SENT", "OVERDUE",
    "PROVIDER_BILLABLE", "PROVIDER_ENDED", "ProviderSubscriptionState", "REJECTED", "SCHEDULED", "UNCONFIRMED",
    "UNKNOWN", "UNREADABLE", "UNRECOGNISED", "bounded_session", "call_provider", "classify", "epoch",
    "interpret_cancellation", "policy_from_settings", "state_from_entity",
]
