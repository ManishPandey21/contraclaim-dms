from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from ..models.ai_guardrails import GuardrailReport
from ..models.evidence_ledger import EvidenceLedgerEntry
from .authority import user_metadata_filters


class SearchStrategy(str, Enum):
    VANILLA = "vanilla"
    HYDE = "hyde"
    RAG_FUSION = "rag_fusion"


class SearchBackend(str, Enum):
    AUTO = "auto"
    QDRANT = "qdrant"
    MONGO = "mongo"


class SearchFilters(BaseModel):
    org_id: str
    project_id: str
    document_id: Optional[str] = None
    date_range: Optional[List[str]] = None
    doc_type: Optional[str] = None
    tags: Optional[List[str]] = None
    letter_no: Optional[str] = None
    chain_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")

    def to_mongo_filter(self) -> Dict[str, Any]:
        query: Dict[str, Any] = {"org_id": self.org_id, "project_id": self.project_id}
        if self.doc_type:
            query["doc_type"] = self.doc_type
        if self.tags:
            query["tags"] = {"$in": self.tags}
        if self.letter_no:
            query["letterNo"] = self.letter_no
        if self.chain_id:
            query["chain_id"] = self.chain_id
        if self.date_range and len(self.date_range) == 2:
            query["date"] = {"$gte": self.date_range[0], "$lte": self.date_range[1]}
        query.update(
            {
                f"metadata.{k}": v
                for k, v in user_metadata_filters(self.metadata).items()
            }
        )
        return query


class SearchRequest(BaseModel):
    query: str
    strategy: SearchStrategy = SearchStrategy.VANILLA
    limit: int = Field(default=8, ge=1, le=50)
    filters: SearchFilters
    use_enriched_text: bool = False
    backend: SearchBackend = SearchBackend.AUTO


class SearchResult(BaseModel):
    document_id: str
    chunk_id: str
    score: float
    page: Optional[int] = None
    snippet: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    results: List[SearchResult]
    strategy_used: SearchStrategy
    backend_used: SearchBackend = SearchBackend.AUTO
    timings: Dict[str, float] = Field(default_factory=dict)


class RagRequest(SearchRequest):
    answer_style: Optional[str] = None
    max_tokens: int = Field(default=512, ge=64, le=4096)


class Citation(BaseModel):
    document_id: str
    chunk_id: str
    page: Optional[int] = None
    score: Optional[float] = None
    snippet: str
    document_title: Optional[str] = None
    letter_no: Optional[str] = None
    file_name: Optional[str] = None
    clause_number: Optional[str] = None
    clause_title: Optional[str] = None
    section_heading: Optional[str] = None
    page_numbers: List[int] = Field(default_factory=list)


class RagResponse(BaseModel):
    answer: str
    citations: List[Citation]
    strategy_used: SearchStrategy
    timings: Dict[str, float] = Field(default_factory=dict)


class IterationTrace(BaseModel):
    iteration: int
    queries: List[str]
    retrieved_ids: List[str] = Field(default_factory=list)
    critique: Optional[str] = None
    refinements: List[str] = Field(default_factory=list)
    notes: Optional[str] = None


class ContractQARequest(RagRequest):
    require_citations: bool = Field(default=False)
    max_iterations: int = Field(default=3, ge=1, le=5)
    metadata_filters: Dict[str, Any] = Field(default_factory=dict)
    # Opt-in hard grounding: when set, retrieval restricts ALL evidence
    # (vector + clause-expansion + graph augmentation) to exactly these
    # document ids and skips cross-document augmentation, so an answer can
    # never draw on unselected uploads. Unset (default) preserves the existing
    # tenant-scoped behaviour for every other caller.
    grounding_document_ids: Optional[List[str]] = None


class ContractQAResponse(BaseModel):
    answer: str
    citations: List[Citation]
    strategy_used: SearchStrategy
    timings: Dict[str, float] = Field(default_factory=dict)
    trace: List[IterationTrace] = Field(default_factory=list)
    # Unified provenance: one ledger entry per citation. `citations` above is
    # generated from these entries so existing consumers keep working.
    evidence_ledger: List[EvidenceLedgerEntry] = Field(default_factory=list)
    guardrail: Optional[GuardrailReport] = None
