"""Backward-compatible adapter facade.

Legacy callers expected a separate adapter module; keep the public factory
surface while delegating to the single maintained config implementation.
"""

try:
    from .document_processing_config import DocumentProcessingConfig
except ImportError:  # pragma: no cover - top-level script compatibility
    try:
        from rbac_backend.config.document_processing_config import (  # type: ignore[no-redef]
            DocumentProcessingConfig,
        )
    except ImportError:
        from document_processing_config import DocumentProcessingConfig  # type: ignore[no-redef]


def create_document_processing_config() -> DocumentProcessingConfig:
    config = DocumentProcessingConfig()
    return config


def create_config() -> DocumentProcessingConfig:
    return create_document_processing_config()


__all__ = [
    "DocumentProcessingConfig",
    "create_document_processing_config",
    "create_config",
]
