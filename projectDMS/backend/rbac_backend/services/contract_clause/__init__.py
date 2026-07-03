"""Contract Clause Chunking Agent (clause-wise indexing of contract documents)."""

from .storage_service import ClauseScopeError, ClauseStorageService

__all__ = ["ClauseStorageService", "ClauseScopeError"]
