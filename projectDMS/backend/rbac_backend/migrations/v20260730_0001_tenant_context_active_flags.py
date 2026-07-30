"""Back the Active Organisation / Active Project context with real state.

``projects`` documents carried no status field at all and ``organizations``
carried ``is_active`` on only some rows, so "only active projects" and the
project-deactivated state had nothing to read. This backfills an explicit
``is_active: true`` on existing rows and indexes the lookups the tenant context
resolver performs on every scoped request.

Absence of the flag is still treated as active by the resolver, so this
migration is additive and cannot lock anyone out of existing data.
"""

from __future__ import annotations

from typing import Any

from .runner import MigrationResult


VERSION = "20260730_0001"
NAME = "tenant_context_active_flags"
DESCRIPTION = "Backfill is_active on organizations/projects and index tenant context lookups."

INDEXES = [
    ("projects", [("organization_id", 1), ("is_active", 1)], "project_org_active"),
    ("organizations", [("is_active", 1)], "organization_active"),
    ("organization_memberships", [("user_id", 1), ("status", 1)], "org_membership_user_status"),
    ("project_memberships", [("user_id", 1), ("status", 1)], "project_membership_user_status"),
]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = []

    for collection in ("organizations", "projects"):
        missing = await db[collection].count_documents({"is_active": {"$exists": False}})
        operations.append(
            {
                "operation": "backfill_is_active",
                "collection": collection,
                "documents": missing,
                "value": True,
            }
        )
        if not dry_run and missing:
            await db[collection].update_many(
                {"is_active": {"$exists": False}}, {"$set": {"is_active": True}}
            )

    for collection, keys, name in INDEXES:
        operations.append(
            {"operation": "create_index", "collection": collection, "keys": keys, "name": name}
        )
        if not dry_run:
            await db[collection].create_index(keys, name=name, background=True)

    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
    )


async def downgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = []
    for collection, _keys, name in INDEXES:
        operations.append({"operation": "drop_index", "collection": collection, "name": name})
        if not dry_run:
            try:
                await db[collection].drop_index(name)
            except Exception:
                pass

    # The is_active backfill is deliberately not reverted: removing the flag
    # would be indistinguishable from a genuine deactivation being lost.
    operations.append({"operation": "retain_is_active_backfill", "reason": "not safely reversible"})

    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "rolled_back",
        operations=operations,
    )
