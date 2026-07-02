import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, List

from ..config.document_processing_config import DocumentProcessingConfig
from ..models.document_metadata import ParsedDocumentMetadata
from ..utils.date_parser import format_date_ddmmyyyy
from ..utils.exceptions import DocumentProcessingError

logger = logging.getLogger(__name__)

class PydanticAIMetadataError(DocumentProcessingError):
    """Raised when the PydanticAI agent fails to extract metadata."""

@dataclass
class MetadataAgentResult:
    """Container for PydanticAI extraction results."""
    metadata: ParsedDocumentMetadata
    raw_result: Dict[str, Any]
    debug: Dict[str, Any]

class PydanticAIService:
    """Wrapper around a PydanticAI agent for metadata extraction."""

    _SYSTEM_PROMPT = (
        "You are an expert contract analyst. Return the requested metadata as structured data. "
        "Only rely on the provided document text. If a field is not explicitly present, return null. "
        "When returning any date, format it as DD-MM-YYYY."
    )

    _MAX_DOCUMENT_CHARS = 12000

    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self._enabled = bool(getattr(config, "use_pydantic_ai", False))
        self._agent = None
        self._metadata_model = None
        self._agent_exceptions: tuple[type[Exception], ...] = tuple()

        if not self._enabled:
            return

        api_key = self._get_api_key()
        if not api_key:
            logger.warning("PydanticAI requested but OPENAI_API_KEY is not configured; disabling agent")
            self._enabled = False
            return

        # Set the API key in environment for pydantic-ai
        os.environ["OPENAI_API_KEY"] = api_key

        try:
            from pydantic import BaseModel, Field, ConfigDict
            from pydantic_ai import Agent, AgentRunError, UnexpectedModelBehavior, UserError
            from pydantic_ai.models.openai import OpenAIModel
            from pydantic_ai.settings import ModelSettings
        except ImportError as exc:
            # Handle missing optional native dependency from griffe specifically
            missing = getattr(exc, "name", None)
            if isinstance(exc, ModuleNotFoundError) and missing == "_griffe":
                logger.warning(
                    "PydanticAI optional dependency missing: '_griffe' (from 'griffe'). "
                    "Disabling PydanticAI and falling back to legacy parsing. "
                    "To enable PydanticAI, install a compatible build of griffe or skip this by setting PYDANTIC_AI_ENABLED=false."
                )
            else:
                logger.warning("PydanticAI library not available (%s); falling back to legacy parsing", exc)
            self._enabled = False
            return

        class LetterRef(BaseModel):
            letter_no: str = Field(alias="letterNo")
            date: str

            model_config = ConfigDict(populate_by_name=True)

        class DocumentMetadataModel(BaseModel):
            date: Optional[str] = None
            subject: Optional[str] = None
            letter_no: Optional[str] = Field(default=None, alias="letterNo")
            from_company: Optional[str] = Field(default=None, alias="fromCompany")
            to_company: Optional[str] = Field(default=None, alias="toCompany")
            references: List[LetterRef] = Field(default_factory=list)
            summary_points: List[str] = Field(default_factory=list)
            summary_text: Optional[str] = Field(default=None, alias="summary")
            keywords: List[str] = Field(default_factory=list)
            contractual_clauses: List[str] = Field(default_factory=list, alias="clauses")
            key_reply_points: List[str] = Field(default_factory=list, alias="keyReplyPoints")
            full_content: Optional[str] = None

            model_config = ConfigDict(populate_by_name=True, extra="ignore")

        model_name = config.pydantic_ai_model or config.openai_model
        model_settings: ModelSettings = {
            "temperature": 0.0,
            "max_tokens": min(config.max_output_tokens or 2048, 2048),
            "timeout": config.openai_timeout,
        }

        try:
            # OpenAIModel reads API key from environment by default
            openai_model = OpenAIModel(model_name=model_name)
            self._agent = Agent(
                model=openai_model,
                output_type=DocumentMetadataModel,  # FIXED: Changed from result_type to output_type
                system_prompt=self._SYSTEM_PROMPT,
                name="pydantic_metadata_agent",
                model_settings=model_settings,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to initialize PydanticAI agent: %s", exc, exc_info=True)
            self._enabled = False
            return

        self._metadata_model = DocumentMetadataModel
        self._letter_ref_model = LetterRef  # Store reference for conversion
        self._agent_exceptions = (AgentRunError, UnexpectedModelBehavior, UserError)

    def _get_api_key(self) -> Optional[str]:
        """Get OpenAI API key from settings or environment"""
        return self.config.openai_api_key

    @property
    def is_enabled(self) -> bool:
        return self._enabled and self._agent is not None and self._metadata_model is not None

    async def extract_metadata(
        self,
        document_text: str,
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[MetadataAgentResult]:
        """Extract structured metadata using the PydanticAI agent."""
        if not self.is_enabled:
            logger.info("PydanticAIService disabled; skipping metadata extraction")
            return None

        if not document_text or not document_text.strip():
            return None

        assert self._agent is not None  # for mypy
        assert self._metadata_model is not None

        trimmed_text = document_text.strip()
        logger.info("PydanticAIService extracting metadata (text length=%d)", len(trimmed_text))

        if len(trimmed_text) > self._MAX_DOCUMENT_CHARS:
            trimmed_text = trimmed_text[: self._MAX_DOCUMENT_CHARS]

        prompt = self._build_prompt(trimmed_text, context or {})

        try:
            run_result = await self._agent.run(prompt)
        except self._agent_exceptions as exc:
            logger.error("PydanticAI agent run failed: %s", exc, exc_info=True)
            raise PydanticAIMetadataError(str(exc)) from exc
        except Exception as exc:  # pragma: no cover - guard against unknown issues
            logger.error("Unexpected error during PydanticAI agent run: %s", exc, exc_info=True)
            raise PydanticAIMetadataError(str(exc)) from exc

        # FIXED: Use run_result.output instead of run_result.data for v1.0+
        model_result = run_result.output
        metadata = self._to_parsed_metadata(model_result, fallback_text=trimmed_text)

        usage = run_result.usage()
        logger.info("PydanticAIService extraction complete (total tokens=%s)", usage.total_tokens)

        debug: Dict[str, Any] = {
            "usage": {
                "requests": usage.requests,
                "request_tokens": usage.request_tokens,
                "response_tokens": usage.response_tokens,
                "total_tokens": usage.total_tokens,
            }
        }

        try:
            debug["messages"] = run_result.new_messages_json().decode("utf-8")
        except Exception:  # pragma: no cover - best effort only
            debug["messages"] = None

        raw_dict = model_result.model_dump(by_alias=True)
        return MetadataAgentResult(metadata=metadata, raw_result=raw_dict, debug=debug)

    def _build_prompt(self, text: str, context: Dict[str, Any]) -> str:
        parts = [
            "Extract the following metadata fields from the contract letter:",
            "- date [dd-mm-yyyy]",
            "- subject",
            "- letter number",
            "- sender company",
            "- recipient company",
            "- references - List all referenced letters/documents with dates",
            "- summary - Write a 4-6 line Contractual/legal summary/Fact of the matter suitable for vector search/RAG.",
            "- keywords - keywords, Tags,Topic,Claim Type",
            "- contractual clauses - What clauses, Employer’s Requirements, GCC/SCC provisions, specifications, drawings, approved proposals, or prior records are relied upon",
            "- key reply points - concise contractual/legal points that must be addressed in a future reply, claim defence, Statement of Defence, rejoinder, variation/payment dispute, or delay response",
            "- cleaned full content if feasible",
            "Return null for any field that is absent.",
            "Format all dates as DD-MM-YYYY (example: 07-03-2025).",
        ]

        if context:
            context_lines = [f"{key}: {value}" for key, value in context.items() if value]
            if context_lines:
                parts.append("Context:")
                parts.extend(context_lines)

        parts.append("\nDocument text:\n" + text)
        return "\n".join(parts)

    def _to_parsed_metadata(self, data: Any, fallback_text: str) -> ParsedDocumentMetadata:
        """Convert PydanticAI result to ParsedDocumentMetadata with proper serialization."""
        summary_lines = getattr(data, "summary_points", None) or []
        summary = None
        
        if summary_lines:
            bullet_lines = [line.strip() for line in summary_lines if line and line.strip()]
            summary = "\n".join(f"- {line}" for line in bullet_lines)
        elif getattr(data, "summary_text", None):
            summary = data.summary_text.strip() or None

        keywords = getattr(data, "keywords", None) or []
        
        # FIXED: Convert LetterRef objects to dictionaries for MongoDB compatibility
        references = getattr(data, "references", None) or []
        serialized_references = []
        
        if references:
            for ref in references:
                if hasattr(ref, 'model_dump'):
                    # Pydantic v2 style
                    serialized_references.append(ref.model_dump())
                elif hasattr(ref, 'dict'):
                    # Pydantic v1 style (fallback)
                    serialized_references.append(ref.dict())
                elif isinstance(ref, dict):
                    # Already a dictionary
                    serialized_references.append(ref)
                else:
                    # Convert manually if needed
                    try:
                        serialized_references.append({
                            "letter_no": getattr(ref, 'letter_no', '') or getattr(ref, 'letterNo', ''),
                            "date": getattr(ref, 'date', '')
                        })
                    except Exception as e:
                        logger.warning(f"Failed to serialize reference {ref}: {e}")
                        continue
        
        clauses = getattr(data, "contractual_clauses", None) or []
        key_reply_points = getattr(data, "key_reply_points", None) or []
        full_content = getattr(data, "full_content", None) or fallback_text

        formatted_date = format_date_ddmmyyyy(getattr(data, "date", None))

        return ParsedDocumentMetadata(
            date=formatted_date,
            subject=getattr(data, "subject", None),
            letter_no=getattr(data, "letter_no", None),
            from_company=getattr(data, "from_company", None),
            to_company=getattr(data, "to_company", None),
            references=serialized_references,  # Now properly serialized as dicts
            summary=summary,
            keywords=keywords,
            contractual_clauses=clauses,
            key_reply_points=key_reply_points,
            full_content=full_content,
        )
