"""Durable Redis queue for governed arbitration filing-bundle exports.

Redis owns delivery and visibility leases; Mongo owns the authoritative export
effect.  A worker may rebuild bytes after a crash, but only the holder of the
current Mongo lease can publish them, and a completed effect key is never
executed again.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError

from ...core.config import settings
from ..observability import observability_registry


logger = logging.getLogger(__name__)
_BLOCKING_READ_SECONDS = 5

_ACQUIRE_LEASE = """
local status = redis.call('HGET', KEYS[1], 'status')
local expires = tonumber(redis.call('HGET', KEYS[1], 'lease_expires_epoch') or '0')
if status == 'completed' or status == 'failed' then return 0 end
if status == 'running' and expires > tonumber(ARGV[2]) then return 0 end
local attempts = redis.call('HINCRBY', KEYS[1], 'attempts', 1)
redis.call('HSET', KEYS[1],
  'status', 'running', 'lease_token', ARGV[1],
  'lease_expires_epoch', ARGV[3], 'worker', ARGV[4],
  'started_at', ARGV[5], 'heartbeat_at', ARGV[5], 'updated_at', ARGV[5])
return attempts
"""

_HEARTBEAT_LEASE = """
if redis.call('HGET', KEYS[1], 'lease_token') ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'lease_expires_epoch', ARGV[2],
  'heartbeat_at', ARGV[3], 'updated_at', ARGV[3])
return 1
"""

_ACK_JOB = """
if redis.call('HGET', KEYS[1], 'lease_token') ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'status', 'completed', 'completed_at', ARGV[2],
  'updated_at', ARGV[2], 'lease_token', '', 'lease_expires_epoch', '0')
redis.call('LREM', KEYS[2], 0, ARGV[3])
redis.call('LREM', KEYS[3], 0, ARGV[3])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
return 1
"""

_FAIL_JOB = """
if redis.call('HGET', KEYS[1], 'lease_token') ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'status', ARGV[2], 'last_error', ARGV[3],
  'updated_at', ARGV[4], 'lease_token', '', 'lease_expires_epoch', '0')
redis.call('LREM', KEYS[2], 0, ARGV[5])
redis.call('LREM', KEYS[3], 0, ARGV[5])
redis.call('LREM', KEYS[4], 0, ARGV[5])
if ARGV[2] == 'queued' then
  redis.call('RPUSH', KEYS[3], ARGV[5])
else
  redis.call('RPUSH', KEYS[4], ARGV[5])
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[6]))
end
return 1
"""

_RECOVER_JOB = """
local status = redis.call('HGET', KEYS[1], 'status')
local expires = tonumber(redis.call('HGET', KEYS[1], 'lease_expires_epoch') or '0')
if status == 'completed' or status == 'failed' then
  redis.call('LREM', KEYS[2], 0, ARGV[1])
  return 0
end
if status == 'running' and expires > tonumber(ARGV[2]) then return 0 end
redis.call('HSET', KEYS[1], 'status', 'queued', 'lease_token', '',
  'lease_expires_epoch', '0', 'recovery_reason', 'visibility_timeout',
  'recovered_at', ARGV[3], 'updated_at', ARGV[3])
redis.call('LREM', KEYS[2], 0, ARGV[1])
redis.call('LREM', KEYS[3], 0, ARGV[1])
redis.call('RPUSH', KEYS[3], ARGV[1])
return 1
"""

_PUSH_UNIQUE = """
redis.call('LREM', KEYS[1], 0, ARGV[1])
redis.call('RPUSH', KEYS[1], ARGV[1])
return 1
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


def _transient(exc: Exception) -> bool:
    if isinstance(exc, HTTPException):
        return int(exc.status_code) >= 500
    return isinstance(
        exc,
        (
            asyncio.TimeoutError,
            TimeoutError,
            ConnectionError,
            OSError,
            RedisConnectionError,
            RedisTimeoutError,
            RedisError,
        ),
    )


