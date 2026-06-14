"""Shared runtime state adapter for Redis-backed production state."""

from __future__ import annotations

import logging
from typing import Optional

from redis.asyncio import Redis

from ..core.config import settings

logger = logging.getLogger(__name__)


class RuntimeStateService:
    """Provides a shared Redis client for sessions, rate limits, and cache."""

    def __init__(self) -> None:
        self._redis: Optional[Redis] = None
        self._available: Optional[bool] = None

    @property
    def redis_url(self) -> Optional[str]:
        return (
            getattr(settings, "RUNTIME_STATE_REDIS_URL", None)
            or getattr(settings, "APP_REDIS_URL", None)
        )

    async def get_redis(self) -> Optional[Redis]:
        url = self.redis_url
        if not url:
            return None
        if self._redis is not None and self._available:
            return self._redis
        try:
            self._redis = Redis.from_url(
                url,
                decode_responses=False,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
                retry_on_timeout=False,
            )
            await self._redis.ping()
            self._available = True
            return self._redis
        except Exception as exc:
            self._available = False
            if self._redis is not None:
                try:
                    await self._redis.aclose()
                except Exception:
                    pass
                self._redis = None
            logger.warning("Runtime Redis unavailable; using local fallback state: %s", exc)
            return None

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
        self._available = None


_runtime_state = RuntimeStateService()


def get_runtime_state() -> RuntimeStateService:
    return _runtime_state
