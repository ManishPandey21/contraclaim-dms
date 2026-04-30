from __future__ import annotations

import logging
from typing import List, Sequence

from .models import Chunk
from ..retrieval.embeddings import EmbeddingClient
from ..retrieval.vector_client import _cosine

logger = logging.getLogger(__name__)


class ChunkEnricher:
    """Contextual enrichment using neighborhood and semantic proximity."""

    def __init__(self, embedding_client: EmbeddingClient):
        self.embedding_client = embedding_client

    async def enrich(
        self,
        chunks: List[Chunk],
        embeddings: List[List[float]],
        strategies: Sequence[str],
        neighborhood: int = 1,
        semantic_k: int = 2,
    ) -> List[Chunk]:
        if not chunks:
            return chunks

        strategies_lower = {s.lower() for s in strategies}
        use_neighborhood = "neighborhood" in strategies_lower
        use_semantic = "semantic" in strategies_lower

        if use_semantic and not embeddings:
            try:
                embeddings = await self.embedding_client.embed([c.text_original for c in chunks])
            except Exception as exc:
                logger.warning("Semantic enrichment embedding failed: %s", exc)
                use_semantic = False

        for idx, chunk in enumerate(chunks):
            pieces = [chunk.text_original]

            if use_neighborhood:
                start = max(0, idx - neighborhood)
                end = min(len(chunks), idx + neighborhood + 1)
                for neighbor_idx in range(start, end):
                    if neighbor_idx == idx:
                        continue
                    pieces.append(chunks[neighbor_idx].text_original)

            if use_semantic and embeddings:
                neighbors = self._top_k_similar(idx, embeddings, k=semantic_k)
                for neighbor_idx in neighbors:
                    pieces.append(chunks[neighbor_idx].text_original)

            # Deduplicate while preserving order
            seen = set()
            cleaned = []
            for piece in pieces:
                key = piece.strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                cleaned.append(key)

            enriched = "\n\n".join(cleaned)
            chunk.text_enriched = enriched
            chunk.enrichment_metadata = {
                "strategies": list(strategies_lower),
                "neighborhood": neighborhood if use_neighborhood else 0,
                "semantic_k": semantic_k if use_semantic else 0,
                "status": "success",
            }

        return chunks

    def _top_k_similar(self, idx: int, embeddings: List[List[float]], k: int) -> List[int]:
        anchor = embeddings[idx]
        scored = []
        for i, emb in enumerate(embeddings):
            if i == idx:
                continue
            scored.append((i, _cosine(anchor, emb)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [i for i, _ in scored[:k]]

