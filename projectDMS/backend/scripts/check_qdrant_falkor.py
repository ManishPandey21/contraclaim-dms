#!/usr/bin/env python
"""
Inspect Qdrant and FalkorDB for uploaded document data.

Examples
--------
Inspect overall counts:
    python backend/scripts/check_qdrant_falkor.py

Check a specific document by Mongo _id:
    python backend/scripts/check_qdrant_falkor.py --document-id 6534a1...
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Optional

from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

# Ensure repo packages resolve when executed from project root.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.models.document import Document
from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.falkor_graph_service import (
    FalkorGraphError,
    FalkorGraphService,
    normalize_letter_code,
)


def _qdrant_client(config: DocumentProcessingConfig) -> Optional[QdrantClient]:
    if not config.qdrant_enabled:
        return None
    return QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key, timeout=5.0)


def _count_qdrant_total(client: Optional[QdrantClient], collection: str) -> Optional[int]:
    if not client:
        return None
    try:
        response = client.count(collection_name=collection, exact=True)
        return int(getattr(response, "count", 0))
    except Exception as exc:
        print(f"[WARN] Unable to count Qdrant collection '{collection}': {exc}")
        return None


def _count_qdrant_for_document(
    client: Optional[QdrantClient],
    collection: str,
    document_id: str,
) -> Optional[int]:
    if not client:
        return None
    try:
        response = client.count(
            collection_name=collection,
            count_filter=Filter(
                must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
            ),
            exact=True,
        )
        return int(getattr(response, "count", 0))
    except Exception as exc:
        print(f"[WARN] Qdrant count for document {document_id} failed: {exc}")
        return None


def _falkor_letter_payload(
    service: FalkorGraphService,
    norm_code: str,
) -> Optional[dict[str, Any]]:
    try:
        entry = service.get_letter(norm_code)
    except FalkorGraphError as exc:
        print(f"[WARN] Falkor lookup failed for {norm_code}: {exc}")
        return None
    return entry


def _count_falkor_letters(service: FalkorGraphService) -> Optional[int]:

    if not service.enabled:
        return None

    # Print credentials being used
    print(f"FalkorDB connection details:")
    print(f"  Host: {service.config.host}")
    print(f"  Port: {service.config.port}")
    print(f"  Graph: {service.config.graph_name}")
    print(f"  Password: {'*' * len(service.config.password) if service.config.password else 'None'}")

    try:
        response = service._execute("MATCH (l:Letter) RETURN COUNT(l)")  # type: ignore[attr-defined]

        if not response or len(response) < 2:
            return 0
        rows = response[1]
        if not rows:
            return 0
        value = rows[0][0]
        if isinstance(value, list):
            value = value[0]
        return int(value)
        # Result is [[count]]
        # return int(rows[0][0])
    except FalkorGraphError as exc:
        print(f"[WARN] Unable to count Falkor letters: {exc}")
        return None


async def _load_document(document_id: str) -> Optional[Document]:
    service = DocumentService()
    return await service.get_document(document_id)


async def main(args: argparse.Namespace) -> int:
    config = DocumentProcessingConfig()
    qdrant = _qdrant_client(config)
    falkor = FalkorGraphService()

    print("=== Storage Inspection ===")
    print(f"Qdrant enabled: {bool(qdrant)} (collection='{config.qdrant_collection}')")
    print(f"Falkor enabled: {falkor.enabled}")

    total_qdrant = _count_qdrant_total(qdrant, config.qdrant_collection)
    if total_qdrant is not None:
        print(f"Qdrant total vectors: {total_qdrant}")

    total_falkor = _count_falkor_letters(falkor)
    if total_falkor is not None:
        print(f"Falkor letters: {total_falkor}")

    if not args.document_id:
        return 0

    document = await _load_document(args.document_id)
    if not document:
        print(f"[FAIL] Document {args.document_id} not found in MongoDB.")
        return 1

    doc_id = str(document.id)
    letter_code = document.letterNo or getattr(document, "letterNoNormalized", None) or doc_id
    norm_code = normalize_letter_code(str(letter_code))

    doc_qdrant = _count_qdrant_for_document(qdrant, config.qdrant_collection, doc_id)
    if doc_qdrant is not None:
        print(f"Qdrant chunks for {doc_id}: {doc_qdrant}")

    entry = _falkor_letter_payload(falkor, norm_code)
    if entry:
        print(f"Falkor letter found (normCode='{entry.get('l.normCode', norm_code)}').")
    else:
        print(f"[WARN] Falkor letter not found for normCode='{norm_code}'.")

    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--document-id",
        help="Mongo document _id to inspect in detail.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    cli_args = parse_args(sys.argv[1:])
    exit_code = asyncio.run(main(cli_args))
    sys.exit(exit_code)
