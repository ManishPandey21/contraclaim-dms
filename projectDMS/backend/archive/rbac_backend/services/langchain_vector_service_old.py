import asyncio
import logging
from typing import Any, Dict, List, Optional

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
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
            self._client = QdrantClient(
                url=self.config.qdrant_url,
                api_key=self.config.qdrant_api_key,
                timeout=10.0,
            )

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
        client.recreate_collection(
            collection_name=self.config.qdrant_collection,
            vectors_config=vectors_config,
        )

    async def replace_document(self, payloads: List[Dict[str, Any]]) -> int:
        """Replace document vectors in Qdrant using LangChain."""
        if not self._enabled or not self._vector_store:
            return 0
        if not payloads:
            return 0

        document_id = str(payloads[0]["metadata"].get("document_id", ""))
        if not document_id:
            logger.debug("Skipping LangChain Qdrant upsert; missing document_id metadata")
            return 0

        try:
            filter_query = {
                "must": [
                    {"key": "document_id", "match": {"value": document_id}}
                ]
            }

            try:
                await asyncio.to_thread(
                    self._vector_store.delete,
                    filter=filter_query,
                )
            except Exception as delete_exc:
                fallback_error = delete_exc
                try:
                    if self._client is not None and self._qdrant_models is not None:
                        models = self._qdrant_models
                        if hasattr(models, "FilterSelector"):
                            selector = models.FilterSelector(
                                filter=models.Filter(
                                    must=[
                                        models.FieldCondition(
                                            key="document_id",
                                            match=models.MatchValue(value=document_id),
                                        )
                                    ]
                                )
                            )
                        else:
                            selector = {
                                "filter": {
                                    "must": [
                                        {
                                            "key": "document_id",
                                            "match": {"value": document_id},
                                        }
                                    ]
                                }
                            }
                        await asyncio.to_thread(
                            self._client.delete,
                            collection_name=self.config.qdrant_collection,
                            points_selector=selector,
                        )
                    else:
                        raise fallback_error
                except Exception as fallback_exc:
                    logger.warning("LangChain Qdrant delete failed: %s", fallback_exc)

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
                chunk_index = metadata.get("chunk_index", len(texts))
                ids.append(f"{document_id}:{chunk_index}")
                texts.append(chunk_text)
                metadatas.append(metadata)

            if not texts:
                return 0

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
