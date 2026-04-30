# utils/rate_limiter.py

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class RateLimiter:
    """Simple rate limiter implementation."""

    def __init__(self, requests_per_minute: int = 100, window_seconds: int = 3600):
        self.requests_per_minute = requests_per_minute
        self.window_seconds = window_seconds
        self.user_requests: Dict[str, list] = {}
        self._lock = asyncio.Lock()
        self._buckets: Dict[str, Dict[str, Any]] = {}

    def _now(self) -> float:
        """Get current timestamp"""
        return datetime.utcnow().timestamp()

    async def check_user_limit(self, user_id: str, cost: int = 1) -> bool:
        """
        Check if user is within rate limits.
        For now, this is a simple implementation that allows all requests.
        """
        try:
            current_time = datetime.utcnow()

            # Initialize user tracking if not exists
            if user_id not in self.user_requests:
                self.user_requests[user_id] = []

            # Clean old requests (older than window)
            cutoff_time = current_time - timedelta(seconds=self.window_seconds)
            self.user_requests[user_id] = [
                req_time for req_time in self.user_requests[user_id]
                if req_time > cutoff_time
            ]  # Fixed: Added missing closing bracket

            # Check if user is within limits
            current_requests = len(self.user_requests[user_id])
            if current_requests + cost > self.requests_per_minute:
                logger.warning(f"Rate limit exceeded for user {user_id}: {current_requests} requests")
                # For now, don't actually block - just log
                # raise HTTPException(status_code=429, detail="Rate limit exceeded")

            # Add current request
            for _ in range(cost):
                self.user_requests[user_id].append(current_time)

            return True

        except Exception as e:
            logger.error(f"Rate limiter error: {str(e)}")
            return False  # Fixed: Return False instead of commented raise
            # raise Exception("Rate limit exceeded")

    # --- Compatibility helpers used by routers/auth.py and routers/users.py ---

    async def check_ip_limit(
        self,
        ip_address: Optional[str],
        cost: int = 1,
        window_seconds: Optional[int] = None,
        max_requests: Optional[int] = None,
    ) -> None:
        """
        Track rate usage by client IP. Parameters window_seconds and max_requests are
        accepted for compatibility but not strictly enforced in this simple in-memory
        implementation (counts are tracked; enforcement can be enabled if needed).
        """
        if not ip_address:
            return

        key = f"ip:{ip_address}"
        await self._check_key(key, cost, window_seconds)

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
        await self._check_key(key, cost, window_seconds)

    async def _check_key(self, key: str, cost: int = 1, window_override: Optional[int] = None) -> None:
        """
        Internal helper to update a bucket keyed by an arbitrary identifier.
        If window_override is provided, that window is applied for the reset logic;
        otherwise, the instance's window_seconds is used.
        """
        window = int(window_override) if window_override and window_override > 0 else self.window_seconds
        now = self._now()

        async with self._lock:  # Fixed: Use async with for asyncio.Lock
            bucket = self._buckets.setdefault(key, {"window_start": now, "count": 0})

            # Reset if window elapsed (respect override)
            window_start = bucket.get("window_start", now)
            if now - window_start >= window:
                bucket["window_start"] = now
                bucket["count"] = 0

            bucket["count"] = float(bucket.get("count", 0)) + float(cost)
