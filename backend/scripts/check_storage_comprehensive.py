#!/usr/bin/env python
"""
Comprehensive storage inspection with recommendations.
Shows current state of Qdrant and FalkorDB and provides next steps.
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


def _get_falkor_client(config: DocumentProcessingConfig):
    """Get FalkorDB client - connect without password"""
    import redis
    try:
        client = redis.Redis(
            host=config.falkordb_host,
            port=config.falkordb_port,
            password=None,  # No password required
            decode_responses=True,
            socket_connect_timeout=5
        )
        client.ping()
        return client
    except Exception as exc:
        print(f"✗ Failed to connect to FalkorDB: {exc}")
        return None


def _check_falkor_schema(config: DocumentProcessingConfig):
    """Check if FalkorDB has the proper schema setup"""
    client = _get_falkor_client(config)
    if not client:
        return False
    
    try:
        # Use the graph name from config
        result = client.execute_command("GRAPH.QUERY", config.falkordb_graph_name, "CALL db.labels()")
        if result and len(result) >= 2:
            labels = [row[0] for row in result[1]] if result[1] else []
            return "Letter" in labels
        return False
    except Exception as exc:
        print(f"Schema check failed: {exc}")
        return False
    finally:
        if client:
            client.close()


def _count_falkor_letters(config: DocumentProcessingConfig) -> Optional[int]:
    """Count letters in FalkorDB using correct graph name"""
    client = _get_falkor_client(config)
    if not client:
        return None
    
    try:
        # Use the graph name from config
        result = client.execute_command("GRAPH.QUERY", config.falkordb_graph_name, "MATCH (l:Letter) RETURN COUNT(l)")
        
        if result and len(result) >= 2 and result[1]:
            count = int(result[1][0][0])
            return count
        return 0
    except Exception as exc:
        print(f"✗ Query failed: {exc}")
        return None
    finally:
        if client:
            client.close()


def _check_mongo_documents() -> Optional[int]:
    """Check how many documents exist in MongoDB"""
    try:
        from pymongo import MongoClient
        from rbac_backend.config.document_processing_config import DocumentProcessingConfig
        
        config = DocumentProcessingConfig()
        client = MongoClient(config.mongo_uri)
        db = client[config.database_name]
        # Adjust collection name as needed
        collection = db['documents']  
        
        count = collection.count_documents({})
        return count
    except Exception as exc:
        print(f"[WARN] Could not check MongoDB: {exc}")
        return None


async def _load_document(document_id: str) -> Optional[Document]:
    service = DocumentService()
    return await service.get_document(document_id)


async def main(args: argparse.Namespace) -> int:
    config = DocumentProcessingConfig()
    qdrant = _qdrant_client(config)
    falkor = FalkorGraphService()

    print("=== STORAGE STATUS OVERVIEW ===")
    print("=" * 40)
    
    # Display actual FalkorDB configuration
    print(f"\n🔧 FalkorDB Configuration:")
    print(f"   URL: {config.falkordb_url}")
    print(f"   Graph: '{config.falkordb_graph_name}'")
    print(f"   Enabled: {config.falkordb_enabled}")
    
    # Check MongoDB
    print("\n📊 MongoDB (Source Data):")
    mongo_count = _check_mongo_documents()
    if mongo_count is not None:
        print(f"   Documents: {mongo_count}")
    else:
        print("   Status: <cannot connect>")
    
    # Check Qdrant
    print("\n🔍 Qdrant (Vector Store):")
    print(f"   Enabled: {bool(qdrant)}")
    print(f"   Collection: '{config.qdrant_collection}'")
    total_qdrant = _count_qdrant_total(qdrant, config.qdrant_collection)
    if total_qdrant is not None:
        print(f"   Vectors: {total_qdrant}")
        if total_qdrant == 0:
            print("   ⚠️  No vectors found - documents need to be processed")
    else:
        print("   Status: <connection failed>")
    
    # Check FalkorDB
    print("\n🕸️  FalkorDB (Graph Database):")
    print(f"   Connection: {config.falkordb_host}:{config.falkordb_port} (no password)")
    
    total_falkor = _count_falkor_letters(config)
    if total_falkor is not None:
        print(f"   Letters: {total_falkor}")
        if total_falkor == 0:
            print("   ⚠️  No letters found - graph needs to be populated")
    else:
        print("   Status: <connection failed>")
    
    has_schema = _check_falkor_schema(config)
    if has_schema:
        print("   Schema: ✓ Letter nodes defined")
    else:
        print("   Schema: ⚠️ No Letter label found")

    # Recommendations
    print("\n🎯 RECOMMENDATIONS:")
    print("=" * 40)
    
    if mongo_count == 0:
        print("1. 📥 Upload documents to MongoDB first")
    elif total_qdrant == 0 and mongo_count > 0:
        print("1. 🔄 Process documents for vector storage")
    elif total_falkor == 0 and total_qdrant > 0:
        print("1. 🕸️  Build knowledge graph from processed documents")
    else:
        print("1. ✅ All systems ready - you can query your data!")
    
    if total_falkor == 0:
        print("2. 💡 Run graph processing to populate FalkorDB with letter data")

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

        print(f"Document ID: {doc_id}")
        print(f"Letter Code: {letter_code}")
        print(f"Normalized: {norm_code}")

        doc_qdrant = _count_qdrant_for_document(qdrant, config.qdrant_collection, doc_id)
        if doc_qdrant is not None:
            print(f"Qdrant chunks: {doc_qdrant}")
            if doc_qdrant == 0:
                print("⚠️  This document has not been processed for vector search")

        # Check if letter exists in FalkorDB
        print(f"FalkorDB lookup...")
        entry = _falkor_letter_payload(falkor, norm_code)
        if entry:
            print(f"✅ Letter found in graph!")
            print("Available fields:")
            for key in list(entry.keys())[:5]:
                value = entry[key]
                if isinstance(value, str) and len(value) > 50:
                    value = value[:50] + "..."
                print(f"  {key}: {value}")
        else:
            print(f"❌ Letter not found in graph")
            if doc_qdrant and doc_qdrant > 0:
                print("💡 Document is in Qdrant but not in FalkorDB - run graph processing")

    print("\n" + "=" * 40)
    print("Next: Upload documents → Process → Query")
    print("=" * 40)

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