from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class HealthStatus(BaseModel):
    """Overall system health state."""

    status: str
    message: Optional[str] = None
    services: Dict[str, Any] = Field(default_factory=dict)
    alerts: List[Dict[str, Any]] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class PerformanceMetrics(BaseModel):
    """Aggregated performance metrics."""

    window_hours: int
    requests: int
    avg_response_time_ms: float
    p95_response_time_ms: float
    error_rate: float
    slow_requests: int
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class EndpointStats(BaseModel):
    """Per-endpoint performance statistics."""

    endpoint: str
    method: str
    average_latency_ms: float
    p95_latency_ms: float
    requests: int
    error_rate: float


class JobStats(BaseModel):
    """Background job metrics."""

    active_jobs: int
    queued_jobs: int
    completed_jobs_last_hour: int
    failed_jobs_last_hour: int
    updated_at: datetime = Field(default_factory=datetime.utcnow)
