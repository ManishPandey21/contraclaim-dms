"""A stopped reprojection runtime leaves no heartbeat behind, even mid-write.

Motor 3 runs each PyMongo call in a worker thread. Cancelling the coroutine that
awaits a write does not stop the thread, so a heartbeat upsert in flight when
the contract-worker stops used to land *after* the runtime's own delete and put
a fresh row back: a stopped owner then read as a live one to the post-deploy
check (CI run 435, ``test_the_post_deploy_check_proves_a_live_owner_on_the_
deployed_release``). These tests reproduce that ordering deterministically with
a collection that applies writes the same way: in a thread that keeps going
after the awaiting task is cancelled.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any, Dict

from rbac_backend.services import contract_reprojection_runtime as runtime_module

WRITE_SECONDS = 0.3


class _ThreadedCollection:
    """Applies writes in a worker thread, like Motor 3; cancelling the awaiter does not stop them."""

    def __init__(self, rows: Dict[str, Dict[str, Any]], lock: threading.Lock, write_started: asyncio.Event, write_seconds: float) -> None:
        self._rows = rows
        self._lock = lock
        self._write_started = write_started
        self._write_seconds = write_seconds

    async def create_index(self, *args: Any, **kwargs: Any) -> str:
        return kwargs.get("name", "index")

    def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> "asyncio.Future[None]":
        loop = asyncio.get_running_loop()
        self._write_started.set()

        def apply() -> None:
            time.sleep(self._write_seconds)
            with self._lock:
                self._rows.setdefault(query["_id"], {}).update(update["$set"])

        return loop.run_in_executor(None, apply)

    def delete_one(self, query: Dict[str, Any]) -> "asyncio.Future[None]":
        def apply() -> None:
            with self._lock:
                self._rows.pop(query["_id"], None)

        return asyncio.get_running_loop().run_in_executor(None, apply)


class _Db:
    def __init__(self, write_seconds: float = WRITE_SECONDS) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.write_started = asyncio.Event()
        self._heartbeats = _ThreadedCollection(self.rows, self.lock, self.write_started, write_seconds)
        self._other = _ThreadedCollection({}, threading.Lock(), asyncio.Event(), 0.0)

    def __getitem__(self, name: str) -> _ThreadedCollection:
        return self._heartbeats if name == runtime_module.RUNTIME_HEARTBEATS_COLLECTION else self._other


def _runtime() -> Any:
    async def run_once() -> Any:
        await asyncio.Event().wait()  # a pass that is still running when the worker stops

    return SimpleNamespace(worker_id="contract-worker-1", run_once=run_once)


async def _stop(task: "asyncio.Task[None]") -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_a_heartbeat_write_in_flight_at_stop_does_not_outlive_the_runtime() -> None:
    async def scenario() -> Dict[str, Any]:
        db = _Db()
        task = asyncio.create_task(runtime_module.run_forever(db, interval_seconds=0.05, runtime=_runtime()))
        await asyncio.wait_for(db.write_started.wait(), timeout=5)
        await _stop(task)  # the pass-start upsert is still running in its thread
        await asyncio.sleep(WRITE_SECONDS * 2)  # long enough for any orphaned write to land
        with db.lock:
            return dict(db.rows)

    rows = asyncio.run(scenario())
    assert "contract-worker-1" not in rows, "a stopped runtime left its heartbeat behind"


def test_a_settled_heartbeat_is_removed_on_stop() -> None:
    async def scenario() -> Dict[str, Any]:
        db = _Db(write_seconds=0.0)
        task = asyncio.create_task(runtime_module.run_forever(db, interval_seconds=0.05, runtime=_runtime()))
        await asyncio.wait_for(db.write_started.wait(), timeout=5)
        for _ in range(50):
            with db.lock:
                if "contract-worker-1" in db.rows:
                    break
            await asyncio.sleep(0.02)
        with db.lock:
            assert db.rows["contract-worker-1"]["pass_started_at"], "the pass start was recorded"
        await _stop(task)
        with db.lock:
            return dict(db.rows)

    assert asyncio.run(scenario()) == {}


def test_a_write_that_never_returns_does_not_hold_the_stop_forever(monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "_STOP_WAIT_SECONDS", 0.2, raising=False)

    async def scenario() -> float:
        db = _Db(write_seconds=1.0)
        task = asyncio.create_task(runtime_module.run_forever(db, interval_seconds=0.05, runtime=_runtime()))
        await asyncio.wait_for(db.write_started.wait(), timeout=5)
        started = time.monotonic()
        await _stop(task)
        stopped_in = time.monotonic() - started
        await asyncio.sleep(1.2)  # let the slow thread finish before the loop closes
        return stopped_in

    assert asyncio.run(scenario()) < 0.9, "the stop waits a bounded time for an in-flight write"
