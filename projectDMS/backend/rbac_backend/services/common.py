"""Shared helpers for service layer implementations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence, Tuple

from motor.motor_asyncio import AsyncIOMotorCollection


class PaginationError(ValueError):
    """Raised when pagination parameters are invalid."""


@dataclass(frozen=True)
class Pagination:
    """Simple pagination metadata container."""

    skip: int
    limit: int
    total: Optional[int] = None


def validate_pagination(skip: int, limit: int, *, max_limit: int = 1000) -> Tuple[int, int]:
    """Validate and normalise pagination arguments."""

    if not isinstance(skip, int):
        raise PaginationError("skip must be an integer")
    if not isinstance(limit, int):
        raise PaginationError("limit must be an integer")
    if skip < 0:
        raise PaginationError("skip must be greater than or equal to zero")
    if limit <= 0:
        raise PaginationError("limit must be greater than zero")
    if limit > max_limit:
        raise PaginationError(f"limit cannot exceed {max_limit}")
    return skip, limit


async def fetch_paginated(
    collection: AsyncIOMotorCollection,
    *,
    filter: Optional[dict[str, Any]] = None,
    projection: Optional[dict[str, Any]] = None,
    sort: Optional[Sequence[Tuple[str, int]]] = None,
    skip: int = 0,
    limit: int = 50,
    count_total: bool = False,
) -> tuple[list[dict[str, Any]], Pagination]:
    """Fetch documents from ``collection`` using validated pagination params."""

    skip, limit = validate_pagination(skip, limit)
    cursor = collection.find(filter or {}, projection)
    if sort:
        cursor = cursor.sort(list(sort))
    cursor = cursor.skip(skip).limit(limit)

    items: list[dict[str, Any]] = []
    async for item in cursor:
        items.append(item)

    total: Optional[int] = None
    if count_total:
        total = await collection.count_documents(filter or {})

    return items, Pagination(skip=skip, limit=limit, total=total)


__all__ = ["Pagination", "PaginationError", "fetch_paginated", "validate_pagination"]
