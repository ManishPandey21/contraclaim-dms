from __future__ import annotations

import hashlib
import logging
from typing import List, Optional

from ..config.document_processing_config import DocumentProcessingConfig

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """Wrapper around OpenAI embeddings with a deterministic fallback."""

    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self._client = None
        self._model = config.openai_embedding_model
        self._initialize()

    def _initialize(self) -> None:
        if not self.config.openai_api_key:
            logger.info("Embedding client running in offline mode (no API key set)")
            return
        try:
            from openai import AsyncOpenAI  # type: ignore

            self._client = AsyncOpenAI(
                api_key=self.config.openai_api_key, timeout=self.config.openai_timeout
            )
        except Exception as exc:  # pragma: no cover - best-effort import
            logger.warning("Failed to initialize AsyncOpenAI client: %s", exc)
            self._client = None

    async def embed(
        self, texts: List[str], model: Optional[str] = None
    ) -> List[List[float]]:
        if not texts:
            return []

        chosen_model = model or self._model
        if self._client:
            try:
                response = await self._client.embeddings.create(
                    model=chosen_model, input=texts
                )
                return [item.embedding for item in response.data]
            except Exception as exc:  # pragma: no cover - external service
                logger.warning(
                    "Embedding call failed, falling back to deterministic embedding: %s",
                    exc,
                )

        # Deterministic fallback to keep tests/local dev working without network
        return [self._fake_embedding(text) for text in texts]

    def _fake_embedding(self, text: str, dims: int = 64) -> List[float]:
        digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).digest()
        values = list(digest[:dims])
        # map to [-1, 1]
        return [((v - 128) / 128.0) for v in values]
