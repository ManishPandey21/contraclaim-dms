"""Upload concurrency guards shared across API replicas when Redis is configured."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict

from fastapi import HTTPException, status

from .runtime_state import get_runtime_state


class UploadConcurrencyLimiter:
    _local_counts: Dict[str, int] = {}

    def __init__(self) -> None:
        self._runtime_state = get_runtime_state()

    @asynccontextmanager
    async def slot(self, key: str, limit: int, ttl_seconds: int = 900) -> AsyncIterator[None]:
        if not key or limit <= 0:
            yield
            return
        redis = await self._runtime_state.get_redis()
        redis_key = f"upload:concurrency:{key}"
        acquired = False
        if redis is not None:
            count = await redis.incr(redis_key)
            if int(count) == 1:
                await redis.expire(redis_key, ttl_seconds)
            if int(count) > int(limit):
                await redis.decr(redis_key)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many concurrent uploads",
                )
            acquired = True
            try:
                yield
            finally:
                if acquired:
                    value = await redis.decr(redis_key)
                    if int(value or 0) <= 0:
                        await redis.delete(redis_key)
            return

        current = self._local_counts.get(key, 0) + 1
        if current > int(limit):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many concurrent uploads",
            )
        self._local_counts[key] = current
        try:
            yield
        finally:
            remaining = self._local_counts.get(key, 1) - 1
            if remaining <= 0:
                self._local_counts.pop(key, None)
            else:
                self._local_counts[key] = remaining


upload_concurrency_limiter = UploadConcurrencyLimiter()
