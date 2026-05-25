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

    def snapshot(self) -> Dict[str, object]:
        total_requests = sum(self._request_total.values())
        total_errors = sum(self._errors_total.values())
        uptime_seconds = max(0.0, time.time() - self.started_at)
        return {
            "uptime_seconds": round(uptime_seconds, 2),
            "request_total": total_requests,
            "server_error_total": total_errors,
            "domain_event_total": sum(self._domain_events_total.values()),
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


observability_registry = ObservabilityRegistry()
