"""
Backfill utility to push existing documents and references into FalkorDB.

Usage:
    python scripts/backfill_falkordb.py --limit 500
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Optional

from backend.rbac_backend.core.database import get_database
from backend.rbac_backend.services.document_service import DocumentService
from backend.rbac_backend.graph.graph_ingestion_service import GraphIngestionService
from backend.rbac_backend.services.falkor_graph_service import FalkorGraphService, normalize_letter_code


async def backfill(
    limit: Optional[int] = None,
    *,
    only_missing: bool = False,
    batch_size: int = 50,
) -> None:
    """Sync existing documents to FalkorDB with optional missing-only filtering."""
    falkor = FalkorGraphService()
    if not falkor.enabled:
        logging.warning("FalkorDB integration is disabled; set FALKORDB_ENABLED=true to run backfill.")
        return

    db = await get_database()
    document_service = DocumentService(db)
    document_service.graph_ingestion = GraphIngestionService(falkor=falkor)

    cursor = db.documents.find({})
    if limit:
        cursor = cursor.limit(int(limit))

    processed_total = 0
    skipped_total = 0
    failures_total = 0

    batch = []
    async for raw_doc in cursor:
        batch.append(raw_doc)
        if len(batch) >= max(batch_size, 1):
            processed, skipped, failures = await _process_batch(document_service, batch, only_missing)
            processed_total += processed
            skipped_total += skipped
            failures_total += failures
            batch.clear()

    if batch:
        processed, skipped, failures = await _process_batch(document_service, batch, only_missing)
        processed_total += processed
        skipped_total += skipped
        failures_total += failures

    logging.info(
        "FalkorDB backfill completed. processed=%s skipped=%s failures=%s",
        processed_total,
        skipped_total,
        failures_total,
    )


async def _process_batch(
    document_service: DocumentService,
    batch: list[dict],
    only_missing: bool,
) -> tuple[int, int, int]:
    processed = 0
    skipped = 0
    failures = 0

    for raw_doc in batch:
        document_id = str(raw_doc.get("_id"))
        try:
            if only_missing:
                letter_code = raw_doc.get("letterNo")
                norm_code = normalize_letter_code(str(letter_code or ""))
                if norm_code:
                    existing = document_service.graph_ingestion.falkor.get_thread(norm_code, depth=0)
                    if existing:
                        skipped += 1
                        logging.debug(
                            "Skipping document %s (letter=%s); already present in FalkorDB",
                            document_id,
                            letter_code,
                        )
                        continue

            upload_type = raw_doc.get("uploadType") if isinstance(raw_doc, dict) else None
            document_service.graph_ingestion.sync_document_to_falkor(
                document_id=document_id,
                document=raw_doc,
                metadata=None,
                upload_type=upload_type,
            )
            processed += 1
        except Exception as exc:  # pragma: no cover - diagnostics
            failures += 1
            logging.exception("Failed to backfill document %s: %s", document_id, exc)

    return processed, skipped, failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill FalkorDB letter graph.")
    parser.add_argument("--limit", type=int, default=None, help="Optional max number of documents to sync.")
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Sync only documents that are not already present in FalkorDB.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of documents to process per batch.",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    args = parse_args()
    asyncio.run(
        backfill(
            args.limit,
            only_missing=args.only_missing,
            batch_size=args.batch_size,
        )
    )


if __name__ == "__main__":
    main()
