"""Backward-compatible shim for legacy imports.

This module intentionally re-exports the maintained configuration and model
types from ``document_processing_config`` to avoid config drift.
"""

try:
    from .document_processing_config import (
        DocumentProcessingConfig,
        ParsedDocumentMetadata,
        ProcessingResult,
        create_config,
    )
except ImportError:  # pragma: no cover - top-level script compatibility
    try:
        from rbac_backend.config.document_processing_config import (  # type: ignore[no-redef]
            DocumentProcessingConfig,
            ParsedDocumentMetadata,
            ProcessingResult,
            create_config,
        )
    except ImportError:
        from document_processing_config import (  # type: ignore[no-redef]
            DocumentProcessingConfig,
            ParsedDocumentMetadata,
            ProcessingResult,
            create_config,
        )

__all__ = [
    "DocumentProcessingConfig",
    "ParsedDocumentMetadata",
    "ProcessingResult",
    "create_config",
]
