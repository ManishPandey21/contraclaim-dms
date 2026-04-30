# Error Handling Utilities
"""
Comprehensive error handling utilities for consistent error management across the application.
"""

import logging
import traceback
from typing import Any, Dict, Optional, Type, Union
from functools import wraps
from fastapi import HTTPException, status, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
import asyncio

logger = logging.getLogger(__name__)


class BaseAppError(Exception):
    """Base application error class."""
    
    def __init__(
        self, 
        message: str, 
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code or self.__class__.__name__
        self.details = details or {}
        super().__init__(self.message)


class DocumentError(BaseAppError):
    """Document-related errors."""
    pass


class ContractError(BaseAppError):
    """Contract-related errors."""
    pass


class AIServiceError(BaseAppError):
    """AI service-related errors."""
    pass


class AuthorizationError(BaseAppError):
    """Authorization-related errors."""
    
    def __init__(self, message: str = "Access denied", **kwargs):
        super().__init__(message, status.HTTP_403_FORBIDDEN, **kwargs)


class ValidationError(BaseAppError):
    """Input validation errors."""
    
    def __init__(self, message: str = "Invalid input", **kwargs):
        super().__init__(message, status.HTTP_400_BAD_REQUEST, **kwargs)


class RateLimitError(BaseAppError):
    """Rate limiting errors."""
    
    def __init__(self, message: str = "Rate limit exceeded", **kwargs):
        super().__init__(message, status.HTTP_429_TOO_MANY_REQUESTS, **kwargs)


class ServiceUnavailableError(BaseAppError):
    """Service unavailable errors."""
    
    def __init__(self, message: str = "Service temporarily unavailable", **kwargs):
        super().__init__(message, status.HTTP_503_SERVICE_UNAVAILABLE, **kwargs)


def handle_exceptions(func):
    """
    Decorator for consistent error handling across API endpoints.
    """
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except BaseAppError as e:
            # Log application errors with context
            logger.warning(
                f"Application error in {func.__name__}: {e.message}",
                extra={
                    "error_code": e.error_code,
                    "status_code": e.status_code,
                    "details": e.details,
                    "function": func.__name__
                }
            )
            raise HTTPException(
                status_code=e.status_code,
                detail={
                    "error": e.error_code,
                    "message": e.message,
                    "details": e.details
                }
            )
        except ValidationError as e:
            # Handle Pydantic validation errors
            logger.warning(f"Validation error in {func.__name__}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "ValidationError",
                    "message": "Input validation failed",
                    "details": str(e)
                }
            )
        except HTTPException:
            # Re-raise FastAPI HTTP exceptions
            raise
        except asyncio.TimeoutError:
            logger.error(f"Timeout in {func.__name__}")
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail={
                    "error": "TimeoutError",
                    "message": "Request timed out"
                }
            )
        except Exception as e:
            # Log unexpected errors with full context
            error_id = id(e)  # Simple error ID for tracking
            logger.error(
                f"Unexpected error in {func.__name__} (ID: {error_id}): {str(e)}",
                extra={
                    "error_id": error_id,
                    "function": func.__name__,
                    "traceback": traceback.format_exc()
                }
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": "InternalServerError",
                    "message": "An unexpected error occurred",
                    "error_id": error_id
                }
            )
    
    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except BaseAppError as e:
            logger.warning(
                f"Application error in {func.__name__}: {e.message}",
                extra={
                    "error_code": e.error_code,
                    "status_code": e.status_code,
                    "details": e.details,
                    "function": func.__name__
                }
            )
            raise HTTPException(
                status_code=e.status_code,
                detail={
                    "error": e.error_code,
                    "message": e.message,
                    "details": e.details
                }
            )
        except ValidationError as e:
            logger.warning(f"Validation error in {func.__name__}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "ValidationError",
                    "message": "Input validation failed",
                    "details": str(e)
                }
            )
        except HTTPException:
            raise
        except Exception as e:
            error_id = id(e)
            logger.error(
                f"Unexpected error in {func.__name__} (ID: {error_id}): {str(e)}",
                extra={
                    "error_id": error_id,
                    "function": func.__name__,
                    "traceback": traceback.format_exc()
                }
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": "InternalServerError",
                    "message": "An unexpected error occurred",
                    "error_id": error_id
                }
            )
    
    # Return appropriate wrapper based on function type
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper


class ErrorHandler:
    """Centralized error handling service."""
    
    @staticmethod
    def handle_database_error(error: Exception, operation: str) -> BaseAppError:
        """Handle database-related errors consistently."""
        error_msg = str(error).lower()
        
        if "timeout" in error_msg:
            return ServiceUnavailableError(
                "Database connection timeout",
                details={"operation": operation}
            )
        elif "connection" in error_msg:
            return ServiceUnavailableError(
                "Database connection error",
                details={"operation": operation}
            )
        elif "duplicate" in error_msg or "unique" in error_msg:
            return ValidationError(
                "Resource already exists",
                details={"operation": operation}
            )
        else:
            logger.error(f"Database error in {operation}: {str(error)}")
            return ServiceUnavailableError(
                "Database operation failed",
                details={"operation": operation}
            )
    
    @staticmethod
    def handle_external_api_error(
        error: Exception, 
        service: str, 
        operation: str
    ) -> BaseAppError:
        """Handle external API errors consistently."""
        error_msg = str(error).lower()
        
        if "rate limit" in error_msg or "429" in error_msg:
            return RateLimitError(
                f"{service} rate limit exceeded",
                details={"service": service, "operation": operation}
            )
        elif "timeout" in error_msg:
            return ServiceUnavailableError(
                f"{service} service timeout",
                details={"service": service, "operation": operation}
            )
        elif "unauthorized" in error_msg or "401" in error_msg:
            return AuthorizationError(
                f"{service} authorization failed",
                details={"service": service, "operation": operation}
            )
        else:
            logger.error(f"{service} API error in {operation}: {str(error)}")
            return ServiceUnavailableError(
                f"{service} service error",
                details={"service": service, "operation": operation}
            )
    
    @staticmethod
    def handle_file_operation_error(error: Exception, operation: str) -> BaseAppError:
        """Handle file operation errors consistently."""
        error_msg = str(error).lower()
        
        if "permission" in error_msg:
            return ServiceUnavailableError(
                "File system permission denied",
                details={"operation": operation}
            )
        elif "not found" in error_msg:
            return ValidationError(
                "File not found",
                details={"operation": operation}
            )
        elif "disk" in error_msg or "space" in error_msg:
            return ServiceUnavailableError(
                "Insufficient disk space",
                details={"operation": operation}
            )
        else:
            logger.error(f"File operation error in {operation}: {str(error)}")
            return ServiceUnavailableError(
                "File operation failed",
                details={"operation": operation}
            )


# Global exception handler for FastAPI
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler for unhandled exceptions."""
    error_id = id(exc)
    
    # Log the error with context
    logger.error(
        f"Unhandled exception (ID: {error_id}): {str(exc)}",
        extra={
            "error_id": error_id,
            "url": str(request.url),
            "method": request.method,
            "traceback": traceback.format_exc()
        }
    )
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "InternalServerError",
            "message": "An unexpected error occurred",
            "error_id": error_id
        }
    )


# Custom exception handlers for specific error types
async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """Handle Pydantic validation errors."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": "ValidationError",
            "message": "Input validation failed",
            "details": exc.errors()
        }
    )


def setup_error_handlers(app):
    """Setup error handlers for FastAPI application."""
    app.add_exception_handler(Exception, global_exception_handler)
    app.add_exception_handler(ValidationError, validation_exception_handler)