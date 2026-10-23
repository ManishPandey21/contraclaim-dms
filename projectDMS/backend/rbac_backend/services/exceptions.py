"""Compatibility shims for service-layer exceptions."""

try:
    from ..utils.exceptions import DocumentProcessingError
except ImportError:  # pragma: no cover - fallback when relative import fails
    from backend.rbac_backend.utils.exceptions import DocumentProcessingError

__all__ = ["DocumentProcessingError"]
