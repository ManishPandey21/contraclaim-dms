"""Shared Pydantic schemas used across the backend."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Standard error payload returned by the API."""

    error: str
    message: str
    details: Optional[Any] = None
    code: Optional[str] = None

    def model_dump_non_null(self) -> dict[str, Any]:
        """Return a dict representation excluding ``None`` values."""

        return self.model_dump(exclude_none=True)


__all__ = ["ErrorResponse"]
