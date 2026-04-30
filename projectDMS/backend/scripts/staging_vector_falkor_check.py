#!/usr/bin/env python
"""
Validate that a processed document is fully indexed in Mongo, Qdrant, and Falkor.

This helper is intended to run inside the backend container (or any environment
with the project dependencies installed). It will optionally re-run the async
document processing pipeline, wait for `vector_sync_status` to report `synced`,
and then confirm that the corresponding Falkor node exists.

Example usage (run inside the backend container):

    python backend/scripts/staging_vector_falkor_check.py --document-id 664d2... \
        --reprocess --timeout 300
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from typing import Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

# Ensure package imports resolve when executed from repo root.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.core.config import settings
from rbac_backend.models.document import Document
from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.falkor_graph_service import (
    FalkorGraphService,
    FalkorGraphError,
    normalize_letter_code,
)

TERMINAL_VECTOR_STATUSES = {
    "synced",
    "mongo_only",
    "mismatch",
    "disabled",
    "empty",
    "error",
}


async def _wait_for_vector_sync(
    service: DocumentService,
    document_id: str,
    *,
    poll_interval: float,
    timeout: float,
) -> Optional[dict[str, Any]]:
    """Poll the vector_sync_status collection until a terminal state is reached."""
    db = await service._get_db()  # noqa: SLF001 - internal helper is acceptable in script context.
    deadline = time.monotonic() + timeout
    last_status: Optional[dict[str, Any]] = None

    while time.monotonic() < deadline:
        status = await db.vector_sync_status.find_one({"document_id": document_id})
        if status:
            last_status = status
            sync_status = (status.get("sync_status") or "").lower()
            if sync_status in TERMINAL_VECTOR_STATUSES:
                return status
        await asyncio.sleep(poll_interval)
    return last_status


def _count_qdrant_chunks(config: DocumentProcessingConfig, document_id: str) -> Optional[int]:
    """Return the number of Qdrant vectors for the document, or None if disabled."""
    if not config.qdrant_enabled:
        return None

    client = QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key)
    response = client.count(
        collection_name=config.qdrant_collection,
        count_filter=Filter(
            must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
        ),
    )
    return response.count


def _lookup_falkor_letter(norm_code: str) -> Optional[dict[str, Any]]:
    """Fetch the Falkor letter node using a zero depth thread lookup."""
    service = FalkorGraphService()
    try:
        entry = service.get_letter(norm_code)
    except FalkorGraphError as exc:
        raise RuntimeError(f"Falkor query failed for {norm_code}: {exc}") from exc

    return entry


async def _maybe_reprocess_document(
    service: DocumentService,
    document: Document,
    *,
    skip_processing: bool,
) -> None:
    """Run process_document_async when requested."""
    if skip_processing:
        return

    file_path = getattr(document, "filepath_local", None)
    if not file_path:
        raise RuntimeError("Document has no filepath_local; cannot reprocess automatically.")
    if not os.path.exists(file_path):
        raise RuntimeError(f"Document file path does not exist on disk: {file_path}")

    await service.process_document_async(
        document_id=document.id,
        file_path=file_path,
        organization_id=document.organization_id,
        project_id=document.project_id,
        upload_type=document.uploadType,
    )


async def main(args: argparse.Namespace) -> int:
    config = DocumentProcessingConfig()
    if not config.qdrant_enabled:
        raise RuntimeError("Qdrant dual-write is disabled; set VECTOR_DUAL_WRITE_ENABLED=true.")
    if not settings.FALKORDB_ENABLED:
        raise RuntimeError("FalkorDB integration is disabled; export FALKORDB_ENABLED=true.")

    service = DocumentService()
    document = await service.get_document(args.document_id)
    if not document:
        raise RuntimeError(f"Document {args.document_id} was not found in MongoDB.")
    document_id = str(document.id)

    await _maybe_reprocess_document(
        service,
        document,
        skip_processing=not args.reprocess,
    )

    vector_status = await _wait_for_vector_sync(
        service,
        document_id,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
    )
    if not vector_status:
        raise RuntimeError("No vector sync status record was produced within the timeout window.")

    sync_state = (vector_status.get("sync_status") or "").lower()
    if sync_state != "synced":
        raise RuntimeError(f"Vector sync did not converge to 'synced' (latest state: {sync_state}).")

    qdrant_count = _count_qdrant_chunks(config, document_id)
    falkor_payload = _lookup_falkor_letter(
        normalize_letter_code(document.letterNo or document.letterNoNormalized or document_id)
    )
    if not falkor_payload:
        raise RuntimeError("Falkor did not return a node for the document's letter code.")

    print("[OK] Vector sync status:", sync_state)
    print(f"[OK] Qdrant chunk count: {qdrant_count if qdrant_count is not None else 'N/A'}")
    print(f"[OK] Falkor node normCode: {falkor_payload.get('l.normCode')}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--document-id",
        required=True,
        help="MongoDB document _id to validate (24-hex ObjectId).",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Trigger the async OCR/metadata pipeline before validation.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Seconds to wait for vector sync to reach a terminal state (default: 180).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Seconds between vector_sync_status polls (default: 5).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    arguments = parse_args(sys.argv[1:])
    try:
        exit_code = asyncio.run(main(arguments))
    except Exception as exc:  # noqa: BLE001 - surface rich error for operators.
        print(f"[FAIL] Validation failed: {exc}")
        exit_code = 1
    sys.exit(exit_code)
