from __future__ import annotations

from typing import Any, Dict, List

from .runner import MigrationResult


VERSION = "20260721_0002"
NAME = "arbitration_workflow_foundation"
DESCRIPTION = "Create durable arbitration workflow run, snapshot, effect, approval, event, plan, and checkpoint indexes."

INDEXES = [
    ("arbitration_workflow_runs", [("case_id", 1), ("created_at", -1)], "arb_wf_case_time", False, {}),
    ("arbitration_workflow_runs", [("case_id", 1), ("idempotency_key", 1)], "arb_wf_idempotency", True, {"idempotency_key": {"$type": "string"}}),
    ("arbitration_workflow_runs", [("status", 1), ("updated_at", 1)], "arb_wf_status_time", False, {}),
    ("arbitration_workflow_snapshots", "effect_key", "arb_wf_snapshot_effect", True, {}),
    ("arbitration_workflow_snapshots", [("run_id", 1), ("kind", 1), ("created_at", 1)], "arb_wf_snapshot_run_kind", False, {}),
    ("arbitration_workflow_effects", "effect_key", "arb_wf_effect_key", True, {}),
    ("arbitration_workflow_effects", [("run_id", 1), ("status", 1)], "arb_wf_effect_run_status", False, {}),
    ("arbitration_workflow_approvals", [("run_id", 1), ("gate", 1), ("artifact_hash", 1), ("decision", 1)], "arb_wf_approval_exact", True, {"run_id": {"$type": "string"}}),
    ("arbitration_workflow_approvals", [("case_id", 1), ("gate", 1), ("invalidated_at", 1)], "arb_wf_approval_validity", False, {}),
    ("arbitration_workflow_events", [("run_id", 1), ("created_at", 1)], "arb_wf_event_run_time", False, {}),
    ("arbitration_plans", [("run_id", 1), ("version", 1)], "arb_plan_run_version", True, {}),
    ("arbitration_plans", [("run_id", 1), ("plan_hash", 1)], "arb_plan_run_hash", True, {}),
    ("arbitration_draft_versions", [("draft_id", 1), ("version", 1)], "arb_draft_version_unique", True, {}),
    ("arbitration_draft_version_counters", "draft_id", "arb_draft_counter_id", True, {}),
    ("arbitration_export_authorizations", [("draft_id", 1), ("draft_version_hash", 1), ("format", 1)], "arb_export_authorization_exact", True, {"draft_id": {"$type": "string"}}),
    ("arbitration_export_authorizations", [("case_id", 1), ("bundle_hash", 1), ("format", 1)], "arb_bundle_authorization_exact", True, {"bundle_hash": {"$type": "string"}}),
    ("arbitration_bundle_exports", [("case_id", 1), ("bundle_hash", 1), ("format", 1)], "arb_bundle_export_exact", True, {"bundle_hash": {"$type": "string"}}),
    ("arbitration_langgraph_checkpoints", "created_at", "arb_checkpoint_created_ttl", False, {}),
    ("arbitration_langgraph_checkpoint_writes", [("thread_id", 1), ("checkpoint_id", 1)], "arb_checkpoint_write_thread", False, {}),
]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations: List[Dict[str, Any]] = [{"operation": "seed_version_counters_from_existing_maximum"}]
    if not dry_run:
        cursor = db.arbitration_draft_versions.aggregate(
            [{"$group": {"_id": "$draft_id", "maximum": {"$max": "$version"}}}]
        )
        async for row in cursor:
            await db.arbitration_draft_version_counters.update_one(
                {"_id": row["_id"]},
                {"$set": {"draft_id": row["_id"], "value": int(row.get("maximum") or 0)}},
                upsert=True,
            )
    for collection, keys, name, unique, partial in INDEXES:
        operation = {"operation": "create_index", "collection": collection, "keys": keys, "name": name, "unique": unique}
        if partial:
            operation["partial_filter"] = partial
        operations.append(operation)
        if not dry_run:
            kwargs: Dict[str, Any] = {"name": name, "unique": unique, "background": True}
            if partial:
                kwargs["partialFilterExpression"] = partial
            if name == "arb_checkpoint_created_ttl":
                kwargs["expireAfterSeconds"] = 30 * 86400
            await db[collection].create_index(keys, **kwargs)
    return MigrationResult(version=VERSION, name=NAME, status="dry_run" if dry_run else "applied", operations=operations)


async def downgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = [{"operation": "drop_index", "collection": collection, "name": name} for collection, _keys, name, _unique, _partial in INDEXES]
    if not dry_run:
        for collection, _keys, name, _unique, _partial in INDEXES:
            try:
                await db[collection].drop_index(name)
            except Exception:
                pass
    return MigrationResult(version=VERSION, name=NAME, status="dry_run" if dry_run else "rolled_back", operations=operations)
