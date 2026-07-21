"""Dedicated durable Redis queue for official LangGraph drafting runs.

This deliberately mirrors the contract-ingestion queue's lease/heartbeat and
dead-letter guarantees, without sharing its queue names or handler.  Drafting
must never be sent through the in-memory ``BackgroundJobProcessor``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError

from ...core.config import settings


logger = logging.getLogger(__name__)
_BLOCKING_READ_TIMEOUT_SECONDS = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class DraftingQueue:
    def __init__(self) -> None:
        self._redis: Optional[Redis] = None
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._recovery_lock = asyncio.Lock()
        self._last_recovery = 0.0

    @property
    def enabled(self) -> bool:
        return bool(settings.DRAFTING_QUEUE_ENABLED)

    @property
    def redis_url(self) -> str:
        value = (settings.DRAFTING_QUEUE_REDIS_URL or settings.APP_REDIS_URL or "").strip()
        if not value:
            raise RuntimeError("DRAFTING_QUEUE_REDIS_URL (or APP_REDIS_URL) is required for drafting jobs")
        return value

    @property
    def visibility_timeout_seconds(self) -> int:
        return max(60, int(settings.DRAFTING_QUEUE_VISIBILITY_TIMEOUT_SECONDS))

    @property
    def heartbeat_seconds(self) -> int:
        return max(5, min(int(settings.DRAFTING_QUEUE_HEARTBEAT_SECONDS), self.visibility_timeout_seconds // 2))

    async def connect(self) -> Redis:
        if not self.enabled:
            raise RuntimeError("The dedicated drafting queue is disabled")
        if self._redis is None:
            redis = Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=None,
                retry_on_timeout=False,
            )
            try:
                await asyncio.wait_for(redis.ping(), timeout=3)
            except Exception:
                await redis.aclose()
                raise
            self._redis = redis
        return self._redis

    async def close(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    async def enqueue(self, job_id: str, payload: Dict[str, Any]) -> str:
        if not job_id:
            raise ValueError("job_id is required")
        redis = await self.connect()
        key = self._job_key(job_id)
        existing_status = await redis.hget(key, "status")
        if existing_status in {"queued", "running"}:
            return job_id
        await redis.hset(
            key,
            mapping={
                "payload": json.dumps(payload, sort_keys=True, default=str),
                "status": "queued",
                "attempts": "0",
                "queued_at": _now(),
                "updated_at": _now(),
                "visibility_timeout_seconds": str(self.visibility_timeout_seconds),
            },
        )
        await self._push_unique(redis, settings.DRAFTING_QUEUE_NAME, job_id)
        return job_id

    async def get_status(self, job_id: str) -> Dict[str, str]:
        redis = await self.connect()
        return await redis.hgetall(self._job_key(job_id))

    async def start_workers(self) -> None:
        if not self.enabled or self._running:
            return
        await self.connect()
        await self.requeue_orphaned_jobs()
        self._running = True
        for index in range(max(1, int(settings.DRAFTING_QUEUE_WORKERS))):
            self._tasks.append(asyncio.create_task(self._worker_loop(f"letter-drafting-{index}")))
        logger.info("Dedicated LangGraph drafting workers started count=%s", len(self._tasks))

    async def requeue_orphaned_jobs(self) -> int:
        redis = await self.connect()
        async with self._recovery_lock:
            processing = await redis.lrange(settings.DRAFTING_QUEUE_PROCESSING_NAME, 0, -1)
            recovered = 0
            now = datetime.now(timezone.utc)
            for job_id in dict.fromkeys(processing):
                metadata = await redis.hgetall(self._job_key(job_id))
                if not metadata:
                    await redis.lrem(settings.DRAFTING_QUEUE_PROCESSING_NAME, 0, job_id)
                    continue
                status = str(metadata.get("status") or "")
                heartbeat = _parse(metadata.get("heartbeat_at") or metadata.get("started_at") or metadata.get("updated_at"))
                stale = heartbeat is None or now - heartbeat > timedelta(seconds=self.visibility_timeout_seconds)
                if status in {"completed", "cancelled", "failed"}:
                    await redis.lrem(settings.DRAFTING_QUEUE_PROCESSING_NAME, 0, job_id)
                elif stale:
                    await redis.hset(
                        self._job_key(job_id),
                        mapping={"status": "queued", "updated_at": _now(), "recovery_reason": "visibility_timeout"},
                    )
                    await redis.lrem(settings.DRAFTING_QUEUE_PROCESSING_NAME, 0, job_id)
                    await self._push_unique(redis, settings.DRAFTING_QUEUE_NAME, job_id)
                    recovered += 1
            self._last_recovery = time.monotonic()
            return recovered

    async def _worker_loop(self, worker_name: str) -> None:
        backoff = 0.5
        while self._running:
            try:
                redis = await self.connect()
                job_id = await redis.brpoplpush(
                    settings.DRAFTING_QUEUE_NAME,
                    settings.DRAFTING_QUEUE_PROCESSING_NAME,
                    timeout=_BLOCKING_READ_TIMEOUT_SECONDS,
                )
                backoff = 0.5
                if not job_id:
                    if time.monotonic() - self._last_recovery > 30:
                        await self.requeue_orphaned_jobs()
                    continue
                await self._process_job(job_id, worker_name)
            except asyncio.CancelledError:
                break
            except (RedisTimeoutError, RedisConnectionError, RedisError) as exc:
                logger.warning("Drafting queue broker error worker=%s error=%s", worker_name, exc)
                await self._reset_connection()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15)
            except Exception:
                logger.exception("Drafting queue worker failed worker=%s", worker_name)

    async def _process_job(self, job_id: str, worker_name: str) -> None:
        redis = await self.connect()
        key = self._job_key(job_id)
        raw = await redis.hget(key, "payload")
        if not raw:
            await redis.lrem(settings.DRAFTING_QUEUE_PROCESSING_NAME, 1, job_id)
            return
        attempts = int(await redis.hget(key, "attempts") or "0") + 1
        await redis.hset(
            key,
            mapping={
                "status": "running",
                "attempts": str(attempts),
                "worker": worker_name,
                "started_at": _now(),
                "heartbeat_at": _now(),
                "updated_at": _now(),
            },
        )
        heartbeat = asyncio.create_task(self._heartbeat(job_id, worker_name))
        try:
            from .langgraph_engine import process_drafting_job

            await process_drafting_job(json.loads(raw), worker_name=worker_name)
            await redis.hset(key, mapping={"status": "completed", "completed_at": _now(), "updated_at": _now()})
            await redis.lrem(settings.DRAFTING_QUEUE_PROCESSING_NAME, 1, job_id)
        except Exception as exc:
            max_retries = max(1, int(settings.DRAFTING_QUEUE_MAX_RETRIES))
            terminal = attempts >= max_retries
            await redis.hset(
                key,
                mapping={
                    "status": "failed" if terminal else "queued",
                    "last_error": str(exc)[:2000],
                    "updated_at": _now(),
                    "failed_at": _now() if terminal else "",
                },
            )
            await redis.lrem(settings.DRAFTING_QUEUE_PROCESSING_NAME, 1, job_id)
            if terminal:
                await self._push_unique(redis, settings.DRAFTING_QUEUE_DEADLETTER_NAME, job_id)
            else:
                await asyncio.sleep(min(2**attempts, 10))
                await self._push_unique(redis, settings.DRAFTING_QUEUE_NAME, job_id)
            raise
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await heartbeat

    async def _heartbeat(self, job_id: str, worker_name: str) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            redis = await self.connect()
            await redis.hset(self._job_key(job_id), mapping={"heartbeat_at": _now(), "updated_at": _now(), "worker": worker_name})

    async def _reset_connection(self) -> None:
        if self._redis:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
        self._redis = None

    @staticmethod
    async def _push_unique(redis: Redis, list_name: str, job_id: str) -> None:
        await redis.lrem(list_name, 0, job_id)
        await redis.rpush(list_name, job_id)

    @staticmethod
    def _job_key(job_id: str) -> str:
        return f"letter_drafting_job:{job_id}"


_queue: Optional[DraftingQueue] = None


def get_drafting_queue() -> DraftingQueue:
    global _queue
    if _queue is None:
        _queue = DraftingQueue()
    return _queue


async def start_drafting_queue() -> None:
    queue = get_drafting_queue()
    if not queue.enabled:
        logger.info("Dedicated LangGraph drafting queue disabled")
        return
    await queue.start_workers()


async def stop_drafting_queue() -> None:
    await get_drafting_queue().close()
