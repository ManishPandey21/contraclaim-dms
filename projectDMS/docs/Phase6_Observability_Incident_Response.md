# Phase 6 - Observability and Incident Response

Date: 03 May 2026

## Implemented

- Added a lightweight in-process observability registry.
- Added Prometheus-compatible `/metrics`.
- Added `/health/observability` for quick operational snapshots.
- Added request latency histograms, request counters, 5xx counters, uptime gauge, and document audit event counters.
- Added slow request warning logs using `SLOW_REQUEST_THRESHOLD_MS`.
- Continued request ID propagation through `X-Request-ID`.
- Added audit-event metrics integration so create/update/delete/download/comment actions become visible.

## Configuration

```env
METRICS_ENABLED=true
METRICS_TOKEN=replace-with-internal-scrape-token
SLOW_REQUEST_THRESHOLD_MS=2000
```

If `METRICS_TOKEN` is set, scrapers must send:

```http
X-Metrics-Token: <token>
```

## Recommended Dashboards

- API request rate by method/path/status class.
- API P50/P95/P99 latency using `contractdms_http_request_duration_ms`.
- HTTP 5xx rate using `contractdms_server_errors_total`.
- Audit event volume by event type using `contractdms_document_audit_events_total`.
- Health readiness failures.
- MongoDB primary state, replication lag, connections, disk, and slow queries.
- Redis queue depth and dead-letter count.
- Upload throughput and failures.

## Incident Response

1. Check `/health/ready`.
2. Check `/health/observability`.
3. Check `/metrics` for elevated 5xx or latency.
4. Inspect backend logs by `X-Request-ID`.
5. Check Redis queues and MongoDB health.
6. Roll back using `docs/Release_Runbook.md` if the incident correlates with a deploy.
7. Capture a post-incident note with customer impact, root cause, rollback/fix, and follow-up tasks.

## Remaining Work

- Export metrics to Prometheus/Grafana or another monitoring stack.
- Add distributed tracing with OpenTelemetry.
- Add exception monitoring such as Sentry.
- Add queue and worker-specific metrics.
- Add alert rules and on-call escalation policy.
