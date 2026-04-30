"""Configuration settings for the backend application."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, ClassVar, Optional, Dict

from dotenv import load_dotenv
from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load deployment/local environment first, then fill any remaining gaps from the example file.
load_dotenv(override=False)
load_dotenv(".env.example", override=False)

logger = logging.getLogger(__name__)

class Settings(BaseSettings):
    CRITICAL_FIELDS: ClassVar[tuple[str, ...]] = (
        "DATABASE_URL",
        "SECRET_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_BUCKET_NAME",
        "OPENAI_API_KEY",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
    )
    
    # FIX: Use ClassVar for LOGGING_CONFIG since it's not a model field
    LOGGING_CONFIG: ClassVar[Dict[str, Any]] = {
        'version': 1,
        'handlers': {
            'file': {
                'class': 'logging.handlers.RotatingFileHandler',
                'filename': 'logs/bulk_upload.log',
                'maxBytes': 10485760,  # 10MB
                'backupCount': 5,
                'formatter': 'detailed',
            },
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'simple',
            }
        },
        'formatters': {
            'detailed': {
                'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            },
            'simple': {
                'format': '%(levelname)s - %(message)s'
            }
        },
        'loggers': {
            'app.services.bulk_upload_service': {
                'level': 'DEBUG',
                'handlers': ['file', 'console'],
                'propagate': False
            },
            'app.api.documents': {
                'level': 'DEBUG',
                'handlers': ['file', 'console'],
                'propagate': False
            }
        }
    }
    
    DATABASE_URL: str = "mongodb://localhost:27017/contraclaim"
    LOCAL_MONGODB_URI: Optional[str] = Field(default=None, validation_alias="LOCAL_MONGODB_URI")
    
    # Authentication
    SECRET_KEY: str = "SECRET_KEY"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # CORS Configuration
    CORS_ORIGINS: list[str] = Field(
        default=[
            "https://web.contraclaim.com",
            "https://app.contraclaim.com",
            "https://www.contraclaim.com",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "https://localhost:5173",
            "https://127.0.0.1:5173",
        ]
    )
    
    # Raw env override to avoid JSON decoding at source layer for list[str]
    CORS_ORIGINS_RAW: str | None = Field(default=None, validation_alias="CORS_ORIGINS")
    
    # AWS Configuration
    AWS_ACCESS_KEY_ID: str = "AWS_ACCESS_KEY_ID"
    AWS_SECRET_ACCESS_KEY: str = "AWS_SECRET_ACCESS_KEY"
    AWS_REGION: str = "ap-south-1"
    AWS_BUCKET_NAME: str = "contraclaim"
    
    # Local uploads directory (absolute path in production recommended)
    UPLOADS_DIR: str = "/var/www/app.contraclaim.com/backend/uploads"
    
    # Secure uploads directory used by file service
    SECURE_UPLOADS_DIR: str = "backend/uploads"
    
    # Bulk upload configuration
    BULK_UPLOAD_MAX_FILES: int = Field(default=100, validation_alias="BULK_UPLOAD_MAX_FILES")
    BULK_UPLOAD_MAX_SIZE_MB: int = Field(default=500, validation_alias="BULK_UPLOAD_MAX_SIZE_MB")
    
    # Allowed MIME types for documents and enclosures
    ALLOWED_DOCUMENT_MIMES: set[str] = Field(
        default={
            "application/pdf",
            "image/png",
            "image/jpeg",
            "text/plain",
        }
    )
    
    ALLOWED_CONTRACT_MIMES: set[str] = Field(
        default={
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    )
    
    ALLOWED_ENCLOSURE_MIMES: set[str] = Field(
        default={
            "application/pdf",
            "image/png",
            "image/jpeg",
            "text/plain",
        }
    )
    
    # Vector storage toggles
    VECTOR_DUAL_WRITE_ENABLED: bool = Field(default=True, validation_alias="VECTOR_DUAL_WRITE_ENABLED")
    VECTOR_VERIFY_AFTER_WRITE: bool = Field(default=False, validation_alias="VECTOR_VERIFY_AFTER_WRITE")
    
    # OpenAI Configuration for AI Assistant
    OPENAI_API_KEY: str = "OPENAI_API_KEY"
    ASSISTANT_ID: str = "ASSISTANT_ID"
    ASSISTANT_ID1: str = Field(default="SECONDARY_ASSISTANT_ID", validation_alias="SECONDARY_ASSISTANT_ID")
    LANGGRAPH_ENABLED: bool = Field(default=True, validation_alias="LANGGRAPH_ENABLED")
    LANGGRAPH_MODEL: str = Field(default="gpt-4o-mini", validation_alias="LANGGRAPH_MODEL")
    LANGGRAPH_DRAFTER_MODEL: str = Field(default="gpt-4o", validation_alias="LANGGRAPH_DRAFTER_MODEL")
    LANGGRAPH_REVIEWER_MODEL: str = Field(default="gpt-4o-mini", validation_alias="LANGGRAPH_REVIEWER_MODEL")
    LANGGRAPH_PLAN_MODEL: str = Field(default="grok-4-1-fast", validation_alias="LANGGRAPH_PLAN_MODEL")
    LANGGRAPH_DRAFT_PROMPT_TEMPLATE: str = Field(
        default="You are drafting a formal contract letter using provided plan, requirements, and sources.",
        validation_alias="LANGGRAPH_DRAFT_PROMPT_TEMPLATE",
    )
    LANGGRAPH_PLAN_PROMPT_TEMPLATE: str = Field(
        default="Build a structured plan for the letter using provided contexts, requirements, and graph-linked letters.",
        validation_alias="LANGGRAPH_PLAN_PROMPT_TEMPLATE",
    )
    LANGGRAPH_TIMEOUT: int = Field(default=90, validation_alias="LANGGRAPH_TIMEOUT")
    LANGGRAPH_TRACE_STORE: Optional[str] = Field(default=None, validation_alias="LANGGRAPH_TRACE_STORE")
    
    # FalkorDB / RedisGraph configuration
    FALKORDB_URL: str = Field(default="redis://localhost:6380", validation_alias="FALKORDB_URL")
    FALKORDB_ENABLED: bool = Field(default=True, validation_alias="FALKORDB_ENABLED")
    FALKORDB_HOST: str = Field(default="localhost", validation_alias="FALKORDB_HOST")
    FALKORDB_PORT: int = Field(default=6380, validation_alias="FALKORDB_PORT")
    FALKORDB_GRAPH_NAME: str = Field(default="contraclaim", validation_alias="FALKORDB_GRAPH_NAME")
    FALKORDB_PASSWORD: Optional[str] = Field(default=None, validation_alias="FALKORDB_PASSWORD")
    FALKORDB_CLEANUP_REFERENCES: bool = Field(default=True, validation_alias="FALKORDB_CLEANUP_REFERENCES")
    FALKORDB_INDEX_NAME: str = Field(default="document_vectors", validation_alias="FALKORDB_INDEX_NAME")
    FALKORDB_VECTOR_DIM: int = Field(default=1536, validation_alias="FALKORDB_VECTOR_DIM")
    
    # SMTP Configuration for Email Sharing
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = "SMTP_USERNAME"
    SMTP_PASSWORD: str = "SMTP_PASSWORD"
    SMTP_FROM_EMAIL: str = "SMTP_FROM_EMAIL"
    
    # Rate Limiting Configuration
    USER_RATE_LIMIT_REQUESTS: int = Field(default=10, description="Max requests per user per minute")
    USER_RATE_LIMIT_WINDOW: int = Field(default=60, description="Rate limit window in seconds")

    # Explicit toggle for legacy dev header authentication (disabled by default)
    ALLOW_DEV_HEADERS: bool = Field(default=False, validation_alias="ALLOW_DEV_HEADERS")

    # Contract upload/ingestion hard limits
    CONTRACT_UPLOAD_MAX_FILE_SIZE_MB: int = Field(default=50, validation_alias="CONTRACT_UPLOAD_MAX_FILE_SIZE_MB")
    CONTRACT_UPLOAD_MAX_CHUNK_SIZE_MB: int = Field(default=5, validation_alias="CONTRACT_UPLOAD_MAX_CHUNK_SIZE_MB")
    CONTRACT_UPLOAD_SESSION_TTL_SECONDS: int = Field(default=3600, validation_alias="CONTRACT_UPLOAD_SESSION_TTL_SECONDS")
    CONTRACT_UPLOAD_MAX_CONCURRENT_SESSIONS: int = Field(default=10, validation_alias="CONTRACT_UPLOAD_MAX_CONCURRENT_SESSIONS")

    # Durable contract ingestion queue
    CONTRACT_QUEUE_ENABLED: bool = Field(default=True, validation_alias="CONTRACT_QUEUE_ENABLED")
    CONTRACT_QUEUE_REDIS_URL: Optional[str] = Field(default=None, validation_alias="CONTRACT_QUEUE_REDIS_URL")
    CONTRACT_QUEUE_NAME: str = Field(default="contract_ingest_queue", validation_alias="CONTRACT_QUEUE_NAME")
    CONTRACT_QUEUE_PROCESSING_NAME: str = Field(default="contract_ingest_processing", validation_alias="CONTRACT_QUEUE_PROCESSING_NAME")
    CONTRACT_QUEUE_DEADLETTER_NAME: str = Field(default="contract_ingest_deadletter", validation_alias="CONTRACT_QUEUE_DEADLETTER_NAME")
    CONTRACT_QUEUE_MAX_RETRIES: int = Field(default=3, validation_alias="CONTRACT_QUEUE_MAX_RETRIES")
    CONTRACT_QUEUE_WORKERS: int = Field(default=1, validation_alias="CONTRACT_QUEUE_WORKERS")

    # Redaction / observability
    OBSERVABILITY_STORE_RAW_QUERIES: bool = Field(default=False, validation_alias="OBSERVABILITY_STORE_RAW_QUERIES")
    
    @field_validator('CORS_ORIGINS', 'ALLOWED_DOCUMENT_MIMES', 'ALLOWED_CONTRACT_MIMES', 'ALLOWED_ENCLOSURE_MIMES', mode='before')
    @classmethod
    def parse_json_strings(cls, v):
        """Parse JSON strings from environment variables into Python objects."""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                # If it's not valid JSON, handle common .env patterns
                s = v.strip()
                if s.startswith('[') and s.endswith(']'):
                    s = s[1:-1]
                if ',' in s:
                    return [item.strip().strip('"\'') for item in s.split(',') if item.strip()]
                if s:
                    return [s.strip().strip('"\'')]
                return []
        return v
    
    @field_validator('ALLOWED_DOCUMENT_MIMES', 'ALLOWED_CONTRACT_MIMES', 'ALLOWED_ENCLOSURE_MIMES', mode='after')
    @classmethod
    def convert_to_set(cls, v):
        """Convert lists to sets for MIME type fields."""
        if isinstance(v, list):
            return set(v)
        return v
    
    @field_validator(*CRITICAL_FIELDS, mode="before")
    @classmethod
    def _ensure_not_blank(cls, value: str, info: ValidationInfo) -> str:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
        raise ValueError(f"{info.field_name} cannot be empty")
    
    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        
        # Apply env override for CORS_ORIGINS via string to avoid JSON decode in settings source
        raw = getattr(self, "CORS_ORIGINS_RAW", None)
        if isinstance(raw, str):
            s = raw.strip()
            parsed: list[str] = []
            if s:
                try:
                    loaded = json.loads(s)
                    if isinstance(loaded, list):
                        parsed = [str(item).strip().strip('"').strip("'") for item in loaded if str(item).strip()]
                except Exception:
                    if s.startswith("[") and s.endswith("]"):
                        s = s[1:-1].strip()
                    parts = [p for p in s.split(",")] if "," in s else [s]
                    parsed = [p.strip().strip('"').strip("'") for p in parts if p.strip()]
            if parsed:
                object.__setattr__(self, "CORS_ORIGINS", parsed)
        
        self._log_default_usage()
    
    def _log_default_usage(self) -> None:
        missing = [field for field in self.CRITICAL_FIELDS if field not in self.model_fields_set]
        if missing:
            logger.warning(
                "Using default values for critical settings: %s", ", ".join(sorted(missing))
            )

    def validate_runtime_configuration(self) -> None:
        """Fail startup when critical settings still use placeholder values."""
        placeholder_values = {
            "SECRET_KEY",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "OPENAI_API_KEY",
            "SMTP_USERNAME",
            "SMTP_PASSWORD",
            "ASSISTANT_ID",
            "SECONDARY_ASSISTANT_ID",
            "changeme",
            "change-me",
            "example",
            "test",
        }
        invalid_fields: list[str] = []
        for field_name in self.CRITICAL_FIELDS:
            value = getattr(self, field_name, None)
            if not isinstance(value, str):
                continue
            stripped = value.strip()
            if not stripped:
                invalid_fields.append(field_name)
                continue
            normalized = stripped.lower()
            if (
                stripped == field_name
                or normalized in placeholder_values
                or normalized == field_name.lower()
            ):
                invalid_fields.append(field_name)
        if invalid_fields:
            raise ValueError(
                "Refusing to start with placeholder critical settings: "
                + ", ".join(sorted(invalid_fields))
            )
    
    # Pydantic v2 configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        enable_decoding=False,
    )


settings = Settings()


# Configure logging based on LOGGING_CONFIG
def configure_logging():
    """Configure logging using the LOGGING_CONFIG from settings."""
    import logging.config
    
    # Create logs directory if it doesn't exist
    log_dir = os.path.dirname(Settings.LOGGING_CONFIG['handlers']['file']['filename'])
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    
    try:
        logging.config.dictConfig(Settings.LOGGING_CONFIG)
        logger.info("Logging configured successfully for bulk upload service")
    except Exception as e:
        logger.error(f"Failed to configure logging: {e}")


# Call this once at startup
configure_logging()
