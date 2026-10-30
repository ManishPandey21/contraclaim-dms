from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlsplit, urlunsplit

from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError

from ..core.config import settings

logger = logging.getLogger(__name__)
REDIS_CONNECT_TIMEOUT_SECONDS = 2.0
REDIS_PING_TIMEOUT_SECONDS = 3.0
REDIS_BLOCKING_READ_TIMEOUT_SECONDS = 5
REDIS_TIMEOUT_BACKOFF_INITIAL_SECONDS = 0.5
REDIS_TIMEOUT_BACKOFF_MAX_SECONDS = 15.0


def _utc_now() -> str:
    return datetime.utcnow().isoformat()


def _utc_now_dt() -> datetime:
    return datetime.utcnow()


def _parse_utc_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


@dataclass(frozen=True)
class ContractQueueEnqueueResult:
    job_id: str
    status: str = "queued"
    degraded: bool = False
    error: Optional[str] = None


class ContractIngestQueue:
    def __init__(self) -> None:
        self._redis: Optional[Redis] = None
        self._worker_tasks: list[asyncio.Task] = []
        self._recovery_lock = asyncio.Lock()
        self._last_recovery_scan_monotonic = 0.0
        self._running = False
        self._logged_connection = False

    @property
    def enabled(self) -> bool:
        return bool(settings.CONTRACT_QUEUE_ENABLED)

    @property
    def redis_url(self) -> str:
        # The queue broker is the app's Redis — never the graph database. Falling
        # back to FALKORDB_URL silently routed jobs into FalkorDB (which then
        # rejected writes when that instance was a read-only replica). Require a
        # real Redis URL and fail loudly otherwise.
        url = (settings.CONTRACT_QUEUE_REDIS_URL or settings.APP_REDIS_URL or "").strip()
        if not url:
            raise RuntimeError(
                "Contract ingest queue needs a Redis broker: set CONTRACT_QUEUE_REDIS_URL "
                "(or APP_REDIS_URL). It no longer falls back to FALKORDB_URL — FalkorDB is "
                "the graph database, not a job broker."
            )
        return url

    async def connect(self) -> Optional[Redis]:
        if not self.enabled:
            return None
        if self._redis is None:
            self._redis = Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
                # This connection performs blocking queue reads. The Redis
                # command timeout controls how long BRPOPLPUSH blocks, so the
                # socket read timeout must not be shorter than that blocking
                # timeout.
                socket_timeout=None,
                retry_on_timeout=False,
            )
            try:
                await asyncio.wait_for(self._redis.ping(), timeout=REDIS_PING_TIMEOUT_SECONDS)
            except Exception:
                await self._redis.aclose()
                self._redis = None
                raise
            if not self._logged_connection:
                logger.info(
                    "Contract ingest Redis broker connected url=%s db=%s queue=%s processing_queue=%s",
                    self.safe_redis_url,
                    self.redis_db_number,
                    settings.CONTRACT_QUEUE_NAME,
                    settings.CONTRACT_QUEUE_PROCESSING_NAME,
                )
                self._logged_connection = True
        return self._redis

    @property
    def safe_redis_url(self) -> str:
        return self._redact_url(self.redis_url)

    @property
    def redis_db_number(self) -> int:
        return self._redis_db_number(self.redis_url)

    @property
    def visibility_timeout_seconds(self) -> int:
        return max(60, int(settings.CONTRACT_QUEUE_VISIBILITY_TIMEOUT_SECONDS))

    @property
    def heartbeat_seconds(self) -> int:
        # Heartbeats must be frequent enough that a slow but healthy ingestion
        # job is not mistaken for an orphan, while still keeping Redis writes low.
        return max(5, min(int(settings.CONTRACT_QUEUE_HEARTBEAT_SECONDS), self.visibility_timeout_seconds // 2))

    @staticmethod
    def _redact_url(url: str) -> str:
        try:
            parsed = urlsplit(url)
            netloc = parsed.netloc
            if "@" in netloc:
                userinfo, host = netloc.rsplit("@", 1)
                if ":" in userinfo:
                    username, _password = userinfo.split(":", 1)
                    userinfo = f"{username}:***"
                else:
                    userinfo = "***"
                netloc = f"{userinfo}@{host}"
            return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
        except Exception:
            return "redis://***"

    @staticmethod
    def _redis_db_number(url: str) -> int:
        try:
            parsed = urlsplit(url)
            query_db = parse_qs(parsed.query).get("db")
            if query_db and query_db[0].isdigit():
                return int(query_db[0])
            path_value = (parsed.path or "").strip("/")
            return int(path_value) if path_value.isdigit() else 0
        except Exception:
            return 0

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
        logger.info(
            "Contract ingest workers started workers=%s queue=%s redis=%s db=%s",
            worker_count,
            settings.CONTRACT_QUEUE_NAME,
            self.safe_redis_url,
            self.redis_db_number,
        )

    async def enqueue(self, payload: Dict[str, Any]) -> str:
        redis = await self.connect()
        if redis is None:
            raise RuntimeError("Contract ingestion queue is disabled")

        job_id = str(payload.get("upload_id") or "")
        if not job_id:
            raise ValueError("upload_id is required for contract ingest jobs")

        key = self._job_key(job_id)
        existing_status = await redis.hget(key, "status")
        # "waiting" is a job between attempts: it is already on its way back.
        if existing_status in {"queued", "running", "waiting"}:
            return job_id

        await redis.hset(
            key,
            mapping={
                "payload": json.dumps(payload),
                "status": "queued",
                "attempts": "0",
                "queued_at": _utc_now(),
                "updated_at": _utc_now(),
                "visibility_timeout_seconds": str(self.visibility_timeout_seconds),
            },
        )
        await self._push_unique(redis, settings.CONTRACT_QUEUE_NAME, job_id)
        return job_id

    async def requeue_orphaned_jobs(self) -> None:
        """Recover only genuinely stale jobs from the processing list.

        Older code deleted the whole processing list on startup and requeued every
        entry. In a multi-process deployment that can duplicate active OCR/AI
        ingestion work. The recovery path now behaves like a visibility timeout:
        completed/failed records are cleaned up, fresh running records are left
        alone, and only stale records whose heartbeat has expired are requeued.
        """
        await self._recover_stale_processing_jobs(force=True)

    async def _recover_stale_processing_jobs(self, *, force: bool = False) -> int:
        redis = await self.connect()
        if redis is None:
            return 0
        if not force:
            now_monotonic = time.monotonic()
            recovery_interval = min(60.0, max(10.0, self.visibility_timeout_seconds / 3))
            if now_monotonic - self._last_recovery_scan_monotonic < recovery_interval:
                return 0
        async with self._recovery_lock:
            if not force:
                now_monotonic = time.monotonic()
                recovery_interval = min(60.0, max(10.0, self.visibility_timeout_seconds / 3))
                if now_monotonic - self._last_recovery_scan_monotonic < recovery_interval:
                    return 0
            recovered = await self._recover_stale_processing_jobs_once(redis)
            self._last_recovery_scan_monotonic = time.monotonic()
            return recovered

    async def _recover_stale_processing_jobs_once(self, redis: Redis) -> int:
        processing_jobs = await redis.lrange(
            settings.CONTRACT_QUEUE_PROCESSING_NAME, 0, -1
        )
        if not processing_jobs:
            return 0
        recovered = 0
        now = _utc_now_dt()
        seen: set[str] = set()
        for job_id in processing_jobs:
            if job_id in seen:
                continue
            seen.add(job_id)
            key = self._job_key(job_id)
            metadata = await redis.hgetall(key)
            if not metadata:
                await redis.lrem(settings.CONTRACT_QUEUE_PROCESSING_NAME, 0, job_id)
                logger.warning("Removed processing queue entry with missing metadata job_id=%s", job_id)
                continue

            status = str(metadata.get("status") or "").lower()
            if status in {"completed", "failed", "superseded"}:
                await redis.lrem(settings.CONTRACT_QUEUE_PROCESSING_NAME, 0, job_id)
                continue
            # A job waiting out a backoff or its turn keeps its worker's
            # heartbeat: it is recovered only when that worker is gone.
            if status in {"running", "waiting"} and not self._job_is_stale(metadata, now):
                continue

            await redis.hset(
                key,
                mapping={
                    "status": "queued",
                    "updated_at": _utc_now(),
                    "recovered_at": _utc_now(),
                    "recovery_reason": "visibility_timeout",
                },
            )
            await redis.lrem(settings.CONTRACT_QUEUE_PROCESSING_NAME, 0, job_id)
            await self._push_unique(redis, settings.CONTRACT_QUEUE_NAME, job_id)
            recovered += 1
            logger.warning(
                "Recovered stale contract ingest job job_id=%s previous_status=%s visibility_timeout_seconds=%s",
                job_id,
                status or "unknown",
                self.visibility_timeout_seconds,
            )
        return recovered

    async def _worker_loop(self, worker_name: str) -> None:
        redis = await self.connect()
        if redis is None:
            return
        backoff = REDIS_TIMEOUT_BACKOFF_INITIAL_SECONDS
        while self._running:
            try:
                job_id = await redis.brpoplpush(
                    settings.CONTRACT_QUEUE_NAME,
                    settings.CONTRACT_QUEUE_PROCESSING_NAME,
                    timeout=REDIS_BLOCKING_READ_TIMEOUT_SECONDS,
                )
                backoff = REDIS_TIMEOUT_BACKOFF_INITIAL_SECONDS
                if not job_id:
                    await self._recover_stale_processing_jobs()
                    continue
                await self._process_job(job_id, worker_name)
            except asyncio.CancelledError:
                break
            except (RedisTimeoutError, RedisConnectionError) as exc:
                logger.warning(
                    "Contract ingest worker Redis timeout/connection issue worker=%s queue=%s redis=%s db=%s backoff=%.1fs error=%s",
                    worker_name,
                    settings.CONTRACT_QUEUE_NAME,
                    self.safe_redis_url,
                    self.redis_db_number,
                    backoff,
                    exc,
                )
                await self._reset_redis_connection()
                await asyncio.sleep(backoff)
                redis = await self._connect_with_backoff(worker_name, backoff)
                backoff = min(backoff * 2, REDIS_TIMEOUT_BACKOFF_MAX_SECONDS)
                if redis is None:
                    break
            except Exception as exc:
                logger.error("Contract ingest worker failure: %s", exc)

    async def _connect_with_backoff(self, worker_name: str, backoff: float) -> Optional[Redis]:
        while self._running:
            try:
                redis = await self.connect()
                if redis is not None:
                    return redis
            except (RedisTimeoutError, RedisConnectionError, RedisError) as exc:
                logger.warning(
                    "Contract ingest worker Redis reconnect failed worker=%s queue=%s backoff=%.1fs error=%s",
                    worker_name,
                    settings.CONTRACT_QUEUE_NAME,
                    backoff,
                    exc,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, REDIS_TIMEOUT_BACKOFF_MAX_SECONDS)
        return None

    async def _reset_redis_connection(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.aclose()
        except Exception:
            pass
        finally:
            self._redis = None

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
                "heartbeat_at": _utc_now(),
                "updated_at": _utc_now(),
                "worker": worker_name,
                "visibility_timeout_seconds": str(self.visibility_timeout_seconds),
            },
        )

        heartbeat_task = asyncio.create_task(self._heartbeat_job(job_id, worker_name))
        try:
            from .contract_service import process_contract_ingest_job

            await process_contract_ingest_job(payload)
            await redis.hset(
                key,
                mapping={
                    "status": "completed",
                    "completed_at": _utc_now(),
                    "updated_at": _utc_now(),
                    "heartbeat_at": _utc_now(),
                },
            )
            await redis.lrem(settings.CONTRACT_QUEUE_PROCESSING_NAME, 1, job_id)
        except Exception as exc:
            from .contract_source_lease import ContractSourceBusy, LostSourceLease

            # A lost lease with no live owner left is an ordinary failed attempt:
            # the source it tainted needs a clean ingest, and only a retry makes
            # one. Only a live owner is yielded to.
            yields = isinstance(exc, ContractSourceBusy) or (
                isinstance(exc, LostSourceLease) and getattr(exc, "holder", None)
            )
            if yields:
                await self._yield_to_source_owner(redis, key, job_id, attempts, exc)
                return
            logger.error("Contract ingest job %s failed: %s", job_id, exc)
            retries = max(1, int(settings.CONTRACT_QUEUE_MAX_RETRIES))
            await redis.hset(
                key,
                mapping={
                    "status": "failed" if attempts >= retries else "waiting",
                    "last_error": str(exc),
                    "updated_at": _utc_now(),
                    "failed_at": _utc_now() if attempts >= retries else "",
                    "next_retry_at": _utc_now() if attempts < retries else "",
                },
            )
            if attempts >= retries:
                await redis.hset(
                    key,
                    mapping={
                        "deadlettered_at": _utc_now(),
                        "deadletter_reason": "max_retries_exceeded",
                    },
                )
                await self._push_unique(redis, settings.CONTRACT_QUEUE_DEADLETTER_NAME, job_id)
            else:
                # The job stays on the processing list until it is back on the
                # queue: a worker killed during the backoff leaves an entry that
                # stale-job recovery re-queues (its hash says "queued"), not a
                # job on no list at all.
                await asyncio.sleep(min(2 ** attempts, 10))
                await self._requeue_if_still_waiting(redis, key, job_id)
            if attempts >= retries:
                await redis.lrem(settings.CONTRACT_QUEUE_PROCESSING_NAME, 1, job_id)
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await heartbeat_task

    async def _yield_to_source_owner(
        self, redis: Redis, key: str, job_id: str, attempts: int, exc: Exception
    ) -> None:
        """Another ingest owns this document's source. Never spends a retry.

        * The owner is this same job (a redelivered copy of a run that holds
          the lease): the copy stops, and leaves the job's processing entry and
          hash to the owning run. If that run has died, the entry is what
          stale-job recovery redelivers once its heartbeat is stale - so the job
          is never lost, and it runs again once the lease lapses.
        * This run lost its lease to another job: its outcome is discarded. The
          hash is closed as ``superseded`` (not left ``running``, which made
          every later enqueue for this upload - an OCR retry, a reindex - a
          silent no-op).
        * Busy with another job, or the holder could not be read: the job
          waits its turn and runs again; its next acquire learns whether a live
          owner exists. An unreadable holder is never taken for "superseded" -
          that would drop the job even when nobody is left to re-ingest, and
          could close the hash of a live copy of this same job.
        """
        from .contract_source_lease import UNKNOWN_HOLDER, LostSourceLease

        holder = getattr(exc, "holder", None)
        if holder == f"ingest:{job_id}":
            logger.info("Contract ingest job %s is already running elsewhere: %s", job_id, exc)
            return
        if isinstance(exc, LostSourceLease) and holder != UNKNOWN_HOLDER:
            await redis.lrem(settings.CONTRACT_QUEUE_PROCESSING_NAME, 1, job_id)
            logger.info("Contract ingest job %s lost its source to %s: %s", job_id, holder, exc)
            await redis.hset(
                key,
                mapping={
                    "status": "superseded",
                    "last_error": str(exc),
                    "updated_at": _utc_now(),
                },
            )
            return
        logger.info("Contract ingest job %s waits for the source owner %s", job_id, holder)
        await redis.hset(
            key,
            mapping={
                "status": "waiting",
                "attempts": str(max(0, attempts - 1)),
                "last_error": str(exc),
                "updated_at": _utc_now(),
            },
        )
        # Still on the processing list while it waits ("waiting", heartbeat
        # alive): recovery leaves it alone while this worker lives, and
        # re-queues it once this worker is stopped or killed.
        await asyncio.sleep(min(2 ** max(1, attempts), 10))
        await self._requeue_if_still_waiting(redis, key, job_id)

    async def _requeue_if_still_waiting(self, redis: Redis, key: str, job_id: str) -> None:
        """Back on the queue, unless something else already moved the job on
        (recovery re-queued it, or it is running again): pushing it then would
        deliver it twice, and "queued" would overwrite a live "running". Only a
        waiter that re-queued removes its processing entry - otherwise the
        entry left there belongs to whoever moved the job on."""
        if await redis.hget(key, "status") != "waiting":
            return
        await redis.hset(key, mapping={"status": "queued", "updated_at": _utc_now()})
        await self._push_unique(redis, settings.CONTRACT_QUEUE_NAME, job_id)
        await redis.lrem(settings.CONTRACT_QUEUE_PROCESSING_NAME, 1, job_id)

    async def _heartbeat_job(self, job_id: str, worker_name: str) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                redis = await self.connect()
                if redis is None:
                    return
                await redis.hset(
                    self._job_key(job_id),
                    mapping={
                        "heartbeat_at": _utc_now(),
                        "updated_at": _utc_now(),
                        "worker": worker_name,
                    },
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # One Redis error must not end the heartbeat of a live job: a
                # silent heartbeat makes recovery redeliver a run still going.
                logger.warning("Contract ingest heartbeat for %s failed", job_id, exc_info=True)

    def _job_is_stale(self, metadata: Dict[str, Any], now: datetime) -> bool:
        heartbeat = _parse_utc_timestamp(
            metadata.get("heartbeat_at") or metadata.get("started_at") or metadata.get("updated_at")
        )
        if heartbeat is None:
            return True
        return now - heartbeat > timedelta(seconds=self.visibility_timeout_seconds)

    async def _push_unique(self, redis: Redis, list_name: str, job_id: str) -> None:
        await redis.lrem(list_name, 0, job_id)
        await redis.rpush(list_name, job_id)

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
