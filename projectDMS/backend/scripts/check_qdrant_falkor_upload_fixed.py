#!/usr/bin/env python

"""
Inspect Qdrant and FalkorDB with sample data upload capability.

Uses the correct FalkorDB connection on port 6380 without password.
FIXED: Now uses proper Cypher query format for FalkorDB.

Examples:
    # Just inspect without uploading
    python scripts/check_qdrant_falkor_upload.py

    # Upload sample data to FalkorDB
    python scripts/check_qdrant_falkor_upload.py --upload-sample

    # Upload and then inspect
    python scripts/check_qdrant_falkor_upload.py --upload-sample --inspect
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


def escape_cypher_string(s: str) -> str:
    """Escape special characters for embedding in Cypher queries."""
    # Escape backslashes first, then quotes
    return s.replace('\\', '\\\\').replace('"', '\\"').replace("'", "\\'")


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


def _upload_sample_data() -> bool:
    """Upload sample letter data to FalkorDB"""

    client = _get_falkor_client()
    if not client:
        return False

    graph_name = os.getenv('FALKORDB_GRAPH_NAME', 'contraclaim')

    try:
        print(f"\n--- Uploading Sample Data to '{graph_name}' ---")

        # Sample letter data
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

        # Sample reference relationships
        sample_refs = [
            {"letter_code": "SAMPLE-LETTER-002", "ref_code": "SAMPLE-LETTER-001", "type": "REFERENCES"},
            {"letter_code": "SAMPLE-LETTER-003", "ref_code": "SAMPLE-LETTER-001", "type": "CITES"},
            {"letter_code": "SAMPLE-LETTER-004", "ref_code": "SAMPLE-LETTER-002", "type": "REFERENCES"},
        ]

        # Create Letter nodes - embed values directly in query
        for letter in sample_letters:
            norm_code = normalize_letter_code(letter["code"])

            # Escape string values
            code_esc = escape_cypher_string(letter["code"])
            norm_esc = escape_cypher_string(norm_code)
            subject_esc = escape_cypher_string(letter["subject"])
            date_esc = escape_cypher_string(letter["date"])
            direction_esc = escape_cypher_string(letter["direction"])
            project_esc = escape_cypher_string(letter["project"])

            # Build query with embedded values (NOT parameterized)
            query = f'''CREATE (l:Letter {{
                code: "{code_esc}",
                normCode: "{norm_esc}",
                subject: "{subject_esc}",
                date: "{date_esc}",
                direction: "{direction_esc}",
                project: "{project_esc}"
            }}) RETURN l.normCode'''

            try:
                result = client.execute_command("GRAPH.QUERY", graph_name, query)
                print(f"✓ Created letter: {letter['code']} (normalized: {norm_code})")
            except Exception as exc:
                print(f"[WARN] Error creating letter {letter['code']}: {exc}")

        # Create Reference relationships - embed values directly
        for ref in sample_refs:
            letter_norm = escape_cypher_string(normalize_letter_code(ref["letter_code"]))
            ref_norm = escape_cypher_string(normalize_letter_code(ref["ref_code"]))
            ref_type = ref["type"]  # No escaping needed for relationship type

            query = f'''MATCH (l:Letter {{normCode: "{letter_norm}"}})
            MATCH (r:Letter {{normCode: "{ref_norm}"}})
            CREATE (l)-[:{ref_type}]->(r)
            RETURN COUNT(*)'''

            try:
                result = client.execute_command("GRAPH.QUERY", graph_name, query)
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
        result = client.execute_command(
            "GRAPH.QUERY",
            graph_name,
            "MATCH (l:Letter) RETURN COUNT(l)"
        )

        # Parse result - format is [['COUNT(l)'], [[count]]]
        if result and len(result) >= 2 and result[1]:
            count = int(result[1][0][0])
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
        result = client.execute_command("GRAPH.QUERY", graph_name, "CALL db.labels()")
        if result and len(result) >= 2 and result[1]:
            labels = [row[0] for row in result[1]]
            print(f"Node labels: {labels}")
        else:
            print("No node labels found")

        # Get relationship types
        result = client.execute_command("GRAPH.QUERY", graph_name, "CALL db.relationshipTypes()")
        if result and len(result) >= 2 and result[1]:
            rel_types = [row[0] for row in result[1]]
            print(f"Relationship types: {rel_types}")
        else:
            print("No relationship types found")

        # Get sample letters if they exist
        result = client.execute_command(
            "GRAPH.QUERY",
            graph_name,
            "MATCH (l:Letter) RETURN l.code, l.subject LIMIT 5"
        )
        if result and len(result) >= 2 and result[1]:
            letters = result[1]
            if letters:
                print(f"\nSample letters:")
                for letter in letters:
                    code, subject = letter
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
        # If only uploading, show what we uploaded
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
            # Show available fields (first 3)
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
