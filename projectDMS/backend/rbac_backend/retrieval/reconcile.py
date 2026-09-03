from __future__ import annotations

import logging
from typing import Dict, Optional, Set

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..services.publication_policy import is_consumable, resolve_canonical_document
from .embeddings import EmbeddingClient
from .vector_client import VectorClient

logger = logging.getLogger(__name__)


class VectorReconciler:
    """Repairs divergence between Mongo chunk records and Qdrant vectors using deterministic chunk IDs."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        embedding_client: EmbeddingClient,
        vector_client: VectorClient,
    ):
        self.db = db
        self.embedding_client = embedding_client
        self.vector_client = vector_client

    async def reconcile_document(
        self,
        document_id: str,
        org_id: str,
        project_id: str,
        namespace: Optional[str] = None,
    ) -> Dict[str, int]:
        document = await resolve_canonical_document(self.db, document_id)
        if not is_consumable(document):
            return {
                "missing_in_qdrant": 0,
                "missing_in_mongo": 0,
                "repaired": 0,
                "removed": 0,
                "qdrant_ids": 0,
                "mongo_chunks": 0,
            }

        chunks = [
            doc
            async for doc in self.db.chunks.find(
                {"document_id": document_id, "org_id": org_id, "project_id": project_id}
            )
        ]
        chunk_ids: Set[str] = {str(c.get("chunk_id")) for c in chunks}
        qdrant_ids = set(
            await self.vector_client.list_chunk_ids(
                {
                    "org_id": org_id,
                    "project_id": project_id,
                    "document_id": document_id,
                },
                namespace=namespace,
            )
        )

        missing_in_qdrant = chunk_ids - qdrant_ids
        missing_in_mongo = qdrant_ids - chunk_ids

        repaired = 0
        if missing_in_qdrant:
            to_write = [
                c for c in chunks if str(c.get("chunk_id")) in missing_in_qdrant
            ]
            if to_write:
                vectors = await self.embedding_client.embed(
                    [c.get("text_original") or "" for c in to_write]
                )
                await self.vector_client.upsert(
                    vectors,
                    [
                        {
                            "chunk_id": c.get("chunk_id"),
                            "document_id": c.get("document_id"),
                            "org_id": c.get("org_id"),
                            "project_id": c.get("project_id"),
                            "page_start": c.get("page_start"),
                            "text": c.get("text_original") or c.get("text"),
                            "text_enriched": c.get("text_enriched"),
                            "tags": c.get("tags", []),
                            "embedding_provider": c.get("embedding_provider"),
                            "embedding_model": c.get("embedding_model"),
                            "embedding_dim": c.get("embedding_dim"),
                            "embedding_version": c.get("embedding_version"),
                            "chunking_version": c.get("chunking_version"),
                        }
                        for c in to_write
                    ],
                    namespace=namespace,
                )
                repaired = len(to_write)

        removed = 0
        if missing_in_mongo:
            removed = await self.vector_client.delete(
                list(missing_in_mongo), namespace=namespace
            )

        return {
            "missing_in_qdrant": len(missing_in_qdrant),
            "missing_in_mongo": len(missing_in_mongo),
            "repaired": repaired,
            "removed": removed,
            "qdrant_ids": len(qdrant_ids),
            "mongo_chunks": len(chunk_ids),
        }
