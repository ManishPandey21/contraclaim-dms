#!/usr/bin/env python
"""
Inspect Qdrant and FalkorDB for uploaded document data - WORKING VERSION.
Uses only public methods that actually exist.
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
    
    # Since _client is None and _execute doesn't work, let's try a different approach
    try:
        # Try to use a direct Redis connection as fallback
        import redis
        
        # Get connection details from environment
        redis_url = os.getenv('FALKORDB_URL', 'redis://localhost:6380')
        redis_password = os.getenv('FALKORDB_PASSWORD')
        
        # Parse Redis URL
        if redis_url.startswith('redis://'):
            redis_url = redis_url[8:]
        
        # Extract host and port
        if '@' in redis_url:
            # Format: password@host:port
            creds, hostport = redis_url.split('@')
            if ':' in hostport:
                host, port = hostport.split(':')
            else:
                host, port = hostport, '6379'
        else:
            # Format: host:port
            if ':' in redis_url:
                host, port = redis_url.split(':')
            else:
                host, port = redis_url, '6379'
            creds = redis_password
        
        # Connect and query
        client = redis.Redis(
            host=host,
            port=int(port),
            password=creds if '@' not in redis_url else redis_password,
            decode_responses=True
        )
        
        # Execute graph query
        result = client.execute_command("GRAPH.QUERY", "G", "MATCH (l:Letter) RETURN COUNT(l)")
        client.close()
        
        # Parse result - format is usually [['COUNT(l)'], [[count]]]
        if result and len(result) >= 2 and result[1]:
            return int(result[1][0][0])
        return 0
        
    except Exception as exc:
        print(f"[WARN] Unable to count Falkor letters: {exc}")
        return None


def _test_falkor_connection():
    """Test if we can connect to FalkorDB directly"""
    try:
        import redis

        # Print credentials being used
        redis_url = os.getenv('FALKORDB_URL', 'redis://localhost:6380')
        redis_password = os.getenv('FALKORDB_PASSWORD')
        host = os.getenv('FALKORDB_HOST', 'localhost')
        port = int(os.getenv('FALKORDB_PORT', '6380'))
        graph_name = os.getenv('FALKORDB_GRAPH_NAME', 'contraclaim')

        print(f"FalkorDB connection details:")
        print(f"  URL: {redis_url}")
        print(f"  Host: {host}")
        print(f"  Port: {port}")
        print(f"  Password: {'*' * len(redis_password) if redis_password else 'None'}")
        print(f"  Graph: {graph_name}")

        print(f"Testing connection to: {redis_url}")

        if redis_url.startswith('redis://'):
            redis_url = redis_url[8:]

        if '@' in redis_url:
            creds, hostport = redis_url.split('@')
            if ':' in hostport:
                host, port = hostport.split(':')
            else:
                host, port = hostport, '6379'
        else:
            if ':' in redis_url:
                host, port = redis_url.split(':')
            else:
                host, port = redis_url, '6379'
            creds = redis_password

        client = redis.Redis(
            host=host,
            port=int(port),
            password=creds if '@' not in redis_url else redis_password,
            decode_responses=True,
            socket_connect_timeout=5
        )

        # Test basic connection
        client.ping()
        print("✓ Redis connection successful")

        # Test graph query
        result = client.execute_command("GRAPH.QUERY", "G", "MATCH (l:Letter) RETURN COUNT(l)")
        print(f"✓ Graph query successful: {result}")

        client.close()
        return True

    except Exception as e:
        print(f"✗ Connection failed: {e}")
        return False


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

    # Test Falkor connection first
    print("\n--- Testing FalkorDB Connection ---")
    connection_ok = _test_falkor_connection()
    
    total_qdrant = _count_qdrant_total(qdrant, config.qdrant_collection)
    if total_qdrant is not None:
        print(f"Qdrant total vectors: {total_qdrant}")

    if connection_ok:
        total_falkor = _count_falkor_letters(falkor)
        if total_falkor is not None:
            print(f"Falkor letters: {total_falkor}")
    else:
        print("Falkor letters: <connection failed>")

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

    if connection_ok:
        entry = _falkor_letter_payload(falkor, norm_code)
        if entry:
            print(f"Falkor letter found (normCode='{entry.get('l.normCode', norm_code)}').")
        else:
            print(f"[WARN] Falkor letter not found for normCode='{norm_code}'.")
    else:
        print(f"Falkor letter check: <connection failed>")

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