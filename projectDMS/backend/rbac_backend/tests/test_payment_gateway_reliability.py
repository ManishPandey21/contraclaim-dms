"""Workstream 4: bounded provider calls, outcome classification, retries and the Razorpay
cycle-end cancellation reading. No network: the Razorpay SDK client is a fake."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest
import requests
from urllib3.exceptions import NewConnectionError

from rbac_backend.services import payment_gateway_reliability as rel
from rbac_backend.services.payment_gateway import RazorpayGateway

FAST = rel.GatewayCallPolicy(connect_timeout=0.05, read_timeout=0.2, read_retries=2, not_sent_retries=1, backoff=0.0)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class BadRequestError(Exception):
    """Named as the Razorpay SDK's 4xx error."""


class _Subscriptions:
    def __init__(self, owner: "FakeClient") -> None:
        self.owner = owner

    def _answer(self, name: str, *args: Any) -> Any:
        self.owner.calls.append((name, *args))
        behaviour = self.owner.behaviour.get(name)
        if isinstance(behaviour, list):
            behaviour = behaviour.pop(0) if behaviour else None
        if isinstance(behaviour, float):
            time.sleep(behaviour)  # a hung socket
            return {"id": args[0] if args else "x", "status": "active"}
        if isinstance(behaviour, BaseException):
            raise behaviour
        if callable(behaviour):
            return behaviour(*args)
        return behaviour if behaviour is not None else {"id": args[0] if args else "x", "status": "active"}

    def fetch(self, sub_id: str) -> Any:
        return self._answer("fetch", sub_id)

    def cancel(self, sub_id: str, body: Dict[str, Any]) -> Any:
        return self._answer("cancel", sub_id, body)

    def create(self, body: Dict[str, Any]) -> Any:
        return self._answer("create", body)

    def resume(self, sub_id: str, body: Dict[str, Any]) -> Any:
        return self._answer("resume", sub_id, body)

    def all(self, query: Dict[str, Any]) -> Any:
        return self._answer("all", query)


class FakeClient:
    def __init__(self, **behaviour: Any) -> None:
        self.calls: List[tuple] = []
        self.behaviour = behaviour
        self.subscription = _Subscriptions(self)


def _gateway(client: FakeClient) -> RazorpayGateway:
    return RazorpayGateway(key_id="k", key_secret="s", webhook_secret="w", client=client, policy=FAST)


def test_a_hung_provider_call_is_bounded_and_never_blocks_the_event_loop() -> None:
    """Reproduced at 6913a01: the SDK was called on the event loop with no timeout."""
    client = FakeClient(cancel=4.0)  # blocked: >= 4 s; bounded: ~2.3 s
    ticks: List[float] = []

    async def scenario() -> None:
        async def ticker() -> None:
            for _ in range(10):
                ticks.append(time.monotonic())
                await asyncio.sleep(0.05)

        started = time.monotonic()
        tick = asyncio.create_task(ticker())
        with pytest.raises(rel.GatewayOutcomeUnknown) as raised:
            await _gateway(client).cancel_subscription(gateway_subscription_id="sub_1")
        assert raised.value.cause_type == "DeadlineExceeded"
        assert time.monotonic() - started < FAST.deadline + 1.2
        await tick
        assert len(ticks) == 10 and ticks[-1] - ticks[0] < 2.5, "the loop kept running during the call"

    _run(scenario())
    assert [c[0] for c in client.calls] == ["cancel"], "an unanswered cancel is never re-sent by the adapter"


@pytest.mark.parametrize("exc, expected", [
    (requests.exceptions.ConnectTimeout(), rel.NOT_SENT),
    (requests.exceptions.ReadTimeout(), rel.UNKNOWN),
    (requests.exceptions.ConnectionError(type("E", (), {"reason": NewConnectionError(None, "refused")})()), rel.NOT_SENT),
    (requests.exceptions.ConnectionError("Connection aborted: reset by peer"), rel.UNKNOWN),
    (ConnectionRefusedError(), rel.NOT_SENT),
    (ConnectionResetError(), rel.UNKNOWN),
    (BadRequestError("Subscription is not cancellable in cancelled status"), rel.REJECTED),
    (type("ServerError", (Exception,), {"status_code": 503})(), rel.UNKNOWN),
    (type("Limited", (Exception,), {"status_code": 429})(), rel.UNKNOWN),
    (type("Conflict", (Exception,), {"status_code": 409})(), rel.UNKNOWN),
    (type("Forbidden", (Exception,), {"status_code": 403})(), rel.REJECTED),
    (TimeoutError(), rel.UNKNOWN),
    (RuntimeError("anything"), rel.UNKNOWN),
])
def test_failures_are_classified_by_what_they_prove(exc: BaseException, expected: str) -> None:
    assert rel.classify(exc) == expected


