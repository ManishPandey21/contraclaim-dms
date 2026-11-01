from __future__ import annotations

from typing import Any

from .runner import MigrationResult


VERSION = "20260814_0001"
NAME = "document_extraction_indexes"
DESCRIPTION = (
    "Index the page-extraction collections: the unique run-scoped page "
    "constraint that stops two workers writing contradictory evidence for the "
    "same page, the unique visible-run pointer per document, batch lookup by "
    "run, and tenant-scoped page reads."
)

PAGES = "document_ocr_pages"
BATCHES = "document_ocr_batches"
HEADS = "document_extraction_heads"


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = [
        {
            "operation": "create_index",
            "collection": PAGES,
            "keys": [("document_id", 1), ("extraction_run_id", 1), ("page_number", 1)],
            "unique": True,
            "note": (
                "one row per page per extraction run; DocumentPageStore upserts "
                "on this triple and two concurrent reclaims must not both insert"
            ),
        },
        {
            "operation": "create_index",
            "collection": PAGES,
            "keys": [("document_id", 1), ("extraction_run_id", 1), ("status", 1)],
            "note": "resume path: find the pages a run still owes",
        },
        {
            "operation": "create_index",
            "collection": PAGES,
            "keys": [("organization_id", 1), ("project_id", 1)],
            "note": "tenant-scoped reads",
        },
        {
            "operation": "create_index",
            "collection": BATCHES,
            "keys": [("document_id", 1), ("extraction_run_id", 1)],
        },
        {
            "operation": "create_index",
            "collection": HEADS,
            "keys": "document_id",
            "unique": True,
            "note": "exactly one visible run pointer per document",
        },
    ]

    if not dry_run:
        await db[PAGES].create_index(
            [("document_id", 1), ("extraction_run_id", 1), ("page_number", 1)],
            unique=True,
            background=True,
        )
        await db[PAGES].create_index(
            [("document_id", 1), ("extraction_run_id", 1), ("status", 1)],
            background=True,
        )
        await db[PAGES].create_index(
            [("organization_id", 1), ("project_id", 1)], background=True
        )
        await db[BATCHES].create_index(
            [("document_id", 1), ("extraction_run_id", 1)], background=True
        )
        await db[HEADS].create_index("document_id", unique=True, background=True)

    return MigrationResult(
        version=VERSION,
        name=NAME,
        status="dry_run" if dry_run else "applied",
        operations=operations,
    )
