from __future__ import annotations

import asyncio
import time
from collections import defaultdict

import pytest

from rbac_backend.core.config import settings
from rbac_backend.services.arbitration_drafting import filing_export_queue as queue_module
from rbac_backend.services.arbitration_drafting.filing_export_queue import FilingExportQueue


class _FakeRedis:
    def __init__(self):
        self.hashes = defaultdict(dict)
        self.lists = defaultdict(list)

    async def ping(self):
        return True

    async def aclose(self):
        return None

    async def hget(self, key, field):
        return self.hashes[key].get(field)

    async def hset(self, key, mapping):
        self.hashes[key].update({name: str(value) for name, value in mapping.items()})

    async def hgetall(self, key):
        return dict(self.hashes[key])

    async def exists(self, key):
        return int(key in self.hashes)

    async def llen(self, key):
        return len(self.lists[key])

    async def lrange(self, key, start, end):
        return list(self.lists[key])

    async def lrem(self, key, count, value):
        before = len(self.lists[key])
        if count == 1:
            try:
                self.lists[key].remove(value)
            except ValueError:
                pass
        else:
            self.lists[key] = [item for item in self.lists[key] if item != value]
        return before - len(self.lists[key])

    async def rpush(self, key, value):
        self.lists[key].append(value)
        return len(self.lists[key])

    async def brpoplpush(self, source, destination, timeout):
        if not self.lists[source]:
            return None
        value = self.lists[source].pop()
        self.lists[destination].insert(0, value)
        return value

    async def eval(self, script, numkeys, *args):
        keys = list(args[:numkeys])
        values = list(args[numkeys:])
        if script == queue_module._PUSH_UNIQUE:
            await self.lrem(keys[0], 0, values[0])
            await self.rpush(keys[0], values[0])
            return 1
        if script == queue_module._ACQUIRE_LEASE:
            row = self.hashes[keys[0]]
            status = row.get("status")
            expires = float(row.get("lease_expires_epoch") or 0)
            if status in {"completed", "failed"}:
                return 0
            if status == "running" and expires > float(values[1]):
                return 0
            attempts = int(row.get("attempts") or 0) + 1
            row.update(
                {
                    "attempts": str(attempts),
                    "status": "running",
                    "lease_token": values[0],
                    "lease_expires_epoch": str(values[2]),
                    "worker": values[3],
                    "started_at": values[4],
                    "heartbeat_at": values[4],
                    "updated_at": values[4],
                }
            )
            return attempts
        if script == queue_module._HEARTBEAT_LEASE:
            row = self.hashes[keys[0]]
            if row.get("lease_token") != values[0]:
                return 0
            row.update(
                {
                    "lease_expires_epoch": str(values[1]),
                    "heartbeat_at": values[2],
                    "updated_at": values[2],
                }
            )
            return 1
        if script == queue_module._ACK_JOB:
            row = self.hashes[keys[0]]
            if row.get("lease_token") != values[0]:
                return 0
            row.update(
                {
                    "status": "completed",
                    "completed_at": values[1],
                    "updated_at": values[1],
                    "lease_token": "",
                    "lease_expires_epoch": "0",
                }
            )
            await self.lrem(keys[1], 0, values[2])
            await self.lrem(keys[2], 0, values[2])
            return 1
        if script == queue_module._FAIL_JOB:
            row = self.hashes[keys[0]]
            if row.get("lease_token") != values[0]:
                return 0
            status = values[1]
            job_id = values[4]
            row.update(
                {
                    "status": status,
                    "last_error": values[2],
                    "updated_at": values[3],
                    "lease_token": "",
                    "lease_expires_epoch": "0",
                }
            )
            await self.lrem(keys[1], 0, job_id)
            await self.lrem(keys[2], 0, job_id)
            await self.lrem(keys[3], 0, job_id)
            await self.rpush(keys[2] if status == "queued" else keys[3], job_id)
            return 1
        if script == queue_module._RECOVER_JOB:
            row = self.hashes[keys[0]]
            job_id = values[0]
            status = row.get("status")
            expires = float(row.get("lease_expires_epoch") or 0)
            if status in {"completed", "failed"}:
                await self.lrem(keys[1], 0, job_id)
                return 0
            if status == "running" and expires > float(values[1]):
                return 0
            row.update(
                {
                    "status": "queued",
                    "lease_token": "",
                    "lease_expires_epoch": "0",
                    "recovery_reason": "visibility_timeout",
                    "recovered_at": values[2],
                    "updated_at": values[2],
                }
            )
            await self.lrem(keys[1], 0, job_id)
            await self.lrem(keys[2], 0, job_id)
            await self.rpush(keys[2], job_id)
            return 1
        raise AssertionError("unexpected Lua script")


@pytest.fixture
def configured_queue(monkeypatch):
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_ENABLED", True)
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_NAME", "test:filing:queued")
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_PROCESSING_NAME", "test:filing:processing")
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_DEADLETTER_NAME", "test:filing:dead")
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_VISIBILITY_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_HEARTBEAT_SECONDS", 20)
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_METADATA_TTL_SECONDS", 3600)
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_MAX_RETRIES", 3)
    queue = FilingExportQueue()
    queue._redis = _FakeRedis()
    return queue


