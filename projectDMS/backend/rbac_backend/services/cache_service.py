import asyncio
import hashlib
import json
import logging
import pickle
from typing import Any, Dict, Optional
from datetime import datetime, timedelta
from functools import wraps

from .runtime_state import get_runtime_state

logger = logging.getLogger(__name__)

class InMemoryCache:
    """
    Simple in-memory cache with TTL expiration.
    In production, use Redis or similar distributed cache.
    """

    def __init__(self):
        # Internal cache dict keyed by strings.
        # Each value is a dict with value, expires_at, created_at, last_accessed.
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._runtime_state = get_runtime_state()

    def start(self) -> None:
        """Start cache cleanup background task."""
        if self._cleanup_task is None:
            try:
                loop = asyncio.get_running_loop()
                self._cleanup_task = loop.create_task(self._cleanup_expired())
            except RuntimeError:
                self._cleanup_task = None

    async def stop(self) -> None:
        """Stop cleanup task gracefully."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            finally:
                self._cleanup_task = None

    async def _cleanup_expired(self):
        """Periodically remove expired cache entries."""
        while True:
            try:
                now = datetime.utcnow()
                keys_to_delete = []
                async with self._lock:
                    for key, entry in self._cache.items():
                        if entry['expires_at'] <= now:
                            keys_to_delete.append(key)
                    for key in keys_to_delete:
                        del self._cache[key]
                if keys_to_delete:
                    logger.info(f"Cleaned up {len(keys_to_delete)} expired cache entries")
                await asyncio.sleep(300)  # sleep 5 minutes
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cache cleanup error: {e}")
                await asyncio.sleep(60)  # sleep before retry on error

    async def get(self, key: str) -> Optional[Any]:
        """Retrieve key from cache if not expired. Does NOT renew TTL on get."""
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            raw = await redis.get(self._redis_key(key))
            if raw is None:
                return None
            try:
                return pickle.loads(raw)
            except Exception:
                logger.warning("Failed to deserialize Redis cache entry %s", key, exc_info=True)
                await redis.delete(self._redis_key(key))
                return None
        async with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            if entry['expires_at'] <= datetime.utcnow():
                del self._cache[key]
                return None
            entry['last_accessed'] = datetime.utcnow()
            return entry['value']

    async def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        """Store key with TTL."""
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            await redis.setex(self._redis_key(key), max(1, int(ttl_seconds)), pickle.dumps(value))
            return
        expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds)
        async with self._lock:
            self._cache[key] = {
                'value': value,
                'expires_at': expires_at,
                'created_at': datetime.utcnow(),
                'last_accessed': datetime.utcnow(),
            }

    async def delete(self, key: str) -> bool:
        """Delete key; return True if existed."""
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            return bool(await redis.delete(self._redis_key(key)))
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    async def clear(self) -> None:
        """Clear cache completely."""
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            keys = [key async for key in redis.scan_iter(match="cache:*", count=500)]
            if keys:
                await redis.delete(*keys)
            return
        async with self._lock:
            self._cache.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Cache usage statistics."""
        now = datetime.utcnow()
        total_entries = len(self._cache)
        expired_entries = sum(1 for e in self._cache.values() if e['expires_at'] <= now)
        return {
            'total_entries': total_entries,
            'active_entries': total_entries - expired_entries,
            'expired_entries': expired_entries,
            'memory_usage_mb': self._estimate_memory_usage(),
        }

    @staticmethod
    def _redis_key(key: str) -> str:
        return f"cache:{key}"

    def _estimate_memory_usage(self) -> float:
        """Estimate approximate memory in megabytes used by cache entries."""
        try:
            import sys
            total_size = sys.getsizeof(self._cache)
            for entry in self._cache.values():
                total_size += sys.getsizeof(entry)
                total_size += sys.getsizeof(entry.get('value'))
            return total_size / (1024 * 1024)
        except Exception:
            return 0.0

# Global cache instance
cache = InMemoryCache()

def generate_cache_key(*args, **kwargs) -> str:
    """
    Generate a md5 hash key based on positional and keyword arguments.
    Sort keyword arguments to ensure consistent keys.
    """
    key_data = {
        'args': args,
        'kwargs': sorted(kwargs.items()) if kwargs else {},
    }
    key_string = json.dumps(key_data, sort_keys=True, default=str)
    return hashlib.md5(key_string.encode()).hexdigest()

def cached(ttl_seconds: int = 300, key_prefix: str = ""):
    """
    Decorator to cache async function results with TTL.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            cache_key = f"{key_prefix}:{func.__name__}:{generate_cache_key(*args, **kwargs)}"
            cached_result = await cache.get(cache_key)
            if cached_result is not None:
                logger.debug(f"Cache hit for key: {cache_key}")
                return cached_result
            logger.debug(f"Cache miss for key: {cache_key}")
            result = await func(*args, **kwargs)
            await cache.set(cache_key, result, ttl_seconds)
            return result
        return wrapper
    return decorator

async def cache_invalidate_pattern(pattern: str):
    """
    Invalidate keys containing the pattern substring.
    Note: this simplistic substring match can lead to unintended clears.
    For production, use redis with native pattern delete support.
    """
    redis = await cache._runtime_state.get_redis()
    if redis is not None:
        matched = [
            key
            async for key in redis.scan_iter(match=f"cache:*{pattern}*", count=500)
        ]
        if matched:
            await redis.delete(*matched)
        logger.info("Invalidated %s Redis cache entries matching pattern: %s", len(matched), pattern)
        return

    keys_to_delete = []
    # Lock while iterating keys
    async with cache._lock:
        keys_to_delete = [key for key in cache._cache.keys() if pattern in key]
    for key in keys_to_delete:
        await cache.delete(key)
    logger.info(f"Invalidated {len(keys_to_delete)} cache entries matching pattern: {pattern}")

class CacheService:
    """Lightweight wrapper used by routers for dependency injection."""

    def __init__(self, backend: InMemoryCache | None = None) -> None:
        self._backend = backend or cache

    async def get(self, key: str):
        return await self._backend.get(key)

    async def set(self, key: str, value, ttl: int = 300) -> None:
        await self._backend.set(key, value, ttl)

    async def delete(self, key: str) -> bool:
        return await self._backend.delete(key)

    async def clear_all(self) -> None:
        await self._backend.clear()

    def get_stats(self) -> Dict[str, Any]:
        return self._backend.get_stats()

async def warm_cache() -> None:
    """Placeholder cache warm-up to keep startup hooks happy."""
    try:
        cache.start()
    except Exception:
        logger.debug("Cache warm-up skipped due to missing event loop", exc_info=True)
