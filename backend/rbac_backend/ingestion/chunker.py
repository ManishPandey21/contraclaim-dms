from __future__ import annotations

from typing import List, Optional

from .models import Chunk, compute_content_hash
from .chunk_ids import deterministic_chunk_id


def chunk_text(
    document_id: str,
    org_id: str,
    project_id: str,
    text: str,
    chunk_size: int,
    chunk_overlap: int,
) -> List[Chunk]:
    """Deterministically chunk text with overlap."""
    chunks: List[Chunk] = []
    if not text:
        return chunks
    start = 0
    idx = 0
    length = len(text)
    while start < length:
        end = min(length, start + chunk_size)
        chunk_body = text[start:end]
        chunk = Chunk(
            id=deterministic_chunk_id(document_id, idx, page_start=None, text=chunk_body[:50]),
            document_id=document_id,
            org_id=org_id,
            project_id=project_id,
            page_start=None,
            page_end=None,
            text_original=chunk_body,
            content_hash=compute_content_hash(chunk_body),
        )
        chunks.append(chunk)
        idx += 1
        if end == length:
            break
        start = end - chunk_overlap if chunk_overlap < chunk_size else end
    return chunks
