"""Stopping the contract source lease renewal must actually stop it.

The renewal task renews the ingest's source lease until the ingest settles, fails
or releases it; ``quiesce`` stops the renewal first and must not return while it
still runs. A renewal that survives its stop renews a lease the ingest believes
released, or reads the release as "lost" and cancels a finished ingest - and
``quiesce`` itself waits for it forever.

The interleaving is built deterministically: the stop's cancellation is delivered
in the same event-loop step in which an in-flight ``renew`` completes. A
cancellation that meets a completing inner await is exactly what an
``asyncio.wait_for`` around the renew may absorb, so this is the case that must
still terminate.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from rbac_backend.services import contract_source_lease as source_lease
from rbac_backend.services.contract_service import ContractService

INTERVAL = timedelta(milliseconds=1)


@pytest.fixture(autouse=True)
def _fast_renewal(monkeypatch):
    monkeypatch.setattr(source_lease, "SOURCE_LEASE_RENEW_EVERY", INTERVAL)


def _lease():
    return SimpleNamespace(key="doc-renewal", owner_token="token", generation=1)


async def _idle_work():
    await asyncio.sleep(3600)


async def _bounded_stop(renewal) -> None:
    """Stop the renewal, bounded by the TEST (a watchdog), never by the code.

    Driven inline so the stop's first action happens in the caller's step.
    """
    current = asyncio.current_task()
    watchdog = asyncio.get_running_loop().call_later(5, current.cancel)
    try:
        await ContractService._stop_renewal(renewal)
    except asyncio.CancelledError:
        pytest.fail("stopping the renewal never returned: the renewal kept running")
    finally:
        watchdog.cancel()


def test_stopping_the_renewal_when_a_renew_is_completing_terminates_it(monkeypatch):
    renews = {"count": 0}
    completing = asyncio.Event  # created inside the loop below

    async def scenario():
        in_renew = completing()

        async def renew(db, lease, **_kwargs):
            renews["count"] += 1
            if renews["count"] == 3:
                # The stop is requested now; this renew returns in the same step.
                in_renew.set()
            return None

        monkeypatch.setattr(source_lease, "renew", renew)
        work = asyncio.create_task(_idle_work())
        state = {"stop": None, "holder": None}
        renewal = asyncio.create_task(ContractService._keep_source_lease(None, _lease(), work, state))
        await in_renew.wait()
        # The stop runs in THIS step (not as a new task), so its cancellation
        # reaches the renewal before the completed renew's wake-up does.
        await _bounded_stop(renewal)
        stopped_at = renews["count"]
        assert renewal.done(), "quiesce returned while the renewal was still running"
        await asyncio.sleep(INTERVAL.total_seconds() * 50)
        assert renews["count"] == stopped_at, "the lease was renewed after the renewal was stopped"
        assert not work.cancelled(), "a stopped renewal cancelled the ingest"
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        leftover = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        assert leftover == [], leftover

    asyncio.run(scenario())


def test_a_stop_requested_during_the_sleep_terminates_without_renewing(monkeypatch):
    renews = {"count": 0}

    async def scenario():
        async def renew(db, lease, **_kwargs):
            renews["count"] += 1

        monkeypatch.setattr(source_lease, "renew", renew)
        monkeypatch.setattr(source_lease, "SOURCE_LEASE_RENEW_EVERY", timedelta(seconds=3600))
        work = asyncio.create_task(_idle_work())
        renewal = asyncio.create_task(
            ContractService._keep_source_lease(None, _lease(), work, {"stop": None, "holder": None})
        )
        await asyncio.sleep(0)
        await _bounded_stop(renewal)
        assert renewal.done() and renews["count"] == 0
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)

    asyncio.run(scenario())


def test_a_renewal_that_completes_as_its_timeout_fires_keeps_its_real_outcome(monkeypatch):
    """The wait timed out, and the renew completed before the loop resumed - it
    reported a lost lease. That outcome must be acted on (the ingest is stopped
    for a lost lease), not read as a timeout and retried a minute later."""
    import types

    from rbac_backend.services import contract_service as service_module

    real_asyncio = asyncio
    calls = {"n": 0}

    async def wait_that_times_out_first(fs, timeout=None, **kwargs):
        if timeout is None:
            return await real_asyncio.wait(fs, **kwargs)
        calls["n"] += 1
        # The timeout "fires"; the renew then completes before we resume.
        await real_asyncio.wait(fs)
        return set(), set(fs)

    proxy = types.SimpleNamespace(**{name: getattr(asyncio, name) for name in dir(asyncio) if not name.startswith("__")})
    proxy.wait = wait_that_times_out_first
    monkeypatch.setattr(service_module, "asyncio", proxy)

    async def lost(db, lease, **_kwargs):
        raise source_lease.LostSourceLease("taken (test)", holder="ingest:other")

    monkeypatch.setattr(source_lease, "renew", lost)

    async def scenario():
        work = real_asyncio.create_task(_idle_work())
        state = {"stop": None, "holder": None}
        renewal = real_asyncio.create_task(ContractService._keep_source_lease(None, _lease(), work, state))
        await real_asyncio.wait({renewal}, timeout=5)
        assert renewal.done(), "the lost lease was not acted on"
        assert state["stop"] == "lost" and state["holder"] == "ingest:other"
        await real_asyncio.gather(work, return_exceptions=True)
        assert work.cancelled()

    asyncio.run(scenario())


def test_the_self_fence_fires_before_the_lease_can_lapse(monkeypatch):
    """The lease is extended from the moment a renew STARTS. A slow success
    followed by slow failures must still stop the ingest before start +
    lease duration - measured from its end, the fence fired after the lease had
    lapsed and another ingest could already be writing."""
    # Scaled down. Each failing round takes three intervals (sleep, bounded
    # renew, bounded cancel). Measured from a renew's END, the checks land at
    # 60+300k ms and the first one past the fence point is at >= 960 ms - after
    # the lease (940 ms from the renew's start) can lapse. Timer drift only
    # makes that later.
    interval = timedelta(milliseconds=100)
    duration = timedelta(milliseconds=940)
    monkeypatch.setattr(source_lease, "SOURCE_LEASE_RENEW_EVERY", interval)
    monkeypatch.setattr(source_lease, "SOURCE_LEASE_DURATION", duration)
    monkeypatch.setattr(source_lease, "SOURCE_LEASE_SELF_FENCE", timedelta(milliseconds=750))
    marks = {"started": None, "calls": 0}

    async def slow(db, lease, **_kwargs):
        marks["calls"] += 1
        loop = asyncio.get_running_loop()
        if marks["calls"] == 1:
            marks["started"] = loop.time()
            await asyncio.sleep(0.06)  # a slow success, inside its bound
            return None
        try:
            await asyncio.sleep(10)  # every later attempt hangs...
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)  # ...and is slow to give up (a driver call)
            raise

    monkeypatch.setattr(source_lease, "renew", slow)

    async def scenario():
        loop = asyncio.get_running_loop()
        work = asyncio.create_task(_idle_work())
        state = {"stop": None, "holder": None}
        renewal = asyncio.create_task(ContractService._keep_source_lease(None, _lease(), work, state))
        await asyncio.wait({work}, timeout=5)
        fenced_at = loop.time()
        await asyncio.wait({renewal}, timeout=5)
        assert state["stop"] == "fenced"
        lapse_at = marks["started"] + duration.total_seconds()
        assert fenced_at < lapse_at, f"fenced {fenced_at - lapse_at:.3f}s after the lease could lapse"

    asyncio.run(scenario())


def test_the_stop_hold_outlasts_one_retried_driver_operation(monkeypatch):
    """Derived from the client's own bounds: two attempts of server selection +
    connect + socket timeout, never under the floor."""
    from rbac_backend.core.config import settings

    monkeypatch.setattr(source_lease, "STOP_RELEASE_HOLD", None)
    monkeypatch.setattr(settings, "MONGODB_SERVER_SELECTION_TIMEOUT_MS", 30_000)
    monkeypatch.setattr(settings, "MONGODB_CONNECT_TIMEOUT_MS", 20_000)
    monkeypatch.setattr(settings, "MONGODB_SOCKET_TIMEOUT_MS", 60_000)
    assert source_lease.stop_release_hold() == timedelta(milliseconds=2 * 110_000)

    monkeypatch.setattr(settings, "MONGODB_SOCKET_TIMEOUT_MS", 1)
    monkeypatch.setattr(settings, "MONGODB_SERVER_SELECTION_TIMEOUT_MS", 1)
    monkeypatch.setattr(settings, "MONGODB_CONNECT_TIMEOUT_MS", 1)
    assert source_lease.stop_release_hold() == source_lease.STOP_RELEASE_HOLD_FLOOR

    # 0 = the client never times out: no derived bound, so a whole lease.
    monkeypatch.setattr(settings, "MONGODB_SOCKET_TIMEOUT_MS", 0)
    assert source_lease.stop_release_hold() == source_lease.SOURCE_LEASE_DURATION
