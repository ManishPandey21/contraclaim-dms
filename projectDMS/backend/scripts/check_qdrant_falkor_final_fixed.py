#!/usr/bin/env python

"""
Inspect Qdrant and FalkorDB with sample data upload capability.

Uses the correct FalkorDB connection on port 6380 without password.
FIXED: Proper response parsing for FalkorDB COUNT queries.

Examples:
    # Just inspect without uploading
    python scripts/check_qdrant_falkor_upload.py
    
    # Upload sample data to FalkorDB (using --params approach)
    python scripts/check_qdrant_falkor_upload.py --upload-sample
    
    # Upload and then inspect
    python scripts/check_qdrant_falkor_upload.py --upload-sample --inspect
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime
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


def _json_default(value: Any) -> Any:
    """JSON serializer for datetime objects (same as FalkorGraphService)."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _get_falkor_client():
    """Get FalkorDB client on the correct port (6380) without password"""
    import redis

    # Print credentials being used
    host = os.getenv('FALKORDB_HOST', 'localhost')
    port = int(os.getenv('FALKORDB_PORT', '6380'))
    password = os.getenv('FALKORDB_PASSWORD', None)
    graph_name = os.getenv('FALKORDB_GRAPH_NAME', 'contraclaim')

    print(f"Connecting to FalkorDB with:")
    print(f"  Host: {host}")
    print(f"  Port: {port}")
    print(f"  Password: {'*' * len(password) if password else 'None'}")
    print(f"  Graph: {graph_name}")

    try:
        client = redis.Redis(
            host=host,
            port=port,
            password=password,
            decode_responses=True,
            socket_connect_timeout=5
        )
        client.ping()
        print("✓ Connected successfully")
        return client
    except Exception as exc:
        print(f"✗ Failed to connect to FalkorDB: {exc}")
        return None


def _execute_query(client, graph_name: str, cypher: str, params: Optional[dict] = None) -> Any:
    """
    Execute Cypher query with optional parameters using --params flag.
    
    This mimics the FalkorGraphService._execute() method.
    """
    try:
        if params:
            # Use --params flag for safe parameter binding (like FalkorGraphService)
            response = client.execute_command(
                "GRAPH.QUERY",
                graph_name,
                cypher,
                "--params",
                json.dumps(params, default=_json_default),
                "--compact",
            )
        else:
            # No parameters - just execute the query
            response = client.execute_command(
                "GRAPH.QUERY",
                graph_name,
                cypher,
                "--compact",
            )
        return response
    except Exception as exc:
        print(f"[ERROR] Query execution failed: {exc}")
        raise


def _parse_rows(response: Any) -> list[dict[str, Any]]:
    """
    Parse FalkorDB response into list of dictionaries.
    
    Handles FalkorDB's response format:
      [['header1', 'header2', ...], [[val1, val2, ...], [val1, val2, ...], ...]]
    
    This properly handles edge cases like COUNT() queries.
    """
    if not response or len(response) < 2:
        return []
    
    header, rows = response[0], response[1]
    
    if not header or not rows:
        return []
    
    # Handle empty rows list
    if not rows or len(rows) == 0:
        return []
    
    # Convert rows to dictionaries
    result = []
    for row in rows:
        # Handle case where row might be wrapped in an extra list (COUNT queries)
        if isinstance(row, list) and len(row) == 1 and isinstance(row[0], list):
            # COUNT query format: [[value]] - unwrap one level
            row = row[0]
        
        result.append(dict(zip(header, row)))
    
    return result


