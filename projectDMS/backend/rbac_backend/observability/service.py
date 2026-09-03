from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..core.config import settings
from .models import AnalyticsRequest, AnalyticsResponse, RagRunLog

logger = logging.getLogger(__name__)


class ObservabilityService:
    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def log_run(
        self,
        run_type: str,
        org_id: Optional[str],
        project_id: Optional[str],
        strategy: Optional[str],
        query: Optional[str],
        retrieved: List[Dict[str, Any]],
        breakdown_ms: Optional[Dict[str, float]] = None,
        user_id: Optional[str] = None,
        model: Optional[str] = None,
        error: Optional[str] = None,
        counts: Optional[Dict[str, int]] = None,
    ) -> None:
        total_latency = None
        if breakdown_ms:
            total_latency = breakdown_ms.get("total_ms") or sum(breakdown_ms.values())

        safe_query = (
            query
            if settings.OBSERVABILITY_STORE_RAW_QUERIES
            else self._redact_query(query)
        )

        log = RagRunLog(
            run_id=str(datetime.utcnow().timestamp()),
            run_type=run_type,
            user_id=user_id,
            org_id=org_id,
            project_id=project_id,
            query=safe_query,
            strategy=strategy,
            filters={},
            retrieved=retrieved,
            model=model,
            breakdown_ms=breakdown_ms or {},
            latency_ms=total_latency,
            error=error,
            extra={"counts": counts or {}},
        )
        await self.db.rag_runs.insert_one(
            log.model_dump(by_alias=True, exclude_none=True)
        )

    @staticmethod
    def _redact_query(query: Optional[str]) -> Optional[str]:
        if not query:
            return None
        normalized = " ".join(str(query).split())
        return f"[redacted len={len(normalized)}]"

    async def get_logs(
        self,
        org_id: Optional[str] = None,
        project_id: Optional[str] = None,
        run_type: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        query: Dict[str, Any] = {}
        if org_id:
            query["org_id"] = org_id
        if project_id:
            query["project_id"] = project_id
        if run_type:
            query["run_type"] = run_type
        if start or end:
            query["created_at"] = {}
            if start:
                query["created_at"]["$gte"] = start
            if end:
                query["created_at"]["$lte"] = end

        cursor = self.db.rag_runs.find(query).sort("created_at", -1).limit(200)
        return [doc async for doc in cursor]

    async def analytics(self, request: AnalyticsRequest) -> AnalyticsResponse:
        window_days = request.window or 7
        since = datetime.utcnow() - timedelta(days=window_days)
        match: Dict[str, Any] = {"created_at": {"$gte": since}}
        if request.org_id:
            match["org_id"] = request.org_id
        if request.project_id:
            match["project_id"] = request.project_id

        pipeline: List[Dict[str, Any]] = [
            {"$match": match},
            {
                "$group": {
                    "_id": {"run_type": "$run_type", "strategy": "$strategy"},
                    "latencies": {"$push": "$latency_ms"},
                    "errors": {
                        "$sum": {"$cond": [{"$ifNull": ["$error", False]}, 1, 0]}
                    },
                    "count": {"$sum": 1},
                    "queries": {"$push": "$query"},
                }
            },
        ]
        grouped = [doc async for doc in self.db.rag_runs.aggregate(pipeline)]
        latency_stats: Dict[str, Any] = {}
        error_counts: Dict[str, int] = {}
        top_queries: List[Dict[str, Any]] = []

        for group in grouped:
            key = f"{group['_id'].get('run_type')}/{group['_id'].get('strategy')}"
            latencies = [
                value
                for value in group.get("latencies", [])
                if isinstance(value, (int, float))
            ]
            if latencies:
                latencies_sorted = sorted(latencies)
                p50 = latencies_sorted[int(0.5 * (len(latencies_sorted) - 1))]
                p95 = latencies_sorted[int(0.95 * (len(latencies_sorted) - 1))]
                latency_stats[key] = {"p50": p50, "p95": p95, "count": len(latencies)}
            error_counts[key] = group.get("errors", 0)
            for q in group.get("queries", [])[:5]:
                if q:
                    top_queries.append(
                        {"query": q, "run_type": group["_id"].get("run_type")}
                    )

        total_runs = sum(stat.get("count", 0) for stat in latency_stats.values())
        empty_results = await self.db.rag_runs.count_documents(
            {**match, "retrieved": {"$size": 0}}
        )
        no_answer_rate = (empty_results / total_runs) if total_runs else 0.0

        return AnalyticsResponse(
            latency_stats=latency_stats,
            no_answer_rate=no_answer_rate,
            top_queries=top_queries[:10],
            error_counts=error_counts,
        )
