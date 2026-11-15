"""
Diagnostic script to investigate contract search issues.
Checks database content and simulates search queries.
"""
import asyncio
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from motor.motor_asyncio import AsyncIOMotorClient
from rbac_backend.core.config import settings
import re
from typing import List, Dict, Any


async def diagnose_contract_search():
    """Diagnose contract search issues"""

    # Connect to MongoDB
    client = AsyncIOMotorClient(settings.MONGO_URI)
    db = client[settings.DATABASE_NAME]

    print("=" * 80)
    print("CONTRACT SEARCH DIAGNOSTIC TOOL")
    print("=" * 80)
    print()

    # 1. Check contract_ingest_jobs collection
    print("1. Checking contract_ingest_jobs collection...")
    print("-" * 80)

    jobs_collection = db.contract_ingest_jobs
    total_jobs = await jobs_collection.count_documents({})
    print(f"Total jobs: {total_jobs}")

    # Find the specific file
    target_filename = "Vol_2_GCC_SCC_KNPCC11.pdf"
    job = await jobs_collection.find_one({"filename": target_filename})

    if job:
        print(f"\n✓ Found job for {target_filename}:")
        print(f"  - upload_id: {job.get('upload_id')}")
        print(f"  - status: {job.get('status')}")
        print(f"  - organization_id: {job.get('organization_id')}")
        print(f"  - project_id: {job.get('project_id')}")
        print(f"  - file_path: {job.get('file_path')}")
        print(f"  - categories: {job.get('categories')}")
        print(f"  - tags: {job.get('tags')}")
        upload_id = job.get('upload_id')
    else:
        print(f"\n✗ No job found for {target_filename}")
        print("\nSearching for similar filenames...")
        cursor = jobs_collection.find({}).limit(10)
        async for doc in cursor:
            print(f"  - {doc.get('filename')} (upload_id: {doc.get('upload_id')})")
        upload_id = None

    print()

    # 2. Check document_vectors collection
    print("2. Checking document_vectors collection...")
    print("-" * 80)

    vectors_collection = db.document_vectors
    total_vectors = await vectors_collection.count_documents({})
    print(f"Total vectors: {total_vectors}")

    # Count contract vectors
    contract_vectors = await vectors_collection.count_documents({"uploadType": "contract"})
    print(f"Contract vectors: {contract_vectors}")

    if upload_id:
        # Check vectors for this specific upload_id
        print(f"\nSearching for vectors with upload_id: {upload_id}")

        count_by_upload_id = await vectors_collection.count_documents({"upload_id": upload_id})
        count_by_document_id = await vectors_collection.count_documents({"document_id": upload_id})

        print(f"  - Vectors with upload_id={upload_id}: {count_by_upload_id}")
        print(f"  - Vectors with document_id={upload_id}: {count_by_document_id}")

        # Get a sample vector
        sample = await vectors_collection.find_one({"upload_id": upload_id})
        if sample:
            print(f"\n✓ Sample vector found:")
            print(f"  - _id: {sample.get('_id')}")
            print(f"  - upload_id: {sample.get('upload_id')}")
            print(f"  - document_id: {sample.get('document_id')}")
            print(f"  - uploadType: {sample.get('uploadType')}")
            print(f"  - filename: {sample.get('filename')}")
            print(f"  - file_name: {sample.get('file_name')}")
            print(f"  - source_filename: {sample.get('source_filename')}")
            print(f"  - clause_number: {sample.get('clause_number')}")
            print(f"  - clause_title: {sample.get('clause_title')}")
            print(f"  - text (first 200 chars): {sample.get('text', '')[:200]}...")
            print(f"  - organization_id: {sample.get('organization_id')}")
            print(f"  - project_id: {sample.get('project_id')}")
            print(f"  - tags: {sample.get('tags')}")
        else:
            print(f"\n✗ No vectors found for upload_id: {upload_id}")
    else:
        # Show sample contract vectors
        print("\nShowing sample contract vectors:")
        cursor = vectors_collection.find({"uploadType": "contract"}).limit(3)
        async for doc in cursor:
            print(f"\n  Vector:")
            print(f"    - upload_id: {doc.get('upload_id')}")
            print(f"    - document_id: {doc.get('document_id')}")
            print(f"    - filename: {doc.get('filename') or doc.get('file_name')}")
            print(f"    - clause_number: {doc.get('clause_number')}")
            print(f"    - text (first 100 chars): {doc.get('text', '')[:100]}...")

    print()

    # 3. Simulate search query
    print("3. Simulating search query for 'taking over'...")
    print("-" * 80)

    text_query = "taking over"

    # Simulate the stopword filtering from contract_service.py
    stopwords = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "in", "into", "is", "it", "of", "on", "or", "per", "the", "this",
        "that", "these", "those", "to", "upon", "was", "were", "with", "without",
    }

    raw_terms = [t for t in re.split(r"\s+", text_query) if t and t.strip()]
    escaped_terms: List[str] = []
    seen: set = set()

    print(f"\nOriginal query: '{text_query}'")
    print(f"Raw terms: {raw_terms}")

    for term in raw_terms:
        cleaned = term.strip().strip(",.;:!?()[]{}\"")
        if not cleaned:
            continue
        lowered = cleaned.lower()

        print(f"\n  Processing term: '{term}'")
        print(f"    - Cleaned: '{cleaned}'")
        print(f"    - Lowered: '{lowered}'")
        print(f"    - Length: {len(lowered)}")
        print(f"    - Is stopword: {lowered in stopwords}")
        print(f"    - Already seen: {lowered in seen}")

        if len(lowered) < 3:
            print(f"    ✗ FILTERED: Too short (< 3 chars)")
            continue
        if lowered in stopwords:
            print(f"    ✗ FILTERED: Stopword")
            continue
        if lowered in seen:
            print(f"    ✗ FILTERED: Duplicate")
            continue

        print(f"    ✓ KEPT")
        seen.add(lowered)
        escaped_terms.append(re.escape(cleaned))

    print(f"\nFinal escaped terms: {escaped_terms}")

    if escaped_terms:
        regex = "|".join(escaped_terms)
        print(f"Regex pattern: {regex}")
    else:
        regex = re.escape(text_query)
        print(f"Fallback regex (literal): {regex}")

    # Build the match stage
    match_stage: Dict[str, Any] = {"uploadType": "contract"}

    if upload_id:
        match_stage["$or"] = [
            {"upload_id": upload_id},
            {"document_id": upload_id},
        ]
        print(f"\nDocument filter: upload_id OR document_id = {upload_id}")

    if regex:
        match_stage["text"] = {"$regex": regex, "$options": "i"}
        print(f"Text filter: regex = {regex}")

    print(f"\nFull match stage:")
    print(f"  {match_stage}")

    # Execute the query
    print("\n4. Executing search query...")
    print("-" * 80)

    matching_docs = await vectors_collection.count_documents(match_stage)
    print(f"Matching documents: {matching_docs}")

    if matching_docs > 0:
        print("\n✓ Found matching documents!")
        cursor = vectors_collection.find(match_stage).limit(3)
        async for doc in cursor:
            print(f"\n  Match:")
            print(f"    - clause_number: {doc.get('clause_number')}")
            print(f"    - clause_title: {doc.get('clause_title')}")
            print(f"    - text (first 200 chars): {doc.get('text', '')[:200]}...")
    else:
        print("\n✗ No matching documents found!")

        # Try without text filter
        print("\nTrying without text filter...")
        match_stage_no_text = {k: v for k, v in match_stage.items() if k != "text"}
        count_no_text = await vectors_collection.count_documents(match_stage_no_text)
        print(f"Documents matching other filters (no text): {count_no_text}")

        if count_no_text > 0:
            print("\n⚠ Issue: Documents exist but text search is not matching!")
            print("Checking sample texts...")
            cursor = vectors_collection.find(match_stage_no_text).limit(3)
            async for doc in cursor:
                text = doc.get('text', '')
                print(f"\n  Sample text (first 300 chars):")
                print(f"    {text[:300]}...")

                # Check if the terms appear in the text
                for term in raw_terms:
                    if term.lower() in text.lower():
                        print(f"    ✓ Contains '{term}'")
                    else:
                        print(f"    ✗ Does NOT contain '{term}'")
        else:
            print("\n⚠ Issue: No documents match the upload_id/document_id filter!")

    print()

    # 5. Check indexes
    print("5. Checking database indexes...")
    print("-" * 80)

    indexes = await vectors_collection.list_indexes().to_list(length=100)
    print(f"Indexes on document_vectors:")
    for idx in indexes:
        print(f"  - {idx.get('name')}: {idx.get('key')}")

    print()
    print("=" * 80)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 80)

    client.close()


if __name__ == "__main__":
    asyncio.run(diagnose_contract_search())
