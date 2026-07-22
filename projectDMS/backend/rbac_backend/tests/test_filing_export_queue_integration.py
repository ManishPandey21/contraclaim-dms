"""Real-Redis acceptance for filing export delivery and leases.

Set FILING_EXPORT_REDIS_INTEGRATION_URL to run. All keys are namespaced and
deleted in teardown; the test never flushes a shared Redis database.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest
from redis.asyncio import Redis

from rbac_backend.core.config import settings
from rbac_backend.services.arbitration_drafting import filing_export_queue as queue_module
from rbac_backend.services.arbitration_drafting.filing_export_queue import FilingExportQueue


REDIS_URL = os.getenv("FILING_EXPORT_REDIS_INTEGRATION_URL", "").strip()
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="real Redis integration URL not configured")


@pytest.mark.asyncio
async def test_real_redis_duplicate_lease_recovery_outage_and_load(monkeypatch):
    namespace = f"acceptance:{uuid.uuid4()}"
    queued = f"{namespace}:queued"
    processing = f"{namespace}:processing"
    dead = f"{namespace}:dead"
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_ENABLED", True)
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_REDIS_URL", REDIS_URL)
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_NAME", queued)
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_PROCESSING_NAME", processing)
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_DEADLETTER_NAME", dead)
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_VISIBILITY_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_HEARTBEAT_SECONDS", 20)
    monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_MAX_RETRIES", 3)
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    queue = FilingExportQueue()
    outage_queue = FilingExportQueue()
    restored_queue = FilingExportQueue()
    created_jobs: list[str] = []
    effect_keys: list[str] = []
    try:
        payload = {"case_id": "acceptance", "export_id": "duplicate", "format": "zip", "effect_key": "effect:duplicate"}
        await asyncio.gather(
            *(queue.enqueue(job_id="duplicate", effect_key="effect:duplicate", payload=payload) for _ in range(25))
        )
        created_jobs.append("duplicate")
        assert await redis.llen(queued) == 1

        job_id = await redis.brpoplpush(queued, processing, timeout=1)
        key = queue._job_key(job_id)
        first = await redis.eval(
            queue_module._ACQUIRE_LEASE,
            1,
            key,
            "killed-worker",
            time.time(),
            time.time() + 60,
            "killed-worker",
            queue_module._now(),
        )
        second = await redis.eval(
            queue_module._ACQUIRE_LEASE,
            1,
            key,
            "duplicate-worker",
            time.time(),
            time.time() + 60,
            "duplicate-worker",
            queue_module._now(),
        )
        assert first == 1 and second == 0

        # Represents the state observed after the killed worker's visibility
        # timeout; no queue or effect metadata is rewritten by the test.
        await redis.hset(key, mapping={"lease_expires_epoch": "0"})
        assert await queue.requeue_orphaned_jobs(force=True) == 1

        async def process(payload, **kwargs):
            effect = f"{namespace}:effect:{payload['export_id']}"
            effect_keys.append(effect)
            count = await redis.incr(effect)
            assert count == 1, "effect executed more than once"
            await asyncio.sleep(0.01)
            return {"status": "completed"}

        async def no_failure(*args, **kwargs):
            raise AssertionError("real Redis acceptance job should not fail")

        monkeypatch.setattr(queue_module, "process_filing_export_job", process)
        monkeypatch.setattr(queue_module, "record_filing_export_failure", no_failure)
        job_id = await redis.brpoplpush(queued, processing, timeout=1)
        await queue._process_job(job_id, "recovery-worker")
        assert (await redis.hgetall(key))["status"] == "completed"

        for index in range(40):
            job = f"load-{index}"
            created_jobs.append(job)
            await queue.enqueue(
                job_id=job,
                effect_key=f"effect:{job}",
                payload={"case_id": "acceptance", "export_id": job, "format": "zip", "effect_key": f"effect:{job}"},
            )

        async def drain(worker: str):
            while True:
                job = await redis.rpoplpush(queued, processing)
                if not job:
                    return
                await queue._process_job(job, worker)

        await asyncio.gather(*(drain(f"load-worker-{index}") for index in range(4)))
        assert await redis.llen(queued) == 0
        assert await redis.llen(processing) == 0
        assert await redis.llen(dead) == 0
        effect_counts = await asyncio.gather(*(redis.get(effect) for effect in effect_keys))
        assert all(value == "1" for value in effect_counts)

        await queue.close()
        monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_REDIS_URL", "redis://127.0.0.1:1/15")
        assert (await outage_queue.health())["ready"] is False
        monkeypatch.setattr(settings, "FILING_EXPORT_QUEUE_REDIS_URL", REDIS_URL)
        assert (await restored_queue.health())["ready"] is True
    finally:
        await queue.close()
        await outage_queue.close()
        await restored_queue.close()
        keys = [queued, processing, dead]
        keys.extend(queue._job_key(job) for job in created_jobs)
        keys.extend(effect_keys)
        if keys:
            await redis.delete(*set(keys))
        await redis.aclose()
