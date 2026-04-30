"""MongoDB connectivity utilities and index management."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import motor.motor_asyncio

from .config import settings

logger = logging.getLogger(__name__)

client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
database: Optional[motor.motor_asyncio.AsyncIOMotorDatabase] = None
_index_task: Optional[asyncio.Task[None]] = None

INDEX_CREATION_ATTEMPTS = 5
INDEX_CREATION_BASE_DELAY = 1.0

async def connect() -> None:
    """Establish a MongoDB connection and kick off index creation."""

    global client, database, _index_task

    if client is None:
        client = motor.motor_asyncio.AsyncIOMotorClient(settings.DATABASE_URL)

    database = client.contraclaim  # Explicitly use the contraclaim database

    try:
        await client.admin.command("ping")
    except Exception:
        # Cleanup on failure
        if client is not None:
            client.close()
        client = None
        database = None
        raise

    # Ensure indexes asynchronously so slow index creation never blocks startup.
    if database is not None:
        if _index_task is None or _index_task.done():
            _index_task = asyncio.create_task(ensure_indexes_with_retry(database))

async def disconnect() -> None:
    """Close the MongoDB connection and cancel index creation if pending."""

    global client, database, _index_task

    if _index_task is not None and not _index_task.done():
        _index_task.cancel()
        try:
            await _index_task
        except asyncio.CancelledError:
            pass
        finally:
            _index_task = None

    if client is not None:
        client.close()
    client = None
    database = None

async def get_db():
    global client, database
    # Ensure database is available
    if database is None:
        if client is None:
            client = motor.motor_asyncio.AsyncIOMotorClient(settings.DATABASE_URL)
        database = client.contraclaim  # Explicitly use the contraclaim database
    if database is None:
        raise RuntimeError("Database connection is not initialised")
    yield database

async def get_database():
    """Get database instance for direct access"""
    global client, database
    if database is None:
        if client is None:
            client = motor.motor_asyncio.AsyncIOMotorClient(settings.DATABASE_URL)
        database = client.contraclaim
    return database

async def ensure_indexes(db):
    """Create indexes to improve RBAC scoped queries and general performance."""

    # Users
    await db.users.create_index("organization_id", background=True)
    await db.users.create_index("roles", background=True)
    await db.users.create_index("projects", background=True)

    # Organizations
    # _id is implicitly indexed by MongoDB; do not attempt to create an _id index with options
    await db.organizations.create_index("name", background=True)

    # Projects
    await db.projects.create_index("organization_id", background=True)

    # Documents
    await db.documents.create_index("organization_id", background=True)
    await db.documents.create_index("project_id", background=True)
    await db.documents.create_index([("$**", "text")], background=True)
    await db.documents.create_index(
        [("organization_id", 1), ("project_id", 1), ("createdAt", -1)],
        background=True,
    )
    await db.documents.create_index("tags", background=True)
    await db.documents.create_index("file_type", background=True)

    # Letters
    await db.letters.create_index("organization_id", background=True)
    await db.letters.create_index("project_id", background=True)
    await db.letters.create_index("conversation_id", background=True)
    await db.letters.create_index("previous_letter_id", background=True)

    # Parties / Representatives
    await db.parties.create_index("organization_id", background=True)
    await db.parties.create_index("projects", background=True)
    await db.representatives.create_index("organization_id", background=True)
    await db.representatives.create_index("project_id", background=True)
    await db.representatives.create_index("party_id", background=True)

    # Tags
    await db.tags.create_index("organization_id", background=True)
    await db.tags.create_index("project_id", background=True)
    await db.tags.create_index("visibility", background=True)

    # Tasks
    await db.tasks.create_index("organization_id", background=True)
    await db.tasks.create_index("project_id", background=True)

    # Emails/logs (if present)
    await db.email_logs.create_index("organization_id", background=True)
    await db.email_logs.create_index("project_id", background=True)

    # Email groups
    await db.email_groups.create_index("organization_id", background=True)
    await db.email_groups.create_index("project_id", background=True)

    # AI Style Profiles
    await db.ai_style_profiles.create_index("organization_id", background=True)
    await db.ai_style_profiles.create_index("project_id", background=True)
    await db.ai_style_profiles.create_index("updated_at", background=True)

    # Contract ingest jobs
    await db.contract_ingest_jobs.create_index("upload_id", unique=True, background=True)
    await db.contract_ingest_jobs.create_index(
        [("organization_id", 1), ("project_id", 1), ("createdAt", -1)],
        background=True,
    )
    await db.contract_ingest_jobs.create_index("status", background=True)

    # Retrieval engine collections
    await db.ingestion_jobs.create_index("job_id", unique=True, background=True)
    await db.ingestion_jobs.create_index([("org_id", 1), ("project_id", 1), ("document_id", 1)], background=True)
    await db.ingestion_jobs.create_index([("document_id", 1), ("content_hash", 1)], background=True)
    await db.chunks.create_index("chunk_id", unique=True, background=True)
    await db.chunks.create_index([("document_id", 1), ("org_id", 1), ("project_id", 1)], background=True)
    await db.chunks.create_index("content_hash", background=True)
    await db.rag_runs.create_index([("org_id", 1), ("project_id", 1), ("run_type", 1), ("created_at", -1)], background=True)
    await db.agent_conversations.create_index("conversation_id", unique=True, background=True)
    await db.agent_messages.create_index([("conversation_id", 1), ("created_at", -1)], background=True)


async def ensure_indexes_with_retry(db, attempts: int = INDEX_CREATION_ATTEMPTS) -> None:
    """Attempt to create indexes with exponential backoff on failure."""

    delay = INDEX_CREATION_BASE_DELAY
    for attempt in range(1, attempts + 1):
        try:
            await ensure_indexes(db)
            logger.info("Database indexes ensured (attempt %s)", attempt)
            return
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
            raise
        except Exception as exc:  # pragma: no cover - logged for observability
            logger.warning(
                "Failed to create database indexes on attempt %s/%s: %s",
                attempt,
                attempts,
                exc,
            )
            if attempt == attempts:
                logger.error("Giving up on database index creation after %s attempts", attempts)
                return
            await asyncio.sleep(delay)
            delay *= 2

def is_connected() -> bool:
    """Return True if DB client and database are initialized."""
    global client, database
    return client is not None and database is not None
