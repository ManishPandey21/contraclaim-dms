from __future__ import annotations

import hashlib


def deterministic_chunk_id(document_id: str, index: int, page_start: int | None = None, text: str | None = None) -> str:
    """
    Build a deterministic chunk identifier that can be reused across Mongo + Qdrant.

    Args:
        document_id: Source document identifier.
        index: Chunk ordinal in the document.
        page_start: Optional page number anchor.
        text: Optional text seed to reduce collision risk.
    """
    seed = f"{document_id}:{index}:{page_start or 0}:{text or ''}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return f"{document_id}-{digest}"

