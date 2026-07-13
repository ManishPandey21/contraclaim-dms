# config/document_processing_config.py

import logging
import os

from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Any, Optional

try:
    from ..models.document_metadata import ParsedDocumentMetadata, ProcessingResult
except ImportError:  # pragma: no cover - top-level script compatibility
    from models.document_metadata import ParsedDocumentMetadata, ProcessingResult


logger = logging.getLogger(__name__)

DEFAULT_MONGO_URI = "mongodb://localhost:27017/contraclaim"
DEFAULT_FALKORDB_HOST = "localhost"
DEFAULT_FALKORDB_PORT = 6380
DEFAULT_FALKORDB_URL_HOSTS = {"localhost", "127.0.0.1"}
DEFAULT_FALKORDB_URLS = {
    f"redis://{host}:{DEFAULT_FALKORDB_PORT}" for host in DEFAULT_FALKORDB_URL_HOSTS
}
LOCAL_QDRANT_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    # Docker Compose service DNS used by production for private-network Qdrant.
    "qdrant",
}
QDRANT_API_KEY_PLACEHOLDERS = {
    "none",
    "null",
    "changeme",
    "change-me",
    "dummy",
    "placeholder",
    "your-qdrant-api-key",
    "replace-with-qdrant-api-key",
}


def _parse_bool(raw_value: str) -> bool:
    return raw_value.lower() in ("1", "true", "yes", "on")


def _coerce_int(raw_value: Any, setting_name: str, current_value: int) -> int:
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        logger.warning(
            "Ignoring invalid %s=%r; using %s",
            setting_name,
            raw_value,
            current_value,
        )
        return current_value


def _coerce_float(raw_value: Any, setting_name: str, current_value: float) -> float:
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        logger.warning(
            "Ignoring invalid %s=%r; using %s",
            setting_name,
            raw_value,
            current_value,
        )
        return current_value


def _normalize_falkordb_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if normalized.startswith("="):
        normalized = normalized.lstrip("=").strip()
    return normalized or None


