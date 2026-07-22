"""Phase-5 shadow parity and rollout-health primitives.

The comparison contract deliberately admits hashes, counts, statuses, and
durations only. It must never receive evidence text or draft prose because the
result is persisted in the workflow audit stream and exported as telemetry.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, Iterable

from ...core.config import settings
from .workflow_repository import artifact_hash


SHADOW_DIMENSIONS = (
    "evidence_set",
    "matrix_rows",
    "readiness",
    "section_coverage",
    "citation_validity",
    "validation_blockers",
    "output_latency",
    "human_interventions",
)

PHASE6_REQUIRED_PLEADING_TYPES = (
    "statement_of_claim",
    "statement_of_defence",
    "counterclaim",
    "rejoinder",
)

ACCEPTED_WORKFLOW_RECEIPT_FIELDS = (
    "readiness_approval_receipt_id",
    "plan_approval_receipt_id",
    "legal_review_approval_receipt_id",
    "draft_approval_receipt_id",
    "export_approval_receipt_id",
)


def _dimension(authoritative: Any, candidate: Any) -> Dict[str, Any]:
    if authoritative is None or candidate is None:
        return {"status": "not_evaluated"}
    return {
        "status": "match" if authoritative == candidate else "mismatch",
        "authoritative": authoritative,
        "candidate": candidate,
    }


def build_shadow_comparison(
    *,
    pleading_type: str,
    state_version: int,
    authoritative: Dict[str, Any],
    candidate: Dict[str, Any],
    authoritative_latency_ms: float | None = None,
    candidate_latency_ms: float | None = None,
) -> Dict[str, Any]:
    """Return a stable, redacted v2-vs-graph comparison payload."""

    dimensions = {
        key: _dimension(authoritative.get(key), candidate.get(key))
        for key in SHADOW_DIMENSIONS
        if key != "output_latency"
    }
    if authoritative_latency_ms is None or candidate_latency_ms is None:
        dimensions["output_latency"] = {"status": "not_evaluated"}
    else:
        dimensions["output_latency"] = {
            "status": "measured",
            "authoritative_ms": round(max(0.0, authoritative_latency_ms), 3),
            "candidate_ms": round(max(0.0, candidate_latency_ms), 3),
        }
    dimensions = {key: dimensions[key] for key in SHADOW_DIMENSIONS}
    evaluated = [item for item in dimensions.values() if item["status"] not in {"not_evaluated", "measured"}]
    mismatches = [key for key, item in dimensions.items() if item["status"] == "mismatch"]
    result = {
        "schema_version": 1,
        "pleading_type": str(pleading_type),
        "state_version": int(state_version),
        "authoritative_engine": "arbitration_v2",
        "candidate_engine": "langgraph_v1",
        "authoritative_writes": False,
        "overall_status": "match" if evaluated and not mismatches else "mismatch" if mismatches else "insufficient_data",
        "evaluated_dimensions": len(evaluated),
        "mismatch_dimensions": mismatches,
        "dimensions": dimensions,
    }
    result["comparison_hash"] = artifact_hash(result)
    return result


def _rate(numerator: int, denominator: int) -> float:
    return round((100.0 * numerator / denominator), 3) if denominator else 0.0


def build_rollout_health(
    runs: Iterable[Dict[str, Any]],
    events: Iterable[Dict[str, Any]],
    *,
    now: datetime | None = None,
    config: Any = settings,
) -> Dict[str, Any]:
    """Build a case-scoped, identifier-free operational readiness summary."""

    run_rows = list(runs)
    event_rows = list(events)
    now = now or datetime.now(timezone.utc)
    total = len(run_rows)
    graph_runs = sum(1 for run in run_rows if run.get("engine") == "langgraph_v1")
    accepted_graph_runs = [
        run
        for run in run_rows
        if run.get("engine") == "langgraph_v1"
        and run.get("status") == "completed"
        and run.get("_acceptance_receipts_valid") is True
        and all(run.get(field) for field in ACCEPTED_WORKFLOW_RECEIPT_FIELDS)
        and run.get("draft_version_hash")
        and run.get("validation_artifact_set_hash")
    ]
    failed = sum(1 for run in run_rows if run.get("status") == "failed")
    fallbacks = sum(1 for event in event_rows if event.get("event_type") == "workflow_fallback_v2")
    shadow_events = [
        event
        for event in event_rows
        if event.get("event_type") in {"shadow_comparison", "shadow_comparison_failed"}
    ]
    shadow_matches = sum(1 for event in shadow_events if (event.get("data") or {}).get("overall_status") == "match")
    drift_by_run: Dict[str, datetime] = {}
    resolved_by_run: Dict[str, datetime] = {}
    for event in event_rows:
        event_type = str(event.get("event_type") or "")
        reason = str((event.get("data") or {}).get("reason") or "")
        timestamp = event.get("created_at")
        if not isinstance(timestamp, datetime):
            timestamp = datetime.min.replace(tzinfo=timezone.utc)
        elif timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        run_id = str(event.get("run_id") or "")
        if event_type == "workflow_dependencies_invalidated" and (
            "drift" in reason or "hash_changed" in reason
        ):
            drift_by_run[run_id] = max(drift_by_run.get(run_id, timestamp), timestamp)
        if event_type in {"matrix_review_approved", "readiness_approved", "workflow_completed"}:
            resolved_by_run[run_id] = max(resolved_by_run.get(run_id, timestamp), timestamp)
    unresolved_drift = sum(
        1 for run_id, timestamp in drift_by_run.items() if resolved_by_run.get(run_id, datetime.min.replace(tzinfo=timezone.utc)) <= timestamp
    )
    checkpoint_pending = sum(1 for run in run_rows if run.get("checkpoint_sync_status") == "pending")
    max_pause_hours = float(getattr(config, "ARBITRATION_ENGINE_MAX_PAUSE_HOURS", 72.0))
    stale_paused = 0
    for run in run_rows:
        if not str(run.get("status") or "").startswith("awaiting_"):
            continue
        timestamp = run.get("updated_at") or run.get("created_at")
        if isinstance(timestamp, datetime):
            value = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
            if (now - value).total_seconds() > max_pause_hours * 3600:
                stale_paused += 1

    failure_rate = _rate(failed, total)
    fallback_rate = _rate(fallbacks, total)
    parity_rate = _rate(shadow_matches, len(shadow_events))
    unresolved_drift_rate = _rate(unresolved_drift, total)
    minimum_sample = int(getattr(config, "ARBITRATION_ENGINE_MIN_ACCEPTANCE_SAMPLE", 20))
    alerts = []

    def alert(code: str, severity: str, value: Any, threshold: Any) -> None:
        alerts.append({"code": code, "severity": severity, "value": value, "threshold": threshold})

    if checkpoint_pending:
        alert("checkpoint_sync_pending", "critical", checkpoint_pending, 0)
    if stale_paused:
        alert("stale_paused_workflow", "warning", stale_paused, 0)
    if unresolved_drift:
        alert("unresolved_source_drift", "critical", unresolved_drift, 0)
    if total >= minimum_sample and failure_rate > float(getattr(config, "ARBITRATION_ENGINE_MAX_FAILURE_RATE_PERCENT", 2.0)):
        alert("workflow_failure_rate", "critical", failure_rate, getattr(config, "ARBITRATION_ENGINE_MAX_FAILURE_RATE_PERCENT", 2.0))
    if total >= minimum_sample and fallback_rate > float(getattr(config, "ARBITRATION_ENGINE_MAX_FALLBACK_RATE_PERCENT", 5.0)):
        alert("fallback_rate", "critical", fallback_rate, getattr(config, "ARBITRATION_ENGINE_MAX_FALLBACK_RATE_PERCENT", 5.0))
    if len(shadow_events) >= minimum_sample and parity_rate < float(getattr(config, "ARBITRATION_ENGINE_MIN_SHADOW_PARITY_PERCENT", 99.0)):
        alert("shadow_parity_rate", "critical", parity_rate, getattr(config, "ARBITRATION_ENGINE_MIN_SHADOW_PARITY_PERCENT", 99.0))

    enough_data = len(accepted_graph_runs) >= minimum_sample and len(shadow_events) >= minimum_sample
    health_status = (
        "blocked"
        if any(item["severity"] == "critical" for item in alerts)
        else "ready" if enough_data else "insufficient_data"
    )
    pleading_type_counts = {
        pleading_type: sum(
            1 for run in accepted_graph_runs if str(run.get("pleading_type") or "") == pleading_type
        )
        for pleading_type in PHASE6_REQUIRED_PLEADING_TYPES
    }
    minimum_pleading_type_sample = int(
        getattr(config, "ARBITRATION_ENGINE_MIN_PLEADING_TYPE_SAMPLE", 1)
    )
    missing_pleading_types = [
        pleading_type
        for pleading_type, count in pleading_type_counts.items()
        if count < minimum_pleading_type_sample
    ]
    production_accepted = bool(
        getattr(config, "ARBITRATION_ENGINE_PRODUCTION_ACCEPTED", False)
    )
    receipt_id_present = bool(
        str(getattr(config, "ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID", "") or "").strip()
    )
    receipt_hash = str(
        getattr(config, "ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256", "") or ""
    ).strip().lower()
    receipt_hash_valid = bool(re.fullmatch(r"[0-9a-f]{64}", receipt_hash))
    compatibility_mode = str(
        getattr(config, "ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE", "active") or "active"
    ).strip().lower()
    compatibility_until = str(
        getattr(config, "ARBITRATION_ENGINE_V2_COMPATIBILITY_UNTIL", "") or ""
    ).strip()
    try:
        compatibility_date = datetime.fromisoformat(compatibility_until).date()
    except ValueError:
        compatibility_date = None
    compatibility_retained = bool(
        compatibility_date is not None
        and compatibility_date >= now.date()
        and compatibility_mode in {"active", "read_replay_only"}
    )
    require_health = bool(
        getattr(config, "ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY", True)
    )
    cutover_blockers = []
    if not production_accepted:
        cutover_blockers.append("production_acceptance_false")
    if not receipt_id_present or not receipt_hash_valid:
        cutover_blockers.append("acceptance_receipt_missing_or_invalid")
    if require_health and health_status != "ready":
        cutover_blockers.append("rollout_health_not_ready")
    if missing_pleading_types:
        cutover_blockers.append("pleading_type_coverage_incomplete")
    if bool(getattr(config, "ARBITRATION_ENGINE_ROLLOUT_PAUSED", False)):
        cutover_blockers.append("rollout_paused")
    if not compatibility_retained:
        cutover_blockers.append("v2_compatibility_window_missing_or_expired")
    primary_eligible = not cutover_blockers
    return {
        "schema_version": 1,
        "status": health_status,
        "production_accepted": production_accepted,
        "rollout_paused": bool(getattr(config, "ARBITRATION_ENGINE_ROLLOUT_PAUSED", False)),
        "minimum_acceptance_sample": minimum_sample,
        "sample": {
            "workflows": total,
            "langgraph_workflows": graph_runs,
            "accepted_langgraph_workflows": len(accepted_graph_runs),
            "shadow_comparisons": len(shadow_events),
        },
        "rates": {
            "workflow_failure_percent": failure_rate,
            "fallback_percent": fallback_rate,
            "shadow_parity_percent": parity_rate,
            "unresolved_source_drift_percent": unresolved_drift_rate,
        },
        "signals": {
            "checkpoint_sync_pending": checkpoint_pending,
            "stale_paused_workflows": stale_paused,
            "unresolved_source_drift": unresolved_drift,
        },
        "primary_cutover": {
            "policy_version": "phase6-v1",
            "eligible": primary_eligible,
            "blockers": cutover_blockers,
            "acceptance_receipt_present": receipt_id_present,
            "acceptance_receipt_hash_valid": receipt_hash_valid,
            "required_health_ready": require_health,
            "minimum_pleading_type_sample": minimum_pleading_type_sample,
            "pleading_type_samples": pleading_type_counts,
            "missing_pleading_types": missing_pleading_types,
            "v2_compatibility_mode": compatibility_mode,
            "v2_compatibility_until": compatibility_until or None,
            "v2_compatibility_retained": compatibility_retained,
        },
        "alerts": alerts,
    }
