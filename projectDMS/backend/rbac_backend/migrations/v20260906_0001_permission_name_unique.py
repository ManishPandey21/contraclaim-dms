"""Make `permissions.name` unique, collapsing any duplicates first.

R-A8I's Gate 8 restore drill produced 396 permission rows for 198 distinct names.
The backend restarted before the Mongo restore had run, re-seeded the catalogue,
and the restore then inserted the dumped rows on top. `db.permissions` carried
only its `_id_` index, so nothing collided: the only trace was
`1 document failed to restore` in the restore log.

`name` is already the identity everywhere that matters -
`PermissionService.get_permission_by_name` reads `find_one({"name": name})`, and
role documents grant permissions by name - so the constraint is not a new rule,
it is the existing rule written down where the database can enforce it.

Two things this migration has to get right.

**Order.** A unique index does not build over a collection that already contains
duplicates; it fails. So the duplicates are collapsed first, and the index
creation that follows is then the assertion that the collapse worked.

**Determinism.** Which of two rows survives decides which `_id` and which
description the catalogue keeps, and "whichever the engine returned first" is the
same order-dependence `publication_policy.graph_codes_denied` documents one layer
down. The survivor is the earliest `created_at`, tie-broken by `_id` as a string -
a rule that gives the same answer on every replica and on every re-run.

The removals are reported as individual operations rather than a count, because a
silent de-duplication is a silent data deletion and the dry run is the only look
an operator gets before it happens.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from .runner import MigrationResult

VERSION = "20260906_0001"
NAME = "permission_name_unique"
DESCRIPTION = (
    "Collapse duplicate permission rows by name and add the unique "
    "`uq_permissions_name` index, so a restore over a live catalogue can no "
    "longer double it silently."
)

COLLECTION = "permissions"
INDEX_NAME = "uq_permissions_name"

#: Sorts after every real timestamp, so a row with no `created_at` never wins a
#: tie against one that has it. A missing timestamp is the weaker claim to being
#: the original.
_NO_TIMESTAMP = datetime.max


def _sort_key(document: Dict[str, Any]):
    created = document.get("created_at")
    if not isinstance(created, datetime):
        created = _NO_TIMESTAMP
    return (created, str(document.get("_id")))


def _duplicate_groups(documents: List[Dict[str, Any]]) -> Dict[Any, List[Dict[str, Any]]]:
    buckets: Dict[Any, List[Dict[str, Any]]] = {}
    for document in documents:
        buckets.setdefault(document.get("name"), []).append(document)
    return {name: rows for name, rows in buckets.items() if len(rows) > 1}


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    documents = await db[COLLECTION].find({}).to_list(None)
    groups = _duplicate_groups(documents)

    operations: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for name in sorted(groups, key=lambda value: str(value)):
        rows = sorted(groups[name], key=_sort_key)
        keep, drop = rows[0], rows[1:]
        operations.append(
            {
                "operation": "delete_duplicate",
                "collection": COLLECTION,
                "name": name,
                "kept_id": str(keep.get("_id")),
                "removed_ids": [str(row.get("_id")) for row in drop],
                "removed": len(drop),
            }
        )
        warnings.append(
            f"{len(drop)} duplicate permission row(s) for name {name!r} will be removed; "
            f"keeping _id {str(keep.get('_id'))!r}"
        )
        if not dry_run:
            for row in drop:
                await db[COLLECTION].delete_one({"_id": row.get("_id")})

    operations.append(
        {
            "operation": "create_index",
            "collection": COLLECTION,
            "keys": [("name", 1)],
            "name": INDEX_NAME,
            "unique": True,
        }
    )

    if not dry_run:
        # Deliberately unguarded: if this raises, duplicates survived the collapse
        # and the constraint is absent. A migration that swallowed that would
        # report success while leaving the collection exactly as R-A8I found it.
        await db[COLLECTION].create_index(
            [("name", 1)], name=INDEX_NAME, unique=True, background=True
        )

    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
        warnings=warnings,
    )
