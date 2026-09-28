import asyncio
import logging
from typing import Any, Dict, List, Optional, Sequence

try:
    from ..config.document_processing_config import DocumentProcessingConfig
    from ..retrieval.correspondence_payload import (
        CorrespondencePayloadError,
        assert_canonical_correspondence_payload,
    )
    from ..retrieval.vector_client import VectorScopeError, VectorStoreUnavailableError
except ImportError:  # pragma: no cover - script compatibility
    from config.document_processing_config import DocumentProcessingConfig
    from retrieval.correspondence_payload import (  # type: ignore[no-redef]
        CorrespondencePayloadError,
        assert_canonical_correspondence_payload,
    )
    from retrieval.vector_client import (  # type: ignore[no-redef]
        VectorScopeError,
        VectorStoreUnavailableError,
    )

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

    async def replace_document(self, points: List[Dict[str, Any]]) -> int:
        """Replace one document's correspondence vectors with canonical points.

        ``points`` come from ``build_correspondence_chunks`` and nothing else:
        each carries its UUID ``point_id``, ``text`` and the canonical flat
        ``payload`` the tenant-scoped readers filter on. This used to hand the
        text to LangChain ``add_texts``, which nests the payload under
        ``metadata`` - a shape no scoped reader can match (DI-B1). LangChain is
        still the embedding model here; it is no longer the payload author.

        A point that is not a canonical, scoped payload is refused with
        ``CorrespondencePayloadError`` before anything is deleted or written.
        """
        if not self._enabled or not self._vector_store:
            return 0

        if not points:
            return 0

        for point in points:
            payload = point.get("payload")
            if not isinstance(payload, dict):
                raise CorrespondencePayloadError(
                    "replace_document takes canonical correspondence points"
                )
            assert_canonical_correspondence_payload(payload)
            if point.get("point_id") != payload.get("qdrant_point_id"):
                raise CorrespondencePayloadError("point_id disagrees with its payload")
        scopes = {
            (p["payload"]["document_id"], p["payload"]["org_id"], p["payload"]["project_id"])
            for p in points
        }
        if len(scopes) != 1:
            raise CorrespondencePayloadError(
                "replace_document writes one document in one scope per call"
            )
        document_id, org_id, project_id = next(iter(scopes))

        try:
            try:
                deleted = await self.delete_document(
                    document_id,
                    organization_id=org_id,
                    project_id=project_id,
                )
                if deleted:
                    logger.debug("Deleted existing vectors for document_id=%s", document_id)
            except Exception as fallback_exc:
                logger.warning("Delete failed for document_id=%s: %s", document_id, fallback_exc)

            writable = [p for p in points if str(p.get("text") or "").strip()]
            if not writable:
                logger.debug("No valid chunks to upsert for document_id=%s", document_id)
                return 0

            embeddings = getattr(self._vector_store, "embeddings", None)
            if embeddings is None:
                raise RuntimeError("vector store exposes no embedding model")
            vectors = await asyncio.to_thread(
                embeddings.embed_documents, [p["text"] for p in writable]
            )
            vector_name = self.config.qdrant_vector_name
            models = self._qdrant_models
            await asyncio.to_thread(
                self._client.upsert,
                collection_name=self.config.qdrant_collection,
                points=[
                    models.PointStruct(
                        id=p["point_id"],
                        vector={vector_name: vector} if vector_name else vector,
                        payload=p["payload"],
                    )
                    for p, vector in zip(writable, vectors)
                ],
                wait=True,
            )

            logger.info(
                "Upserted %s canonical correspondence points to Qdrant (document_id=%s)",
                len(writable),
                document_id,
            )
            return len(writable)

        except Exception as exc:
            logger.warning("Qdrant correspondence upsert failed: %s", exc)
            return 0

    async def similarity_search(
        self,
        query_text: str,
        *,
        org_ids: Sequence[str],
        project_ids: Optional[Sequence[str]] = None,
        upload_type: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Semantic search over canonical correspondence points, tenant-bounded.

        The authority is required and becomes a typed Qdrant filter on the
        canonical flat fields. The old signature took a caller-built ``dict``;
        LangChain rejected it (``'dict' object has no attribute 'must'``), the
        error was swallowed, and every search answered ``[]`` (DI-N2). A
        failing search now raises ``VectorStoreUnavailableError``.
        """
        if not query_text or not self._enabled or not self._vector_store:
            return []
        orgs = [str(o) for o in (org_ids or []) if o]
        if not orgs:
            raise VectorScopeError("correspondence vector search requires an organisation scope")
        models = self._qdrant_models
        must = [models.FieldCondition(key="org_id", match=models.MatchAny(any=orgs))]
        projects = [str(p) for p in (project_ids or []) if p]
        if projects:
            must.append(
                models.FieldCondition(key="project_id", match=models.MatchAny(any=projects))
            )
        if upload_type:
            must.append(
                models.FieldCondition(
                    key="uploadType", match=models.MatchValue(value=str(upload_type).lower())
                )
            )
        vector_name = self.config.qdrant_vector_name

        def _search():
            query_vector = self._vector_store.embeddings.embed_query(query_text)
            return self._client.search(
                collection_name=self.config.qdrant_collection,
                query_vector=(vector_name, query_vector) if vector_name else query_vector,
                query_filter=models.Filter(must=must),
                limit=top_k,
                with_payload=True,
            )

        try:
            hits = await asyncio.to_thread(_search)
        except Exception as exc:
            raise VectorStoreUnavailableError(f"Qdrant search failed: {exc}") from exc

        results: List[Dict[str, Any]] = []
        for hit in hits:
            payload = dict(getattr(hit, "payload", None) or {})
            results.append(
                {
                    "text": str(payload.get("text") or ""),
                    "score": float(hit.score) if hit.score is not None else 0.0,
                    "metadata": payload,
                }
            )
        return results
