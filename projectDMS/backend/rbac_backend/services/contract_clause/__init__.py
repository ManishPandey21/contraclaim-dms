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
from .index_service import ClauseIndexService, ClauseMergeError, ClauseNotFoundError
from .modification_detector import ModificationDetector, ModificationSignal
from .boq_detector import BOQRow, BOQTable, detect_boq_tables, is_boq_header, parse_boq_row
from .storage_service import ClauseScopeError, ClauseStorageService
from .subitem_detector import SubItem, detect_subitems

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
    "ClauseIndexService",
    "ClauseNotFoundError",
    "ClauseMergeError",
    "SubItem",
    "detect_subitems",
    "BOQRow",
    "BOQTable",
    "detect_boq_tables",
    "is_boq_header",
    "parse_boq_row",
]
