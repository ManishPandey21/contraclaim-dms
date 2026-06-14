from __future__ import annotations

from typing import Callable

from ..services.cache_service import cache, cached


def cache_with_ttl(ttl_seconds: int):
    """Light wrapper around the shared cache decorator."""

    def decorator(func: Callable):
        return cached(ttl_seconds=ttl_seconds)(func)

    return decorator

__all__ = ["cache", "cache_with_ttl"]