class FilingExportQueue:
    def __init__(self) -> None:
        self._redis: Optional[Redis] = None
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._recovery_lock = asyncio.Lock()
        self._last_recovery = 0.0

    @property
    def enabled(self) -> bool:
        return bool(settings.FILING_EXPORT_QUEUE_ENABLED)

    @property
    def redis_url(self) -> str:
        value = (settings.FILING_EXPORT_QUEUE_REDIS_URL or settings.APP_REDIS_URL or "").strip()
        if not value:
            raise RuntimeError("FILING_EXPORT_QUEUE_REDIS_URL (or APP_REDIS_URL) is required")
        return value

    @property
    def visibility_timeout_seconds(self) -> int:
        return max(60, int(settings.FILING_EXPORT_QUEUE_VISIBILITY_TIMEOUT_SECONDS))

    @property
    def heartbeat_seconds(self) -> int:
        return max(5, min(int(settings.FILING_EXPORT_QUEUE_HEARTBEAT_SECONDS), self.visibility_timeout_seconds // 2))

    @property
    def metadata_ttl_seconds(self) -> int:
        return max(3600, int(settings.FILING_EXPORT_QUEUE_METADATA_TTL_SECONDS))

    async def connect(self) -> Redis:
        if not self.enabled:
            raise RuntimeError("The durable filing export queue is disabled")
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
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def enqueue(self, *, job_id: str, effect_key: str, payload: Dict[str, Any]) -> str:
        if not job_id or not effect_key:
            raise ValueError("job_id and effect_key are required")
        redis = await self.connect()
        key = self._job_key(job_id)
        existing_effect = await redis.hget(key, "effect_key")
        if existing_effect and existing_effect != effect_key:
            raise RuntimeError("Filing export job ID is already bound to another effect key")
        existing_status = await redis.hget(key, "status")
        if existing_status in {"queued", "running", "completed"}:
            return job_id
        timestamp = _now()
        await redis.hset(
            key,
            mapping={
                "payload": json.dumps(payload, sort_keys=True, default=str),
                "effect_key": effect_key,
                "status": "queued",
                "attempts": str(await redis.hget(key, "attempts") or "0"),
                "queued_at": timestamp,
                "updated_at": timestamp,
                "visibility_timeout_seconds": str(self.visibility_timeout_seconds),
            },
        )
        await redis.eval(_PUSH_UNIQUE, 1, settings.FILING_EXPORT_QUEUE_NAME, job_id)
        return job_id

    async def health(self) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "ready": False, "reason": "disabled"}
        try:
            redis = await self.connect()
            queued, processing, deadletter = await asyncio.gather(
                redis.llen(settings.FILING_EXPORT_QUEUE_NAME),
                redis.llen(settings.FILING_EXPORT_QUEUE_PROCESSING_NAME),
                redis.llen(settings.FILING_EXPORT_QUEUE_DEADLETTER_NAME),
            )
            return {
                "enabled": True,
                "ready": True,
                "queued": int(queued),
                "processing": int(processing),
                "deadletter": int(deadletter),
                "visibility_timeout_seconds": self.visibility_timeout_seconds,
            }
        except Exception as exc:
            return {"enabled": True, "ready": False, "reason": type(exc).__name__}

    async def start_workers(self) -> None:
        if not self.enabled or self._running:
            return
        await self.connect()
        await self.requeue_orphaned_jobs(force=True)
        self._running = True
        for index in range(max(1, int(settings.FILING_EXPORT_QUEUE_WORKERS))):
            self._tasks.append(asyncio.create_task(self._worker_loop(f"filing-export-{index}")))
        logger.info("Durable filing export workers started count=%s", len(self._tasks))

    async def requeue_orphaned_jobs(self, *, force: bool = False) -> int:
        if not force and time.monotonic() - self._last_recovery < min(60, self.visibility_timeout_seconds / 3):
            return 0
        redis = await self.connect()
        async with self._recovery_lock:
            jobs = await redis.lrange(settings.FILING_EXPORT_QUEUE_PROCESSING_NAME, 0, -1)
            recovered = 0
            now_epoch = _epoch()
            timestamp = _now()
            for job_id in dict.fromkeys(jobs):
                key = self._job_key(job_id)
                if not await redis.exists(key):
                    await redis.lrem(settings.FILING_EXPORT_QUEUE_PROCESSING_NAME, 0, job_id)
                    continue
                recovered += int(
                    await redis.eval(
                        _RECOVER_JOB,
                        3,
                        key,
                        settings.FILING_EXPORT_QUEUE_PROCESSING_NAME,
                        settings.FILING_EXPORT_QUEUE_NAME,
                        job_id,
                        now_epoch,
                        timestamp,
                    )
                    or 0
                )
            self._last_recovery = time.monotonic()
            if recovered:
                await observability_registry.record_arbitration_runtime_event(
                    signal="lease_expiry_recovery", node="filing_export_worker", reason="visibility_timeout", count=recovered
                )
            return recovered

    async def _worker_loop(self, worker_name: str) -> None:
        backoff = 0.5
        while self._running:
            try:
                redis = await self.connect()
                job_id = await redis.brpoplpush(
                    settings.FILING_EXPORT_QUEUE_NAME,
                    settings.FILING_EXPORT_QUEUE_PROCESSING_NAME,
                    timeout=_BLOCKING_READ_SECONDS,
                )
                backoff = 0.5
                if not job_id:
                    await self.requeue_orphaned_jobs()
                    continue
                await self._process_job(job_id, worker_name)
            except asyncio.CancelledError:
                break
            except (RedisTimeoutError, RedisConnectionError, RedisError) as exc:
                logger.warning("Filing export Redis error worker=%s error=%s", worker_name, exc)
                await self._reset_connection()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15)
            except Exception:
                logger.exception("Filing export worker iteration failed worker=%s", worker_name)

    async def _process_job(self, job_id: str, worker_name: str) -> None:
        redis = await self.connect()
        key = self._job_key(job_id)
        raw = await redis.hget(key, "payload")
        if not raw:
            await redis.lrem(settings.FILING_EXPORT_QUEUE_PROCESSING_NAME, 0, job_id)
            return
        token = str(uuid.uuid4())
        now_epoch = _epoch()
        attempts = int(
            await redis.eval(
                _ACQUIRE_LEASE,
                1,
                key,
                token,
                now_epoch,
                now_epoch + self.visibility_timeout_seconds,
                worker_name,
                _now(),
            )
            or 0
        )
        if attempts <= 0:
            await redis.lrem(settings.FILING_EXPORT_QUEUE_PROCESSING_NAME, 1, job_id)
            return
        queued_at = await redis.hget(key, "queued_at")
        if queued_at:
            try:
                queued_value = datetime.fromisoformat(str(queued_at).replace("Z", "+00:00"))
                if queued_value.tzinfo is None:
                    queued_value = queued_value.replace(tzinfo=timezone.utc)
                await observability_registry.record_arbitration_runtime_value(
                    signal="filing_export_queue_lag_seconds",
                    scope="worker",
                    value=max(0.0, (datetime.now(timezone.utc) - queued_value).total_seconds()),
                )
            except ValueError:
                pass
        await observability_registry.record_arbitration_runtime_event(
            signal="node_attempt", node="filing_export_worker", reason="delivery"
        )
        payload = json.loads(raw)
        heartbeat = asyncio.create_task(self._heartbeat(job_id, payload, token, worker_name))
        effect_completed = False
        try:
            await process_filing_export_job(
                payload,
                lease_token=token,
                lease_seconds=self.visibility_timeout_seconds,
                worker_name=worker_name,
            )
            effect_completed = True
            acknowledged = await redis.eval(
                _ACK_JOB,
                3,
                key,
                settings.FILING_EXPORT_QUEUE_PROCESSING_NAME,
                settings.FILING_EXPORT_QUEUE_NAME,
                token,
                _now(),
                job_id,
                self.metadata_ttl_seconds,
            )
            if not acknowledged:
                raise RuntimeError("Filing export completion lost its Redis lease")
        except Exception as exc:
            if effect_completed:
                # Mongo already owns the completed effect. Leave Redis in the
                # processing list so visibility recovery can idempotently ack it;
                # never downgrade a committed export because broker ack failed.
                raise
            terminal = attempts >= max(1, int(settings.FILING_EXPORT_QUEUE_MAX_RETRIES)) or not _transient(exc)
            await record_filing_export_failure(payload, lease_token=token, terminal=terminal, error=str(exc))
            await redis.eval(
                _FAIL_JOB,
                4,
                key,
                settings.FILING_EXPORT_QUEUE_PROCESSING_NAME,
                settings.FILING_EXPORT_QUEUE_NAME,
                settings.FILING_EXPORT_QUEUE_DEADLETTER_NAME,
                token,
                "failed" if terminal else "queued",
                str(exc)[:2000],
                _now(),
                job_id,
                self.metadata_ttl_seconds,
            )
            if terminal:
                await observability_registry.record_arbitration_runtime_event(
                    signal="export_blocked", node="filing_export_worker", reason="terminal_failure"
                )
                logger.error("Filing export failed permanently job_id=%s attempts=%s", job_id, attempts)
            else:
                await observability_registry.record_arbitration_runtime_event(
                    signal="retry", node="filing_export_worker", reason=type(exc).__name__
                )
                logger.warning("Filing export scheduled for retry job_id=%s attempts=%s", job_id, attempts)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await heartbeat

    async def _heartbeat(self, job_id: str, payload: Dict[str, Any], token: str, worker_name: str) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            redis = await self.connect()
            now_epoch = _epoch()
            timestamp = _now()
            renewed = await redis.eval(
                _HEARTBEAT_LEASE,
                1,
                self._job_key(job_id),
                token,
                now_epoch + self.visibility_timeout_seconds,
                timestamp,
            )
            if not renewed:
                raise RuntimeError("Filing export Redis lease was lost")
            await heartbeat_filing_export_job(
                payload,
                lease_token=token,
                lease_seconds=self.visibility_timeout_seconds,
                worker_name=worker_name,
            )

    async def _reset_connection(self) -> None:
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
        self._redis = None

    @staticmethod
    def _job_key(job_id: str) -> str:
        return f"arbitration_filing_export_job:{job_id}"


