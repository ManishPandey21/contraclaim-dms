from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from ..config.document_processing_config import DocumentProcessingConfig

logger = logging.getLogger(__name__)

# Canonical degraded-output text. Exposed so callers can recognise a fallback
# answer instead of mistaking it for model output (see LLMUnavailableError).
FALLBACK_ANSWER = (
    "Answer unavailable: the language model could not generate a grounded "
    "response from the retrieved contract context. Please retry when the AI "
    "service is available; do not treat this as legal or contractual advice."
)


class LLMUnavailableError(RuntimeError):
    """Raised in strict mode when the configured LLM cannot produce output.

    Strict callers (letter drafting, drafting pipelines) must supply their own
    deterministic fallback and mark the run degraded; returning the canned
    fallback string to them would let an outage message masquerade as a draft.
    """


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

    @property
    def available(self) -> bool:
        """True when a live LLM client is configured; False in offline fallback mode."""
        return self._client is not None

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        model: Optional[str] = None,
        *,
        strict: bool = False,
    ) -> str:
        """Generate a completion.

        ``strict=False`` (default) preserves the historical contract: any
        failure returns the canned :data:`FALLBACK_ANSWER` text. ``strict=True``
        raises :class:`LLMUnavailableError` instead, so the caller's own
        deterministic fallback runs and the degradation stays visible.
        """
        resolved_model = model or self._model
        if self._client:
            try:
                completion = await self._client.chat.completions.create(
                    model=resolved_model,
                    messages=[
                        {"role": "system", "content": "You are a precise contract assistant. Keep responses grounded in provided context."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens,
                    temperature=0.2,
                )
                content = completion.choices[0].message.content
                self._record_outcome(resolved_model, "success")
                return content or ""
            except Exception as exc:  # pragma: no cover - external call
                logger.warning("LLM generation failed%s: %s", " (strict)" if strict else ", using fallback", exc)
                self._record_outcome(resolved_model, "failed")
                if strict:
                    raise LLMUnavailableError(f"LLM generation failed: {exc}") from exc
                return self._fallback(prompt)
        self._record_outcome(resolved_model, "offline")
        if strict:
            raise LLMUnavailableError("LLM is not configured (offline mode)")
        return self._fallback(prompt)

    def _record_outcome(self, model: str, outcome: str) -> None:
        """Fire-and-forget LLM call telemetry; never fails the generation path."""
        try:
            from ..services.observability import observability_registry

            loop = asyncio.get_running_loop()
            loop.create_task(observability_registry.record_llm_call(model=model, outcome=outcome))
        except RuntimeError:
            pass  # no running loop (sync/test context) — the log line still lands
        except Exception:  # pragma: no cover - defensive
            logger.debug("Failed to record LLM call outcome", exc_info=True)

    def _fallback(self, prompt: str) -> str:
        return FALLBACK_ANSWER
