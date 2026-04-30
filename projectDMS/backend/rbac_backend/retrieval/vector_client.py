from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional

from ..config.document_processing_config import DocumentProcessingConfig

logger = logging.getLogger(__name__)


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class VectorClient:
    """Small wrapper to write/read vectors from Qdrant with an in-memory fallback."""

    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self._client = None
        self._qmodels = None
        self.enabled = False
        self._memory_index: List[Dict[str, Any]] = []
        self.collection_name: Optional[str] = self.config.qdrant_collection
        self._initialize()

    def _initialize(self) -> None:
        if not self.config.qdrant_url:
            logger.info("Vector client offline: QDRANT_URL not configured")
            return
        try:
            from qdrant_client import QdrantClient  # type: ignore
            from qdrant_client.http import models as qm  # type: ignore

            self._qmodels = qm
            self._client = QdrantClient(
                url=self.config.qdrant_url,
                api_key=self.config.qdrant_api_key,
                timeout=self.config.qdrant_timeout,
            )
            self.enabled = True
        except Exception as exc:  # pragma: no cover - best-effort init
            logger.warning("Qdrant client unavailable; using in-memory index: %s", exc)
            self._client = None
            self.enabled = False

    def _ensure_collection(self, namespace: Optional[str] = None) -> str:
        """
        Ensure a collection exists with the configured vector size/distance.
        If an existing collection has a mismatched dimension, fall back to a
        suffix-based collection name to avoid repeated 400 errors.
        """
        base_collection = namespace or self.collection_name or self.config.qdrant_collection
        desired_size = self.config.qdrant_vector_size
        desired_distance = getattr(self._qmodels.Distance, str(self.config.qdrant_distance).upper(), None) if self._qmodels else None
        desired_distance = desired_distance or (self._qmodels.Distance.COSINE if self._qmodels else None)

        if not (self.enabled and self._client and self._qmodels):
            self.collection_name = base_collection
            return base_collection

        def _extract_vectors(info: Any) -> tuple[Optional[int], Optional[str], list[str]]:
            """
            Extract vector size and available vector names from a Qdrant collection info response.
            Handles both legacy and newer response shapes.
            """
            vectors_conf = None
            config = getattr(info, "config", None)
            params = getattr(config, "params", None)
            if params is not None and getattr(params, "vectors", None) is not None:
                vectors_conf = getattr(params, "vectors")
            if vectors_conf is None:
                vectors_conf = getattr(info, "vectors", None)

            available: list[str] = []
            chosen_name = self.config.qdrant_vector_name
            size: Optional[int] = None

            def _size_from_params(params_obj: Any) -> Optional[int]:
                try:
                    val = getattr(params_obj, "size", None)
                    return int(val) if val is not None else None
                except Exception:
                    return None

            if vectors_conf is None:
                return size, chosen_name, available

            # Named vectors (VectorParamsMap or plain dict)
            root = getattr(vectors_conf, "__root__", None)
            if isinstance(root, dict):
                vectors_conf = root
            if isinstance(vectors_conf, dict):
                available = list(vectors_conf.keys())
                if chosen_name and chosen_name in vectors_conf:
                    size = _size_from_params(vectors_conf[chosen_name])
                else:
                    chosen_name = chosen_name or (available[0] if available else None)
                    params_obj = vectors_conf.get(chosen_name) if chosen_name else None
                    size = _size_from_params(params_obj) if params_obj is not None else None
                return size, chosen_name, available

            # Single unnamed vector
            size = _size_from_params(vectors_conf)
            return size, chosen_name, available

        def _create_collection(name: str) -> None:
            vec_params = self._qmodels.VectorParams(size=desired_size, distance=desired_distance)
            vectors_config = {self.config.qdrant_vector_name: vec_params} if self.config.qdrant_vector_name else vec_params
            try:
                self._client.create_collection(
                    collection_name=name,
                    vectors_config=vectors_config,
                )
                logger.info("Created Qdrant collection %s (dim=%s)", name, desired_size)
            except Exception as exc:
                logger.warning("Failed to create Qdrant collection %s: %s", name, exc)

        collection_to_use = base_collection
        try:
            info = self._client.get_collection(collection_to_use)
            size, detected_name, available = _extract_vectors(info)
            if detected_name and not self.config.qdrant_vector_name:
                self.config.qdrant_vector_name = detected_name
            elif self.config.qdrant_vector_name and available and self.config.qdrant_vector_name not in available:
                logger.warning(
                    "Vector '%s' not found in collection '%s'; available=%s. Falling back to first vector.",
                    self.config.qdrant_vector_name,
                    collection_to_use,
                    available,
                )
                self.config.qdrant_vector_name = available[0]

            if size is None:
                logger.info(
                    "Qdrant collection %s exists but vector size is unknown; assuming compatible with desired dim %s",
                    collection_to_use,
                    desired_size,
                )
                self.collection_name = collection_to_use
                return collection_to_use

            if desired_size and int(size) == int(desired_size):
                self.collection_name = collection_to_use
                return collection_to_use
            # Dimension mismatch: fall back to suffixed collection
            fallback = f"{collection_to_use}_dim{desired_size}"
            logger.warning(
                "Qdrant collection %s dimension mismatch (have=%s, want=%s); using %s",
                collection_to_use,
                size,
                desired_size,
                fallback,
            )
            _create_collection(fallback)
            self.collection_name = fallback
            self.config.qdrant_collection = fallback
            return fallback
        except Exception:
            # Collection likely missing; try to create it
            _create_collection(collection_to_use)
            self.collection_name = collection_to_use
            return collection_to_use

    def is_healthy(self) -> bool:
        if not (self.enabled and self._client):
            return False
        try:
            # Lightweight call to validate collection exists
            self._client.get_collection(self.config.qdrant_collection)
            return True
        except Exception:
            return False

    async def upsert(
        self,
        vectors: List[List[float]],
        chunks: List[Dict[str, Any]],
        namespace: Optional[str] = None,
    ) -> int:
        if not chunks or not vectors:
            return 0
        collection = self._ensure_collection(namespace)
        payloads: List[Dict[str, Any]] = []
        for vector, chunk in zip(vectors, chunks):
            payload = {
                "org_id": chunk.get("org_id"),
                "project_id": chunk.get("project_id"),
                "document_id": chunk.get("document_id"),
                "chunk_id": chunk.get("chunk_id"),
                "page": chunk.get("page_start"),
                "text": chunk.get("text"),
                "text_enriched": chunk.get("text_enriched"),
                "tags": chunk.get("tags", []),
                "embedding_provider": chunk.get("embedding_provider"),
                "embedding_model": chunk.get("embedding_model"),
                "embedding_dim": chunk.get("embedding_dim"),
                "embedding_version": chunk.get("embedding_version"),
                "chunking_version": chunk.get("chunking_version"),
            }
            extra_payload = chunk.get("payload") or chunk.get("metadata") or {}
            if isinstance(extra_payload, dict):
                for key, value in extra_payload.items():
                    if key in payload or value is None:
                        continue
                    payload[key] = value
            payloads.append(payload)
            self._memory_index.append(
                {"vector": vector, "payload": payload, "namespace": namespace or self.config.qdrant_collection}
            )

        if self.enabled and self._client and self._qmodels:
            points = []
            for vector, payload in zip(vectors, payloads):
                point_id = payload["chunk_id"]
                point = self._qmodels.PointStruct(
                    id=point_id,
                    vector={self.config.qdrant_vector_name: vector}
                    if self.config.qdrant_vector_name
                    else vector,
                    payload=payload,
                )
                points.append(point)
            await asyncio.to_thread(
                self._client.upsert,
                collection_name=collection,
                points=points,
                wait=True,
            )
        return len(payloads)

    async def delete(self, chunk_ids: List[str], namespace: Optional[str] = None) -> int:
        if not chunk_ids:
            return 0
        collection = namespace or self.collection_name or self.config.qdrant_collection
        removed = 0
        if self.enabled and self._client and self._qmodels:
            try:
                await asyncio.to_thread(
                    self._client.delete,
                    collection_name=collection,
                    points_selector=self._qmodels.PointIdsList(points=chunk_ids),
                    wait=True,
                )
                removed = len(chunk_ids)
            except Exception as exc:  # pragma: no cover - external dependency
                logger.warning("Failed to delete points from Qdrant: %s", exc)

        before = len(self._memory_index)
        self._memory_index = [entry for entry in self._memory_index if entry["payload"].get("chunk_id") not in chunk_ids]
        removed = max(removed, before - len(self._memory_index))
        return removed

    async def list_chunk_ids(self, filters: Dict[str, Any], namespace: Optional[str] = None, limit: int = 1000) -> List[str]:
        collection = namespace or self.collection_name or self.config.qdrant_collection
        if self.enabled and self._client and self._qmodels:
            qfilter = self._build_filter(filters)
            try:
                res, _ = await asyncio.to_thread(
                    self._client.scroll,
                    collection_name=collection,
                    scroll_filter=qfilter,
                    limit=limit,
                    with_payload=False,
                    with_vectors=False,
                )
                return [str(point.id) for point in res]
            except Exception as exc:  # pragma: no cover
                logger.warning("Qdrant scroll failed, falling back to in-memory: %s", exc)
        ids: List[str] = []
        for entry in self._memory_index:
            if entry.get("namespace") != collection:
                continue
            payload = entry.get("payload", {})
            if payload.get("org_id") != filters.get("org_id") or payload.get("project_id") != filters.get("project_id"):
                continue
            ids.append(str(payload.get("chunk_id")))
        return ids

    async def search(
        self,
        query_vector: List[float],
        filters: Dict[str, Any],
        limit: int = 5,
        namespace: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not query_vector:
            return []
        collection = namespace or self.collection_name or self.config.qdrant_collection
        if self.enabled and self._client and self._qmodels:
            qfilter = self._build_filter(filters)
            try:
                # Qdrant 1.x ships `search_points`; older releases expose `search`.
                # Prefer the new API when available to avoid AttributeErrors.
                search_fn = getattr(self._client, "search_points", None) or getattr(self._client, "search", None)
                if not search_fn:
                    raise AttributeError("Qdrant client missing search/search_points")
                results = await asyncio.to_thread(
                    search_fn,
                    collection_name=collection,
                    query_vector={self.config.qdrant_vector_name: query_vector}
                    if self.config.qdrant_vector_name
                    else query_vector,
                    query_filter=qfilter,
                    limit=limit,
                )
                return [
                    {
                        "score": item.score,
                        "payload": item.payload,
                        "id": getattr(item, "id", None),
                    }
                    for item in results
                ]
            except Exception as exc:  # pragma: no cover - network interaction
                logger.warning("Vector search failed; falling back to in-memory index: %s", exc)

        candidates = [
            entry
            for entry in self._memory_index
            if entry.get("namespace") == collection
            and entry["payload"].get("org_id") == filters.get("org_id")
            and entry["payload"].get("project_id") == filters.get("project_id")
        ]
        scored = [
            {
                "score": _cosine(query_vector, entry["vector"]),
                "payload": entry["payload"],
                "id": entry["payload"].get("chunk_id"),
            }
            for entry in candidates
        ]
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    def _build_filter(self, filters: Dict[str, Any]):
        if not self._qmodels:
            return None
        must = []
        for key, value in filters.items():
            if value is None:
                continue
            if key == "tags":
                if isinstance(value, list) and value:
                    must.append(self._qmodels.FieldCondition(key=key, match=self._qmodels.MatchAny(any=value)))
                elif value:
                    must.append(self._qmodels.FieldCondition(key=key, match=self._qmodels.MatchValue(value=value)))
                continue
            if isinstance(value, list):
                if value:
                    must.append(self._qmodels.FieldCondition(key=key, match=self._qmodels.MatchAny(any=value)))
                continue
            must.append(self._qmodels.FieldCondition(key=key, match=self._qmodels.MatchValue(value=value)))
        if not must:
            return None
        return self._qmodels.Filter(must=must)
