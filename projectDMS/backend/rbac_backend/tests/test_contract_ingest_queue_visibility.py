"""Contract ingest queue visibility-timeout recovery tests."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

import pytest

from rbac_backend.core.config import settings
from rbac_backend.services.contract_ingest_queue import ContractIngestQueue


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = defaultdict(list)
        self.hashes: dict[str, dict[str, str]] = defaultdict(dict)

    async def lrange(self, name: str, start: int, end: int) -> list[str]:
        values = list(self.lists[name])
        if end == -1:
            return values[start:]
        return values[start : end + 1]

    async def lrem(self, name: str, count: int, value: str) -> int:
        values = self.lists[name]
        removed = 0
        if count == 0:
            kept = [item for item in values if item != value]
            removed = len(values) - len(kept)
            self.lists[name] = kept
            return removed
        new_values: list[str] = []
        remaining = abs(count)
        iterable = values if count > 0 else list(reversed(values))
        for item in iterable:
            if item == value and remaining > 0:
                removed += 1
                remaining -= 1
                continue
            new_values.append(item)
        self.lists[name] = new_values if count > 0 else list(reversed(new_values))
        return removed

    async def rpush(self, name: str, value: str) -> int:
        self.lists[name].append(value)
        return len(self.lists[name])

    async def hget(self, key: str, field: str) -> str | None:
        return self.hashes[key].get(field)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes[key])

    async def hset(self, key: str, mapping: dict[str, Any]) -> int:
        self.hashes[key].update({field: str(value) for field, value in mapping.items()})
        return len(mapping)


def _timestamp(seconds_ago: int = 0) -> str:
    return (datetime.utcnow() - timedelta(seconds=seconds_ago)).isoformat()


def _configure_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CONTRACT_QUEUE_NAME", "queue")
    monkeypatch.setattr(settings, "CONTRACT_QUEUE_PROCESSING_NAME", "processing")
    monkeypatch.setattr(settings, "CONTRACT_QUEUE_DEADLETTER_NAME", "deadletter")
    monkeypatch.setattr(settings, "CONTRACT_QUEUE_VISIBILITY_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(settings, "CONTRACT_QUEUE_HEARTBEAT_SECONDS", 5)


def _queue_with_fake_redis(monkeypatch: pytest.MonkeyPatch, redis: FakeRedis) -> ContractIngestQueue:
    queue = ContractIngestQueue()

    async def connect() -> FakeRedis:
        return redis

    monkeypatch.setattr(queue, "connect", connect)
    return queue


@pytest.mark.asyncio
async def test_recovery_keeps_fresh_running_job_in_processing(monkeypatch):
    _configure_queue(monkeypatch)
    redis = FakeRedis()
    redis.lists["processing"] = ["job-1"]
    redis.hashes["contract_ingest_job:job-1"] = {
        "status": "running",
        "heartbeat_at": _timestamp(),
    }

    await _queue_with_fake_redis(monkeypatch, redis).requeue_orphaned_jobs()

    assert redis.lists["processing"] == ["job-1"]
    assert redis.lists["queue"] == []
    assert redis.hashes["contract_ingest_job:job-1"]["status"] == "running"


@pytest.mark.asyncio
async def test_recovery_requeues_only_stale_running_job(monkeypatch):
    _configure_queue(monkeypatch)
    redis = FakeRedis()
    redis.lists["processing"] = ["job-1"]
    redis.hashes["contract_ingest_job:job-1"] = {
        "status": "running",
        "heartbeat_at": _timestamp(seconds_ago=120),
    }

    await _queue_with_fake_redis(monkeypatch, redis).requeue_orphaned_jobs()

    assert redis.lists["processing"] == []
    assert redis.lists["queue"] == ["job-1"]
    metadata = redis.hashes["contract_ingest_job:job-1"]
    assert metadata["status"] == "queued"
    assert metadata["recovery_reason"] == "visibility_timeout"
    assert "recovered_at" in metadata


@pytest.mark.asyncio
async def test_recovery_cleans_terminal_jobs_without_requeue(monkeypatch):
    _configure_queue(monkeypatch)
    redis = FakeRedis()
    redis.lists["processing"] = ["job-1", "job-2"]
    redis.hashes["contract_ingest_job:job-1"] = {"status": "completed"}
    redis.hashes["contract_ingest_job:job-2"] = {"status": "failed"}

    await _queue_with_fake_redis(monkeypatch, redis).requeue_orphaned_jobs()

    assert redis.lists["processing"] == []
    assert redis.lists["queue"] == []


@pytest.mark.asyncio
async def test_recovery_deduplicates_stale_processing_entries(monkeypatch):
    _configure_queue(monkeypatch)
    redis = FakeRedis()
    redis.lists["processing"] = ["job-1", "job-1"]
    redis.hashes["contract_ingest_job:job-1"] = {
        "status": "running",
        "heartbeat_at": _timestamp(seconds_ago=120),
    }

    await _queue_with_fake_redis(monkeypatch, redis).requeue_orphaned_jobs()

    assert redis.lists["processing"] == []
    assert redis.lists["queue"] == ["job-1"]


# --------------------------------------------------------------------------- #
# a second ingest of one document's source waits; it never spends a retry
# --------------------------------------------------------------------------- #


def _run_job_raising(monkeypatch: pytest.MonkeyPatch, exc: Exception, *, attempts: str = "0") -> FakeRedis:
    import asyncio
    import json

    from rbac_backend.services import contract_service

    _configure_queue(monkeypatch)
    redis = FakeRedis()
    queue = _queue_with_fake_redis(monkeypatch, redis)
    key = queue._job_key("upload-b")
    redis.hashes[key].update(
        {"payload": json.dumps({"upload_id": "upload-b"}), "status": "queued", "attempts": attempts}
    )
    redis.lists["processing"].append("upload-b")

    async def process(payload):
        raise exc

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(contract_service, "process_contract_ingest_job", process)
    monkeypatch.setattr("rbac_backend.services.contract_ingest_queue.asyncio.sleep", no_sleep)
    asyncio.run(queue._process_job("upload-b", "worker-1"))
    return redis


def test_a_job_whose_source_is_busy_is_requeued_without_spending_a_retry(monkeypatch):
    from rbac_backend.services.contract_source_lease import ContractSourceBusy

    redis = _run_job_raising(
        monkeypatch, ContractSourceBusy("busy", holder="ingest:upload-a"), attempts="2"
    )
    job = redis.hashes[next(iter(redis.hashes))]
    assert job["status"] == "queued"
    assert job["attempts"] == "2", "waiting for another ingest spent a retry"
    assert redis.lists["queue"] == ["upload-b"]
    assert redis.lists["deadletter"] == []
    assert redis.lists["processing"] == []


def test_a_redelivered_copy_of_a_live_job_is_dropped(monkeypatch):
    """The owner is this very job, still running: the copy must not requeue it
    or overwrite the live run's status."""
    from rbac_backend.services.contract_source_lease import ContractSourceBusy

    redis = _run_job_raising(monkeypatch, ContractSourceBusy("busy", holder="ingest:upload-b"))
    job = redis.hashes[next(iter(redis.hashes))]
    assert job["status"] == "running"
    assert redis.lists["queue"] == [] and redis.lists["deadletter"] == []
    # The processing entry stays: if the owning run has died, stale-job
    # recovery redelivers it; if it lives, its completion removes it.
    assert redis.lists["processing"] == ["upload-b"]


