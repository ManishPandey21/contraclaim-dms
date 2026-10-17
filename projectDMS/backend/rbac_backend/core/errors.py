"""Centralised error utilities and exception handlers."""
from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any, Iterable, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from ..schemas.common import ErrorResponse
from ..utils.error_handler import BaseDomainError

logger = logging.getLogger(__name__)

_HTTP_STATUS_DEFAULTS: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "BadRequestError",
    status.HTTP_401_UNAUTHORIZED: "UnauthorizedError",
    status.HTTP_403_FORBIDDEN: "ForbiddenError",
    status.HTTP_404_NOT_FOUND: "NotFoundError",
    status.HTTP_409_CONFLICT: "ConflictError",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "ValidationError",
    status.HTTP_429_TOO_MANY_REQUESTS: "RateLimitError",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "InternalServerError",
    status.HTTP_503_SERVICE_UNAVAILABLE: "ServiceUnavailableError",
}


def _default_error_name(status_code: int) -> str:
    """Return a sensible error identifier for an HTTP status code."""

    if status_code in _HTTP_STATUS_DEFAULTS:
        return _HTTP_STATUS_DEFAULTS[status_code]
    try:
        status_enum = HTTPStatus(status_code)
    except ValueError:
        return "ApplicationError"
    return f"{status_enum.name.title().replace('_', '')}Error"


def _normalise_error_response(detail: Any, status_code: int) -> ErrorResponse:
    """Convert arbitrary ``HTTPException.detail`` payloads into :class:`ErrorResponse`."""

    if isinstance(detail, ErrorResponse):
        return detail

    if isinstance(detail, dict):
        error_name = detail.get("error") or detail.get("code")
        message = detail.get("message") or detail.get("detail")
        details = detail.get("details")
        code = detail.get("code") if isinstance(detail.get("code"), str) else None
        if not isinstance(error_name, str) or not error_name:
            error_name = _default_error_name(status_code)
        if not isinstance(message, str) or not message:
            message = detail.get("error") if isinstance(detail.get("error"), str) else error_name
        return ErrorResponse(error=error_name, message=message, details=details, code=code)

    if isinstance(detail, str):
        return ErrorResponse(error=_default_error_name(status_code), message=detail)

    if detail is None:
        return ErrorResponse(
            error=_default_error_name(status_code),
            message=_default_error_name(status_code).replace("Error", ""),
        )

    # Lists and any other payloads (e.g. from FastAPI validation)
    return ErrorResponse(
        error=_default_error_name(status_code),
        message=str(detail),
        details=detail,
    )


async def _render_error_response(
    status_code: int,
    response: ErrorResponse,
    headers: Optional[dict[str, str]] = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump_non_null(),
        headers=headers,
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:  # noqa: D401
    """Handle ``HTTPException`` errors using the canonical :class:`ErrorResponse`."""

    response = _normalise_error_response(exc.detail, exc.status_code)
    return await _render_error_response(exc.status_code, response, headers=exc.headers)


async def request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Render FastAPI request validation errors in the common format."""

    response = ErrorResponse(
        error="RequestValidationError",
        message="Request validation failed",
        details={"errors": exc.errors()},
        code="request_validation_error",
    )
    return await _render_error_response(status.HTTP_422_UNPROCESSABLE_ENTITY, response)


async def pydantic_validation_exception_handler(
    request: Request,
    exc: PydanticValidationError,
) -> JSONResponse:
    """Render Pydantic validation errors that bubble out of services."""

    response = ErrorResponse(
        error="ValidationError",
        message="Validation failed",
        details={"errors": exc.errors()},
        code="pydantic_validation_error",
    )
    return await _render_error_response(status.HTTP_422_UNPROCESSABLE_ENTITY, response)


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Final fallback for uncaught exceptions."""

    error_id = id(exc)
    logger.exception("Unhandled exception (%s) while processing %s", error_id, request.url.path)
    response = ErrorResponse(
        error="InternalServerError",
        message="An unexpected error occurred",
        details={"error_id": error_id},
        code="internal_server_error",
    )
    return await _render_error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, response)


async def domain_error_handler(request: Request, exc: BaseDomainError) -> JSONResponse:
    """Render a domain error at its own status when no route decorator did.

    `handle_exceptions` renders `BaseDomainError` for the routes it wraps. Routes
    that re-raise the family without that decorator used to reach Starlette's
    500, which is the F-A8W-B3 outcome one layer further out: an
    `AuthorizationError` refusal reported as an outage. Starlette resolves
    handlers along the exception's MRO, so this one is chosen before any
    `Exception` fallback.
    """

    status_code = getattr(exc, "http_status", status.HTTP_400_BAD_REQUEST)
    logger.warning("%s domain error on %s: %s", exc.__class__.__name__, request.url.path, exc)
    server_side = status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR
    response = ErrorResponse(
        error=getattr(exc, "error", None) or exc.__class__.__name__,
        # A 5xx domain error's text is internal (`f"... {exc}"` is common), so
        # only a refusal's own message reaches the client.
        message="An unexpected error occurred" if server_side else str(exc),
        details=None if server_side else getattr(exc, "details", None),
        code=getattr(exc, "code", None),
    )
    # The same `{"detail": ...}` envelope `handle_exceptions` produces, which is
    # what the client reads (`err.response.data.detail`); no HTTPException
    # handler is registered on the app, so every other error has this shape.
    return JSONResponse(status_code=status_code, content={"detail": response.model_dump_non_null()})


def register_domain_error_handler(app: FastAPI) -> None:
    """Attach only the domain-error backstop, leaving every other handler as is."""

    app.add_exception_handler(BaseDomainError, domain_error_handler)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach standard exception handlers to ``app`` if not already registered."""

    handlers: Iterable[tuple[Any, Any]] = (
        (BaseDomainError, domain_error_handler),
        (HTTPException, http_exception_handler),
        (RequestValidationError, request_validation_exception_handler),
        (PydanticValidationError, pydantic_validation_exception_handler),
        (Exception, generic_exception_handler),
    )

    for exc_class, handler in handlers:
        app.add_exception_handler(exc_class, handler)


__all__ = [
    "ErrorResponse",
    "domain_error_handler",
    "register_domain_error_handler",
    "generic_exception_handler",
    "http_exception_handler",
    "pydantic_validation_exception_handler",
    "register_exception_handlers",
    "request_validation_exception_handler",
]
