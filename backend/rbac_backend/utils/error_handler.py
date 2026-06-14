"""Shared error utilities for FastAPI routes and services."""

from __future__ import annotations

import inspect
import logging
from functools import wraps
from typing import Any, Callable, Coroutine, Optional, TypeVar, get_type_hints

from fastapi import HTTPException, status

from ..schemas.common import ErrorResponse

logger = logging.getLogger(__name__)


class BaseDomainError(Exception):
    """Base class for service/route domain errors."""

    def __init__(
        self,
        message: str,
        http_status: int = status.HTTP_400_BAD_REQUEST,
        *,
        error: Optional[str] = None,
        details: Optional[Any] = None,
        code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.error = error or self.__class__.__name__
        self.details = details
        self.code = code


class DocumentError(BaseDomainError):
    pass


class LetterError(BaseDomainError):
    pass


class EmailError(BaseDomainError):
    pass


class UserError(BaseDomainError):
    pass


class AuthenticationError(BaseDomainError):
    def __init__(self, message: str, http_status: int = status.HTTP_401_UNAUTHORIZED) -> None:
        super().__init__(message, http_status)


class AuthorizationError(BaseDomainError):
    def __init__(self, message: str, http_status: int = status.HTTP_403_FORBIDDEN) -> None:
        super().__init__(message, http_status)


class ValidationError(BaseDomainError):
    def __init__(self, message: str, http_status: int = status.HTTP_422_UNPROCESSABLE_ENTITY) -> None:
        super().__init__(message, http_status)


class RateLimitError(BaseDomainError):
    def __init__(self, message: str, http_status: int = status.HTTP_429_TOO_MANY_REQUESTS) -> None:
        super().__init__(message, http_status)


class ServiceError(BaseDomainError):
    def __init__(self, message: str, http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR) -> None:
        super().__init__(message, http_status)


class RoleError(BaseDomainError):
    pass


class PermissionError(BaseDomainError):
    pass


class OrganizationError(BaseDomainError):
    pass


class PartyError(BaseDomainError):
    pass


class ProjectError(BaseDomainError):
    pass


class RepresentativeError(BaseDomainError):
    pass


class TagError(BaseDomainError):
    pass


class TemplateError(BaseDomainError):
    pass


class ConcernError(BaseDomainError):
    pass


class EmailGroupError(BaseDomainError):
    pass


class InputRequestError(BaseDomainError):
    pass


class FolderError(BaseDomainError):
    pass


class ContractError(BaseDomainError):
    pass


class PerformanceError(BaseDomainError):
    pass


class S3ServiceError(BaseDomainError):
    def __init__(self, message: str, http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR) -> None:
        super().__init__(message, http_status)


F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])


def handle_exceptions(func: F) -> F:
    """Decorator to convert domain errors to HTTP responses."""

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any):
        try:
            return await func(*args, **kwargs)
        except BaseDomainError as exc:  # Expected domain error
            logger.warning("%s domain error: %s", func.__name__, exc)
            response = ErrorResponse(
                error=getattr(exc, "error", exc.__class__.__name__),
                message=str(exc),
                details=getattr(exc, "details", None),
                code=getattr(exc, "code", None),
            )
            raise HTTPException(
                status_code=getattr(exc, "http_status", status.HTTP_400_BAD_REQUEST),
                detail=response.model_dump_non_null(),
            ) from exc
        except HTTPException:
            raise
        except ValueError as exc:  # Common validation error (e.g., invalid ObjectId)
            logger.warning("%s validation error: %s", func.__name__, exc)
            response = ErrorResponse(
                error="ValidationError",
                message=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=response.model_dump_non_null(),
            ) from exc
        except Exception as exc:  # Unexpected failure
            logger.exception("Unhandled error in %s", func.__name__)
            response = ErrorResponse(
                error="InternalServerError",
                message="Internal server error",
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=response.model_dump_non_null(),
            ) from exc

    # Preserve resolved annotations from the original function so FastAPI can
    # inspect wrapped endpoints even when routers use postponed annotations.
    try:
        signature = inspect.signature(func)
        type_hints = get_type_hints(func, include_extras=True)
        parameters = [
            parameter.replace(annotation=type_hints.get(name, parameter.annotation))
            for name, parameter in signature.parameters.items()
        ]
        wrapper.__signature__ = signature.replace(  # type: ignore[attr-defined]
            parameters=parameters,
            return_annotation=type_hints.get("return", signature.return_annotation),
        )
        wrapper.__annotations__ = {
            name: type_hints.get(name, parameter.annotation)
            for name, parameter in signature.parameters.items()
            if type_hints.get(name, parameter.annotation) is not inspect.Signature.empty
        }
        if "return" in type_hints:
            wrapper.__annotations__["return"] = type_hints["return"]
    except Exception:
        logger.debug("Unable to preserve resolved signature for %s", func.__name__, exc_info=True)

    return wrapper  # type: ignore[return-value]


def raise_not_found(resource_type: str, resource_id: Optional[str] = None) -> None:
    message = (
        f"{resource_type} with ID '{resource_id}' not found"
        if resource_id
        else f"{resource_type} not found"
    )
    detail = ErrorResponse(
        error="NotFoundError",
        message=message,
        details={"resource": resource_type, "id": resource_id} if resource_id else {"resource": resource_type},
        code="not_found",
    ).model_dump_non_null()
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def raise_permission_denied(action: Optional[str] = None, resource: Optional[str] = None) -> None:
    if action and resource:
        message = f"Permission denied to {action} {resource}"
    elif action:
        message = f"Permission denied to {action}"
    else:
        message = "Permission denied"
    detail = ErrorResponse(
        error="ForbiddenError",
        message=message,
        details={"action": action, "resource": resource},
        code="permission_denied",
    ).model_dump_non_null()
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def raise_validation_error(field: str, message: str) -> None:
    detail = ErrorResponse(
        error="ValidationError",
        message=f"Validation error for field '{field}': {message}",
        details={"field": field},
        code="validation_error",
    ).model_dump_non_null()
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def raise_rate_limit_error(retry_after: Optional[int] = None) -> None:
    headers = {"Retry-After": str(retry_after)} if retry_after else {}
    detail = ErrorResponse(
        error="RateLimitError",
        message="Rate limit exceeded. Please try again later.",
        details={"retry_after": retry_after} if retry_after else None,
        code="rate_limit",
    ).model_dump_non_null()
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        headers=headers,
    )


__all__ = [
    "handle_exceptions",
    "raise_not_found",
    "raise_permission_denied",
    "raise_validation_error",
    "raise_rate_limit_error",
    "BaseDomainError",
    "AuthenticationError",
    "AuthorizationError",
    "ValidationError",
    "RateLimitError",
    "ServiceError",
    "DocumentError",
    "LetterError",
    "EmailError",
    "UserError",
    "RoleError",
    "PermissionError",
    "OrganizationError",
    "PartyError",
    "ProjectError",
    "RepresentativeError",
    "TagError",
    "TemplateError",
    "ConcernError",
    "EmailGroupError",
    "InputRequestError",
    "FolderError",
    "ContractError",
    "PerformanceError",
    "S3ServiceError",
]
