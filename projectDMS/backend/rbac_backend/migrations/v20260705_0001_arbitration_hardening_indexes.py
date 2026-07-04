from __future__ import annotations

from typing import Any, Dict, List

from .runner import MigrationResult


VERSION = "20260705_0001"
NAME = "arbitration_hardening_indexes"
DESCRIPTION = (
    "Create production indexes for arbitration background agent runs, filing "
    "bundle export jobs, readiness trend lookup, and matrix review status scans."
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
]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations: List[Dict[str, Any]] = [
        {
            "operation": "create_index",
            "collection": "arbitration_cases",
            "keys": [("status", 1), ("readiness_score", 1), ("updated_at", -1)],
        },
        {
            "operation": "create_index",
            "collection": "arbitration_readiness_checks",
            "keys": [("case_id", 1), ("status", 1), ("updated_at", -1)],
        },
        {
            "operation": "create_index",
            "collection": "arbitration_agent_runs",
            "keys": [("case_id", 1), ("status", 1), ("created_at", -1)],
        },
        {"operation": "create_index", "collection": "arbitration_agent_runs", "keys": "background_job_id"},
        {
            "operation": "create_index",
            "collection": "arbitration_bundle_exports",
            "keys": [("case_id", 1), ("status", 1), ("created_at", -1)],
        },
        {
            "operation": "create_index",
            "collection": "arbitration_bundle_exports",
            "keys": [("case_id", 1), ("format", 1), ("created_at", -1)],
        },
        {"operation": "create_index", "collection": "arbitration_bundle_exports", "keys": "background_job_id"},
        {"operation": "create_index", "collection": "arbitration_bundle_exports", "keys": "expires_at"},
    ]
    operations.extend(
        {
            "operation": "create_index",
            "collection": collection,
            "keys": [("case_id", 1), ("readiness_status", 1), ("approval_status", 1), ("updated_at", -1)],
        }
        for collection in MATRIX_COLLECTIONS
    )

    if not dry_run:
        await db.arbitration_cases.create_index(
            [("status", 1), ("readiness_score", 1), ("updated_at", -1)],
            background=True,
        )
        await db.arbitration_readiness_checks.create_index(
            [("case_id", 1), ("status", 1), ("updated_at", -1)],
            background=True,
        )
        await db.arbitration_agent_runs.create_index(
            [("case_id", 1), ("status", 1), ("created_at", -1)],
            background=True,
        )
        await db.arbitration_agent_runs.create_index("background_job_id", background=True)
        await db.arbitration_bundle_exports.create_index(
            [("case_id", 1), ("status", 1), ("created_at", -1)],
            background=True,
        )
        await db.arbitration_bundle_exports.create_index(
            [("case_id", 1), ("format", 1), ("created_at", -1)],
            background=True,
        )
        await db.arbitration_bundle_exports.create_index("background_job_id", background=True)
        await db.arbitration_bundle_exports.create_index("expires_at", background=True)
        for collection in MATRIX_COLLECTIONS:
            await db[collection].create_index(
                [("case_id", 1), ("readiness_status", 1), ("approval_status", 1), ("updated_at", -1)],
                background=True,
            )

    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
    )