def _upload_sample_data() -> bool:
    """Upload sample letter data to FalkorDB using --params approach"""
    
    client = _get_falkor_client()
    if not client:
        return False
    
    graph_name = os.getenv('FALKORDB_GRAPH_NAME', 'contraclaim')
    
    try:
        print(f"\n--- Uploading Sample Data to '{graph_name}' ---")
        
        # Sample letters to upload
        sample_letters = [
            {
                "code": "SAMPLE-LETTER-001",
                "subject": "Project Initiation Document",
                "date": "2025-01-15",
                "direction": "incoming",
                "project": "sample-project-1"
            },
            {
                "code": "SAMPLE-LETTER-002",
                "subject": "Scope Definition and Requirements",
                "date": "2025-01-20",
                "direction": "outgoing",
                "project": "sample-project-1"
            },
            {
                "code": "SAMPLE-LETTER-003",
                "subject": "Budget Allocation and Timeline",
                "date": "2025-01-25",
                "direction": "incoming",
                "project": "sample-project-2"
            },
            {
                "code": "SAMPLE-LETTER-004",
                "subject": "Risk Assessment Report",
                "date": "2025-02-01",
                "direction": "internal",
                "project": "sample-project-1"
            },
        ]
        
        # Create Letter nodes using --params (parameterized approach)
        for letter in sample_letters:
            norm_code = normalize_letter_code(letter["code"])
            
            # Cypher query with parameter placeholders
            cypher = """
            CREATE (l:Letter {
                code: $code,
                normCode: $normCode,
                subject: $subject,
                date: $date,
                direction: $direction,
                project: $project,
                createdAt: datetime(),
                lastUpdated: datetime()
            })
            RETURN l.normCode
            """
            
            # Parameters dictionary - safe parameter binding
            params = {
                "code": letter["code"],
                "normCode": norm_code,
                "subject": letter["subject"],
                "date": letter["date"],
                "direction": letter["direction"],
                "project": letter["project"]
            }
            
            try:
                result = _execute_query(client, graph_name, cypher, params)
                print(f"✓ Created letter: {letter['code']} (normalized: {norm_code})")
            except Exception as exc:
                print(f"[WARN] Error creating letter {letter['code']}: {exc}")
        
        # Sample reference relationships
        sample_refs = [
            {"letter_code": "SAMPLE-LETTER-002", "ref_code": "SAMPLE-LETTER-001", "type": "REFERENCES"},
            {"letter_code": "SAMPLE-LETTER-003", "ref_code": "SAMPLE-LETTER-001", "type": "CITES"},
            {"letter_code": "SAMPLE-LETTER-004", "ref_code": "SAMPLE-LETTER-002", "type": "REFERENCES"},
        ]
        
        # Create relationships using --params
        for ref in sample_refs:
            letter_norm = normalize_letter_code(ref["letter_code"])
            ref_norm = normalize_letter_code(ref["ref_code"])
            ref_type = ref["type"]
            
            cypher = f"""
            MATCH (l:Letter {{normCode: $letterNorm}})
            MATCH (r:Letter {{normCode: $refNorm}})
            CREATE (l)-[:{ref_type}]->(r)
            RETURN COUNT(*)
            """
            
            params = {
                "letterNorm": letter_norm,
                "refNorm": ref_norm,
            }
            
            try:
                result = _execute_query(client, graph_name, cypher, params)
                print(f"✓ Created relationship: {ref['letter_code']} -{ref['type']}-> {ref['ref_code']}")
            except Exception as exc:
                print(f"[WARN] Error creating relationship: {exc}")
        
        print("\n✓ Sample data upload completed")
        return True
        
    except Exception as exc:
        print(f"✗ Upload failed: {exc}")
        return False
    finally:
        client.close()


def _count_falkor_letters() -> Optional[int]:
    """Count letters in FalkorDB"""
    client = _get_falkor_client()
    if not client:
        return None
    
    graph_name = os.getenv('FALKORDB_GRAPH_NAME', 'contraclaim')
    
    try:
        print("Counting letters in FalkorDB...")
        result = _execute_query(client, graph_name, "MATCH (l:Letter) RETURN COUNT(l)")
        
        # Parse result using _parse_rows which handles COUNT format properly
        rows = _parse_rows(result)
        if rows:
            count = int(rows[0].get('COUNT(l)', 0))
            print(f"✓ Found {count} letters")
            return count
        else:
            print("✓ No letters found (database is empty)")
            return 0
    except Exception as exc:
        print(f"✗ Query failed: {exc}")
        return None
    finally:
        client.close()


