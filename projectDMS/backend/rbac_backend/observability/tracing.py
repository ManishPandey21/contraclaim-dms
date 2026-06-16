"""OpenTelemetry distributed tracing (Phase 3 / M13).

Opt-in and dependency-light: when ``OTEL_ENABLED`` is false (default) or the
OpenTelemetry libraries are not installed, every function here is a safe no-op,
so the app and CI run unchanged. Enable by installing the
``opentelemetry-*`` packages and setting ``OTEL_ENABLED=true`` +
``OTEL_EXPORTER_OTLP_ENDPOINT`` (see docs/OPERATIONS.md).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..core.config import settings

logger = logging.getLogger(__name__)

_TRACING_READY = False


def setup_tracing(app: Any) -> bool:
    """Initialise OTLP tracing + FastAPI instrumentation. Returns True if enabled."""
    global _TRACING_READY
    if not getattr(settings, "OTEL_ENABLED", False):
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:  # pragma: no cover - optional dependency
        logger.warning("OTEL_ENABLED but OpenTelemetry libraries are not installed: %s", exc)
        return False

    try:
        resource = Resource.create({"service.name": settings.OTEL_SERVICE_NAME})
        provider = TracerProvider(resource=resource)
        endpoint = str(settings.OTEL_EXPORTER_OTLP_ENDPOINT or "").strip()
        exporter = OTLPSpanExporter(endpoint=endpoint) if endpoint else OTLPSpanExporter()
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        _TRACING_READY = True
        logger.info("OpenTelemetry tracing enabled (service=%s)", settings.OTEL_SERVICE_NAME)
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to initialize OpenTelemetry tracing: %s", exc)
        return False


def current_trace_id() -> Optional[str]:
    """Return the active trace id (32-hex) for log correlation, or None."""
    if not _TRACING_READY:
        return None
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context() if span else None
        trace_id = getattr(ctx, "trace_id", 0) if ctx else 0
        if trace_id:
            return format(trace_id, "032x")
    except Exception:  # pragma: no cover - defensive
        return None
    return None
