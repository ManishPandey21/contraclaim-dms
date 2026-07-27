import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

try:
    from ..config.document_processing_config import DocumentProcessingConfig
except ImportError:  # pragma: no cover - script compatibility
    from config.document_processing_config import DocumentProcessingConfig

logger = logging.getLogger(__name__)


class LangChainVectorService:
    """Dual-write vector service backed by LangChain + Qdrant."""

    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self._client = None
        self._vector_store = None
        self._enabled = False
        self._init_error: Optional[str] = None
        self._qdrant_models = None
        self._initialize()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _initialize(self) -> None:
        if not self.config.qdrant_enabled:
            auth_error = self.config.qdrant_auth_configuration_error
            if auth_error:
                logger.warning("Qdrant dual-write disabled: %s", auth_error)
            else:
                logger.info("Qdrant dual-write disabled via configuration")
            self._enabled = False
            return

        try:
            from qdrant_client import QdrantClient
            from langchain_openai import OpenAIEmbeddings
            from langchain_qdrant import QdrantVectorStore
            from qdrant_client.http import models as qmodels

            self._qdrant_models = qmodels
        except ImportError as exc:
            self._init_error = str(exc)
            logger.warning("LangChain Qdrant dependencies missing: %s", exc)
            self._enabled = False
            return

        try:
            distance = self._resolve_distance(qmodels)
            if self._is_in_memory_qdrant():
                self._client = QdrantClient(
                    location=":memory:",
                    timeout=self.config.qdrant_timeout,
                )
            else:
                self._client = QdrantClient(**self.config.qdrant_client_kwargs())

            self._ensure_collection(self._client, qmodels, distance)

            embedding = OpenAIEmbeddings(
                model=self.config.openai_embedding_model,
                api_key=self.config.openai_api_key,
            )

            vector_store_kwargs = {
                "client": self._client,
                "collection_name": self.config.qdrant_collection,
            }

            if self.config.qdrant_vector_name:
                vector_store_kwargs["vector_name"] = self.config.qdrant_vector_name

            try:
                vector_store_kwargs["embeddings"] = embedding
                self._vector_store = QdrantVectorStore(**vector_store_kwargs)
            except TypeError:
                vector_store_kwargs.pop("embeddings", None)
                vector_store_kwargs["embedding"] = embedding
                self._vector_store = QdrantVectorStore(**vector_store_kwargs)

            self._enabled = True
            logger.info(
                "LangChain Qdrant vector service ready (collection=%s)",
                self.config.qdrant_collection,
            )
        except Exception as exc:
            self._init_error = str(exc)
            logger.warning("Failed to initialize LangChain Qdrant vector service: %s", exc)
            self._enabled = False

    def _is_in_memory_qdrant(self) -> bool:
        normalized = (self.config.qdrant_url or "").strip().lower()
        return normalized in {":memory:", "memory://", "qdrant://:memory:"}

    def _resolve_distance(self, qmodels) -> Any:
        distance_name = (self.config.qdrant_distance or "cosine").upper()
        normalized = distance_name.replace("-", "_")
        if not hasattr(qmodels.Distance, normalized):
            logger.warning(
                "Unsupported Qdrant distance '%s'; defaulting to COSINE",
                self.config.qdrant_distance,
            )
            return qmodels.Distance.COSINE
        return getattr(qmodels.Distance, normalized)

    def _ensure_collection(self, client, qmodels, distance) -> None:
        try:
            collection_exists = getattr(client, "collection_exists", None)
            if callable(collection_exists) and not collection_exists(self.config.qdrant_collection):
                raise ValueError("collection does not exist")

            collection = client.get_collection(self.config.qdrant_collection)
            vectors_conf = getattr(collection.config.params, "vectors", None)

            if isinstance(vectors_conf, dict):
                available = list(vectors_conf.keys())
                if not self.config.qdrant_vector_name:
                    if len(available) == 1:
                        self.config.qdrant_vector_name = available[0]
                    else:
                        logger.warning(
                            "Multiple vectors present in collection '%s'; specify QDRANT_VECTOR_NAME to select one (available=%s)",
                            self.config.qdrant_collection,
                            available,
                        )
                elif self.config.qdrant_vector_name not in available:
                    raise ValueError(
                        f"Vector '{self.config.qdrant_vector_name}' not found in collection '{self.config.qdrant_collection}'. Available: {available}"
                    )
            return
        except Exception:
            logger.info(
                "Creating Qdrant collection '%s' (size=%s, distance=%s)",
                self.config.qdrant_collection,
                self.config.qdrant_vector_size,
                distance,
            )

            if self.config.qdrant_vector_name:
                vectors_config = {
                    self.config.qdrant_vector_name: qmodels.VectorParams(
                        size=self.config.qdrant_vector_size,
                        distance=distance,
                    )
                }
            else:
                vectors_config = qmodels.VectorParams(
                    size=self.config.qdrant_vector_size,
                    distance=distance,
                )

            create_collection = getattr(client, "create_collection", None)
            if callable(create_collection):
                create_collection(
                    collection_name=self.config.qdrant_collection,
                    vectors_config=vectors_config,
                )
                return

            client.recreate_collection(
                collection_name=self.config.qdrant_collection,
                vectors_config=vectors_config,
            )

    async def delete_document(
        self,
        document_id: str,
        *,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> bool:
        """Delete every vector point belonging to a document from Qdrant.

        Returns True when the delete was executed, False when the service is
        disabled or the client is unavailable (config/no-op cases). Raises on
        Qdrant errors so callers can surface the failure.
        """
        document_id = str(document_id or "").strip()
        if not document_id:
            return False
        if not self._enabled:
            return False
        if self._client is None or self._qdrant_models is None:
            logger.warning(
                "Qdrant client not initialized; cannot delete vectors for document_id=%s",
                document_id,
            )
            return False

        models = self._qdrant_models

        def _schema_variant(prefix: str, org_field: Optional[str]):
            conditions = [
                models.FieldCondition(
                    key=f"{prefix}document_id",
                    match=models.MatchValue(value=document_id),
                )
            ]
            if organization_id and org_field:
                conditions.append(
                    models.FieldCondition(
                        key=f"{prefix}{org_field}",
                        match=models.MatchValue(value=str(organization_id)),
                    )
                )
            if project_id:
                conditions.append(
                    models.FieldCondition(
                        key=f"{prefix}project_id",
                        match=models.MatchValue(value=str(project_id)),
                    )
                )
            return models.Filter(must=conditions)

        # Native VectorClient payloads are flat; LangChain wraps the same
        # metadata under ``metadata``. Include both organization aliases so
        # a replacement removes old points before its UUID-backed write.
        org_fields = ("org_id", "organization_id") if organization_id else (None,)
        variants = [
            _schema_variant(prefix, org_field)
            for prefix in ("", "metadata.")
            for org_field in org_fields
        ]
        qdrant_filter = models.Filter(should=variants)

        await asyncio.to_thread(
            self._client.delete,
            collection_name=self.config.qdrant_collection,
            points_selector=models.FilterSelector(filter=qdrant_filter),
            wait=True,
        )
        logger.info("Deleted Qdrant vectors for document_id=%s", document_id)
        return True

    async def replace_document(self, payloads: List[Dict[str, Any]]) -> int:
        """Replace document vectors in Qdrant using LangChain with UUID-based point IDs."""
        if not self._enabled or not self._vector_store:
            return 0

        if not payloads:
            return 0

        document_id = str(payloads[0]["metadata"].get("document_id", ""))
        if not document_id:
            logger.debug("Skipping LangChain Qdrant upsert; missing document_id metadata")
            return 0

        try:
            try:
                base_metadata = dict(payloads[0].get("metadata") or {})
                org_id = base_metadata.get("organization_id")
                project_id = base_metadata.get("project_id")
                deleted = await self.delete_document(
                    document_id,
                    organization_id=str(org_id) if org_id else None,
                    project_id=str(project_id) if project_id else None,
                )
                if deleted:
                    logger.debug("Deleted existing vectors for document_id=%s", document_id)
            except Exception as fallback_exc:
                logger.warning("Delete failed for document_id=%s: %s", document_id, fallback_exc)

            # Prepare texts, metadatas, and deterministic point IDs.
            #
            # Keep the application-level chunk_id unchanged in metadata for
            # Mongo/reconciliation, but never pass it directly as the Qdrant
            # point id. Qdrant accepts only unsigned integers or UUID strings;
            # legacy document chunk ids are often "<document_id>-<digest>".
            texts: List[str] = []
            metadatas: List[Dict[str, Any]] = []
            ids: List[str] = []

            for payload in payloads:
                chunk_text = payload.get("text") or ""
                if not chunk_text.strip():
                    continue

                metadata = dict(payload.get("metadata") or {})
                metadata["checksum"] = payload.get("checksum")
                metadata.setdefault("document_id", document_id)

                chunk_id = payload.get("chunk_id") or metadata.get("chunk_id")
                if not chunk_id:
                    chunk_index = metadata.get("chunk_index", len(texts))
                    chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{document_id}:{chunk_index}"))
                chunk_id = str(chunk_id)
                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"contraclaim:qdrant:{document_id}:{chunk_id}"))
                metadata["chunk_id"] = chunk_id
                metadata["qdrant_point_id"] = point_id
                metadata.setdefault("embedding_model", self.config.openai_embedding_model)
                metadata.setdefault("embedding_provider", "openai")

                ids.append(point_id)
                texts.append(chunk_text)
                metadatas.append(metadata)

            if not texts:
                logger.debug("No valid chunks to upsert for document_id=%s", document_id)
                return 0

            # Upsert vectors with valid point IDs
            await asyncio.to_thread(
                self._vector_store.add_texts,
                texts=texts,
                metadatas=metadatas,
                ids=ids,
            )

            logger.info(
                "Upserted %s chunks to Qdrant via LangChain (document_id=%s)",
                len(texts),
                document_id,
            )
            return len(texts)

        except Exception as exc:
            logger.warning("LangChain Qdrant upsert failed: %s", exc)
            return 0

    async def similarity_search(
        self,
        query_text: str,
        *,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Perform semantic search against Qdrant and return scored chunks."""
        if not query_text or not self._enabled or not self._vector_store:
            return []

        search_filter = filters or None

        def _search():
            try:
                return self._vector_store.similarity_search_with_score(
                    query_text,
                    k=top_k,
                    filter=search_filter,
                )
            except Exception as exc:
                logger.warning("LangChain Qdrant similarity search failed: %s", exc)
                return []

        pairs = await asyncio.to_thread(_search)

        results: List[Dict[str, Any]] = []
        for doc, score in pairs:
            metadata = dict(getattr(doc, "metadata", {}) or {})
            results.append(
                {
                    "text": getattr(doc, "page_content", "") or "",
                    "score": float(score) if score is not None else 0.0,
                    "metadata": metadata,
                }
            )

        return results
