"""Lightweight in-process observability primitives.

The registry intentionally has no external dependency so it works in local,
Compose, and production deployments. A Prometheus scraper can consume the
rendered text endpoint, while logs continue to carry request IDs.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, Tuple


_LATENCY_BUCKETS = (50, 100, 250, 500, 1000, 2500, 5000, 10000)


def _label_value(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _labels(items: Iterable[Tuple[str, object]]) -> str:
    pairs = [f'{key}="{_label_value(value)}"' for key, value in items]
    return "{" + ",".join(pairs) + "}" if pairs else ""


@dataclass
class ObservabilityRegistry:
    started_at: float = field(default_factory=time.time)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _request_total: Dict[Tuple[str, str, str], int] = field(default_factory=dict)
    _request_latency_bucket: Dict[Tuple[str, str, str, int], int] = field(default_factory=dict)
    _request_latency_sum: Dict[Tuple[str, str, str], float] = field(default_factory=dict)
    _request_latency_count: Dict[Tuple[str, str, str], int] = field(default_factory=dict)
    _errors_total: Dict[Tuple[str, str, str], int] = field(default_factory=dict)
    _domain_events_total: Dict[Tuple[str, str], int] = field(default_factory=dict)
    _audit_events_total: Dict[Tuple[str, str, str], int] = field(default_factory=dict)
    _admin_review_items_total: Dict[Tuple[str, str, str], int] = field(default_factory=dict)
    _backup_health: Dict[str, float] = field(default_factory=dict)
    _arbitration_agent_runs_total: Dict[Tuple[str, str], int] = field(default_factory=dict)
    _arbitration_bundle_exports_total: Dict[Tuple[str, str], int] = field(default_factory=dict)
    _arbitration_readiness_score: Dict[Tuple[str, str], float] = field(default_factory=dict)
    _arbitration_missing_evidence: Dict[Tuple[str, str], int] = field(default_factory=dict)
    _arbitration_workflow_events_total: Dict[Tuple[str, str, str, str], int] = field(default_factory=dict)
    _vector_store_failures_total: Dict[Tuple[str, str], int] = field(default_factory=dict)
    _dependency_health: Dict[str, float] = field(default_factory=dict)

    async def record_request(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
    ) -> None:
        status_class = f"{int(status_code / 100)}xx" if status_code else "unknown"
        key = (method.upper(), self._normalize_path(path), status_class)
        async with self._lock:
            self._request_total[key] = self._request_total.get(key, 0) + 1
            self._request_latency_sum[key] = self._request_latency_sum.get(key, 0.0) + duration_ms
            self._request_latency_count[key] = self._request_latency_count.get(key, 0) + 1
            for bucket in _LATENCY_BUCKETS:
                if duration_ms <= bucket:
                    bucket_key = (*key, bucket)
                    self._request_latency_bucket[bucket_key] = (
                        self._request_latency_bucket.get(bucket_key, 0) + 1
                    )
            inf_key = (*key, -1)
            self._request_latency_bucket[inf_key] = self._request_latency_bucket.get(inf_key, 0) + 1
            if status_code >= 500:
                self._errors_total[key] = self._errors_total.get(key, 0) + 1

    async def record_domain_event(self, *, resource_type: str, event_type: str) -> None:
        key = (str(resource_type or "unknown"), str(event_type or "unknown"))
        async with self._lock:
            self._domain_events_total[key] = self._domain_events_total.get(key, 0) + 1

    async def record_audit_event(
        self,
        *,
        action: str,
        result: str,
        resource_type: str | None = None,
    ) -> None:
        key = (
            str(action or "unknown"),
            str(result or "unknown"),
            str(resource_type or "unknown"),
        )
        async with self._lock:
            self._audit_events_total[key] = self._audit_events_total.get(key, 0) + 1

    async def record_admin_review_item(
        self,
        *,
        status: str,
        severity: str,
        source: str,
    ) -> None:
        key = (
            str(status or "unknown"),
            str(severity or "unknown"),
            str(source or "unknown"),
        )
        async with self._lock:
            self._admin_review_items_total[key] = self._admin_review_items_total.get(key, 0) + 1

    async def record_backup_health(
        self,
        *,
        healthy: bool,
        latest_age_hours: float | None,
        missing_artifacts: int,
        unhealthy_artifacts: int,
    ) -> None:
        async with self._lock:
            self._backup_health = {
                "healthy": 1.0 if healthy else 0.0,
                "latest_age_seconds": -1.0 if latest_age_hours is None else latest_age_hours * 3600,
                "missing_artifacts": float(missing_artifacts),
                "unhealthy_artifacts": float(unhealthy_artifacts),
            }

    async def record_arbitration_agent_run(
        self,
        *,
        agent_type: str,
        status: str,
        missing_evidence_count: int = 0,
    ) -> None:
        key = (str(agent_type or "unknown"), str(status or "unknown"))
        async with self._lock:
            self._arbitration_agent_runs_total[key] = self._arbitration_agent_runs_total.get(key, 0) + 1
            missing_key = (str(agent_type or "unknown"), str(status or "unknown"))
            self._arbitration_missing_evidence[missing_key] = int(missing_evidence_count or 0)

    async def record_arbitration_bundle_export(self, *, format: str, status: str) -> None:
        key = (str(format or "unknown"), str(status or "unknown"))
        async with self._lock:
            self._arbitration_bundle_exports_total[key] = self._arbitration_bundle_exports_total.get(key, 0) + 1

    async def record_arbitration_readiness(
        self,
        *,
        case_id: str,
        status: str,
        score: float,
        missing_evidence_count: int = 0,
    ) -> None:
        key = (self._case_label(case_id), str(status or "unknown"))
        async with self._lock:
            self._arbitration_readiness_score[key] = float(score or 0)
            self._arbitration_missing_evidence[("readiness", str(status or "unknown"))] = int(missing_evidence_count or 0)

    async def record_arbitration_workflow(
        self, *, engine: str, status: str, node: str, event: str
    ) -> None:
        key = tuple(str(value or "unknown") for value in (engine, status, node, event))
        async with self._lock:
            self._arbitration_workflow_events_total[key] = self._arbitration_workflow_events_total.get(key, 0) + 1

    async def record_vector_store_failure(self, *, operation: str, namespace: str | None = None) -> None:
        """Count a Qdrant operation that failed while the store was enabled.

        This is the alerting signal for the silent-degradation class of incident
        (vector outage presenting as "no results"): the failure is no longer
        swallowed, and this counter makes it visible to a scraper.
        """
        key = (str(operation or "unknown"), str(namespace or "default"))
        async with self._lock:
            self._vector_store_failures_total[key] = self._vector_store_failures_total.get(key, 0) + 1

    async def record_dependency_health(self, *, name: str, healthy: bool) -> None:
        async with self._lock:
            self._dependency_health[str(name)] = 1.0 if healthy else 0.0

    def snapshot(self) -> Dict[str, object]:
        total_requests = sum(self._request_total.values())
        total_errors = sum(self._errors_total.values())
        uptime_seconds = max(0.0, time.time() - self.started_at)
        return {
            "uptime_seconds": round(uptime_seconds, 2),
            "request_total": total_requests,
            "server_error_total": total_errors,
            "domain_event_total": sum(self._domain_events_total.values()),
            "audit_event_total": sum(self._audit_events_total.values()),
            "admin_review_item_total": sum(self._admin_review_items_total.values()),
            "backup_health": dict(self._backup_health),
            "arbitration_agent_run_total": sum(self._arbitration_agent_runs_total.values()),
            "arbitration_bundle_export_total": sum(self._arbitration_bundle_exports_total.values()),
            "arbitration_workflow_event_total": sum(self._arbitration_workflow_events_total.values()),
            "vector_store_failure_total": sum(self._vector_store_failures_total.values()),
            "dependency_health": dict(self._dependency_health),
        }

    def render_prometheus(self) -> str:
        lines = [
            "# HELP contractdms_uptime_seconds Process uptime in seconds.",
            "# TYPE contractdms_uptime_seconds gauge",
            f"contractdms_uptime_seconds {max(0.0, time.time() - self.started_at):.3f}",
            "# HELP contractdms_http_requests_total HTTP requests by method, path, and status class.",
            "# TYPE contractdms_http_requests_total counter",
        ]

        for (method, path, status_class), value in sorted(self._request_total.items()):
            lines.append(
                "contractdms_http_requests_total"
                f"{_labels((('method', method), ('path', path), ('status_class', status_class)))} {value}"
            )

        lines.extend(
            [
                "# HELP contractdms_http_request_duration_ms HTTP request duration in milliseconds.",
                "# TYPE contractdms_http_request_duration_ms histogram",
            ]
        )
        for (method, path, status_class, bucket), value in sorted(self._request_latency_bucket.items()):
            le = "+Inf" if bucket < 0 else str(bucket)
            lines.append(
                "contractdms_http_request_duration_ms_bucket"
                f"{_labels((('method', method), ('path', path), ('status_class', status_class), ('le', le)))} {value}"
            )
        for (method, path, status_class), value in sorted(self._request_latency_sum.items()):
            labels = _labels((("method", method), ("path", path), ("status_class", status_class)))
            lines.append(f"contractdms_http_request_duration_ms_sum{labels} {value:.3f}")
        for (method, path, status_class), value in sorted(self._request_latency_count.items()):
            labels = _labels((("method", method), ("path", path), ("status_class", status_class)))
            lines.append(f"contractdms_http_request_duration_ms_count{labels} {value}")

        lines.extend(
            [
                "# HELP contractdms_server_errors_total HTTP 5xx responses by method, path, and status class.",
                "# TYPE contractdms_server_errors_total counter",
            ]
        )
        for (method, path, status_class), value in sorted(self._errors_total.items()):
            lines.append(
                "contractdms_server_errors_total"
                f"{_labels((('method', method), ('path', path), ('status_class', status_class)))} {value}"
            )

        lines.extend(
            [
                "# HELP contractdms_document_audit_events_total Document audit events by resource and event type.",
                "# TYPE contractdms_document_audit_events_total counter",
            ]
        )
        for (resource_type, event_type), value in sorted(self._domain_events_total.items()):
            labels = _labels((("resource_type", resource_type), ("event_type", event_type)))
            lines.append(f"contractdms_document_audit_events_total{labels} {value}")

        lines.extend(
            [
                "# HELP contractdms_audit_events_total Tenant audit events by action, result, and resource type.",
                "# TYPE contractdms_audit_events_total counter",
            ]
        )
        for (action, result, resource_type), value in sorted(self._audit_events_total.items()):
            labels = _labels((("action", action), ("result", result), ("resource_type", resource_type)))
            lines.append(f"contractdms_audit_events_total{labels} {value}")

        lines.extend(
            [
                "# HELP contractdms_admin_review_items_total Admin review items raised by status, severity, and source.",
                "# TYPE contractdms_admin_review_items_total counter",
            ]
        )
        for (review_status, severity, source), value in sorted(self._admin_review_items_total.items()):
            labels = _labels((("status", review_status), ("severity", severity), ("source", source)))
            lines.append(f"contractdms_admin_review_items_total{labels} {value}")

        lines.extend(
            [
                "# HELP contractdms_backup_health Backup freshness health, 1 means healthy and 0 means failed.",
                "# TYPE contractdms_backup_health gauge",
                f"contractdms_backup_health {self._backup_health.get('healthy', -1.0):.0f}",
                "# HELP contractdms_backup_latest_age_seconds Age in seconds of the oldest required latest backup artifact, -1 when unavailable.",
                "# TYPE contractdms_backup_latest_age_seconds gauge",
                f"contractdms_backup_latest_age_seconds {self._backup_health.get('latest_age_seconds', -1.0):.3f}",
                "# HELP contractdms_backup_missing_artifacts Number of required backup artifacts not found.",
                "# TYPE contractdms_backup_missing_artifacts gauge",
                f"contractdms_backup_missing_artifacts {self._backup_health.get('missing_artifacts', 0.0):.0f}",
                "# HELP contractdms_backup_unhealthy_artifacts Number of missing, stale, or empty required backup artifacts.",
                "# TYPE contractdms_backup_unhealthy_artifacts gauge",
                f"contractdms_backup_unhealthy_artifacts {self._backup_health.get('unhealthy_artifacts', 0.0):.0f}",
            ]
        )

        lines.extend(
            [
                "# HELP contractdms_arbitration_agent_runs_total Arbitration case agent runs by agent type and status.",
                "# TYPE contractdms_arbitration_agent_runs_total counter",
            ]
        )
        for (agent_type, run_status), value in sorted(self._arbitration_agent_runs_total.items()):
            labels = _labels((("agent_type", agent_type), ("status", run_status)))
            lines.append(f"contractdms_arbitration_agent_runs_total{labels} {value}")

        lines.extend(
            [
                "# HELP contractdms_arbitration_bundle_exports_total Arbitration filing bundle export jobs by format and status.",
                "# TYPE contractdms_arbitration_bundle_exports_total counter",
            ]
        )
        for (format_value, export_status), value in sorted(self._arbitration_bundle_exports_total.items()):
            labels = _labels((("format", format_value), ("status", export_status)))
            lines.append(f"contractdms_arbitration_bundle_exports_total{labels} {value}")

        lines.extend(
            [
                "# HELP contractdms_arbitration_readiness_score Latest arbitration readiness score by case and status.",
                "# TYPE contractdms_arbitration_readiness_score gauge",
            ]
        )
        for (case_id, readiness_status), value in sorted(self._arbitration_readiness_score.items()):
            labels = _labels((("case_id", case_id), ("status", readiness_status)))
            lines.append(f"contractdms_arbitration_readiness_score{labels} {value:.3f}")

        lines.extend(
            [
                "# HELP contractdms_arbitration_missing_evidence Latest missing evidence count by arbitration signal and status.",
                "# TYPE contractdms_arbitration_missing_evidence gauge",
            ]
        )
        for (signal, signal_status), value in sorted(self._arbitration_missing_evidence.items()):
            labels = _labels((("signal", signal), ("status", signal_status)))
            lines.append(f"contractdms_arbitration_missing_evidence{labels} {value}")
        lines.extend(
            [
                "# HELP contractdms_arbitration_workflow_events_total Arbitration workflow transitions by engine, status, node, and event.",
                "# TYPE contractdms_arbitration_workflow_events_total counter",
            ]
        )
        for (engine, workflow_status, node, event), value in sorted(self._arbitration_workflow_events_total.items()):
            labels = _labels((("engine", engine), ("status", workflow_status), ("node", node), ("event", event)))
            lines.append(f"contractdms_arbitration_workflow_events_total{labels} {value}")

        lines.extend(
            [
                "# HELP contractdms_vector_store_failures_total Qdrant operations that failed while the vector store was enabled.",
                "# TYPE contractdms_vector_store_failures_total counter",
            ]
        )
        for (operation, namespace), value in sorted(self._vector_store_failures_total.items()):
            labels = _labels((("operation", operation), ("namespace", namespace)))
            lines.append(f"contractdms_vector_store_failures_total{labels} {value}")

        lines.extend(
            [
                "# HELP contractdms_dependency_up Last observed dependency health, 1 up / 0 down.",
                "# TYPE contractdms_dependency_up gauge",
            ]
        )
        for name, value in sorted(self._dependency_health.items()):
            lines.append(f"contractdms_dependency_up{_labels((('dependency', name),))} {value:.0f}")

        return "\n".join(lines) + "\n"

    def _normalize_path(self, path: str) -> str:
        if not path:
            return "/"
        parts = []
        for part in path.strip("/").split("/"):
            if len(part) == 24 and all(ch in "0123456789abcdefABCDEF" for ch in part):
                parts.append("{id}")
            elif len(part) >= 32 and "-" in part:
                parts.append("{id}")
            else:
                parts.append(part)
        return "/" + "/".join(parts)

    def _case_label(self, case_id: str) -> str:
        value = str(case_id or "unknown")
        if len(value) == 24 and all(ch in "0123456789abcdefABCDEF" for ch in value):
            return "{id}"
        if len(value) >= 32 and "-" in value:
            return "{id}"
        return value


observability_registry = ObservabilityRegistry()
