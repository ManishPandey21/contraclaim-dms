# utils/rate_limiter.py

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from fastapi import HTTPException, status

from ..services.runtime_state import get_runtime_state

logger = logging.getLogger(__name__)

class RateLimiter:
    """Simple rate limiter implementation.

    ``scope`` isolates this limiter's buckets from every other limiter's.
    Without it, all limiters share one bucket per user/ip/email key, so an
    endpoint with a small budget gets starved by unrelated traffic counted
    against another endpoint's larger budget (and vice versa). Always pass a
    scope for endpoint-group limiters.

    ``requests_per_minute`` is a historical misnomer kept for backward
    compatibility: the limit applies per ``window_seconds``, not per minute.
    Prefer the explicit ``max_requests`` keyword.
    """

    _shared_lock = asyncio.Lock()
    _shared_user_requests: Dict[str, list] = {}
    _shared_buckets: Dict[str, Dict[str, Any]] = {}

    def __init__(
        self,
        requests_per_minute: int = 100,
        window_seconds: int = 3600,
        *,
        max_requests: Optional[int] = None,
        scope: Optional[str] = None,
    ):
        self.max_requests = int(max_requests) if max_requests is not None else int(requests_per_minute)
        # Legacy alias; some call sites and helpers still read this name.
        self.requests_per_minute = self.max_requests
        self.window_seconds = window_seconds
        self.scope = scope
        self.user_requests = self._shared_user_requests
        self._lock = self._shared_lock
        self._buckets = self._shared_buckets
        self._runtime_state = get_runtime_state()

    def _now(self) -> float:
        """Get current timestamp"""
        return datetime.utcnow().timestamp()

    def _scoped(self, key: str) -> str:
        return f"{self.scope}:{key}" if self.scope else key

    async def check_user_limit(self, user_id: str, cost: int = 1) -> bool:
        """Check and enforce a per-user fixed-window limit."""
        if not user_id:
            return True
        try:
            await self._check_key(f"user:{user_id}", cost, self.window_seconds, self.max_requests)
            return True
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Rate limiter error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Rate limiter unavailable",
            ) from e

    async def _check_user_limit_local(self, user_id: str, cost: int = 1) -> bool:
        try:
            current_time = datetime.utcnow()

            async with self._lock:
                self.user_requests.setdefault(user_id, [])
                cutoff_time = current_time - timedelta(seconds=self.window_seconds)
                self.user_requests[user_id] = [
                    req_time for req_time in self.user_requests[user_id]
                    if req_time > cutoff_time
                ]

                current_requests = len(self.user_requests[user_id])
                if current_requests + cost > self.requests_per_minute:
                    logger.warning(
                        "Rate limit exceeded for user %s: %s/%s",
                        user_id,
                        current_requests,
                        self.requests_per_minute,
                    )
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Rate limit exceeded",
                    )

                for _ in range(cost):
                    self.user_requests[user_id].append(current_time)

            return True

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Rate limiter error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Rate limiter unavailable",
            ) from e

    # --- Compatibility helpers used by routers/auth.py and routers/users.py ---

    async def check_ip_limit(
        self,
        ip_address: Optional[str],
        cost: int = 1,
        window_seconds: Optional[int] = None,
        max_requests: Optional[int] = None,
    ) -> None:
        """
        Track and enforce rate usage by client IP.
        """
        if not ip_address:
            return

        key = f"ip:{ip_address}"
        await self._check_key(key, cost, window_seconds, max_requests)

    async def check_email_limit(
        self,
        email: Optional[str],
        cost: int = 1,
        window_seconds: Optional[int] = None,
        max_requests: Optional[int] = None,
    ) -> None:
        """
        Track rate usage by email. Parameters are accepted for compatibility.
        """
        if not email:
            return

        key = f"email:{email.lower()}"
        await self._check_key(key, cost, window_seconds, max_requests)

    async def check_client_limit(
        self,
        client_id: Optional[str],
        cost: int = 1,
        window_seconds: Optional[int] = None,
        max_requests: Optional[int] = None,
    ) -> None:
        if not client_id:
            return
        await self._check_key(f"client:{client_id}", cost, window_seconds, max_requests)

    async def _check_key(
        self,
        key: str,
        cost: int = 1,
        window_override: Optional[int] = None,
        max_requests: Optional[int] = None,
    ) -> None:
        """
        Internal helper to update a bucket keyed by an arbitrary identifier.
        If window_override is provided, that window is applied for the reset logic;
        otherwise, the instance's window_seconds is used.
        """
        window = int(window_override) if window_override and window_override > 0 else self.window_seconds
        limit = int(max_requests) if max_requests and max_requests > 0 else self.max_requests
        key = self._scoped(key)
        now = self._now()
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            redis_key = f"rate:{key}"
            count = await redis.incrby(redis_key, int(cost))
            if int(count) == int(cost):
                await redis.expire(redis_key, window)
            else:
                # Heal keys left without a TTL (e.g. the creating request lost
                # the race or the expire call failed): a persistent counter
                # would otherwise rate-limit the key forever.
                ttl = await redis.ttl(redis_key)
                if ttl is not None and int(ttl) < 0:
                    await redis.expire(redis_key, window)
            if int(count) > limit:
                logger.warning("Rate limit exceeded for %s: %s/%s", key, count, limit)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded",
                )
            return

        async with self._lock:
            bucket = self._buckets.setdefault(key, {"window_start": now, "count": 0})

            # Reset if window elapsed (respect override)
            window_start = bucket.get("window_start", now)
            if now - window_start >= window:
                bucket["window_start"] = now
                bucket["count"] = 0

            current_count = float(bucket.get("count", 0))
            if current_count + float(cost) > limit:
                logger.warning("Rate limit exceeded for %s: %s/%s", key, current_count, limit)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded",
                )

            bucket["count"] = current_count + float(cost)
