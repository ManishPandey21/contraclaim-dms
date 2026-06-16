"""Configuration settings for the backend application."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, ClassVar, Optional, Dict

from dotenv import load_dotenv
from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load deployment/local environment. Example files document settings but must not
# influence runtime behavior.
load_dotenv(override=False)

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
    
    DATABASE_URL: str = Field(
        default="mongodb://localhost:27017/contraclaim",
        validation_alias="DATABASE_URL",
    )
    MONGODB_DATABASE: str = Field(default="contraclaim", validation_alias="MONGODB_DATABASE")
    MONGODB_APP_NAME: str = Field(default="ContractDMS", validation_alias="MONGODB_APP_NAME")
    MONGODB_MAX_POOL_SIZE: int = Field(default=100, validation_alias="MONGODB_MAX_POOL_SIZE")
    MONGODB_MIN_POOL_SIZE: int = Field(default=0, validation_alias="MONGODB_MIN_POOL_SIZE")
    MONGODB_SERVER_SELECTION_TIMEOUT_MS: int = Field(default=5000, validation_alias="MONGODB_SERVER_SELECTION_TIMEOUT_MS")
    MONGODB_CONNECT_TIMEOUT_MS: int = Field(default=5000, validation_alias="MONGODB_CONNECT_TIMEOUT_MS")
    MONGODB_SOCKET_TIMEOUT_MS: int = Field(default=20000, validation_alias="MONGODB_SOCKET_TIMEOUT_MS")
    MONGODB_REPLICA_SET: Optional[str] = Field(default=None, validation_alias="MONGODB_REPLICA_SET")
    MONGODB_RETRY_WRITES: bool = Field(default=True, validation_alias="MONGODB_RETRY_WRITES")
    MONGODB_ALLOW_STANDALONE_PRODUCTION: bool = Field(default=False, validation_alias="MONGODB_ALLOW_STANDALONE_PRODUCTION")
    LOCAL_MONGODB_URI: Optional[str] = Field(default=None, validation_alias="LOCAL_MONGODB_URI")
    APP_REDIS_URL: Optional[str] = Field(default=None, validation_alias="APP_REDIS_URL")
    RUNTIME_STATE_REDIS_URL: Optional[str] = Field(default=None, validation_alias="RUNTIME_STATE_REDIS_URL")
    
    # Authentication
    ENVIRONMENT: str = Field(default="development", validation_alias="ENVIRONMENT")
    ENABLE_API_DOCS: bool = Field(default=True, validation_alias="ENABLE_API_DOCS")
    SECRET_KEY: str = Field(default="", validation_alias="SECRET_KEY")
    ALGORITHM: str = Field(default="HS256", validation_alias="ALGORITHM")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    AUTH_COOKIE_NAME: str = Field(default="cc_access_token", validation_alias="AUTH_COOKIE_NAME")
    AUTH_COOKIE_SECURE: bool = Field(default=False, validation_alias="AUTH_COOKIE_SECURE")
    AUTH_COOKIE_SAMESITE: str = Field(default="lax", validation_alias="AUTH_COOKIE_SAMESITE")
    AUTH_COOKIE_DOMAIN: Optional[str] = Field(default=None, validation_alias="AUTH_COOKIE_DOMAIN")
    
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
    AWS_ACCESS_KEY_ID: str = Field(default="", validation_alias="AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY: str = Field(default="", validation_alias="AWS_SECRET_ACCESS_KEY")
    AWS_REGION: str = Field(default="ap-south-1", validation_alias="AWS_REGION")
    AWS_BUCKET_NAME: str = Field(default="", validation_alias="AWS_BUCKET_NAME")
    
    # Local uploads directory (absolute path in production recommended)
    UPLOADS_DIR: str = Field(default="uploads", validation_alias="UPLOADS_DIR")
    
    # Secure uploads directory used by file service
    SECURE_UPLOADS_DIR: str = Field(default="backend/uploads", validation_alias="SECURE_UPLOADS_DIR")
    
    # Bulk upload configuration
    BULK_UPLOAD_MAX_FILES: int = Field(default=100, validation_alias="BULK_UPLOAD_MAX_FILES")
    BULK_UPLOAD_MAX_SIZE_MB: int = Field(default=500, validation_alias="BULK_UPLOAD_MAX_SIZE_MB")
    UPLOAD_STREAM_CHUNK_SIZE_MB: int = Field(default=1, validation_alias="UPLOAD_STREAM_CHUNK_SIZE_MB")
    UPLOAD_VALIDATION_SAMPLE_BYTES: int = Field(default=8192, validation_alias="UPLOAD_VALIDATION_SAMPLE_BYTES")
    GENERAL_UPLOAD_MAX_FILE_SIZE_MB: int = Field(default=100, validation_alias="GENERAL_UPLOAD_MAX_FILE_SIZE_MB")
    UPLOAD_MAX_CONCURRENT_PER_USER: int = Field(default=3, validation_alias="UPLOAD_MAX_CONCURRENT_PER_USER")
    UPLOAD_MAX_CONCURRENT_PER_ORG: int = Field(default=20, validation_alias="UPLOAD_MAX_CONCURRENT_PER_ORG")
    
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
    
    # Antivirus Configuration
    ANTIVIRUS_ENABLED: bool = Field(default=False, validation_alias="ANTIVIRUS_ENABLED")
    CLAMAV_HOST: str = Field(default="localhost", validation_alias="CLAMAV_HOST")
    CLAMAV_PORT: int = Field(default=3310, validation_alias="CLAMAV_PORT")
    CLAMAV_TIMEOUT: int = Field(default=30, validation_alias="CLAMAV_TIMEOUT")
    CLAMAV_FAIL_OPEN: bool = Field(default=True, validation_alias="CLAMAV_FAIL_OPEN")
    
    # Vector storage toggles
    VECTOR_DUAL_WRITE_ENABLED: bool = Field(default=True, validation_alias="VECTOR_DUAL_WRITE_ENABLED")
    VECTOR_VERIFY_AFTER_WRITE: bool = Field(default=False, validation_alias="VECTOR_VERIFY_AFTER_WRITE")
    
    # OpenAI Configuration for AI Assistant
    OPENAI_API_KEY: str = Field(default="", validation_alias="OPENAI_API_KEY")
    ASSISTANT_ID: str = Field(default="", validation_alias="ASSISTANT_ID")
    ASSISTANT_ID1: str = Field(default="", validation_alias="SECONDARY_ASSISTANT_ID")
    LANGGRAPH_ENABLED: bool = Field(default=False, validation_alias="LANGGRAPH_ENABLED")
    LANGGRAPH_API_TOKEN: Optional[str] = Field(default=None, validation_alias="LANGGRAPH_API_TOKEN")
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

    # Knowledge graph provider selection. Production uses direct FalkorDB access;
    # Graphiti remains an explicitly enabled experimental adapter path.
    GRAPH_PROVIDER: str = Field(default="direct_falkor", validation_alias="GRAPH_PROVIDER")
    GRAPHITI_ENABLED: bool = Field(default=False, validation_alias="GRAPHITI_ENABLED")
    GRAPHITI_API_URL: Optional[str] = Field(default=None, validation_alias="GRAPHITI_API_URL")
    GRAPHITI_BASE_URL: Optional[str] = Field(default=None, validation_alias="GRAPHITI_BASE_URL")
    GRAPHITI_API_KEY: Optional[str] = Field(default=None, validation_alias="GRAPHITI_API_KEY")
    GRAPHITI_WORKSPACE: Optional[str] = Field(default="ContraClaim", validation_alias="GRAPHITI_WORKSPACE")
    
    # SMTP Configuration for Email Sharing
    SMTP_HOST: str = Field(default="smtp.gmail.com", validation_alias="SMTP_HOST")
    SMTP_PORT: int = Field(default=587, validation_alias="SMTP_PORT")
    SMTP_USERNAME: str = Field(default="", validation_alias="SMTP_USERNAME")
    SMTP_PASSWORD: str = Field(default="", validation_alias="SMTP_PASSWORD")
    SMTP_FROM_EMAIL: str = Field(default="", validation_alias="SMTP_FROM_EMAIL")
    CONTACT_RECIPIENT_EMAIL: str = Field(
        default="",
        validation_alias="CONTACT_RECIPIENT_EMAIL",
    )
    
    # Payment gateway configuration
    PAYMENT_PROVIDER: str = Field(default="noop", validation_alias="PAYMENT_PROVIDER")
    RAZORPAY_KEY_ID: str = Field(default="", validation_alias="RAZORPAY_KEY_ID")
    RAZORPAY_KEY_SECRET: str = Field(default="", validation_alias="RAZORPAY_KEY_SECRET")
    RAZORPAY_WEBHOOK_SECRET: str = Field(default="", validation_alias="RAZORPAY_WEBHOOK_SECRET")
    STRIPE_API_KEY: str = Field(default="", validation_alias="STRIPE_API_KEY")
    STRIPE_WEBHOOK_SECRET: str = Field(default="", validation_alias="STRIPE_WEBHOOK_SECRET")

    # Rate Limiting Configuration
    USER_RATE_LIMIT_REQUESTS: int = Field(default=10, description="Max requests per user per minute")
    USER_RATE_LIMIT_WINDOW: int = Field(default=60, description="Rate limit window in seconds")
    LOGIN_IP_RATE_LIMIT_REQUESTS: int = Field(
        default=60,
        validation_alias="LOGIN_IP_RATE_LIMIT_REQUESTS",
        description="Max login attempts per IP in the login rate-limit window",
    )
    LOGIN_IP_RATE_LIMIT_WINDOW: int = Field(
        default=900,
        validation_alias="LOGIN_IP_RATE_LIMIT_WINDOW",
        description="Login IP rate-limit window in seconds",
    )
    LOGIN_EMAIL_RATE_LIMIT_REQUESTS: int = Field(
        default=30,
        validation_alias="LOGIN_EMAIL_RATE_LIMIT_REQUESTS",
        description="Max login attempts per email in the login rate-limit window",
    )
    LOGIN_EMAIL_RATE_LIMIT_WINDOW: int = Field(
        default=3600,
        validation_alias="LOGIN_EMAIL_RATE_LIMIT_WINDOW",
        description="Login email rate-limit window in seconds",
    )

    # Explicit toggle for legacy dev header authentication (disabled by default)
    ALLOW_DEV_HEADERS: bool = Field(default=False, validation_alias="ALLOW_DEV_HEADERS")
    RBAC_ENTITLEMENT_FAIL_OPEN: bool = Field(default=True, validation_alias="RBAC_ENTITLEMENT_FAIL_OPEN")

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
    START_BACKGROUND_SERVICES: bool = Field(default=True, validation_alias="START_BACKGROUND_SERVICES")
    START_CONTRACT_QUEUE_WORKERS: bool = Field(default=True, validation_alias="START_CONTRACT_QUEUE_WORKERS")

    # Redaction / observability
    OBSERVABILITY_STORE_RAW_QUERIES: bool = Field(default=False, validation_alias="OBSERVABILITY_STORE_RAW_QUERIES")
    METRICS_ENABLED: bool = Field(default=True, validation_alias="METRICS_ENABLED")
    METRICS_TOKEN: Optional[str] = Field(default=None, validation_alias="METRICS_TOKEN")
    SLOW_REQUEST_THRESHOLD_MS: int = Field(default=2000, validation_alias="SLOW_REQUEST_THRESHOLD_MS")
    
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

        graph_provider = str(getattr(self, "GRAPH_PROVIDER", "direct_falkor") or "direct_falkor").lower()
        if graph_provider not in {"direct_falkor", "graphiti"}:
            raise ValueError(
                "GRAPH_PROVIDER must be one of: direct_falkor, graphiti"
            )
        if graph_provider == "graphiti" and not str(self.GRAPHITI_BASE_URL or self.GRAPHITI_API_URL or "").strip():
            raise ValueError(
                "GRAPHITI_BASE_URL or GRAPHITI_API_URL is required when GRAPH_PROVIDER=graphiti"
            )

        environment = str(getattr(self, "ENVIRONMENT", "development") or "development").lower()
        if environment == "production":
            production_errors: list[str] = []
            if self.ALLOW_DEV_HEADERS:
                production_errors.append("ALLOW_DEV_HEADERS must be false in production")
            if self.RBAC_ENTITLEMENT_FAIL_OPEN:
                production_errors.append("RBAC_ENTITLEMENT_FAIL_OPEN must be false in production")
            if len(str(self.SECRET_KEY or "")) < 32:
                production_errors.append("SECRET_KEY must be at least 32 characters in production")
            if not self.AUTH_COOKIE_SECURE:
                production_errors.append("AUTH_COOKIE_SECURE must be true in production")
            if str(self.AUTH_COOKIE_SAMESITE or "").lower() not in {"lax", "strict", "none"}:
                production_errors.append("AUTH_COOKIE_SAMESITE must be one of: lax, strict, none")
            database_url = str(self.DATABASE_URL or "").lower()
            if "localhost" in database_url or "127.0.0.1" in database_url:
                production_errors.append("DATABASE_URL must not point to localhost in production")
            replica_set_configured = (
                bool(getattr(self, "MONGODB_REPLICA_SET", None))
                or "replicaset=" in database_url
            )
            if not replica_set_configured and not self.MONGODB_ALLOW_STANDALONE_PRODUCTION:
                production_errors.append(
                    "MongoDB production deployment must use a replica set; set MONGODB_REPLICA_SET or explicitly set MONGODB_ALLOW_STANDALONE_PRODUCTION=true"
                )
            dev_origins = []
            for origin in self.CORS_ORIGINS or []:
                lowered = str(origin).lower()
                if "localhost" in lowered or "127.0.0.1" in lowered or "0.0.0.0" in lowered:
                    dev_origins.append(str(origin))
            if dev_origins:
                production_errors.append(
                    "CORS_ORIGINS must not include development origins in production: "
                    + ", ".join(dev_origins)
                )
            langgraph_enabled = bool(getattr(self, "LANGGRAPH_ENABLED", False))
            langgraph_token = str(getattr(self, "LANGGRAPH_API_TOKEN", "") or "").strip()
            if langgraph_enabled and not langgraph_token:
                production_errors.append("LANGGRAPH_API_TOKEN is required when LANGGRAPH_ENABLED=true in production")
            runtime_redis = (
                str(getattr(self, "RUNTIME_STATE_REDIS_URL", "") or "").strip()
                or str(getattr(self, "APP_REDIS_URL", "") or "").strip()
            )
            if not runtime_redis:
                production_errors.append("APP_REDIS_URL or RUNTIME_STATE_REDIS_URL is required in production")
            if self.METRICS_ENABLED and not str(self.METRICS_TOKEN or "").strip():
                production_errors.append("METRICS_TOKEN is required when METRICS_ENABLED=true in production")
            if self.ANTIVIRUS_ENABLED and self.CLAMAV_FAIL_OPEN:
                production_errors.append("CLAMAV_FAIL_OPEN must be false in production antivirus environments")
            if getattr(self, "OBSERVABILITY_STORE_RAW_QUERIES", False):
                # M10: raw RAG/search queries can contain sensitive contract/claim
                # content. Redaction is the default; forbid opting back into raw
                # storage in production so it can't be enabled by accident.
                production_errors.append(
                    "OBSERVABILITY_STORE_RAW_QUERIES must be false in production (raw queries may contain sensitive content)"
                )
            if production_errors:
                raise ValueError(
                    "Invalid production configuration: " + "; ".join(production_errors)
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
