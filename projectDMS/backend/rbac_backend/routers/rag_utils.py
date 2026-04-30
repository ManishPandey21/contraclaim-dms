"""
rag_utils.py
Shared RAG utilities for MongoDB + OpenAI Assistants.
- ensure_vector_store_for_context: one vector store per (org, project)
- upload_files_to_vector_store: sha256-deduped file upload + attach
- get_text_embedding: OpenAI embeddings (text-embedding-3-small by default)
Env:
  OPENAI_API_KEY (required)
  EMBED_MODEL (optional, defaults to text-embedding-3-small)
Collections:
  vector_stores: {organization_id, project_id, vector_store_id, created_at}
  vector_files:  {organization_id, project_id, sha256, file_id, filename, size, vector_store_id, created_at}
"""
from __future__ import annotations

import os
import time
import hashlib
from datetime import datetime
from typing import List, Tuple

from bson.objectid import ObjectId

try:
    from openai import OpenAI  # OpenAI SDK v1+
except Exception as e:  # pragma: no cover
    OpenAI = None  # type: ignore

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")


def _client() -> "OpenAI":
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK not available. Run: pip install openai>=1.0.0")
    return OpenAI()


def _sha256_of_path(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def get_text_embedding(text: str) -> list[float]:
    client = _client()
    resp = client.embeddings.create(model=EMBED_MODEL, input=text or "")
    return list(resp.data[0].embedding)


async def ensure_vector_store_for_context(db, current_user) -> Tuple[str, dict]:
    """
    Ensure a vector store exists for (organization_id, project_id).
    Returns (vector_store_id, store_doc).
    """
    if not getattr(current_user, "organization_id", None):
        raise ValueError("current_user.organization_id is required")

    org_id = ObjectId(str(current_user.organization_id))
    proj_id = ObjectId(str(getattr(current_user, "project_id", None))) if getattr(current_user, "project_id", None) else None

    q = {"organization_id": org_id, "project_id": proj_id}
    store = await db.vector_stores.find_one(q)
    if store and store.get("vector_store_id"):
        return store["vector_store_id"], store

    client = _client()
    name = f"vs-org-{org_id}-proj-{proj_id or 'none'}"
    vs = client.beta.vector_stores.create(name=name)

    doc = {
        "organization_id": org_id,
        "project_id": proj_id,
        "vector_store_id": vs.id,
        "created_at": datetime.utcnow(),
    }
    await db.vector_stores.insert_one(doc)
    return vs.id, doc


async def upload_files_to_vector_store(
    filepaths: List[str],
    vector_store_id: str,
    db,
    current_user,
    skip_if_exists: bool = True,
) -> list[str]:
    """
    Upload local files to OpenAI Vector Store with sha256 dedupe into Mongo.
    Returns list of file_ids added (or reused).
    """
    client = _client()
    added_ids: list[str] = []

    org_id = ObjectId(str(current_user.organization_id))
    proj_id = ObjectId(str(getattr(current_user, "project_id", None))) if getattr(current_user, "project_id", None) else None

    for path in filepaths:
        if not path or not os.path.exists(path):
            continue

        sha = _sha256_of_path(path)
        if skip_if_exists:
            existing = await db.vector_files.find_one({
                "organization_id": org_id,
                "project_id": proj_id,
                "sha256": sha,
                "vector_store_id": vector_store_id,
            })
            if existing and existing.get("file_id"):
                added_ids.append(existing["file_id"])
                continue

        with open(path, "rb") as f:
            file_obj = client.files.create(file=f, purpose="assistants")
        client.beta.vector_stores.files.create(vector_store_id=vector_store_id, file_id=file_obj.id)

        record = {
            "organization_id": org_id,
            "project_id": proj_id,
            "sha256": sha,
            "file_id": file_obj.id,
            "filename": os.path.basename(path),
            "size": os.path.getsize(path),
            "vector_store_id": vector_store_id,
            "created_at": datetime.utcnow(),
        }
        await db.vector_files.insert_one(record)
        added_ids.append(file_obj.id)

    return added_ids
