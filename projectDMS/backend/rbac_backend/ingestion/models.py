from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class IngestionStage(str, Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    ENRICHING = "enriching"
    DONE = "done"
    FAILED = "failed"


class IngestionOptions(BaseModel):
    chunk_size: int = Field(default=1200, ge=200, le=8000)
    chunk_overlap: int = Field(default=120, ge=0, le=1000)
    embedding_model: Optional[str] = None
    embedding_version: str = "v1"
    chunking_version: str = "v1"
    embedding_provider: str = "openai"
    embedding_dim: Optional[int] = None
    enrichment_on: bool = False
    enrichment_strategies: List[str] = Field(default_factory=list)
    use_enriched_text: bool = False
    vector_namespace: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class IngestionJobCreate(BaseModel):
    org_id: str
    project_id: str
    document_id: str
    options: IngestionOptions = Field(default_factory=IngestionOptions)
    content_hash: Optional[str] = None


class StageTiming(BaseModel):
    stage: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[float] = None


class IngestionJob(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="job_id")
    org_id: str
    project_id: str
    document_id: str
    content_hash: Optional[str] = None
    status: IngestionStage = Field(default=IngestionStage.QUEUED)
    stage: IngestionStage = Field(default=IngestionStage.QUEUED)
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    stage_timings: List[StageTiming] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    options: IngestionOptions = Field(default_factory=IngestionOptions)
    deduped: bool = False

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)


class Chunk(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="chunk_id")
    document_id: str
    org_id: str
    project_id: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    text_original: str
    text_enriched: Optional[str] = None
    enrichment_metadata: Optional[Dict[str, Any]] = None
    tags: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    content_hash: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_version: Optional[str] = None
    embedding_provider: Optional[str] = None
    embedding_dim: Optional[int] = None
    chunking_version: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def deterministic_id(
        cls,
        document_id: str,
        idx: int,
        page_start: Optional[int] = None,
        text: Optional[str] = None,
    ) -> str:
        from .chunk_ids import deterministic_chunk_id

        return deterministic_chunk_id(
            document_id, idx, page_start=page_start, text=text
        )


def compute_content_hash(raw: bytes | str) -> str:
    if isinstance(raw, str):
        raw = raw.encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()
