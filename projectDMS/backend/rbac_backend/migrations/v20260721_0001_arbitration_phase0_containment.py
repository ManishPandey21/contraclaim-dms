from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from .runner import MigrationResult


VERSION = "20260721_0001"
NAME = "arbitration_phase0_containment"
DESCRIPTION = (
    "Return unverifiable arbitration approvals to review, normalize rejoinder permission fields, "
    "and add readiness-receipt, export-authorization, and version-lineage indexes."
)

MATRIX_COLLECTIONS = [
    "arbitration_document_index",
    "arbitration_chronology_matrix",
    "arbitration_clause_matrix",
    "arbitration_issue_matrix",
    "arbitration_claim_matrix",
    "arbitration_defence_matrix",
    "arbitration_counterclaim_matrix",
    "arbitration_rejoinder_matrix",
    "arbitration_quantum_annexures",
    "arbitration_notice_compliance",
    "arbitration_jurisdiction_matrix",
    "arbitration_expert_alignment",
]

INDEXES = [
    (
        "arbitration_workflow_approvals",
        [("case_id", 1), ("gate", 1), ("draft_type", 1), ("approved_at", -1)],
        "arb_approval_case_gate_type_time",
        False,
    ),
    (
        "arbitration_workflow_approvals",
        [("artifact_hash", 1), ("decision", 1), ("invalidated_at", 1)],
        "arb_approval_artifact_decision_validity",
        False,
    ),
    (
        "arbitration_export_authorizations",
        [("draft_id", 1), ("draft_version_id", 1), ("format", 1), ("authorized_at", -1)],
        "arb_export_draft_version_format_time",
        False,
    ),
    (
        "arbitration_draft_versions",
        [("parent_version_id", 1), ("created_at", -1)],
        "arb_version_parent_time",
        False,
    ),
    (
        "arbitration_draft_versions",
        "version_hash",
        "arb_version_hash",
        False,
    ),
]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    now = datetime.now(timezone.utc)
    operations: List[Dict[str, Any]] = []
    for collection in MATRIX_COLLECTIONS:
        operations.append(
            {
                "operation": "update_many",
                "collection": collection,
                "purpose": "return rows with no approval event to needs_review",
            }
        )
    operations.extend(
        [
            {
                "operation": "update_many",
                "collection": "arbitration_cases",
                "purpose": "invalidate legacy readiness approvals with no immutable receipt",
            },
            {
                "operation": "update_many",
                "collection": "arbitration_drafts",
                "purpose": "return approved standalone drafts to review",
            },
            {
                "operation": "normalize_fields",
                "collection": "arbitration_rejoinder_matrix",
                "from": "tribunal_permission_required",
                "to": ["permission_required", "permission_obtained"],
            },
        ]
    )
    operations.extend(
        {
            "operation": "create_index",
            "collection": collection,
            "keys": keys,
            "name": name,
            "unique": unique,
        }
        for collection, keys, name, unique in INDEXES
    )

    warnings = [
        "Historical approver identities and timestamps are not fabricated. Rows without an approval event return to needs_review.",
        "The data downgrade intentionally does not restore legacy approvals; only indexes are removed on rollback.",
    ]
    if not dry_run:
        approval_query = {
            "$and": [
                {
                    "$or": [
                        {"approval_status": "approved"},
                        {"human_approval_status": "approved"},
                        {"readiness_status": "ready"},
                        {"verification_status": "verified"},
                        {"status": "ready"},
                    ]
                },
                {"approval_log": {"$not": {"$elemMatch": {"action": "approve"}}}},
            ]
        }
        review_update = {
            "$set": {
                "approval_status": "needs_review",
                "human_approval_status": "needs_review",
                "readiness_status": "needs_review",
                "verification_status": "needs_review",
                "review_status": "needs_review",
                "review_completed_roles": [],
                "approved_by": None,
                "approved_at": None,
                "migration_review_reason": "No verifiable approval event was present during Phase 0 containment migration.",
                "migration_reviewed_at": now,
            }
        }
        for collection in MATRIX_COLLECTIONS:
            await db[collection].update_many(approval_query, review_update)

        await db.arbitration_cases.update_many(
            {"readiness_approved_by": {"$ne": None}, "readiness_approval_receipt_id": None},
            {
                "$set": {
                    "status": "matrix_preparation",
                    "readiness_approved_by": None,
                    "readiness_approved_at": None,
                    "readiness_approval_receipt_id": None,
                    "readiness_matrix_revision_set_id": None,
                    "readiness_matrix_revision_hash": None,
                    "readiness_evidence_snapshot_hash": None,
                    "readiness_draft_type": None,
                    "readiness_invalidation_reason": "Legacy approval was not bound to an immutable revision receipt.",
                    "readiness_invalidated_at": now,
                }
            },
        )
        await db.arbitration_drafts.update_many(
            {"case_id": None, "status": {"$in": ["approved", "exported"]}},
            {
                "$set": {
                    "status": "under_review",
                    "is_locked": False,
                    "approved_by": None,
                    "approved_at": None,
                    "approved_version_id": None,
                    "approved_version": None,
                    "approved_version_hash": None,
                    "exported_by": None,
                    "exported_at": None,
                    "migration_review_reason": "Standalone drafts cannot be approved or filed without an exception receipt.",
                    "updated_at": now,
                }
            },
        )
        await db.arbitration_rejoinder_matrix.update_many(
            {"tribunal_permission_required": True},
            {
                "$set": {
                    "permission_required": True,
                    "permission_obtained": False,
                    "permission_source_id": None,
                    "permission_approved_by": None,
                    "permission_approved_at": None,
                    "permission_notes": "Migrated requirement only; permission must be recorded through the approval endpoint.",
                }
            },
        )
        await db.arbitration_rejoinder_matrix.update_many(
            {"tribunal_permission_required": {"$ne": True}},
            {"$set": {"permission_required": False, "permission_obtained": False}},
        )
        for collection, keys, name, unique in INDEXES:
            await db[collection].create_index(keys, name=name, unique=unique, background=True)

    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
        warnings=warnings,
    )


async def downgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = [
        {"operation": "drop_index", "collection": collection, "name": name}
        for collection, _keys, name, _unique in INDEXES
    ]
    if not dry_run:
        for collection, _keys, name, _unique in INDEXES:
            try:
                await db[collection].drop_index(name)
            except Exception:
                # Rollback remains idempotent when an index was never created or
                # was removed manually. Data is deliberately left review-safe.
                pass
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "rolled_back",
        operations=operations,
        warnings=["Review-safe data changes are intentionally retained; rollback never restores unverifiable approvals."],
    )