def test_a_mutating_call_is_retried_only_when_it_was_provably_not_sent() -> None:
    client = FakeClient(cancel=[requests.exceptions.ConnectTimeout(), requests.exceptions.ConnectTimeout()])
    with pytest.raises(rel.GatewayCallNotSent):
        _run(_gateway(client).cancel_subscription(gateway_subscription_id="sub_1"))
    assert len(client.calls) == 2, "one retry for a request that never left"

    client = FakeClient(cancel=[requests.exceptions.ConnectTimeout(), None])
    assert _run(_gateway(client).cancel_subscription(gateway_subscription_id="sub_1")) is True
    assert len(client.calls) == 2

    for failure, kind in ((requests.exceptions.ReadTimeout(), rel.GatewayOutcomeUnknown),
                          (BadRequestError("refused"), rel.GatewayRejected)):
        client = FakeClient(create=[failure])
        with pytest.raises(kind):
            _run(_gateway(client).create_subscription(gateway_customer_id="c", plan_code="p", billing_period="monthly"))
        assert len(client.calls) == 1, "never a second create after an unanswered or refused one"


def test_reads_are_retried_a_bounded_number_of_times() -> None:
    client = FakeClient(fetch=[requests.exceptions.ReadTimeout()] * 5)
    with pytest.raises(rel.GatewayUnavailable):
        _run(_gateway(client).fetch_subscription_state(gateway_subscription_id="sub_1"))
    assert len(client.calls) == 1 + FAST.read_retries

    client = FakeClient(fetch=[requests.exceptions.ReadTimeout(), {"id": "sub_1", "status": "halted"}])
    state = _run(_gateway(client).fetch_subscription_state(gateway_subscription_id="sub_1"))
    assert state is not None and state.status == "halted" and len(client.calls) == 2


def test_a_cycle_end_cancel_returns_the_providers_answer_as_evidence() -> None:
    end = int(datetime(2030, 1, 31, tzinfo=timezone.utc).timestamp())
    client = FakeClient(cancel=lambda sub_id, body: {"id": sub_id, "status": "active", "current_end": end,
                                                     "has_scheduled_changes": False})
    state = _run(_gateway(client).cancel_with_evidence(gateway_subscription_id="sub_1", immediate=False))
    assert client.calls == [("cancel", "sub_1", {"cancel_at_cycle_end": 1})]
    assert state.status == "active" and state.current_end == datetime(2030, 1, 31)  # naive UTC
    client = FakeClient()
    _run(_gateway(client).cancel_with_evidence(gateway_subscription_id="sub_1", immediate=True))
    assert client.calls == [("cancel", "sub_1", {"cancel_at_cycle_end": 0})]


def test_reference_search_scans_bounded_pages_and_matches_the_note() -> None:
    pages = [{"items": [{"id": f"s{i}", "notes": {"checkout_reference": "other"}} for i in range(100)]},
             {"items": [{"id": "hit", "customer_id": "c", "plan_id": "p", "notes": {"checkout_reference": "op_1"}}]}]
    client = FakeClient(all=pages)
    found = _run(_gateway(client).find_subscription_by_reference(reference="op_1"))
    assert found is not None and found.gateway_subscription_id == "hit"
    assert [c[1]["skip"] for c in client.calls] == [0, 100]
    client = FakeClient(all=[{"items": [{"id": f"s{i}", "notes": {}} for i in range(100)]}] * 6)
    with pytest.raises(rel.GatewayUnavailable):  # review 13 R13-4: an incomplete scan is not "absent"
        _run(_gateway(client).find_subscription_by_reference(reference="op_1"))
    assert len(client.calls) == 5, "bounded: never an unbounded scan"
    client = FakeClient(all=[{"items": [{"id": "s1", "notes": {}}]}])
    assert _run(_gateway(client).find_subscription_by_reference(reference="op_1")) is None, "whole window read"


