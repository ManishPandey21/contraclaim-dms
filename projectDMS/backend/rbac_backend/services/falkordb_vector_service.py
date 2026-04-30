import logging
from typing import Any, Dict, List, Optional

from redis import Redis
from redis.commands.search.field import VectorField, TagField, TextField
from redis.commands.search.index_definition import IndexDefinition, IndexType

try:
    from ..config.document_processing_config import DocumentProcessingConfig
except ImportError:  # pragma: no cover - script compatibility
    from config.document_processing_config import DocumentProcessingConfig

logger = logging.getLogger(__name__)

class FalkorDBVectorService:
    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self.client = Redis.from_url(self.config.falkordb_url)
        self.index_name = self.config.falkordb_index_name

    def _create_index(self):
        try:
            self.client.ft(self.index_name).info()
            logger.info("FalkorDB index already exists: %s", self.index_name)
        except Exception:
            logger.info("Creating FalkorDB index: %s", self.index_name)
            schema = (
                TextField("text"),
                VectorField(
                    "vector",
                    "FLAT",
                    {
                        "TYPE": "FLOAT32",
                        "DIM": self.config.falkordb_vector_dim,
                        "DISTANCE_METRIC": "COSINE",
                    },
                ),
                TagField("document_id"),
            )
            definition = IndexDefinition(prefix=[f"{self.index_name}:"], index_type=IndexType.HASH)
            self.client.ft(self.index_name).create_index(fields=schema, definition=definition)

    async def save_data(self, payloads: List[Dict[str, Any]]):
        if not payloads:
            return 0

        try:
            self._create_index()
            saved = 0
            for payload in payloads:
                metadata = dict(payload.get("metadata") or {})
                document_id = metadata.get("document_id")
                chunk_index = metadata.get("chunk_index")
                vector_bytes = self._vector_to_bytes(payload.get("vector"))

                if not document_id or chunk_index is None:
                    logger.warning("Skipping FalkorDB payload with incomplete metadata: %s", metadata)
                    continue
                if vector_bytes is None:
                    logger.warning(
                        "Skipping FalkorDB payload without serializable vector for document_id=%s chunk_index=%s",
                        document_id,
                        chunk_index,
                    )
                    continue

                key = f"{self.index_name}:{document_id}:{chunk_index}"
                self.client.hset(
                    key,
                    mapping={
                        "text": payload.get("text", ""),
                        "vector": vector_bytes,
                        "document_id": str(document_id),
                    },
                )
                saved += 1
            logger.info("Saved %s documents to FalkorDB", saved)
            return saved
        except Exception as exc:
            logger.error("Failed to save data to FalkorDB: %s", exc)
            return 0

    @staticmethod
    def _vector_to_bytes(vector: Optional[Any]) -> Optional[bytes]:
        if vector is None:
            return None
        if isinstance(vector, (bytes, bytearray, memoryview)):
            return bytes(vector)
        if hasattr(vector, "tobytes"):
            return vector.tobytes()
        return None
