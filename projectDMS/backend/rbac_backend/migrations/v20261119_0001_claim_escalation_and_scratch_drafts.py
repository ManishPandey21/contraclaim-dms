from __future__ import annotations

from typing import Any, Dict, List

from .runner import MigrationResult
from .v20261114_0001_skill_registry import apply_indexes


VERSION = "20261119_0001"
NAME = "claim_escalation_and_scratch_drafts"
DESCRIPTION = (
    "Index the link between claims and arbitration cases, and mark pleading drafts that have "
    "no case workspace as scratch drafts."
)

INDEXES = [
    {
        # "Which claims were escalated into this case."
        "collection": "claims",
        "keys": [("organization_id", 1), ("arbitration_case_id", 1)],
        "name": "claim_arbitration_case",
        "unique": False,
        "partialFilterExpression": {"arbitration_case_id": {"$type": "string"}},
    },
    {
        "collection": "arbitration_cases",
        "keys": [("organization_id", 1), ("claim_ids", 1)],
        "name": "arbitration_case_claim_ids",
        "unique": False,
    },
]

# A draft with no case. It could already be neither approved nor exported for
# filing; the flag only names what it is.
CASELESS = {"case_id": {"$in": [None, ""]}, "is_scratch": {"$ne": True}}


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations: List[Dict[str, Any]] = list(await apply_indexes(db, INDEXES, dry_run))
    matched = await db.arbitration_drafts.count_documents(CASELESS)
    operations.append(
        {"operation": "update_many", "collection": "arbitration_drafts", "set": {"is_scratch": True}, "matched": matched}
    )
    if not dry_run and matched:
        await db.arbitration_drafts.update_many(CASELESS, {"$set": {"is_scratch": True}})
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
        notices=[
            "Pleading drafts with no case workspace are marked as scratch drafts. None is deleted, "
            "and none changes status: such a draft could already be neither approved nor exported for filing.",
            "No claim is linked to a case and no case is given claims by this migration.",
        ],
    )