def test_the_bounded_session_adds_timeouts_unless_the_caller_gave_one(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: List[Any] = []
    monkeypatch.setattr(requests.Session, "request", lambda self, method, url, *a, **kw: seen.append(kw.get("timeout")))
    session = rel.bounded_session(FAST)
    session.request("GET", "https://example.invalid")
    session.request("GET", "https://example.invalid", timeout=9)
    assert seen == [(FAST.connect_timeout, FAST.read_timeout), 9]


def test_configured_timeouts_are_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "PAYMENT_GATEWAY_READ_TIMEOUT_SECONDS", 100000, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_GATEWAY_CONNECT_TIMEOUT_SECONDS", 0, raising=False)
    monkeypatch.setattr(settings, "PAYMENT_GATEWAY_READ_RETRIES", 50, raising=False)
    policy = rel.policy_from_settings()
    assert (policy.read_timeout, policy.connect_timeout, policy.read_retries) == (60.0, 0.1, 5)


NOW = datetime(2030, 1, 10)


@pytest.mark.parametrize("status, mode, confirmed, effective, expected", [
    ("active", "cycle_end", True, NOW + timedelta(days=5), rel.SCHEDULED),
    ("active", "cycle_end", True, NOW - timedelta(hours=23), rel.SCHEDULED),   # within the provider's tolerance
    ("active", "cycle_end", True, NOW - timedelta(hours=25), rel.OVERDUE),
    ("cancelled", "cycle_end", True, NOW + timedelta(days=5), rel.CANCELLED),
    ("completed", None, False, None, rel.CANCELLED),
    ("active", None, False, None, rel.UNCONFIRMED),
    # A cycle-end request never confirmed by the provider is not "scheduled", whatever the date.
    ("active", "cycle_end", False, NOW + timedelta(days=5), rel.UNCONFIRMED),
    ("active", "cycle_end", True, None, rel.UNCONFIRMED),
    ("active", "immediate", True, NOW, rel.NOT_APPLIED),
    ("halted", "cycle_end", True, NOW - timedelta(days=9), rel.OVERDUE),
    ("weird", "cycle_end", True, NOW, rel.UNRECOGNISED),
    (None, "cycle_end", True, NOW, rel.UNREADABLE),
])
def test_cancellation_interpretation(status: Any, mode: Any, confirmed: bool, effective: Any, expected: str) -> None:
    state = rel.ProviderSubscriptionState(status=status) if status else None
    assert rel.interpret_cancellation(state, requested_mode=mode, request_confirmed=confirmed,
                                      effective_at=effective, now=NOW) == expected


def test_r10_6_a_resume_applied_but_unread_is_an_unknown_outcome() -> None:
    """Review 10 R10-6: the resume reached the provider; only its state could not be read
    afterwards. That is never "not sent"."""
    client = FakeClient(resume=None, fetch=[requests.exceptions.ReadTimeout()] * 5)
    with pytest.raises(rel.GatewayOutcomeUnknown):
        _run(_gateway(client).reactivate_subscription(gateway_subscription_id="sub_1"))
    assert [c[0] for c in client.calls][0] == "resume"
    assert [c[0] for c in client.calls].count("resume") == 1


def test_r10_3_authentication_is_not_an_activation() -> None:
    event = _gateway(FakeClient()).parse_webhook_event(
        payload={"event": "subscription.authenticated", "payload": {"subscription": {"entity": {"id": "sub_1"}}}})
    assert event is not None and event.event_type == "subscription.authenticated"


def test_r14_1_the_search_window_is_utc_whatever_the_host_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review 14 R14-1: stored datetimes are naive UTC; the search's ``from`` must not move with
    the host's time zone (a host behind UTC skipped the subscription it looked for)."""
    import calendar
    import time as _time

    monkeypatch.setenv("TZ", "America/New_York")
    _time.tzset()
    try:
        created = datetime(2030, 1, 10, 12, 0, 0)  # naive UTC, as stored
        client = FakeClient(all=[{"items": []}])
        _run(_gateway(client).find_subscription_by_reference(reference="op_1", created_after=created))
        assert client.calls[0][1]["from"] == calendar.timegm(created.utctimetuple())
    finally:
        monkeypatch.delenv("TZ", raising=False)
        _time.tzset()