async def process_filing_export_job(
    payload: Dict[str, Any], *, lease_token: str, lease_seconds: int, worker_name: str
) -> Dict[str, Any]:
    from ...core.database import get_database
    from .case_workspace import ArbitrationCaseWorkspaceService

    db = await get_database()
    return await ArbitrationCaseWorkspaceService(db).execute_filing_bundle_export_job(
        str(payload["case_id"]),
        str(payload["export_id"]),
        str(payload["format"]),
        effect_key=str(payload["effect_key"]),
        lease_token=lease_token,
        lease_seconds=lease_seconds,
        worker_name=worker_name,
    )


async def heartbeat_filing_export_job(
    payload: Dict[str, Any], *, lease_token: str, lease_seconds: int, worker_name: str
) -> None:
    from ...core.database import get_database
    from .case_workspace import ArbitrationCaseWorkspaceService

    db = await get_database()
    await ArbitrationCaseWorkspaceService(db).heartbeat_filing_bundle_export_job(
        str(payload["case_id"]),
        str(payload["export_id"]),
        effect_key=str(payload["effect_key"]),
        lease_token=lease_token,
        lease_seconds=lease_seconds,
        worker_name=worker_name,
    )


async def record_filing_export_failure(
    payload: Dict[str, Any], *, lease_token: str, terminal: bool, error: str
) -> None:
    from ...core.database import get_database
    from .case_workspace import ArbitrationCaseWorkspaceService

    db = await get_database()
    await ArbitrationCaseWorkspaceService(db).record_filing_bundle_export_failure(
        str(payload["case_id"]),
        str(payload["export_id"]),
        effect_key=str(payload["effect_key"]),
        lease_token=lease_token,
        terminal=terminal,
        error=error,
    )


_queue: Optional[FilingExportQueue] = None


def get_filing_export_queue() -> FilingExportQueue:
    global _queue
    if _queue is None:
        _queue = FilingExportQueue()
    return _queue


async def start_filing_export_queue() -> None:
    await get_filing_export_queue().start_workers()


async def stop_filing_export_queue() -> None:
    await get_filing_export_queue().close()
