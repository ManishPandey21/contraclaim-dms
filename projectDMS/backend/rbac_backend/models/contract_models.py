from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ContractUploadSessionRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    organization_id: Optional[str] = None
    project_id: Optional[str] = None


class ContractUploadSessionResponse(BaseModel):
    upload_id: str
    organization_id: str
    project_id: Optional[str] = None
    expires_at: datetime
    max_file_size_bytes: int
    max_chunk_size_bytes: int
    max_chunks: int
    allowed_extensions: List[str] = Field(default_factory=list)
    allowed_mime_types: List[str] = Field(default_factory=list)


class UploadResult(BaseModel):
    """Result for a single uploaded contract."""

    upload_id: str
    document_id: str
    filename: str
    status: str = Field(default="queued")


class UploadMultipartResponse(BaseModel):
    """Response for multi-file upload."""

    organization_id: str
    project_id: Optional[str] = None
    results: List[UploadResult] = Field(default_factory=list)


class ChunkUploadResponse(BaseModel):
    """Response for chunked uploads."""

    upload_id: str
    document_id: Optional[str] = None
    filename: str
    chunk_index: int
    total_chunks: int
    received: bool = False
    merged: bool = False
    scheduled: bool = False
    received_chunks: List[int] = Field(default_factory=list)
    missing_chunks: List[int] = Field(default_factory=list)
    upload_complete: bool = False


class StatusResponse(BaseModel):
    """Contract processing job status."""

    upload_id: str
    document_id: Optional[str] = None
    status: str
    filename: Optional[str] = None
    categories: Optional[List[str]] = None
    error: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    size: Optional[int] = None
    tags: List[str] = Field(default_factory=list)
    updatedAt: datetime = Field(default_factory=datetime.utcnow)
    createdAt: Optional[datetime] = None
    queue_job_id: Optional[str] = None
    progress: Optional[int] = None
    processing_stage: Optional[str] = None
    stage_label: Optional[str] = None
    ocr_pages_total: Optional[int] = None
    ocr_batches_total: Optional[int] = None
    ocr_failed_pages: List[int] = Field(default_factory=list)
    ocr_page_status_counts: Dict[str, int] = Field(default_factory=dict)


class OCRRetryRequest(BaseModel):
    """Request to retry OCR for failed or selected contract PDF pages."""

    page_numbers: List[int] = Field(default_factory=list)


class ContractUploadRecord(BaseModel):
    """Normalized upload record for list endpoint."""

    document_id: str
    upload_id: Optional[str] = None
    filename: Optional[str] = None
    status: str
    categories: Optional[List[str]] = None
    error: Optional[str] = None
    size: Optional[int] = None
    tags: List[str] = Field(default_factory=list)
    createdAt: Optional[datetime] = None
    updatedAt: Optional[datetime] = None


class ContractListResponse(BaseModel):
    """Paginated upload list response."""

    uploads: List[ContractUploadRecord] = Field(default_factory=list)
    count: int = 0


class HighlightOffset(BaseModel):
    """Start/end offsets for highlighted spans."""

    start: int
    end: int


class ContractClauseChunk(BaseModel):
    """Single chunk of a contract clause."""

    document_id: Optional[str] = None
    file_name: Optional[str] = None
    source_filename: Optional[str] = None
    uploadType: Optional[str] = None
    upload_id: Optional[str] = None
    filename: Optional[str] = None
    chunk_index: Optional[int] = None
    clause_number: Optional[str] = None
    clause_no: Optional[str] = None
    clause_title: Optional[str] = None
    clause_id: Optional[str] = None
    clause_type: Optional[str] = None
    chunk_type: Optional[str] = None
    clause_level: Optional[int] = None
    parent_clause_number: Optional[str] = None
    is_complete_clause: Optional[bool] = True
    clause_start_position: Optional[int] = None
    clause_end_position: Optional[int] = None
    toc_path: Optional[List[str]] = None
    text: str
    score: Optional[float] = None
    page_number: Optional[int] = None
    page: Optional[int] = None
    page_numbers: Optional[List[int]] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    source_pdf_page_link: Optional[str] = None
    section: Optional[str] = None
    section_heading: Optional[str] = None
    section_title: Optional[str] = None
    clause_tags: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    offsets: Optional[List[HighlightOffset]] = None
    contract_id: Optional[str] = None
    ai_chunked: Optional[bool] = False
    ai_confidence: Optional[float] = None
    createdAt: Optional[datetime] = None


class ContractSource(BaseModel):
    """Compact source reference for a clause hit."""

    document_id: Optional[str] = None
    upload_id: Optional[str] = None
    file_name: Optional[str] = None
    clause_number: Optional[str] = None
    clause_title: Optional[str] = None
    section_heading: Optional[str] = None
    clause_tags: List[str] = Field(default_factory=list)
    page_numbers: List[int] = Field(default_factory=list)
    page_number: Optional[int] = None
    page: Optional[int] = None


class ContractSearchRequest(BaseModel):
    """Contract search payload."""

    query: str = Field(default="", max_length=2000)
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    document_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
    top_docs: Optional[int] = Field(default=None, ge=1, le=20)
    chunks_per_doc: Optional[int] = Field(default=None, ge=1, le=20)
    summarize: bool = False
    exact_phrase: bool = False
    clause_number: Optional[str] = None
    clause_title: Optional[str] = None
    section_heading: Optional[str] = None
    clause_tags: List[str] = Field(default_factory=list)
    category_terms: List[str] = Field(default_factory=list)
    page_from: Optional[int] = Field(default=None, ge=1)
    page_to: Optional[int] = Field(default=None, ge=1)


class ContractSearchResponse(BaseModel):
    """Contract search response."""

    results: List[ContractClauseChunk] = Field(default_factory=list)
    summary: Optional[str] = None
    ai_summary_title: Optional[str] = None
    took_ms: int = 0
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    total_count: int = 0
    has_more: bool = False
    current_page: int = 1
    page_size: int = 10
    sources: List[ContractSource] = Field(default_factory=list)


__all__ = [
    "ChunkUploadResponse",
    "ContractClauseChunk",
    "ContractListResponse",
    "ContractSearchRequest",
    "ContractSearchResponse",
    "ContractSource",
    "ContractUploadRecord",
    "ContractUploadSessionRequest",
    "ContractUploadSessionResponse",
    "HighlightOffset",
    "OCRRetryRequest",
    "StatusResponse",
    "UploadMultipartResponse",
    "UploadResult",
]
