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
    await registry.record_audit_event(
        action="policy.authorize",
        result="deny",
        resource_type="document",
    )
    await registry.record_admin_review_item(
        status="open",
        severity="critical",
        source="authorization",
    )
    await registry.record_backup_health(
        healthy=False,
        latest_age_hours=30.0,
        missing_artifacts=1,
        unhealthy_artifacts=2,
    )
    await registry.record_arbitration_agent_run(
        agent_type="orchestrator",
        status="completed",
        missing_evidence_count=2,
    )
    await registry.record_arbitration_bundle_export(format="zip", status="completed")
    await registry.record_arbitration_readiness(
        case_id="case-1",
        status="blocked",
        score=67,
        missing_evidence_count=1,
    )
    comparison = {
        "pleading_type": "statement_of_claim",
        "dimensions": {
            "matrix_rows": {"status": "match"},
            "output_latency": {"status": "measured", "authoritative_ms": 10.0, "candidate_ms": 12.0},
        },
    }
    await registry.record_arbitration_shadow_comparison(comparison)
    await registry.record_arbitration_workflow_health(
        {"alerts": [{"severity": "critical", "code": "shadow_parity_rate"}]}
    )
    await registry.record_arbitration_fallback(from_engine="langgraph_v1", reason="operator_requested")

    rendered = registry.render_prometheus()

    assert "contractdms_http_requests_total" in rendered
    assert 'path="/api/documents/{id}"' in rendered
    assert "contractdms_server_errors_total" in rendered
    assert "contractdms_document_audit_events_total" in rendered
    assert "contractdms_audit_events_total" in rendered
    assert 'action="policy.authorize"' in rendered
    assert "contractdms_admin_review_items_total" in rendered
    assert 'severity="critical"' in rendered
    assert "contractdms_backup_health 0" in rendered
    assert "contractdms_backup_latest_age_seconds 108000.000" in rendered
    assert "contractdms_arbitration_agent_runs_total" in rendered
    assert 'agent_type="orchestrator"' in rendered
    assert "contractdms_arbitration_bundle_exports_total" in rendered
    assert "contractdms_arbitration_readiness_score" in rendered
    assert "contractdms_arbitration_missing_evidence" in rendered
    assert "contractdms_arbitration_shadow_comparisons_total" in rendered
    assert 'dimension="matrix_rows"' in rendered
    assert "contractdms_arbitration_shadow_latency_ms" in rendered
    assert "contractdms_arbitration_workflow_alert" in rendered
    assert "contractdms_arbitration_workflow_fallbacks_total" in rendered


def test_observability_snapshot_counts_recorded_events():
    registry = ObservabilityRegistry()

    snapshot = registry.snapshot()

    assert snapshot["request_total"] == 0
    assert snapshot["server_error_total"] == 0
    assert snapshot["audit_event_total"] == 0
    assert snapshot["admin_review_item_total"] == 0
    assert snapshot["arbitration_shadow_comparison_total"] == 0
    assert snapshot["arbitration_fallback_total"] == 0
    assert snapshot["arbitration_workflow_active_alerts"] == 0
