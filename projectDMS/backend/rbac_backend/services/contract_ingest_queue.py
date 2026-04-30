from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from redis.asyncio import Redis

from ..core.config import settings

logger = logging.getLogger(__name__)
REDIS_CONNECT_TIMEOUT_SECONDS = 2.0
REDIS_PING_TIMEOUT_SECONDS = 3.0


def _utc_now() -> str:
    return datetime.utcnow().isoformat()


class ContractIngestQueue:
    def __init__(self) -> None:
        self._redis: Optional[Redis] = None
        self._worker_tasks: list[asyncio.Task] = []
        self._running = False

    @property
    def enabled(self) -> bool:
        return bool(settings.CONTRACT_QUEUE_ENABLED)

    @property
    def redis_url(self) -> str:
        return settings.CONTRACT_QUEUE_REDIS_URL or settings.FALKORDB_URL

    async def connect(self) -> Optional[Redis]:
        if not self.enabled:
            return None
        if self._redis is None:
            self._redis = Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
                socket_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
                retry_on_timeout=False,
            )
            try:
                await asyncio.wait_for(self._redis.ping(), timeout=REDIS_PING_TIMEOUT_SECONDS)
            except Exception:
                await self._redis.aclose()
                self._redis = None
                raise
        return self._redis

    async def close(self) -> None:
        self._running = False
        for task in self._worker_tasks:
            task.cancel()
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def start_workers(self) -> None:
        if not self.enabled or self._running:
            return
        await self.connect()
        await self.requeue_orphaned_jobs()
        self._running = True
        worker_count = max(1, int(settings.CONTRACT_QUEUE_WORKERS))
        for idx in range(worker_count):
            task = asyncio.create_task(self._worker_loop(f"contract-worker-{idx}"))
            self._worker_tasks.append(task)

    async def enqueue(self, payload: Dict[str, Any]) -> str:
        redis = await self.connect()
        if redis is None:
            raise RuntimeError("Contract ingestion queue is disabled")

        job_id = str(payload.get("upload_id") or "")
        if not job_id:
            raise ValueError("upload_id is required for contract ingest jobs")

        key = self._job_key(job_id)
        existing_status = await redis.hget(key, "status")
        if existing_status in {"queued", "running"}:
            return job_id

        await redis.hset(
            key,
            mapping={
                "payload": json.dumps(payload),
                "status": "queued",
                "attempts": "0",
                "queued_at": _utc_now(),
                "updated_at": _utc_now(),
            },
        )
        await redis.rpush(settings.CONTRACT_QUEUE_NAME, job_id)
        return job_id

    async def requeue_orphaned_jobs(self) -> None:
        redis = await self.connect()
        if redis is None:
            return
        processing_jobs = await redis.lrange(
            settings.CONTRACT_QUEUE_PROCESSING_NAME, 0, -1
        )
        if not processing_jobs:
            return
        if processing_jobs:
            await redis.delete(settings.CONTRACT_QUEUE_PROCESSING_NAME)
        for job_id in processing_jobs:
            await redis.hset(
                self._job_key(job_id),
                mapping={
                    "status": "queued",
                    "updated_at": _utc_now(),
                    "recovered_at": _utc_now(),
                },
            )
            await redis.rpush(settings.CONTRACT_QUEUE_NAME, job_id)

    async def _worker_loop(self, worker_name: str) -> None:
        redis = await self.connect()
        if redis is None:
            return
        while self._running:
            try:
                job_id = await redis.brpoplpush(
                    settings.CONTRACT_QUEUE_NAME,
                    settings.CONTRACT_QUEUE_PROCESSING_NAME,
                    timeout=1,
                )
                if not job_id:
                    continue
                await self._process_job(job_id, worker_name)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Contract ingest worker failure: %s", exc)

    async def _process_job(self, job_id: str, worker_name: str) -> None:
        redis = await self.connect()
        if redis is None:
            return

        key = self._job_key(job_id)
        payload_raw = await redis.hget(key, "payload")
        if not payload_raw:
            await redis.lrem(settings.CONTRACT_QUEUE_PROCESSING_NAME, 1, job_id)
            return

        payload = json.loads(payload_raw)
        attempts = int(await redis.hget(key, "attempts") or "0") + 1
        await redis.hset(
            key,
            mapping={
                "status": "running",
                "attempts": str(attempts),
                "started_at": _utc_now(),
                "updated_at": _utc_now(),
                "worker": worker_name,
            },
        )

        try:
            from .contract_service import process_contract_ingest_job

            await process_contract_ingest_job(payload)
            await redis.hset(
                key,
                mapping={
                    "status": "completed",
                    "completed_at": _utc_now(),
                    "updated_at": _utc_now(),
                },
            )
            await redis.lrem(settings.CONTRACT_QUEUE_PROCESSING_NAME, 1, job_id)
        except Exception as exc:
            logger.error("Contract ingest job %s failed: %s", job_id, exc)
            retries = max(1, int(settings.CONTRACT_QUEUE_MAX_RETRIES))
            await redis.hset(
                key,
                mapping={
                    "status": "failed" if attempts >= retries else "queued",
                    "last_error": str(exc),
                    "updated_at": _utc_now(),
                },
            )
            await redis.lrem(settings.CONTRACT_QUEUE_PROCESSING_NAME, 1, job_id)
            if attempts >= retries:
                await redis.rpush(settings.CONTRACT_QUEUE_DEADLETTER_NAME, job_id)
            else:
                await asyncio.sleep(min(2 ** attempts, 10))
                await redis.rpush(settings.CONTRACT_QUEUE_NAME, job_id)

    @staticmethod
    def _job_key(job_id: str) -> str:
        return f"contract_ingest_job:{job_id}"


_queue: Optional[ContractIngestQueue] = None


def get_contract_ingest_queue() -> ContractIngestQueue:
    global _queue
    if _queue is None:
        _queue = ContractIngestQueue()
    return _queue


async def start_contract_ingest_queue() -> None:
    queue = get_contract_ingest_queue()
    if not queue.enabled:
        logger.info("Contract ingestion queue disabled")
        return
    try:
        await queue.start_workers()
        logger.info("Contract ingestion queue started")
    except Exception as exc:
        logger.warning(
            "Contract ingestion queue unavailable; continuing without queue startup: %s",
            exc,
        )


async def stop_contract_ingest_queue() -> None:
    queue = get_contract_ingest_queue()
    await queue.close()
