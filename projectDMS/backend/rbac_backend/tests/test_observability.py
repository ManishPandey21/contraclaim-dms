import pytest

from rbac_backend.services.observability import ObservabilityRegistry


@pytest.mark.asyncio
async def test_observability_registry_renders_prometheus_metrics():
    registry = ObservabilityRegistry()

    await registry.record_request(
        method="GET",
        path="/api/documents/507f1f77bcf86cd799439011",
        status_code=200,
        duration_ms=42.0,
    )
    await registry.record_request(
        method="GET",
        path="/api/documents/507f1f77bcf86cd799439011",
        status_code=500,
        duration_ms=2501.0,
    )
    await registry.record_domain_event(
        resource_type="document",
        event_type="document.updated",
    )
    await registry.record_backup_health(
        healthy=False,
        latest_age_hours=30.0,
        missing_artifacts=1,
        unhealthy_artifacts=2,
    )

    rendered = registry.render_prometheus()

    assert "contractdms_http_requests_total" in rendered
    assert 'path="/api/documents/{id}"' in rendered
    assert "contractdms_server_errors_total" in rendered
    assert "contractdms_document_audit_events_total" in rendered
    assert "contractdms_backup_health 0" in rendered
    assert "contractdms_backup_latest_age_seconds 108000.000" in rendered


def test_observability_snapshot_counts_recorded_events():
    registry = ObservabilityRegistry()

    snapshot = registry.snapshot()

    assert snapshot["request_total"] == 0
    assert snapshot["server_error_total"] == 0
