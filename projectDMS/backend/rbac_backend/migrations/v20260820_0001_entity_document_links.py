from __future__ import annotations

from typing import Any

from .runner import MigrationResult


VERSION = "20260820_0001"
NAME = "entity_document_links"
DESCRIPTION = (
    "Create the canonical entity/event-to-Document relationship indexes, "
    "including the partial unique constraint for active role links and "
    "forward, reverse, parent-context, and history traversal indexes."
)

COLLECTION = "entity_document_links"

INDEXES = [
    {
        "keys": [
            ("organization_id", 1),
            ("project_id", 1),
            ("target_type", 1),
            ("target_id", 1),
            ("document_id", 1),
            ("relationship_role", 1),
        ],
        "name": "uq_entity_document_links_active",
        "unique": True,
        "partialFilterExpression": {"removed_at": None},
    },
    {
        "keys": [
            ("organization_id", 1),
            ("project_id", 1),
            ("target_type", 1),
            ("target_id", 1),
            ("removed_at", 1),
        ],
        "name": "ix_entity_document_links_forward",
    },
    {
        "keys": [
            ("organization_id", 1),
            ("project_id", 1),
            ("document_id", 1),
            ("removed_at", 1),
        ],
        "name": "ix_entity_document_links_reverse",
    },
    {
        "keys": [("parent_type", 1), ("parent_id", 1), ("removed_at", 1)],
        "name": "ix_entity_document_links_parent",
    },
    {
        "keys": [("target_type", 1), ("target_id", 1), ("created_at", -1)],
        "name": "ix_entity_document_links_target_history",
    },
    {
        "keys": [("document_id", 1), ("created_at", -1)],
        "name": "ix_entity_document_links_document_history",
    },
]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = [
        {
            "operation": "create_index",
            "collection": COLLECTION,
            **index,
        }
        for index in INDEXES
    ]

    if not dry_run:
        for index in INDEXES:
            options = {key: value for key, value in index.items() if key != "keys"}
            await db[COLLECTION].create_index(
                index["keys"], background=True, **options
            )

    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
    )
