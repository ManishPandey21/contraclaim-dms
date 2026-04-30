"""Check why the job failed"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from motor.motor_asyncio import AsyncIOMotorClient
from rbac_backend.core.config import settings


async def check_job():
    db_url = settings.DATABASE_URL
    db_name = db_url.split('/')[-1] if '/' in db_url else 'contraclaim'
    
    client = AsyncIOMotorClient(db_url)
    db = client[db_name]
    
    upload_id = "64bfd824-8434-4d3a-8e59-586907388137"
    
    job = await db.contract_ingest_jobs.find_one({"upload_id": upload_id})
    
    if job:
        print("\n" + "=" * 80)
        print("JOB DETAILS")
        print("=" * 80)
        print(f"Upload ID: {job.get('upload_id')}")
        print(f"Filename: {job.get('filename')}")
        print(f"Status: {job.get('status')}")
        print(f"Error: {job.get('error')}")
        print(f"File path: {job.get('file_path')}")
        print(f"Organization ID: {job.get('organization_id')}")
        print(f"Project ID: {job.get('project_id')}")
        print(f"Created: {job.get('createdAt')}")
        print(f"Updated: {job.get('updatedAt')}")
        print(f"Categories: {job.get('categories')}")
        print(f"Tags: {job.get('tags')}")
        print("=" * 80)
    
    client.close()


if __name__ == "__main__":
    asyncio.run(check_job())
