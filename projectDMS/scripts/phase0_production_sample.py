"""Phase 0 production sampling: how much did the 5-page heuristic cost us?

The general upload path decides OCR once per document: if ANY of the first five
pages yields text, OCR is skipped for the whole file. This script bounds how
many stored documents may therefore be missing pages, which decides whether a
backfill is a footnote or a project.

READ-ONLY. Issues count queries only; writes nothing.

Run inside the backend container:

    docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml \
      exec -T backend python -m scripts.phase0_production_sample
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict


async def main() -> None:
    from rbac_backend.core.database import get_database

    db = await get_database()

    report: Dict[str, Any] = {}

    report["documents_total"] = await db.documents.count_documents({})
    report["pdf_documents"] = await db.documents.count_documents(
        {"filetype": "application/pdf"}
    )
    report["completed"] = await db.documents.count_documents(
        {"processing_status": "completed"}
    )
    report["ocr_enabled"] = await db.documents.count_documents({"ocrEnabled": True})
    report["with_ocr_text"] = await db.documents.count_documents(
        {"filetype": "application/pdf", "ocrText": {"$exists": True, "$ne": ""}}
    )
    report["completed_but_no_text"] = await db.documents.count_documents(
        {
            "filetype": "application/pdf",
            "processing_status": "completed",
            "$or": [
                {"ocrText": {"$exists": False}},
                {"ocrText": ""},
                {"ocrText": None},
            ],
        }
    )

    # Page counts are not persisted before Phase 3, so bytes-per-extracted-char
    # is the only available proxy for "large file, suspiciously little text".
    report["suspicious_large_file_tiny_text"] = await db.documents.count_documents(
        {
            "filetype": "application/pdf",
            "processing_status": "completed",
            "$expr": {
                "$and": [
                    {"$gt": ["$filesize", 200000]},
                    {"$lt": [{"$strLenCP": {"$ifNull": ["$ocrText", ""]}}, 500]},
                ]
            },
        }
    )

    report["_notes"] = [
        "suspicious_large_file_tiny_text is a PROXY, not a page-level count: "
        "page counts are not persisted before Phase 3. It bounds the backfill "
        "question, it does not answer it.",
        "completed_but_no_text is the sharper signal: a document marked "
        "completed with no extracted text at all.",
    ]

    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
