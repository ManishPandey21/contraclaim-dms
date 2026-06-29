from __future__ import annotations

from typing import Any

from ..core.database import ensure_indexes
from .runner import MigrationResult


VERSION = "20260629_0001"
NAME = "startup_index_baseline"
DESCRIPTION = "Run and record the current MongoDB startup index catalog as a production schema baseline."


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations = [
        {
            "operation": "create_index",
            "collection": "schema_migrations",
            "keys": "version",
            "unique": True,
            "note": "The migration runner creates this ledger index before apply.",
        },
        {
            "operation": "ensure_indexes",
            "source": "rbac_backend.core.database.ensure_indexes",
            "collections": [
                "documents",
                "file_objects",
                "contracts",
                "letters",
                "letter_draft_runs",
                "arbitration_drafts",
                "matter_chronologies",
                "project_events",
                "event_links",
                "ai_extractions",
                "drawing_references",
                "delay_events",
                "programme_milestones",
                "contract_appraisal_jobs",
                "document_share_tokens",
                "billing_webhook_events",
                "contract_ingest_jobs",
                "document_vectors",
                "chunks",
            ],
        },
    ]
    if not dry_run:
        await ensure_indexes(db)
    return MigrationResult(version=VERSION, name=NAME, status="dry_run" if dry_run else "applied", operations=operations)
