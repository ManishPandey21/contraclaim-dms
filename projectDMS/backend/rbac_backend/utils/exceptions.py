"""Custom exceptions for document processing"""
from typing import Optional

class DocumentProcessingError(Exception):
    """Custom exception for document processing errors"""
    pass


#: The provider stopped because it hit the configured output-token limit.
TRUNCATION_REASON_MAX_OUTPUT_TOKENS = "max_output_tokens"


class ModelOutputIncompleteError(DocumentProcessingError):
    """A model reply ended before it was complete (token cap, content filter...).

    The one internal representation of "model output truncated / incomplete"
    for every extraction path (Chat Completions ``finish_reason``, Responses
    ``status == "incomplete"``, PydanticAI ``finish_reason``).

    The message and :meth:`failure_record` carry only safe operational
    metadata: provider, stage, reason and the configured limit. The partial
    reply is kept on ``partial_output`` for a caller that can salvage the
    items it finished, and never appears in ``str()``, ``repr()`` or the
    failure record - it is document-derived text.
    """

    def __init__(
        self,
        *,
        provider: str,
        stage: str,
        reason: str,
        max_output_tokens: Optional[int] = None,
        partial_output: Optional[str] = None,
    ) -> None:
        self.provider = provider
        self.stage = stage
        self.reason = reason
        self.max_output_tokens = max_output_tokens
        self.partial_output = partial_output
        super().__init__(
            f"model output incomplete (provider={provider}, stage={stage}, "
            f"reason={reason}, max_output_tokens={max_output_tokens})"
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self})"

    @property
    def truncated(self) -> bool:
        return self.reason == TRUNCATION_REASON_MAX_OUTPUT_TOKENS

    def failure_record(self) -> dict:
        """A ``partial_failures`` entry. Safe fields only - no document text."""
        return {
            "stage": self.stage,
            "message": str(self),
            "provider": self.provider,
            "reason": self.reason,
            "truncated": self.truncated,
            "incomplete": True,
            "max_output_tokens": self.max_output_tokens,
        }
