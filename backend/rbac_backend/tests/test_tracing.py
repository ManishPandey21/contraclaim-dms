"""Tracing setup (Phase 3 / M13) is a safe no-op when disabled / OTel absent."""

from __future__ import annotations

from rbac_backend.observability.tracing import current_trace_id, setup_tracing


class _App:
    """Minimal stand-in for a FastAPI app."""


def test_setup_tracing_is_noop_when_disabled():
    # OTEL_ENABLED defaults to False in the test environment.
    assert setup_tracing(_App()) is False


def test_current_trace_id_is_none_without_active_tracing():
    assert current_trace_id() is None