def _normalize_qdrant_api_key(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().strip('"').strip("'")
    if normalized.startswith("="):
        normalized = normalized.lstrip("=").strip()
    if not normalized or normalized.lower() in QDRANT_API_KEY_PLACEHOLDERS:
        return None
    return normalized


def _load_project_settings():
    try:
        from ..core.config import settings as project_settings

        return project_settings
    except ImportError:
        try:
            from core.config import settings as project_settings

            return project_settings
        except ImportError:
            return None

@dataclass
class DocumentProcessingConfig:
    """Configuration for document processing services"""

    # OpenAI Settings
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4o"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_timeout: float = 60.0
    max_output_tokens: int = 4096
    ai_enabled: bool = True

    # OCR Settings
    ocr_language: str = "eng"
    ocr_enabled: bool = True
    contract_ocr_batch_size: int = 25
    contract_ocr_min_text_chars_per_page: int = 40
    contract_text_cleaning_enabled: bool = True
    contract_ai_chunking_enabled: bool = False
    contract_ai_chunking_min_confidence: float = 0.70

    # File Processing
    uploads_dir: str = "uploads"
    process_dir: str = "uploads/process_file"
    max_file_size_mb: int = 100

    # Text Processing
    chunk_size: int = 3000
    chunk_overlap: int = 200

    # Contract parsing (Marker + clause extraction)
    marker_enabled: bool = True
    marker_cmd: Optional[str] = None
    marker_output_dir: Optional[str] = None
    clause_extraction_enabled: bool = True
    clause_extraction_model: Optional[str] = None
    clause_extraction_max_chars: int = 60000

    # Database / Vector Store Settings
    mongo_uri: Optional[str] = None
    database_name: str = "contraclaim"
    vector_store_enabled: bool = True
    vector_store_collection: str = "document_vectors"
    use_pydantic_ai: bool = False
    pydantic_ai_model: Optional[str] = None

    qdrant_url: Optional[str] = None
    qdrant_api_key: Optional[str] = None
    qdrant_collection: str = "document_vectors"
    qdrant_vector_size: int = 1536
    qdrant_distance: str = "cosine"
    qdrant_vector_name: Optional[str] = None
    qdrant_timeout: float = 15.0

    dual_vector_write: bool = True
    vector_dual_write_enabled: bool = True
    vector_verify_after_write: bool = False

    falkordb_enabled: bool = True
    falkordb_host: str = DEFAULT_FALKORDB_HOST
    falkordb_port: int = DEFAULT_FALKORDB_PORT
    falkordb_password: Optional[str] = None
    # NOTE: falkordb_url is now a @property only - removed field declaration to avoid conflicts
    falkordb_graph_name: str = "contraclaim"
    falkordb_index_name: str = "document_vectors"
    falkordb_vector_dim: int = 1536
    
    _falkordb_url_override: Optional[str] = None

    def __post_init__(self):
        """Initialize configuration from environment if not provided"""

        if not self.openai_api_key:
            self.openai_api_key = os.getenv("OPENAI_API_KEY")

        contract_ocr_batch_env = os.getenv("CONTRACT_OCR_BATCH_SIZE")
        if contract_ocr_batch_env:
            self.contract_ocr_batch_size = max(
                1,
                _coerce_int(
                    contract_ocr_batch_env,
                    "CONTRACT_OCR_BATCH_SIZE",
                    self.contract_ocr_batch_size,
                ),
            )

        contract_ocr_min_env = os.getenv("CONTRACT_OCR_MIN_TEXT_CHARS_PER_PAGE")
        if contract_ocr_min_env:
            self.contract_ocr_min_text_chars_per_page = max(
                0,
                _coerce_int(
                    contract_ocr_min_env,
                    "CONTRACT_OCR_MIN_TEXT_CHARS_PER_PAGE",
                    self.contract_ocr_min_text_chars_per_page,
                ),
            )

        text_cleaning_env = os.getenv("CONTRACT_TEXT_CLEANING_ENABLED")
        if text_cleaning_env is not None:
            self.contract_text_cleaning_enabled = _parse_bool(text_cleaning_env)

        ai_chunking_env = os.getenv("CONTRACT_AI_CHUNKING_ENABLED")
        if ai_chunking_env is not None:
            self.contract_ai_chunking_enabled = _parse_bool(ai_chunking_env)

        ai_chunking_conf_env = os.getenv("CONTRACT_AI_CHUNKING_MIN_CONFIDENCE")
        if ai_chunking_conf_env:
            self.contract_ai_chunking_min_confidence = max(
                0.0,
                min(
                    1.0,
                    _coerce_float(
                        ai_chunking_conf_env,
                        "CONTRACT_AI_CHUNKING_MIN_CONFIDENCE",
                        self.contract_ai_chunking_min_confidence,
                    ),
                ),
            )

        marker_enabled_env = os.getenv("MARKER_ENABLED")
        if marker_enabled_env is not None:
            self.marker_enabled = _parse_bool(marker_enabled_env)

        marker_cmd_env = os.getenv("MARKER_CMD")
        if marker_cmd_env:
            self.marker_cmd = marker_cmd_env

        marker_output_env = os.getenv("MARKER_OUTPUT_DIR")
        if marker_output_env:
            self.marker_output_dir = marker_output_env

        clause_enabled_env = os.getenv("CLAUSE_EXTRACTION_ENABLED")
        if clause_enabled_env is not None:
            self.clause_extraction_enabled = _parse_bool(clause_enabled_env)

        clause_model_env = os.getenv("CLAUSE_EXTRACTION_MODEL")
        if clause_model_env:
            self.clause_extraction_model = clause_model_env

        clause_max_env = os.getenv("CLAUSE_EXTRACTION_MAX_CHARS")
        if clause_max_env:
            self.clause_extraction_max_chars = _coerce_int(
                clause_max_env,
                "CLAUSE_EXTRACTION_MAX_CHARS",
                self.clause_extraction_max_chars,
            )

        if not self.qdrant_url:
            self.qdrant_url = os.getenv("QDRANT_URL")

        if not self.qdrant_api_key:
            self.qdrant_api_key = os.getenv("QDRANT_API_KEY")

        timeout_env = os.getenv("QDRANT_TIMEOUT")
        if timeout_env:
            self.qdrant_timeout = _coerce_float(
                timeout_env,
                "QDRANT_TIMEOUT",
                self.qdrant_timeout,
            )

        env_collection = os.getenv("QDRANT_COLLECTION")
        if env_collection:
            self.qdrant_collection = env_collection
        elif not self.qdrant_collection:
            self.qdrant_collection = self.vector_store_collection

        vector_size_env = os.getenv("QDRANT_VECTOR_SIZE")
        if vector_size_env:
            self.qdrant_vector_size = _coerce_int(
                vector_size_env,
                "QDRANT_VECTOR_SIZE",
                self.qdrant_vector_size,
            )

        distance_env = os.getenv("QDRANT_DISTANCE")
        if distance_env:
            self.qdrant_distance = distance_env

        vector_name_env = os.getenv("QDRANT_VECTOR_NAME")
        if vector_name_env:
            self.qdrant_vector_name = vector_name_env

        env_url = _normalize_falkordb_url(os.getenv("FALKORDB_URL"))
        if env_url:
            self._set_falkordb_url_override(env_url)

        falkor_enabled_env = os.getenv("FALKORDB_ENABLED")
        if falkor_enabled_env is not None:
            self.falkordb_enabled = _parse_bool(falkor_enabled_env)

        host_env = os.getenv("FALKORDB_HOST")
        port_env = os.getenv("FALKORDB_PORT")
        password_env = os.getenv("FALKORDB_PASSWORD")
        graph_env = os.getenv("FALKORDB_GRAPH_NAME")

        if host_env:
            self.falkordb_host = host_env

        if port_env:
            self.falkordb_port = _coerce_int(port_env, "FALKORDB_PORT", self.falkordb_port)

        if password_env is not None:
            self.falkordb_password = password_env or None

        if graph_env:
            self.falkordb_graph_name = graph_env

        index_env = os.getenv("FALKORDB_INDEX_NAME")
        if index_env:
            self.falkordb_index_name = index_env

        vector_dim_env = os.getenv("FALKORDB_VECTOR_DIM")
        if vector_dim_env:
            self.falkordb_vector_dim = _coerce_int(
                vector_dim_env,
                "FALKORDB_VECTOR_DIM",
                self.falkordb_vector_dim,
            )

        self._clear_default_falkordb_override_if_components_explicit()

        dual_env = os.getenv("DUAL_VECTOR_WRITE")
        if dual_env is not None:
            self.dual_vector_write = _parse_bool(dual_env)

        vector_dual_env = os.getenv("VECTOR_DUAL_WRITE_ENABLED")
        if vector_dual_env is not None:
            self.vector_dual_write_enabled = _parse_bool(vector_dual_env)
            self.dual_vector_write = self.vector_dual_write_enabled
        else:
            self.vector_dual_write_enabled = self.dual_vector_write

        verify_env = os.getenv("VECTOR_VERIFY_AFTER_WRITE")
        if verify_env is not None:
            self.vector_verify_after_write = _parse_bool(verify_env)

        pydantic_ai_enabled_env = os.getenv("PYDANTIC_AI_ENABLED")
        if pydantic_ai_enabled_env is not None:
            self.use_pydantic_ai = _parse_bool(pydantic_ai_enabled_env)

        pydantic_ai_model_env = os.getenv("PYDANTIC_AI_MODEL")
        if pydantic_ai_model_env:
            self.pydantic_ai_model = pydantic_ai_model_env

        local_override = os.getenv("LOCAL_MONGODB_URI")
        if local_override:
            self.mongo_uri = local_override

        if not self.mongo_uri:
            self.mongo_uri = os.getenv("DATABASE_URL", DEFAULT_MONGO_URI)

        # Try to get settings from project config
        settings = _load_project_settings()
        if settings is not None:
            self.openai_api_key = self.openai_api_key or getattr(settings, "OPENAI_API_KEY", None)
            self.openai_model = getattr(settings, "OPENAI_RESPONSES_MODEL", self.openai_model)
            self.openai_embedding_model = getattr(
                settings, "OPENAI_EMBEDDING_MODEL", self.openai_embedding_model
            )
            self.marker_enabled = getattr(settings, "MARKER_ENABLED", self.marker_enabled)
            self.marker_cmd = getattr(settings, "MARKER_CMD", self.marker_cmd)
            self.marker_output_dir = getattr(settings, "MARKER_OUTPUT_DIR", self.marker_output_dir)
            self.clause_extraction_enabled = getattr(
                settings, "CLAUSE_EXTRACTION_ENABLED", self.clause_extraction_enabled
            )
            self.clause_extraction_model = getattr(
                settings, "CLAUSE_EXTRACTION_MODEL", self.clause_extraction_model
            )
            self.clause_extraction_max_chars = getattr(
                settings, "CLAUSE_EXTRACTION_MAX_CHARS", self.clause_extraction_max_chars
            )
            self.contract_ocr_batch_size = getattr(
                settings, "CONTRACT_OCR_BATCH_SIZE", self.contract_ocr_batch_size
            )
            self.contract_ocr_min_text_chars_per_page = getattr(
                settings,
                "CONTRACT_OCR_MIN_TEXT_CHARS_PER_PAGE",
                self.contract_ocr_min_text_chars_per_page,
            )
            self.contract_text_cleaning_enabled = getattr(
                settings, "CONTRACT_TEXT_CLEANING_ENABLED", self.contract_text_cleaning_enabled
            )
            self.contract_ai_chunking_enabled = getattr(
                settings, "CONTRACT_AI_CHUNKING_ENABLED", self.contract_ai_chunking_enabled
            )
            self.contract_ai_chunking_min_confidence = getattr(
                settings,
                "CONTRACT_AI_CHUNKING_MIN_CONFIDENCE",
                self.contract_ai_chunking_min_confidence,
            )

            settings_local_uri = getattr(settings, "LOCAL_MONGODB_URI", None)
            if settings_local_uri:
                self.mongo_uri = settings_local_uri
            elif not local_override:
                self.mongo_uri = getattr(settings, "DATABASE_URL", self.mongo_uri)

            self.database_name = getattr(settings, "VECTOR_STORE_DB_NAME", self.database_name)
            self.vector_store_collection = getattr(
                settings, "VECTOR_STORE_COLLECTION", self.vector_store_collection
            )
            self.vector_store_enabled = getattr(
                settings, "VECTOR_STORE_ENABLED", self.vector_store_enabled
            )
            self.use_pydantic_ai = getattr(settings, "PYDANTIC_AI_ENABLED", self.use_pydantic_ai)
            self.pydantic_ai_model = getattr(
                settings, "PYDANTIC_AI_MODEL", self.pydantic_ai_model
            )

            self.qdrant_url = getattr(settings, "QDRANT_URL", self.qdrant_url)
            self.qdrant_api_key = getattr(settings, "QDRANT_API_KEY", self.qdrant_api_key)
            self.qdrant_collection = getattr(settings, "QDRANT_COLLECTION", self.qdrant_collection)
            self.qdrant_vector_size = getattr(settings, "QDRANT_VECTOR_SIZE", self.qdrant_vector_size)
            self.qdrant_distance = getattr(settings, "QDRANT_DISTANCE", self.qdrant_distance)
            self.qdrant_vector_name = getattr(
                settings, "QDRANT_VECTOR_NAME", self.qdrant_vector_name
            )
            settings_timeout = getattr(settings, "QDRANT_TIMEOUT", None)
            if settings_timeout is not None:
                self.qdrant_timeout = _coerce_float(
                    settings_timeout,
                    "QDRANT_TIMEOUT",
                    self.qdrant_timeout,
                )

            settings_dual = getattr(settings, "VECTOR_DUAL_WRITE_ENABLED", None)
            if settings_dual is None:
                settings_dual = getattr(settings, "DUAL_VECTOR_WRITE", self.vector_dual_write_enabled)
            self.vector_dual_write_enabled = bool(settings_dual)
            self.dual_vector_write = self.vector_dual_write_enabled

            self.vector_verify_after_write = getattr(
                settings, "VECTOR_VERIFY_AFTER_WRITE", self.vector_verify_after_write
            )

            self.falkordb_enabled = getattr(settings, "FALKORDB_ENABLED", self.falkordb_enabled)
            self.falkordb_host = getattr(settings, "FALKORDB_HOST", self.falkordb_host)
            self.falkordb_port = getattr(settings, "FALKORDB_PORT", self.falkordb_port)
            self.falkordb_password = getattr(settings, "FALKORDB_PASSWORD", self.falkordb_password)
            self.falkordb_graph_name = getattr(
                settings, "FALKORDB_GRAPH_NAME", self.falkordb_graph_name
            )
            self.falkordb_index_name = getattr(
                settings, "FALKORDB_INDEX_NAME", self.falkordb_index_name
            )
            settings_falkordb_vector_dim = getattr(settings, "FALKORDB_VECTOR_DIM", None)
            if settings_falkordb_vector_dim is not None:
                self.falkordb_vector_dim = _coerce_int(
                    settings_falkordb_vector_dim,
                    "FALKORDB_VECTOR_DIM",
                    self.falkordb_vector_dim,
                )

            override = _normalize_falkordb_url(getattr(settings, "FALKORDB_URL", None))
            if override and not (
                self._is_default_falkordb_url(override) and self._has_custom_falkordb_components()
            ):
                self._set_falkordb_url_override(override)
            elif override and self._is_default_falkordb_url(override):
                self._falkordb_url_override = None

            self._clear_default_falkordb_override_if_components_explicit()

        if not self.mongo_uri:
            self.mongo_uri = DEFAULT_MONGO_URI

        if not self.openai_api_key:
            self.ai_enabled = False
            self.use_pydantic_ai = False
        self.qdrant_api_key = _normalize_qdrant_api_key(self.qdrant_api_key)

    @property
    def qdrant_url_scheme(self) -> str:
        return urlparse((self.qdrant_url or "").strip()).scheme.lower()

    @property
    def qdrant_url_host(self) -> str:
        return (urlparse((self.qdrant_url or "").strip()).hostname or "").lower()

    @property
    def qdrant_is_local_http(self) -> bool:
        return self.qdrant_url_scheme == "http" and self.qdrant_url_host in LOCAL_QDRANT_HOSTS

    @property
    def qdrant_auth_configuration_error(self) -> Optional[str]:
        api_key = _normalize_qdrant_api_key(self.qdrant_api_key)
        if api_key and self.qdrant_url_scheme == "http" and not self.qdrant_is_local_http:
            return (
                "QDRANT_API_KEY is configured for an insecure QDRANT_URL; "
                "use https:// for authenticated Qdrant or unset QDRANT_API_KEY."
            )
        return None

    @property
    def effective_qdrant_api_key(self) -> Optional[str]:
        api_key = _normalize_qdrant_api_key(self.qdrant_api_key)
        if not api_key or self.qdrant_auth_configuration_error:
            return None
        return api_key

    def qdrant_client_kwargs(self) -> dict[str, Any]:
        return {
            "url": self.qdrant_url,
            "api_key": self.effective_qdrant_api_key,
            "timeout": self.qdrant_timeout,
        }

    @property
    def qdrant_enabled(self) -> bool:
        return bool(
            self.vector_store_enabled
            and self.vector_dual_write_enabled
            and self.qdrant_url
            and not self.qdrant_auth_configuration_error
        )

    @property
    def embedding_model(self) -> str:
        """Backward compatibility property for embedding_model access"""
        return self.openai_embedding_model

    @embedding_model.setter
    def embedding_model(self, value: str):
        """Backward compatibility setter for embedding_model"""
        self.openai_embedding_model = value

    @property
    def falkordb_url(self) -> str:
        """Get FalkorDB URL from components or override"""
        if self._falkordb_url_override:
            return self._falkordb_url_override
        
        password = self.falkordb_password or ""
        if password:
            return f"redis://:{password}@{self.falkordb_host}:{self.falkordb_port}"
        return f"redis://{self.falkordb_host}:{self.falkordb_port}"

    @falkordb_url.setter
    def falkordb_url(self, value: str) -> None:
        """Allow setting the FalkorDB URL override - required for pytest compatibility"""
        self._set_falkordb_url_override(value)

    def _set_falkordb_url_override(self, value: Optional[str]) -> None:
        normalized = _normalize_falkordb_url(value)
        self._falkordb_url_override = normalized
        if not normalized:
            return

        parsed = urlparse(normalized)
        if parsed.hostname:
            self.falkordb_host = parsed.hostname
        if parsed.port:
            self.falkordb_port = parsed.port
        if parsed.password is not None:
            self.falkordb_password = parsed.password or None

    def _clear_default_falkordb_override_if_components_explicit(self) -> None:
        if (
            self._falkordb_url_override
            and self._is_default_falkordb_url(self._falkordb_url_override)
            and self._has_custom_falkordb_components()
        ):
            self._falkordb_url_override = None

    def _has_custom_falkordb_components(self) -> bool:
        return bool(
            self.falkordb_host not in DEFAULT_FALKORDB_URL_HOSTS
            or self.falkordb_port != DEFAULT_FALKORDB_PORT
            or self.falkordb_password is not None
        )

    def _is_default_falkordb_url(self, value: str) -> bool:
        normalized = _normalize_falkordb_url(value)
        if not normalized:
            return False
        if normalized in DEFAULT_FALKORDB_URLS:
            return True

        parsed = urlparse(normalized)
        return bool(
            parsed.scheme == "redis"
            and (parsed.hostname in DEFAULT_FALKORDB_URL_HOSTS)
            and (parsed.port or DEFAULT_FALKORDB_PORT) == DEFAULT_FALKORDB_PORT
            and parsed.password in (None, "")
        )
# Factory function for backward compatibility
def create_config() -> DocumentProcessingConfig:
    """
    Backward-compatible factory that returns a default DocumentProcessingConfig.

    Services can call this to obtain a valid configuration instance.
    """
    return DocumentProcessingConfig()
