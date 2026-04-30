#!/usr/bin/env python
"""
Inspect Qdrant and FalkorDB for uploaded document data - FIXED URL PARSING.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Optional
from urllib.parse import urlparse

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


def _parse_redis_url(redis_url: str) -> tuple[str, int, str]:
    """Parse Redis URL and return (host, port, password)"""
    # Remove redis:// prefix if present
    if redis_url.startswith('redis://'):
        redis_url = redis_url[8:]
    
    # Remove any unexpected = signs
    redis_url = redis_url.lstrip('=')
    
    # Parse with urllib for proper handling
    parsed = urlparse(f"redis://{redis_url}")
    
    host = parsed.hostname or 'localhost'
    port = parsed.port or 6379
    password = parsed.password or os.getenv('FALKORDB_PASSWORD', 'default_password')
    
    # If no password in URL but we have username, use that as password
    if not password and parsed.username:
        password = parsed.username
    
    return host, port, password


def _count_falkor_letters_direct() -> Optional[int]:
    """Count letters using direct Redis connection since service._execute is broken"""
    try:
        import redis

        # Get connection details from environment
        redis_url = os.getenv('FALKORDB_URL', 'redis://localhost:6380')
        host = os.getenv('FALKORDB_HOST', 'localhost')
        port = int(os.getenv('FALKORDB_PORT', '6380'))
        password = os.getenv('FALKORDB_PASSWORD', None)
        graph_name = os.getenv('FALKORDB_GRAPH_NAME', 'G')

        print(f"Connecting to FalkorDB with:")
        print(f"  URL: {redis_url}")
        print(f"  Host: {host}")
        print(f"  Port: {port}")
        print(f"  Password: {'*' * len(password) if password else 'None'}")
        print(f"  Graph: {graph_name}")

        # Parse the URL properly
        parsed_host, parsed_port, parsed_password = _parse_redis_url(redis_url)

        # Use parsed values if available, otherwise fall back to env vars
        final_host = parsed_host or host
        final_port = parsed_port or port
        final_password = parsed_password or password

        print(f"Final connection details:")
        print(f"  Host: {final_host}")
        print(f"  Port: {final_port}")
        print(f"  Password: {'*' * len(final_password) if final_password else 'None'}")

        # Connect to FalkorDB
        client = redis.Redis(
            host=final_host,
            port=final_port,
            password=final_password,
            decode_responses=True,
            socket_connect_timeout=10,
            socket_timeout=10
        )
        
        # Test connection
        client.ping()
        print("✓ Connected to FalkorDB")
        
        # Execute graph query to count letters
        print("Executing graph query...")
        result = client.execute_command("GRAPH.QUERY", "G", "MATCH (l:Letter) RETURN COUNT(l)")
        
        print(f"Raw result: {result}")
        
        # Parse result - FalkorDB result format can vary
        if result:
            # Format 1: [['COUNT(l)'], [[count]]]
            if len(result) >= 2 and result[1]:
                count = int(result[1][0][0])
                print(f"✓ Found {count} letters")
                return count
            # Format 2: Different structure
            elif len(result) >= 1:
                # Try to find count in nested structures
                for item in result:
                    if isinstance(item, list):
                        for subitem in item:
                            if isinstance(subitem, list) and len(subitem) > 0:
                                try:
                                    count = int(subitem[0])
                                    print(f"✓ Found {count} letters (alternative format)")
                                    return count
                                except (ValueError, TypeError):
                                    continue
        print(f"✗ Unexpected result format: {result}")
        return 0
        
    except redis.ConnectionError as exc:
        print(f"✗ Connection failed: {exc}")
        print("  Check if FalkorDB is running: docker compose ps falkordb")
        return None
    except Exception as exc:
        print(f"✗ Failed to count Falkor letters: {exc}")
        import traceback
        traceback.print_exc()
        return None


def _test_basic_commands() -> bool:
    """Test basic Redis commands to verify connection works"""
    try:
        import redis
        
        redis_url = os.getenv('FALKORDB_URL', 'redis://localhost:6380')
        host, port, password = _parse_redis_url(redis_url)
        
        client = redis.Redis(
            host=host,
            port=port,
            password=password,
            decode_responses=True,
            socket_connect_timeout=5
        )
        
        # Test basic Redis
        client.ping()
        print("✓ Basic Redis connection works")
        
        # Test if graph commands are available
        try:
            result = client.execute_command("GRAPH.LIST")
            print(f"✓ Graph commands available: {result}")
        except redis.ResponseError as e:
            if "unknown command" in str(e).lower():
                print("✗ Graph commands not available - is this really FalkorDB?")
            else:
                print(f"✓ Graph commands available (different error): {e}")
        
        client.close()
        return True
        
    except Exception as exc:
        print(f"✗ Basic connection test failed: {exc}")
        return False


def _sample_falkor_letters(sample_size: int = 5) -> list[str]:
    """Get sample letter codes to verify data exists"""
    try:
        import redis
        
        redis_url = os.getenv('FALKORDB_URL', 'redis://localhost:6380')
        host, port, password = _parse_redis_url(redis_url)
        
        client = redis.Redis(
            host=host,
            port=port,
            password=password,
            decode_responses=True
        )
        
        # Get sample of letter codes
        result = client.execute_command("GRAPH.QUERY", "G", f"MATCH (l:Letter) RETURN l.normCode LIMIT {sample_size}")
        
        if result and len(result) >= 2 and result[1]:
            letters = [row[0] for row in result[1] if row and row[0]]
            return letters
        return []
        
    except Exception as exc:
        print(f"[WARN] Failed to get sample letters: {exc}")
        return []


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

    # Check Qdrant
    total_qdrant = _count_qdrant_total(qdrant, config.qdrant_collection)
    if total_qdrant is not None:
        print(f"Qdrant total vectors: {total_qdrant}")

    # Check FalkorDB
    print("\n--- FalkorDB Inspection ---")
    
    # First test basic connection
    print("Testing basic connection...")
    connection_ok = _test_basic_commands()
    
    if connection_ok:
        total_falkor = _count_falkor_letters_direct()
        if total_falkor is not None:
            print(f"Falkor letters: {total_falkor}")
            
            # Show sample letters if any exist
            if total_falkor > 0:
                sample_letters = _sample_falkor_letters(3)
                if sample_letters:
                    print(f"Sample letter codes: {sample_letters}")
                    
                    # Test get_letter method with first sample
                    test_code = sample_letters[0]
                    print(f"\nTesting get_letter with: {test_code}")
                    letter_data = _falkor_letter_payload(falkor, test_code)
                    if letter_data:
                        print(f"✓ get_letter successful: {list(letter_data.keys())}")
                    else:
                        print(f"✗ get_letter failed for {test_code}")
        else:
            print("Falkor letters: <count failed>")
    else:
        print("FalkorDB: <connection failed>")

    # Check specific document if requested
    if args.document_id:
        print(f"\n--- Document Details: {args.document_id} ---")
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

        # Check if letter exists in FalkorDB
        if connection_ok:
            print(f"Looking for letter with normCode: {norm_code}")
            entry = _falkor_letter_payload(falkor, norm_code)
            if entry:
                print(f"✓ Falkor letter found!")
                # Show available fields
                for key in list(entry.keys())[:5]:  # Show first 5 fields
                    value = entry[key]
                    if isinstance(value, str) and len(value) > 50:
                        value = value[:50] + "..."
                    print(f"  {key}: {value}")
            else:
                print(f"✗ Falkor letter not found for normCode '{norm_code}'")
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