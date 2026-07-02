from __future__ import annotations

import logging
from typing import List, Optional

from ..config.document_processing_config import DocumentProcessingConfig

logger = logging.getLogger(__name__)


class LLMGenerator:
    """Small wrapper for answer drafting with an offline fallback."""

    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self._client = None
        self._model = config.openai_model
        self._initialize()

    def _initialize(self) -> None:
        if not self.config.openai_api_key:
            logger.info("LLM generator running in offline mode (no API key)")
            return
        try:
            from openai import AsyncOpenAI  # type: ignore

            self._client = AsyncOpenAI(api_key=self.config.openai_api_key, timeout=self.config.openai_timeout)
        except Exception as exc:  # pragma: no cover - best-effort init
            logger.warning("Failed to initialize AsyncOpenAI client: %s", exc)
            self._client = None

    async def generate(self, prompt: str, max_tokens: int = 512, model: Optional[str] = None) -> str:
        if self._client:
            try:
                completion = await self._client.chat.completions.create(
                    model=model or self._model,
                    messages=[
                        {"role": "system", "content": "You are a precise contract assistant. Keep responses grounded in provided context."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=0.2,
                )
                content = completion.choices[0].message.content
                return content or ""
            except Exception as exc:  # pragma: no cover - external call
                logger.warning("LLM generation failed, using fallback: %s", exc)
        return self._fallback(prompt)

    def _fallback(self, prompt: str) -> str:
        return (
            "Answer unavailable: the language model could not generate a grounded "
            "response from the retrieved contract context. Please retry when the AI "
            "service is available; do not treat this as legal or contractual advice."
        )
