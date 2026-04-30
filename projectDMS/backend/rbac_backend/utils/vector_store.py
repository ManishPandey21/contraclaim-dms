"""Helpers for storing AI-ready files in the vector store."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def sha256_file(path: str | Path) -> str:
    """Return SHA-256 digest for the given file path."""
    file_path = Path(path)
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4096), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def upload_to_vector_store(
    file_path: str | Path,
    db: Any,
    current_user: Any,
    vector_store: Any,
) -> str:
    """Upload file if not already present, returning the vector store id."""
    sha = sha256_file(file_path)
    existing = await db.ai_files.find_one({"sha256": sha})
    if existing:
        return existing["file_id"]

    with Path(file_path).open("rb") as handle:
        file_obj = vector_store.files.create(file=handle, purpose="assistants")
    file_id = getattr(file_obj, "id", None)
    if not file_id:
        raise RuntimeError("Vector store upload did not return an id")

    await db.ai_files.insert_one(
        {
            "sha256": sha,
            "file_id": file_id,
            "path": str(file_path),
            "user_id": getattr(current_user, "id", None),
            "uploaded_at": getattr(current_user, "timestamp", None),
        }
    )
    return file_id
