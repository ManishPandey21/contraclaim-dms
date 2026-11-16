"""M-05 (plan 4B-36, D-18, C-10): one matrix row per (case, origin).

Escalation carries a claim's heads, notice position, cost lines, dates and
documents into a case's matrices. Each row it seeds carries ``origin_key``
("claim:<claim id>:<part>"). It looked for an existing row and then inserted,
with no index behind the check, so two escalations of the same claim into the
same case at the same moment both inserted. This migration adds a partial
unique index on (``case_id``, ``origin_key``), where ``origin_key`` is a string,
to every matrix collection; escalation then treats a duplicate-key refusal as
"already carried".

Rows added by hand carry no ``origin_key`` and are not covered.

A collection that already holds duplicates is reported as a warning (a property
of this database) and its index is not created: the duplicates must be resolved
by a person first. Nothing is deleted or merged here.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..services.arbitration_drafting.matrix_registry import MATRIX_COLLECTIONS
from .runner import MigrationResult
from .v20261114_0001_skill_registry import apply_indexes

VERSION = "20261120_0001"
NAME = "arbitration_matrix_origin_unique"
DESCRIPTION = "One arbitration matrix row per case and escalation origin (partial unique index on case_id, origin_key)."
INDEX_NAME = "matrix_case_origin_unique"


def index_for(collection: str) -> Dict[str, Any]:
    return {
        "collection": collection,
        "keys": [("case_id", 1), ("origin_key", 1)],
        "name": INDEX_NAME,
        "unique": True,
        "partialFilterExpression": {"origin_key": {"$type": "string"}},
    }


INDEXES = [index_for(collection) for collection in sorted(set(MATRIX_COLLECTIONS.values()))]


async def duplicate_groups(db: Any, collection: str) -> int:
    """How many (case, origin) pairs already hold more than one row."""
    rows = await db[collection].aggregate(
        [
            {"$match": {"origin_key": {"$type": "string"}}},
            {"$group": {"_id": {"case_id": "$case_id", "origin_key": "$origin_key"}, "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}},
            {"$count": "groups"},
        ]
    ).to_list(length=1)
    return int(rows[0]["groups"]) if rows else 0


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    warnings: List[str] = []
    clean: List[Dict[str, Any]] = []
    for index in INDEXES:
        groups = await duplicate_groups(db, index["collection"])
        if groups:
            warnings.append(
                f"{index['collection']}: {groups} (case, origin) pair(s) hold more than one row; "
                f"index {INDEX_NAME} is not created until they are resolved"
            )
        else:
            clean.append(index)
    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=await apply_indexes(db, clean, dry_run),
        warnings=warnings,
        notices=[
            "Only rows carrying an escalation origin are covered; rows added by hand are not.",
            "No row is deleted or merged. A collection with duplicate (case, origin) rows keeps no new index.",
        ],
    )
