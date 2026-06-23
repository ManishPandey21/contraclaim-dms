"""Mongo-backed leader lock for scheduled (cron) jobs — H2.

Guarantees a cron job runs once across all web replicas / worker processes.
Mirrors the billing-webhook idempotency pattern: an atomic upsert keyed by the
job id; a ``DuplicateKeyError`` means another instance currently holds the lock.
A ``locked_until`` TTL field auto-releases the lock if the holder crashes
mid-run, so jobs can never get permanently wedged.
"""

from __future__ import annotations

import functools
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional, TypeVar

from pymongo.errors import DuplicateKeyError

from ..core.database import get_database

logger = logging.getLogger(__name__)

# Stable per-process identity so a holder only ever releases its own lock.
HOLDER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

_COLLECTION = "scheduler_locks"


async def acquire(job_id: str, ttl_seconds: int, holder: str = HOLDER, db: Any = None) -> bool:
    """Try to take the lock for ``job_id``. Returns True iff acquired.

    Canonical Mongo lock: match a doc whose lock has expired (or none yet) and
    upsert it. If a live lock exists the filter misses, the upsert tries to
    insert a colliding ``_id`` and Mongo raises DuplicateKeyError → not ours.
    """
    db = db if db is not None else await get_database()
    now = datetime.utcnow()
    expiry = now + timedelta(seconds=max(1, int(ttl_seconds)))
    try:
        await db[_COLLECTION].find_one_and_update(
            {"_id": job_id, "locked_until": {"$lte": now}},
            {"$set": {"locked_until": expiry, "holder": holder, "acquired_at": now}},
            upsert=True,
        )
        return True
    except DuplicateKeyError:
        return False


async def release(job_id: str, holder: str = HOLDER, db: Any = None) -> None:
    """Release the lock if we still hold it (best-effort; TTL is the backstop)."""
    try:
        db = db if db is not None else await get_database()
        await db[_COLLECTION].update_one(
            {"_id": job_id, "holder": holder},
            {"$set": {"locked_until": datetime.utcnow() - timedelta(seconds=1)}},
        )
    except Exception:  # pragma: no cover - release must never raise
        logger.debug("Failed to release scheduler lock %s", job_id, exc_info=True)


F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def with_leader_lock(job_id: str, ttl_seconds: int) -> Callable[[F], F]:
    """Wrap an async job so only the lock holder runs it; other instances skip."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Optional[Any]:
            if not await acquire(job_id, ttl_seconds):
                logger.debug("Scheduler job '%s' skipped — lock held by another instance", job_id)
                return None
            try:
                return await func(*args, **kwargs)
            finally:
                await release(job_id)

        return wrapper  # type: ignore[return-value]

    return decorator
