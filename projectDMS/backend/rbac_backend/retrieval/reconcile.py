from __future__ import annotations

import logging
from typing import Dict, Optional, Set

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..services.publication_policy import is_consumable, resolve_canonical_document
from .embeddings import EmbeddingClient
from .point_ids import generic_chunk_point_id
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

    @staticmethod
    def _untouched() -> Dict[str, int]:
        return {
            "missing_in_qdrant": 0,
            "missing_in_mongo": 0,
            "repaired": 0,
            "removed": 0,
            "qdrant_ids": 0,
            "mongo_chunks": 0,
        }

    async def _is_contract_projection(self, document_id: str, document: Dict) -> bool:
        from ..services.contract_source import is_contract_upload

        if is_contract_upload(document):
            return True
        from ..services.contract_document_store import CONTRACT_DOCUMENTS_COLLECTION

        instruments = getattr(self.db, CONTRACT_DOCUMENTS_COLLECTION, None)
        if instruments is None:
            return False
        ids = sorted(
            {str(document_id), str((document or {}).get("_id") or document_id)}
        )
        return (
            await instruments.find_one({"document_id": {"$in": ids}}, {"_id": 1})
            is not None
        )

    async def reconcile_document(
        self,
        document_id: str,
        org_id: str,
        project_id: str,
        namespace: Optional[str] = None,
    ) -> Dict[str, int]:
        document = await resolve_canonical_document(self.db, document_id)
        if not is_consumable(document):
            return self._untouched()
        from .correspondence_payload import (
            CorrespondencePayloadError,
            canonical_scope_id,
        )

        try:
            document_scope = (
                canonical_scope_id(document.get("organization_id"), "organization_id"),
                canonical_scope_id(document.get("project_id"), "project_id"),
            )
        except CorrespondencePayloadError:
            document_scope = None  # not an id: no caller scope can be the document's
        if document_scope != (org_id, project_id):
            # The scope comes from the caller and selects the rows; it must be
            # the document's own, or rows stamped with another scope would be
            # republished into it.
            logger.warning(
                "Not reconciling %s under a scope that is not the document's",
                document_id,
            )
            result = self._untouched()
            result["skipped_scope_mismatch"] = 1
            return result
        if await self._is_contract_projection(document_id, document):
            # Contract vectors are not described by ``chunks`` rows: every point
            # would read as "missing in Mongo" and be deleted, emptying the
            # evidence vector source under a CURRENT Contract Master projection.
            # Their only writers are contract ingest and the contract-worker's
            # reprojection, so this reconciler leaves them alone.
            result = self._untouched()
            result["skipped_contract"] = 1
            return result

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
        unpublishable = False
        if missing_in_qdrant:
            from .correspondence_payload import (
                CorrespondencePayloadError,
                refuse_unpublishable_stored_rows,
            )

            try:
                # Every row of the document, not only the missing ones: a row
                # boundary can split the report's advice.
                refuse_unpublishable_stored_rows(
                    document,
                    [c.get("text_original") or c.get("text") or "" for c in chunks],
                )
            except CorrespondencePayloadError:
                logger.warning(
                    "Not republishing the stored chunks of %s: they are extraction-report "
                    "text, not the letter; reprocess the document",
                    document_id,
                )
                unpublishable = True
            to_write = [
                c for c in chunks if str(c.get("chunk_id")) in missing_in_qdrant
            ]
            if to_write and not unpublishable:
                vectors = await self.embedding_client.embed(
                    [c.get("text_original") or "" for c in to_write]
                )
                await self.vector_client.upsert(
                    vectors,
                    [
                        {
                            "chunk_id": c.get("chunk_id"),
                            "document_id": c.get("document_id"),
                            # The rows were read under exactly this scope.
                            "org_id": org_id,
                            "project_id": project_id,
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
                    point_id_for=generic_chunk_point_id,
                )
                repaired = len(to_write)

        # Removing points no stored row backs is the safe direction; it runs
        # whether or not the rows themselves may be republished.
        removed = 0
        if missing_in_mongo:
            removed = await self.vector_client.delete(
                list(missing_in_mongo),
                namespace=namespace,
                point_id_for=generic_chunk_point_id,
            )

        result = {
            "missing_in_qdrant": len(missing_in_qdrant),
            "missing_in_mongo": len(missing_in_mongo),
            "repaired": repaired,
            "removed": removed,
            "qdrant_ids": len(qdrant_ids),
            "mongo_chunks": len(chunk_ids),
        }
        if unpublishable:
            result["skipped_unpublishable"] = 1
        return result
