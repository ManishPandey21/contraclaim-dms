from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from ..services.performance_monitor import performance_monitor
from ..services.cache_service import cache
from ..models.performance_models import JobStats

try:
    from .background_jobs import BackgroundJobProcessor
except ImportError:  # pragma: no cover - fallback
    BackgroundJobProcessor = None  # type: ignore


class PerformanceService:
    """Facade exposing monitoring information to the API layer."""

    def __init__(self) -> None:
        self._job_processor = BackgroundJobProcessor() if BackgroundJobProcessor else None

    async def get_system_health(self) -> Dict[str, Any]:
        health = await performance_monitor.get_system_health()
        return {
            "status": health.status,
            "message": None,
            "services": {
                "memory": health.memory,
                "cpu": health.cpu,
                "disk": health.disk,
            },
            "alerts": [],
            "timestamp": health.timestamp,
        }

    async def get_performance_metrics(self, hours: int) -> Dict[str, Any]:
        summary = await performance_monitor.get_performance_summary(hours=hours)
        generated_at = summary.get("generated_at") or datetime.utcnow()
        return {
            "window_hours": hours,
            "requests": summary.get("total_requests", 0),
            "avg_response_time_ms": summary.get("avg_response_time", 0.0) * 1000,
            "p95_response_time_ms": summary.get("p95_response_time", 0.0) * 1000,
            "error_rate": summary.get("error_rate", 0.0),
            "slow_requests": summary.get("slow_requests", 0),
            "updated_at": generated_at,
        }

    async def get_endpoint_statistics(self, limit: int) -> List[Dict[str, Any]]:
        return await performance_monitor.get_endpoint_stats(limit=limit)

    async def get_slow_queries(self, limit: int) -> List[Dict[str, Any]]:
        return await performance_monitor.get_slow_queries(limit=limit)

    async def get_cache_stats(self) -> Dict[str, Any]:
        return cache.get_stats()

    async def clear_cache(self) -> None:
        await cache.clear()

    async def get_job_stats(self) -> JobStats:
        if not self._job_processor:
            return JobStats(
                active_jobs=0,
                queued_jobs=0,
                completed_jobs_last_hour=0,
                failed_jobs_last_hour=0,
            )
        stats = self._job_processor.get_stats()
        return JobStats(
            active_jobs=stats.get("active_jobs", 0),
            queued_jobs=stats.get("queue_size", 0),
            completed_jobs_last_hour=stats.get("completed_jobs", 0),
            failed_jobs_last_hour=stats.get("failed_jobs", 0),
        )

    async def cancel_job(self, job_id: str) -> bool:
        if not self._job_processor:
            return False
        return self._job_processor.cancel_job(job_id)

    def ensure_job_processor(self):
        if not self._job_processor and BackgroundJobProcessor:
            self._job_processor = BackgroundJobProcessor()
        return self._job_processor
