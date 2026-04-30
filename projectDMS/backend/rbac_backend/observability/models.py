from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class RunType(str):
    SEARCH = "search"
    RAG = "rag"
    AGENT = "agent"
    INGESTION = "ingestion"


class RagRunLog(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    run_id: str
    run_type: str
    user_id: Optional[str] = None
    org_id: Optional[str] = None
    project_id: Optional[str] = None
    query: Optional[str] = None
    strategy: Optional[str] = None
    filters: Dict[str, Any] = Field(default_factory=dict)
    retrieved: List[Dict[str, Any]] = Field(default_factory=list)
    model: Optional[str] = None
    token_counts: Optional[Dict[str, int]] = None
    latency_ms: Optional[float] = None
    breakdown_ms: Dict[str, float] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    error: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class AnalyticsRequest(BaseModel):
    org_id: Optional[str] = None
    project_id: Optional[str] = None
    window: Optional[int] = Field(default=7, description="Lookback window in days")
    group_by: List[str] = Field(default_factory=list)


class AnalyticsResponse(BaseModel):
    latency_stats: Dict[str, Any]
    no_answer_rate: float
    top_queries: List[Dict[str, Any]]
    error_counts: Dict[str, int]