@pytest.mark.asyncio
async def test_enqueue_is_effect_key_idempotent_and_duplicate_free(configured_queue):
    queue = configured_queue
    payload = {"case_id": "case-1", "export_id": "export-1", "format": "zip", "effect_key": "effect-1"}

    await queue.enqueue(job_id="export-1", effect_key="effect-1", payload=payload)
    await queue.enqueue(job_id="export-1", effect_key="effect-1", payload=payload)

    assert queue._redis.lists[settings.FILING_EXPORT_QUEUE_NAME] == ["export-1"]
    assert queue._redis.hashes[queue._job_key("export-1")]["effect_key"] == "effect-1"
    with pytest.raises(RuntimeError, match="another effect key"):
        await queue.enqueue(job_id="export-1", effect_key="effect-2", payload=payload)


@pytest.mark.asyncio
async def test_visibility_recovery_requeues_only_expired_lease(configured_queue):
    queue = configured_queue
    payload = {"case_id": "case-1", "export_id": "export-1", "format": "zip", "effect_key": "effect-1"}
    await queue.enqueue(job_id="export-1", effect_key="effect-1", payload=payload)
    await queue._redis.brpoplpush(
        settings.FILING_EXPORT_QUEUE_NAME,
        settings.FILING_EXPORT_QUEUE_PROCESSING_NAME,
        timeout=1,
    )
    row = queue._redis.hashes[queue._job_key("export-1")]
    row.update({"status": "running", "lease_token": "dead", "lease_expires_epoch": "0"})

    assert await queue.requeue_orphaned_jobs(force=True) == 1
    assert queue._redis.lists[settings.FILING_EXPORT_QUEUE_NAME] == ["export-1"]
    assert row["recovery_reason"] == "visibility_timeout"

    await queue._redis.brpoplpush(
        settings.FILING_EXPORT_QUEUE_NAME,
        settings.FILING_EXPORT_QUEUE_PROCESSING_NAME,
        timeout=1,
    )
    row.update({"status": "running", "lease_token": "live", "lease_expires_epoch": str(time.time() + 120)})
    assert await queue.requeue_orphaned_jobs(force=True) == 0
    assert queue._redis.lists[settings.FILING_EXPORT_QUEUE_PROCESSING_NAME] == ["export-1"]


@pytest.mark.asyncio
async def test_transient_failure_retries_then_commits_effect_once(configured_queue, monkeypatch):
    queue = configured_queue
    payload = {"case_id": "case-1", "export_id": "export-1", "format": "zip", "effect_key": "effect-1"}
    calls = []
    failures = []

    async def process(payload, **kwargs):
        calls.append(kwargs["lease_token"])
        if len(calls) == 1:
            raise ConnectionError("temporary object-store outage")
        return {"status": "completed"}

    async def record_failure(payload, **kwargs):
        failures.append(kwargs)

    monkeypatch.setattr(queue_module, "process_filing_export_job", process)
    monkeypatch.setattr(queue_module, "record_filing_export_failure", record_failure)
    await queue.enqueue(job_id="export-1", effect_key="effect-1", payload=payload)

    job_id = await queue._redis.brpoplpush(
        settings.FILING_EXPORT_QUEUE_NAME,
        settings.FILING_EXPORT_QUEUE_PROCESSING_NAME,
        timeout=1,
    )
    await queue._process_job(job_id, "worker-1")
    assert queue._redis.hashes[queue._job_key(job_id)]["status"] == "queued"
    assert failures[0]["terminal"] is False

    job_id = await queue._redis.brpoplpush(
        settings.FILING_EXPORT_QUEUE_NAME,
        settings.FILING_EXPORT_QUEUE_PROCESSING_NAME,
        timeout=1,
    )
    await queue._process_job(job_id, "worker-2")
    row = queue._redis.hashes[queue._job_key(job_id)]
    assert row["status"] == "completed"
    assert row["attempts"] == "2"
    assert len(calls) == 2
    assert calls[0] != calls[1]


@pytest.mark.asyncio
async def test_active_lease_rejects_duplicate_worker(configured_queue):
    queue = configured_queue
    payload = {"case_id": "case-1", "export_id": "export-1", "format": "zip", "effect_key": "effect-1"}
    await queue.enqueue(job_id="export-1", effect_key="effect-1", payload=payload)
    key = queue._job_key("export-1")
    first = await queue._redis.eval(
        queue_module._ACQUIRE_LEASE,
        1,
        key,
        "lease-1",
        time.time(),
        time.time() + 60,
        "worker-1",
        queue_module._now(),
    )
    second = await queue._redis.eval(
        queue_module._ACQUIRE_LEASE,
        1,
        key,
        "lease-2",
        time.time(),
        time.time() + 60,
        "worker-2",
        queue_module._now(),
    )
    assert first == 1
    assert second == 0