def test_an_ingest_that_lost_its_source_lease_is_not_retried(monkeypatch):
    from rbac_backend.services.contract_source_lease import LostSourceLease

    redis = _run_job_raising(monkeypatch, LostSourceLease("lost", holder="ingest:upload-a"))
    assert redis.lists["queue"] == [] and redis.lists["deadletter"] == []
    job = redis.hashes[next(iter(redis.hashes))]
    # Closed, not left "running": a later OCR retry or reindex of this upload
    # must enqueue, and enqueue dedupes on queued/running.
    assert job["status"] == "superseded"


def test_a_job_waiting_for_the_source_is_not_lost_when_the_worker_stops(monkeypatch):
    import asyncio
    import json

    from rbac_backend.services import contract_service
    from rbac_backend.services.contract_source_lease import ContractSourceBusy

    _configure_queue(monkeypatch)
    redis = FakeRedis()
    queue = _queue_with_fake_redis(monkeypatch, redis)
    key = queue._job_key("upload-b")
    redis.hashes[key].update({"payload": json.dumps({"upload_id": "upload-b"}), "status": "queued"})
    redis.lists["processing"].append("upload-b")

    async def process(payload):
        raise ContractSourceBusy("busy", holder="ingest:upload-a")

    monkeypatch.setattr(contract_service, "process_contract_ingest_job", process)

    async def run():
        task = asyncio.create_task(queue._process_job("upload-b", "worker-1"))
        await asyncio.sleep(0.2)  # inside the wait
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    # Stopped (or killed) while waiting: the job is still on the processing
    # list, "waiting" - recovery re-queues it once its heartbeat goes stale.
    assert "upload-b" in redis.lists["processing"] + redis.lists["queue"], "a stopped waiter dropped its job"
    assert redis.hashes[key]["status"] == "waiting"


