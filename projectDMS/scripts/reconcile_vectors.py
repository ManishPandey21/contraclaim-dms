"""
Vector storage reconciliation utility.

Usage examples:
    python scripts/reconcile_vectors.py --limit 100
    python scripts/reconcile_vectors.py --document-id 6512c... --repair
    python scripts/reconcile_vectors.py --dry-run --repair --limit 10
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Tuple

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels
except ImportError:  # pragma: no cover - optional runtime dependency
    QdrantClient = None  # type: ignore[assignment]
    qmodels = None  # type: ignore[assignment]

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.rbac_backend.config.document_processing_config import DocumentProcessingConfig
from backend.rbac_backend.retrieval.correspondence_payload import (
    CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION,
    PAYLOAD_SCHEMA_VERSION_FIELD,
    CorrespondencePayloadError,
    build_correspondence_chunks,
    rows_need_reprocess,
)
from backend.rbac_backend.services.publication_policy import (
    is_consumable,
    resolve_canonical_document,
)
from backend.rbac_backend.core.database import get_database
from backend.rbac_backend.services.langchain_vector_service import LangChainVectorService

logger = logging.getLogger(__name__)
sync_logger = logging.getLogger("storage.sync")


async def _fetch_candidate_ids(db, limit: Optional[int]) -> List[str]:
    cursor = db.documents.find({}, {"_id": 1}).sort("updatedAt", -1)
    if limit:
        cursor = cursor.limit(limit)
        documents = await cursor.to_list(length=limit)
    else:
        documents = await cursor.to_list(length=None)
    return [str(doc["_id"]) for doc in documents if doc.get("_id") is not None]


async def _fetch_qdrant_count(
    client: Optional[QdrantClient],
    collection: str,
    document_id: str,
) -> Optional[int]:
    if client is None or qmodels is None:
        return None

    def _count() -> Optional[int]:
        try:
            response = client.count(
                collection_name=collection,
                count_filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="document_id",
                            match=qmodels.MatchValue(value=document_id),
                        )
                    ]
                ),
                exact=True,
            )
            return getattr(response, "count", None)
        except Exception as exc:  # pragma: no cover - diagnostics only
            logger.warning("Qdrant count failed for %s: %s", document_id, exc)
            return None

    return await asyncio.to_thread(_count)


async def _repair_document(
    db,
    langchain_service: LangChainVectorService,
    document_id: str,
) -> Optional[int]:
    vector_docs = await db.document_vectors.find({"document_id": document_id}).to_list(length=None)
    if not vector_docs:
        logger.info("No Mongo vector chunks available to repair document %s", document_id)
        return None

    # Authority comes from the canonical document row, never from the stored
    # vector rows; the payload from the one correspondence builder (DI-B1).
    document = await resolve_canonical_document(db, document_id)
    if not is_consumable(document):
        logger.info("Skipped repair for %s; document is not consumable", document_id)
        return None
    vector_docs.sort(key=lambda row: str(row.get("chunk_index") or 0).zfill(12))
    texts = [
        chunk["text"]
        for chunk in vector_docs
        if isinstance(chunk.get("text"), str) and chunk["text"].strip()
    ]
    written_canonically = all(
        row.get(PAYLOAD_SCHEMA_VERSION_FIELD) == CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION
        for row in vector_docs
    )
    if not written_canonically and rows_need_reprocess(document, texts):
        logger.warning(
            "Skipped repair for %s; stored rows carry extraction-report reply advice "
            "(reprocess the document instead)",
            document_id,
        )
        return None
    try:
        payloads = build_correspondence_chunks(
            document,
            texts,
            embedding_model=langchain_service.config.openai_embedding_model,
        )
    except CorrespondencePayloadError as exc:
        logger.warning("Skipped repair for %s; %s", document_id, exc)
        return None

    if not payloads:
        logger.info("Skipped repair for %s; no valid payloads derived from Mongo", document_id)
        return None

    logger.info("Re-uploading %s chunks to Qdrant for %s", len(payloads), document_id)
    sync_logger.info(
        "vector.sync.repair_start document_id=%s chunks=%s",
        document_id,
        len(payloads),
    )
    repaired = await langchain_service.replace_document(payloads)
    sync_logger.info(
        "vector.sync.repair_complete document_id=%s chunks=%s",
        document_id,
        repaired,
    )
    return repaired


async def _update_vector_status(
    db,
    document_id: str,
    *,
    status: str,
    mongo_chunks: int,
    qdrant_chunks: Optional[int],
    details: Optional[str],
    dry_run: bool,
) -> None:
    payload = {
        "document_id": document_id,
        "sync_status": status,
        "mongo_chunks": int(mongo_chunks),
        "updatedAt": datetime.utcnow(),
    }
    if qdrant_chunks is not None:
        payload["qdrant_chunks"] = int(qdrant_chunks)
    if details:
        payload["details"] = details

    if dry_run:
        return

    await db.vector_sync_status.update_one(
        {"document_id": document_id},
        {
            "$set": payload,
            "$setOnInsert": {"createdAt": datetime.utcnow()},
        },
        upsert=True,
    )


async def reconcile(args: argparse.Namespace) -> None:
    db = await get_database()
    config = DocumentProcessingConfig()

    logging.getLogger("storage.sync").setLevel(logging.INFO)
    logger.info("Starting vector reconciliation (repair=%s, dry_run=%s)", args.repair, args.dry_run)

    if args.document_id:
        document_ids = [doc_id.strip() for doc_id in args.document_id if doc_id.strip()]
    else:
        document_ids = await _fetch_candidate_ids(db, args.limit)

    if not document_ids:
        logger.info("No documents found for reconciliation.")
        return

    qclient: Optional[QdrantClient] = None
    if QdrantClient and config.qdrant_url:
        try:
            qclient = QdrantClient(
                url=config.qdrant_url,
                api_key=config.qdrant_api_key,
                timeout=10.0,
            )
        except Exception as exc:  # pragma: no cover - network diagnostics
            logger.warning("Unable to initialise Qdrant client: %s", exc)
            qclient = None
    elif config.qdrant_enabled:
        logger.warning("Qdrant client unavailable; results will not include Qdrant counts.")

    langchain_service: Optional[LangChainVectorService] = None
    if args.repair and not args.dry_run and config.qdrant_enabled:
        try:
            service_candidate = LangChainVectorService(config)
            if service_candidate.enabled:
                langchain_service = service_candidate
            else:
                logger.warning("LangChain Qdrant vector service disabled; repair mode unavailable.")
        except Exception as exc:  # pragma: no cover
            logger.warning("Failed to initialise LangChain vector service: %s", exc)

    summary: List[Tuple[str, str, int, Optional[int]]] = []

    from backend.rbac_backend.services.document_service import governed_by_contract_master

    for document_id in document_ids:
        if await governed_by_contract_master(db, document_id):
            # The contract-worker's reprojection is the only writer of a
            # governed contract's evidence. Replacing its points here (LangChain
            # shape, no revision tag) would rewrite a CURRENT projection behind
            # its fence; storage repair delegates the same way.
            logger.info("document=%s status=delegated (Contract Master reprojection)", document_id)
            if args.repair and not args.dry_run:
                # Never over "mismatch": storage repair records a damaged or
                # unverifiable projection that way, and it must stay counted.
                # One row per document: no upsert against a filter a "mismatch"
                # row would fail, which inserted a second row beside it.
                existing = await db.vector_sync_status.find_one({"document_id": document_id})
                if existing is None:
                    await db.vector_sync_status.insert_one(
                        {
                            "document_id": document_id,
                            "sync_status": "delegated",
                            "delegated_to": "contract_master_reprojection",
                            "updatedAt": datetime.utcnow(),
                            "createdAt": datetime.utcnow(),
                        }
                    )
                elif existing.get("sync_status") != "mismatch":
                    await db.vector_sync_status.update_one(
                        {"_id": existing["_id"], "sync_status": {"$ne": "mismatch"}},
                        {
                            "$set": {
                                "sync_status": "delegated",
                                "delegated_to": "contract_master_reprojection",
                                "updatedAt": datetime.utcnow(),
                            }
                        },
                    )
            summary.append((document_id, "delegated", 0, None))
            continue
        mongo_count = await db.document_vectors.count_documents({"document_id": document_id})
        qdrant_count = await _fetch_qdrant_count(qclient, config.qdrant_collection, document_id)

        if qdrant_count is None and config.qdrant_enabled:
            status = "unknown"
            details = "Qdrant count unavailable"
        elif mongo_count == 0 and (qdrant_count or 0) == 0:
            status = "empty"
            details = None
        elif qdrant_count is None:
            status = "mongo_only"
            details = "Qdrant disabled"
        elif mongo_count == qdrant_count:
            status = "synced"
            details = None
        else:
            status = "mismatch"
            details = f"Mongo={mongo_count}, Qdrant={qdrant_count}"

        logger.info(
            "document=%s status=%s mongo_chunks=%s qdrant_chunks=%s",
            document_id,
            status,
            mongo_count,
            qdrant_count,
        )

        repaired_chunks: Optional[int] = None
        if args.repair and status == "mismatch" and langchain_service:
            if args.dry_run:
                logger.info("Dry-run: would attempt repair for %s", document_id)
            else:
                repaired_chunks = await _repair_document(db, langchain_service, document_id)
                if repaired_chunks is not None:
                    qdrant_count = await _fetch_qdrant_count(qclient, config.qdrant_collection, document_id)
                    if qdrant_count == mongo_count:
                        status = "synced"
                        details = None
                    else:
                        status = "mismatch"
                        details = f"Mongo={mongo_count}, Qdrant={qdrant_count}, repaired={repaired_chunks}"

        await _update_vector_status(
            db,
            document_id,
            status=status,
            mongo_chunks=mongo_count,
            qdrant_chunks=qdrant_count,
            details=details,
            dry_run=args.dry_run,
        )

        summary.append((document_id, status, mongo_count, qdrant_count))

    mismatches = [item for item in summary if item[1] == "mismatch"]
    logger.info(
        "Reconciliation complete. processed=%s mismatches=%s",
        len(summary),
        len(mismatches),
    )
    if mismatches:
        logger.info("Mismatched documents: %s", ", ".join(doc_id for doc_id, *_ in mismatches))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare MongoDB and Qdrant vector counts per document.")
    parser.add_argument(
        "--document-id",
        action="append",
        help="Specific document ID to reconcile (can be provided multiple times).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of documents to process.")
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Attempt to re-upload mismatched vectors to Qdrant using existing Mongo chunks.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report differences without mutating Qdrant or Mongo status collections.",
    )
    return parser.parse_args(argv)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    args = parse_args()
    asyncio.run(reconcile(args))


if __name__ == "__main__":
    main()
