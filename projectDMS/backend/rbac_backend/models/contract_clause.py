"""Structured clause records for the Contract Clause Chunking Agent.

Contract documents are indexed clause-by-clause (not blind token chunks). Each
record in the ``contract_clauses`` collection is traceable to its organisation,
project, contract, document, page range, hierarchy and revision so that the
Relevant Clause / drafting / claim (SoC/SoD/rejoinder) agents can ground on it.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def _now() -> datetime:
    return datetime.utcnow()


# A clause chunk is either a real numbered clause, a sub-part of a long clause,
# a section without a clause number, or a table linked to a clause.
ChunkType = Literal["clause", "clause_part", "section_chunk", "table"]
Confidence = Literal["high", "medium", "low"]
# validated = safe for AI; needs_review = human check; incomplete_metadata = missing scope/type.
QualityStatus = Literal["validated", "needs_review", "incomplete_metadata"]
ExtractionMethod = Literal["text", "ocr", "hybrid"]
EmbeddingStatus = Literal["pending", "queued", "done", "failed", "skipped"]


class ContractClause(BaseModel):
    """One clause (or clause part / section / table) record."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = Field(default=None, alias="_id")

    # Deterministic idempotency key: same (org, project, contract, document,
    # clause_no, chunk_part) always maps to the same record on reprocessing.
    clause_uid: str

    # --- Scope (all mandatory; processing is blocked without them) ---
    org_id: str
    project_id: str
    contract_id: str
    document_id: str

    # --- Document context ---
    document_title: Optional[str] = None
    document_type: Optional[str] = None  # GCC, SCC, Employer's Requirements, BOQ, ...
    volume: Optional[str] = None
    revision: Optional[str] = None
    effective_date: Optional[datetime] = None

    # --- Clause identity & hierarchy ---
    clause_no: Optional[str] = None
    clause_title: Optional[str] = None
    parent_clause_no: Optional[str] = None
    clause_path: List[str] = Field(default_factory=list)
    level: int = 1

    # --- Content ---
    text: str = ""                     # original wording (preserved exactly)
    cleaned_text: str = ""             # header/footer/noise-stripped; embeddings use this
    summary: str = ""
    keywords: List[str] = Field(default_factory=list)

    # --- Source grounding (page + char span for source-viewer jumps, req 3) ---
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    source_file_path: Optional[str] = None
    source_pdf_url: Optional[str] = None

    # --- Duplicate handling for repeated clause numbers (req 2/#2) ---
    duplicate_group_key: Optional[str] = None
    duplicate_ordinal: int = 1
    duplicate_status: Literal["unique", "duplicate"] = "unique"

    # --- Chunking ---
    chunk_type: ChunkType = "clause"
    chunk_index: int = 0
    chunk_part: int = 1
    chunk_total: int = 1
    # For table chunks: the clause they are linked to.
    linked_clause_no: Optional[str] = None
    table_title: Optional[str] = None

    # --- Quality / lifecycle ---
    confidence: Confidence = "high"
    quality_status: QualityStatus = "validated"
    extraction_method: ExtractionMethod = "text"
    is_current: bool = True
    is_superseded: bool = False
    superseded_by_clause_id: Optional[str] = None
    is_authorised_for_ai: bool = True
    human_review_required: bool = False

    # --- Manual editorial state (Clause Index UI, req 25) ---
    manually_edited: bool = False
    verified_by: Optional[str] = None
    verified_at: Optional[datetime] = None

    # --- Embedding / graph bookkeeping ---
    embedding_status: EmbeddingStatus = "pending"
    qdrant_point_id: Optional[str] = None

    # --- Change detection & audit ---
    checksum: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def to_mongo(self) -> dict:
        """Serialise for storage, dropping a null ``_id`` so Mongo assigns one."""
        data = self.model_dump(by_alias=True, exclude_none=False)
        if data.get("_id") is None:
            data.pop("_id", None)
        return data


class ClauseProcessingRun(BaseModel):
    """Audit record for one clause-processing run (req 23)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Optional[str] = Field(default=None, alias="_id")
    document_id: str
    user_id: Optional[str] = None
    org_id: str
    project_id: str
    contract_id: str
    clauses_detected: int = 0
    section_chunks: int = 0
    tables_detected: int = 0
    low_confidence_chunks: int = 0
    duplicates_detected: int = 0
    modifications_detected: int = 0
    human_review_required: bool = False
    errors: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


__all__ = [
    "ContractClause",
    "ClauseProcessingRun",
    "ChunkType",
    "Confidence",
    "QualityStatus",
    "ExtractionMethod",
    "EmbeddingStatus",
]