def test_recovery_leaves_a_live_waiting_job_alone_and_takes_a_dead_one(monkeypatch):
    import asyncio

    _configure_queue(monkeypatch)
    redis = FakeRedis()
    queue = _queue_with_fake_redis(monkeypatch, redis)
    live, dead = queue._job_key("live"), queue._job_key("dead")
    redis.hashes[live].update({"status": "waiting", "heartbeat_at": _timestamp(1)})
    redis.hashes[dead].update({"status": "waiting", "heartbeat_at": _timestamp(3600)})
    redis.lists["processing"].extend(["live", "dead"])

    asyncio.run(queue._recover_stale_processing_jobs_once(redis))
    assert redis.lists["queue"] == ["dead"], "a waiting job whose worker is alive was run twice"
    assert redis.lists["processing"] == ["live"]


def test_a_failed_job_waiting_out_its_backoff_is_never_on_no_list(monkeypatch):
    import asyncio
    import json

    from rbac_backend.services import contract_service

    _configure_queue(monkeypatch)
    redis = FakeRedis()
    queue = _queue_with_fake_redis(monkeypatch, redis)
    key = queue._job_key("upload-c")
    redis.hashes[key].update({"payload": json.dumps({"upload_id": "upload-c"}), "status": "queued"})
    redis.lists["processing"].append("upload-c")

    async def process(payload):
        raise RuntimeError("OCR outage (test)")

    monkeypatch.setattr(contract_service, "process_contract_ingest_job", process)

    async def run():
        task = asyncio.create_task(queue._process_job("upload-c", "worker-1"))
        await asyncio.sleep(0.2)  # inside the retry backoff
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert "upload-c" in redis.lists["processing"] + redis.lists["queue"], "the retry was lost"


def test_a_lost_lease_with_no_live_owner_is_retried_not_superseded(monkeypatch):
    from rbac_backend.services.contract_source_lease import LostSourceLease

    redis = _run_job_raising(monkeypatch, LostSourceLease("lost, nobody holds it", holder=None))
    job = redis.hashes[next(iter(redis.hashes))]
    assert job["status"] != "superseded", "a tainted source with no owner was never re-ingested"
    assert redis.lists["queue"] == ["upload-b"] or redis.lists["deadletter"] == ["upload-b"]


def test_a_reindex_during_a_backoff_does_not_deliver_the_job_twice(monkeypatch):
    import asyncio
    import json

    _configure_queue(monkeypatch)
    redis = FakeRedis()
    queue = _queue_with_fake_redis(monkeypatch, redis)
    key = queue._job_key("upload-w")
    redis.hashes[key].update({"payload": json.dumps({"upload_id": "upload-w"}), "status": "waiting"})
    redis.lists["processing"].append("upload-w")

    assert asyncio.run(queue.enqueue({"upload_id": "upload-w"})) == "upload-w"
    assert redis.lists["queue"] == [], "a waiting job was enqueued a second time"


def test_a_waiter_does_not_requeue_a_job_something_else_moved_on(monkeypatch):
    import asyncio

    _configure_queue(monkeypatch)
    redis = FakeRedis()
    queue = _queue_with_fake_redis(monkeypatch, redis)
    key = queue._job_key("upload-r")
    redis.hashes[key].update({"status": "running"})  # recovery re-delivered it meanwhile

    asyncio.run(queue._requeue_if_still_waiting(redis, key, "upload-r"))
    assert redis.hashes[key]["status"] == "running"
    assert redis.lists["queue"] == []


def test_a_waiter_whose_job_moved_on_leaves_the_new_runs_entry(monkeypatch):
    import asyncio

    _configure_queue(monkeypatch)
    redis = FakeRedis()
    queue = _queue_with_fake_redis(monkeypatch, redis)
    key = queue._job_key("upload-m")
    redis.hashes[key].update({"status": "running"})
    redis.lists["processing"].append("upload-m")  # the new run's only entry

    asyncio.run(queue._requeue_if_still_waiting(redis, key, "upload-m"))
    assert redis.lists["processing"] == ["upload-m"], "the waiter removed the new run's entry"


def test_an_unknown_holder_waits_and_retries_instead_of_being_superseded(monkeypatch):
    from rbac_backend.services.contract_source_lease import UNKNOWN_HOLDER, LostSourceLease

    redis = _run_job_raising(monkeypatch, LostSourceLease("lost; holder unreadable", holder=UNKNOWN_HOLDER), attempts="1")
    job = redis.hashes[next(iter(redis.hashes))]
    assert job["status"] != "superseded", "a job was dropped on an unreadable holder"
    assert job["attempts"] == "1", "waiting on an unknown holder spent a retry"
    assert redis.lists["queue"] == ["upload-b"]


def test_recovery_retires_a_superseded_leftover_entry(monkeypatch):
    import asyncio

    _configure_queue(monkeypatch)
    redis = FakeRedis()
    queue = _queue_with_fake_redis(monkeypatch, redis)
    redis.hashes[queue._job_key("old")].update({"status": "superseded"})
    redis.lists["processing"].append("old")
    asyncio.run(queue._recover_stale_processing_jobs_once(redis))
    assert redis.lists["processing"] == [] and redis.lists["queue"] == []
