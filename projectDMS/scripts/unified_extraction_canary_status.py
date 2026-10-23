"""Canary status for the unified extraction pipeline.

READ-ONLY. Issues counting queries only; writes nothing. Run before, during,
and after a canary window and paste the output into the runbook's evidence
section.

    docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
      exec -T document-worker python -m scripts.unified_extraction_canary_status
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict


async def main() -> None:
    from rbac_backend.core.config import settings
    from rbac_backend.core.database import get_database
    from rbac_backend.services.extraction.fallback.ledger import InterventionLedger
    from rbac_backend.services.extraction_adapters.document_page_store import (
        DOCUMENT_EXTRACTION_HEADS,
        DOCUMENT_OCR_PAGES,
    )
    from rbac_backend.services.pipeline_routing import (
        LEGACY_PIPELINE,
        UNIFIED_PIPELINE,
    )

    db = await get_database()
    report: Dict[str, Any] = {
        "config": {
            "UNIFIED_EXTRACTION_ENABLED": settings.UNIFIED_EXTRACTION_ENABLED,
            "canary_org_ids": sorted(settings.canary_org_id_set()),
            "EXTRACTION_FALLBACK_ENABLED": settings.EXTRACTION_FALLBACK_ENABLED,
            "EXTRACTION_FALLBACK_MAX_PAGES_PER_DOCUMENT": (
                settings.EXTRACTION_FALLBACK_MAX_PAGES_PER_DOCUMENT
            ),
            "START_DOCUMENT_EXTRACTION_WORKERS": (
                settings.START_DOCUMENT_EXTRACTION_WORKERS
            ),
            "RAR_UPLOAD_ENABLED": settings.RAR_UPLOAD_ENABLED,
        }
    }

    jobs: Dict[str, Any] = {}
    for version in (UNIFIED_PIPELINE, LEGACY_PIPELINE, None):
        key = version or "unrecorded"
        query = (
            {"pipeline_version": version}
            if version
            else {"pipeline_version": {"$exists": False}}
        )
        jobs[key] = {}
        for status in (
            "queued",
            "processing",
            "retrying",
            "completed",
            "partially_processed",
            "human_review_required",
            "stored_only",
            "failed",
            "dead_lettered",
        ):
            jobs[key][status] = await db.document_processing_jobs.count_documents(
                {**query, "status": status}
            )
    report["jobs_by_pipeline_and_status"] = jobs

    report["page_evidence"] = {
        "document_ocr_pages": await db[DOCUMENT_OCR_PAGES].count_documents({}),
        "published_extraction_heads": await db[
            DOCUMENT_EXTRACTION_HEADS
        ].count_documents({}),
        "pages_needing_review": await db[DOCUMENT_OCR_PAGES].count_documents(
            {"needs_review": True}
        ),
    }

    interventions = db[InterventionLedger.COLLECTION]
    report["fallback_interventions"] = {
        "total": await interventions.count_documents({}),
        "resolved": await interventions.count_documents({"outcome": "resolved"}),
        "escalated": await interventions.count_documents({"outcome": "escalated"}),
        "human_review_required": await interventions.count_documents(
            {"outcome": "human_review_required"}
        ),
    }

    # The acceptance number: a clean canary document must add zero of these.
    report["_go_no_go_notes"] = [
        "fallback_interventions.total must not increase while processing the "
        "clean canary fixture",
        "no organisation outside canary_org_ids may have unified_v1 jobs",
        "jobs_by_pipeline_and_status.unrecorded is pre-canary work and must "
        "stay on the legacy path",
    ]

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
