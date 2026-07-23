from __future__ import annotations

from typing import Any, Dict, List

from .runner import MigrationResult
from .v20260721_0002_arbitration_workflow_foundation import ensure_compatible_index


VERSION = "20260723_0001"
NAME = "arbitration_effect_recovery"
DESCRIPTION = "Index recoverable arbitration effects, historical paragraph review, and server-backed acceptance evidence."

INDEXES = [
    (
        "arbitration_workflow_effects",
        [("status", 1), ("lease_expires_at", 1)],
        "arb_wf_effect_lease_recovery",
        False,
        {},
    ),
    (
        "arbitration_defence_matrix",
        [("historical_review_status", 1), ("case_id", 1), ("draft_id", 1)],
        "arb_defence_historical_review",
        False,
        {"historical_review_status": {"$type": "string"}},
    ),
    (
        "arbitration_rejoinder_matrix",
        [("historical_review_status", 1), ("case_id", 1), ("draft_id", 1)],
        "arb_rejoinder_historical_review",
        False,
        {"historical_review_status": {"$type": "string"}},
    ),
    (
        "arbitration_acceptance_evidence",
        [
            ("organization_id", 1),
            ("project_id", 1),
            ("criterion", 1),
            ("evidence_hash", 1),
        ],
        "arb_acceptance_evidence_scope_criterion_hash",
        True,
        {"invalidated_at": {"$exists": False}},
    ),
    (
        "arbitration_acceptance_signoffs",
        [
            ("organization_id", 1),
            ("project_id", 1),
            ("acceptance_bundle_hash", 1),
            ("actor_id", 1),
        ],
        "arb_acceptance_signoff_scope_bundle_actor",
        True,
        {"invalidated_at": {"$exists": False}},
    ),
]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations: List[Dict[str, Any]] = []
    for collection, keys, name, unique, partial in INDEXES:
        operation: Dict[str, Any] = {
            "operation": "create_index",
            "collection": collection,
            "keys": keys,
            "name": name,
            "unique": unique,
        }
        if partial:
            operation["partial_filter"] = partial
        operations.append(operation)
        if dry_run:
            continue
        kwargs: Dict[str, Any] = {
            "name": name,
            "unique": unique,
            "background": True,
        }
        if partial:
            kwargs["partialFilterExpression"] = partial
        await ensure_compatible_index(db[collection], keys, **kwargs)
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
    )


async def downgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = [
        {"operation": "drop_index", "collection": collection, "name": name}
        for collection, _keys, name, _unique, _partial in INDEXES
    ]
    if not dry_run:
        for collection, _keys, name, _unique, _partial in INDEXES:
            try:
                await db[collection].drop_index(name)
            except Exception:
                pass
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "rolled_back",
        operations=operations,
    )