def _get_falkor_schema():
    """Get schema information from FalkorDB"""
    client = _get_falkor_client()
    if not client:
        return
    
    graph_name = os.getenv('FALKORDB_GRAPH_NAME', 'contraclaim')
    
    try:
        print("\n--- FalkorDB Schema ---")
        
        # Get node labels
        result = _execute_query(client, graph_name, "CALL db.labels()")
        rows = _parse_rows(result)
        if rows:
            # db.labels() returns [{label: 'Label'}]
            labels = [row.get('label', '') for row in rows if 'label' in row]
            print(f"Node labels: {labels}")
        else:
            print("No node labels found")
        
        # Get relationship types
        result = _execute_query(client, graph_name, "CALL db.relationshipTypes()")
        rows = _parse_rows(result)
        if rows:
            # db.relationshipTypes() returns [{relationshipType: 'TYPE'}]
            rel_types = [row.get('relationshipType', '') for row in rows if 'relationshipType' in row]
            print(f"Relationship types: {rel_types}")
        else:
            print("No relationship types found")
        
        # Get sample letters if they exist
        result = _execute_query(
            client,
            graph_name,
            "MATCH (l:Letter) RETURN l.code, l.subject LIMIT 5"
        )
        rows = _parse_rows(result)
        if rows:
            print(f"\nSample letters:")
            for row in rows:
                code = row.get('l.code') or row.get('code')
                subject = row.get('l.subject') or row.get('subject')
                print(f"  - {code}: {subject}")
    except Exception as exc:
        print(f"Schema query failed: {exc}")
    finally:
        client.close()


async def _load_document(document_id: str) -> Optional[Document]:
    service = DocumentService()
    return await service.get_document(document_id)


async def main(args: argparse.Namespace) -> int:
    config = DocumentProcessingConfig()
    qdrant = _qdrant_client(config)
    falkor = FalkorGraphService()
    
    # Upload sample data if requested
    if args.upload_sample:
        print("=" * 60)
        success = _upload_sample_data()
        if not success:
            print("[ERROR] Sample data upload failed")
            return 1
    
    # Inspect storage
    if args.upload_sample and not args.inspect:
        print("\n" + "=" * 60)
    else:
        print("=" * 60)
    
    print("=== Storage Inspection ===")
    print(f"Qdrant enabled: {bool(qdrant)} (collection='{config.qdrant_collection}')")
    print(f"Falkor enabled: {falkor.enabled}")
    
    # Check Qdrant
    total_qdrant = _count_qdrant_total(qdrant, config.qdrant_collection)
    if total_qdrant is not None:
        print(f"Qdrant total vectors: {total_qdrant}")
    
    # Check FalkorDB
    print("\n--- FalkorDB Inspection ---")
    print("Connecting to FalkorDB on localhost:6380 (no password)...")
    total_falkor = _count_falkor_letters()
    if total_falkor is not None:
        print(f"Falkor letters: {total_falkor}")
    
    # Show schema if we have letters
    if total_falkor and total_falkor > 0:
        _get_falkor_schema()
    
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
        print(f"Looking for letter with normCode: {norm_code}")
        entry = _falkor_letter_payload(falkor, norm_code)
        if entry:
            print(f"✓ Falkor letter found!")
            print("Letter fields:")
            for key in list(entry.keys())[:3]:
                value = entry[key]
                if isinstance(value, str) and len(value) > 50:
                    value = value[:50] + "..."
                print(f"  {key}: {value}")
        else:
            print(f"✗ Falkor letter not found for normCode '{norm_code}'")
    
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--document-id",
        help="Mongo document _id to inspect in detail."
    )
    parser.add_argument(
        "--upload-sample",
        action="store_true",
        help="Upload sample letter data to FalkorDB before inspection."
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help="Run inspection after upload (used with --upload-sample)."
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    cli_args = parse_args(sys.argv[1:])
    exit_code = asyncio.run(main(cli_args))
    sys.exit(exit_code)
