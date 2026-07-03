"""Contract Clause Chunking Agent (clause-wise indexing of contract documents)."""

from .agent import (
    ClauseChunkingAgent,
    ClauseProcessingSummary,
    DetectedClause,
    DetectedTable,
    DocumentScope,
)
from .embedding_service import ClauseEmbeddingService
from .graph_service import ClauseGraphService, GraphModel, clause_node_id
from .modification_detector import ModificationDetector, ModificationSignal
from .storage_service import ClauseScopeError, ClauseStorageService

__all__ = [
    "ClauseStorageService",
    "ClauseScopeError",
    "ClauseChunkingAgent",
    "DetectedClause",
    "DetectedTable",
    "DocumentScope",
    "ClauseProcessingSummary",
    "ClauseEmbeddingService",
    "ClauseGraphService",
    "GraphModel",
    "clause_node_id",
    "ModificationDetector",
    "ModificationSignal",
]
