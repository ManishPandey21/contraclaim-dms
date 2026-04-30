"""
Quick test to identify the contract search issue
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from motor.motor_asyncio import AsyncIOMotorClient
from rbac_backend.core.config import settings


async def test_search():
    """Test the search issue"""
    
    # Parse database name from DATABASE_URL
    db_url = settings.DATABASE_URL
    db_name = db_url.split('/')[-1] if '/' in db_url else 'contraclaim'
    
    client = AsyncIOMotorClient(db_url)
    db = client[db_name]
    vectors = db.document_vectors
    
    print("\n" + "=" * 80)
    print("TESTING CONTRACT SEARCH ISSUE")
    print("=" * 80)
    
    # 1. Check if the file exists in contract_ingest_jobs
    print("\n1. Checking contract_ingest_jobs...")
    jobs = db.contract_ingest_jobs
    target_file = "Vol_2_GCC_SCC_KNPCC11.pdf"
    
    job = await jobs.find_one({"filename": target_file})
    if job:
        print(f"✓ Found job for {target_file}")
        print(f"  upload_id: {job.get('upload_id')}")
        print(f"  status: {job.get('status')}")
        upload_id = job.get('upload_id')
    else:
        print(f"✗ No job found for {target_file}")
        print("\nListing all jobs:")
        async for j in jobs.find().limit(5):
            print(f"  - {j.get('filename')} (upload_id: {j.get('upload_id')})")
        upload_id = None
    
    if not upload_id:
        print("\n⚠ Cannot proceed without upload_id")
        client.close()
        return
    
    # 2. Check vectors for this upload_id
    print(f"\n2. Checking vectors for upload_id: {upload_id}")
    
    count_upload = await vectors.count_documents({"upload_id": upload_id})
    count_document = await vectors.count_documents({"document_id": upload_id})
    count_contract = await vectors.count_documents({"uploadType": "contract", "upload_id": upload_id})
    
    print(f"  Vectors with upload_id={upload_id}: {count_upload}")
    print(f"  Vectors with document_id={upload_id}: {count_document}")
    print(f"  Contract vectors with upload_id={upload_id}: {count_contract}")
    
    if count_upload == 0 and count_document == 0:
        print("\n✗ NO VECTORS FOUND! This is the problem.")
        print("  The file was uploaded but vectors were not created.")
        print("  Check if ingestion completed successfully.")
        client.close()
        return
    
    # 3. Test the search query
    print("\n3. Testing search query 'taking over'...")
    
    # Simulate the backend's stopword filtering
    query = "taking over"
    stopwords = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
                 "in", "into", "is", "it", "of", "on", "or", "per", "the", "this",
                 "that", "these", "those", "to", "upon", "was", "were", "with", "without"}
    
    terms = query.split()
    kept_terms = []
    for term in terms:
        if len(term) >= 3 and term.lower() not in stopwords:
            kept_terms.append(term)
    
    print(f"  Original query: '{query}'")
    print(f"  Terms: {terms}")
    print(f"  Kept after filtering: {kept_terms}")
    
    if not kept_terms:
        print("\n✗ ALL TERMS FILTERED OUT! This is the problem.")
        print("  'over' is being filtered as a stopword.")
        print("  Solution: Remove 'over' from stopwords or use fallback regex.")
    else:
        regex = "|".join(kept_terms)
        print(f"  Regex pattern: {regex}")
        
        # Test the query
        match_stage = {
            "uploadType": "contract",
            "$or": [
                {"upload_id": upload_id},
                {"document_id": upload_id}
            ],
            "text": {"$regex": regex, "$options": "i"}
        }
        
        count = await vectors.count_documents(match_stage)
        print(f"\n  Matching documents: {count}")
        
        if count == 0:
            print("\n  ✗ No matches found. Testing without text filter...")
            match_no_text = {
                "uploadType": "contract",
                "$or": [
                    {"upload_id": upload_id},
                    {"document_id": upload_id}
                ]
            }
            count_no_text = await vectors.count_documents(match_no_text)
            print(f"  Documents without text filter: {count_no_text}")
            
            if count_no_text > 0:
                print("\n  ⚠ Documents exist but text search doesn't match!")
                print("  Checking sample text...")
                sample = await vectors.find_one(match_no_text)
                if sample:
                    text = sample.get('text', '')[:500]
                    print(f"\n  Sample text:\n  {text}...")
                    for term in terms:
                        if term.lower() in text.lower():
                            print(f"  ✓ Text contains '{term}'")
                        else:
                            print(f"  ✗ Text does NOT contain '{term}'")
        else:
            print(f"\n  ✓ Found {count} matching documents!")
    
    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)
    
    client.close()


if __name__ == "__main__":
    asyncio.run(test_search())
