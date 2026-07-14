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
