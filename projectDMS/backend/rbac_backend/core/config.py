"""Configuration settings for the backend application."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
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
    # C2: when the session store (runtime Redis) is configured but unreachable,
    # revocation state (logout, forced token invalidation, lockout) cannot be
    # evaluated. Default fail-closed: deny authentication with a 503 until the
    # store returns, so a revoked session can never outlive a Redis outage.
    # Setting this false is an explicit availability-over-revocation tradeoff;
    # deployments without a Redis URL configured are unaffected either way.
    AUTH_SESSION_FAIL_CLOSED: bool = Field(default=True, validation_alias="AUTH_SESSION_FAIL_CLOSED")
    
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
    # P0-005: production launch requires upload antivirus to be enabled and
    # fail-closed. Mirrors BACKUP_REQUIRED_IN_PRODUCTION: defaults to required, with
    # an explicit, auditable escape hatch for environments that consciously accept
    # the risk (e.g. ClamAV not yet deployed). Setting this false in production is a
    # deliberate, recorded decision rather than a silent gap.
    ANTIVIRUS_REQUIRED_IN_PRODUCTION: bool = Field(default=True, validation_alias="ANTIVIRUS_REQUIRED_IN_PRODUCTION")
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

    # Official LangGraph drafting migration.  The server-side policy is the
    # authority; the older LANGGRAPH_ENABLED frontend/sidecar flag must never
    # select the production drafting engine by itself.
    DRAFT_ENGINE_DEFAULT: str = Field(default="v2", validation_alias="DRAFT_ENGINE_DEFAULT")
    DRAFT_ENGINE_ROLLOUT_MODE: str = Field(default="off", validation_alias="DRAFT_ENGINE_ROLLOUT_MODE")
    DRAFT_ENGINE_CANARY_PERCENT: int = Field(default=0, ge=0, le=100, validation_alias="DRAFT_ENGINE_CANARY_PERCENT")
    DRAFT_ENGINE_CANARY_TENANT_IDS: str = Field(default="", validation_alias="DRAFT_ENGINE_CANARY_TENANT_IDS")
    DRAFT_ENGINE_SHADOW_ENABLED: bool = Field(default=False, validation_alias="DRAFT_ENGINE_SHADOW_ENABLED")
    DRAFT_ENGINE_GRAPH_VERSION: str = Field(default="v3", validation_alias="DRAFT_ENGINE_GRAPH_VERSION")
    DRAFT_ENGINE_STATE_SCHEMA_VERSION: int = Field(default=1, ge=1, validation_alias="DRAFT_ENGINE_STATE_SCHEMA_VERSION")
    DRAFT_ENGINE_PRODUCTION_ACCEPTED: bool = Field(default=False, validation_alias="DRAFT_ENGINE_PRODUCTION_ACCEPTED")
    DRAFT_ENGINE_MAX_CHECKPOINT_BYTES: int = Field(default=262144, ge=4096, validation_alias="DRAFT_ENGINE_MAX_CHECKPOINT_BYTES")
    DRAFT_ENGINE_CHECKPOINT_RETENTION_DAYS: int = Field(default=30, ge=1, validation_alias="DRAFT_ENGINE_CHECKPOINT_RETENTION_DAYS")
    ARBITRATION_ENGINE_DEFAULT: str = Field(default="arbitration_v2", validation_alias="ARBITRATION_ENGINE_DEFAULT")
    ARBITRATION_ENGINE_ROLLOUT_MODE: str = Field(default="off", validation_alias="ARBITRATION_ENGINE_ROLLOUT_MODE")
    ARBITRATION_ENGINE_CANARY_PERCENT: int = Field(default=0, ge=0, le=100, validation_alias="ARBITRATION_ENGINE_CANARY_PERCENT")
    ARBITRATION_ENGINE_CANARY_TENANT_IDS: str = Field(default="", validation_alias="ARBITRATION_ENGINE_CANARY_TENANT_IDS")
    ARBITRATION_ENGINE_CANARY_PROJECT_IDS: str = Field(default="", validation_alias="ARBITRATION_ENGINE_CANARY_PROJECT_IDS")
    ARBITRATION_ENGINE_FORCE_V2_TENANT_IDS: str = Field(default="", validation_alias="ARBITRATION_ENGINE_FORCE_V2_TENANT_IDS")
    ARBITRATION_ENGINE_FORCE_V2_PROJECT_IDS: str = Field(default="", validation_alias="ARBITRATION_ENGINE_FORCE_V2_PROJECT_IDS")
    ARBITRATION_ENGINE_ROLLOUT_PAUSED: bool = Field(default=False, validation_alias="ARBITRATION_ENGINE_ROLLOUT_PAUSED")
    ARBITRATION_ENGINE_GRAPH_VERSION: str = Field(default="phase4-v1", validation_alias="ARBITRATION_ENGINE_GRAPH_VERSION")
    ARBITRATION_ENGINE_STATE_SCHEMA_VERSION: int = Field(default=2, ge=1, validation_alias="ARBITRATION_ENGINE_STATE_SCHEMA_VERSION")
    ARBITRATION_ENGINE_PRODUCTION_ACCEPTED: bool = Field(default=False, validation_alias="ARBITRATION_ENGINE_PRODUCTION_ACCEPTED")
    ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID: str = Field(default="", validation_alias="ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID")
    ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256: str = Field(default="", validation_alias="ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256")
    ARBITRATION_ENGINE_PRIMARY_PERCENT: int = Field(default=0, ge=0, le=100, validation_alias="ARBITRATION_ENGINE_PRIMARY_PERCENT")
    ARBITRATION_ENGINE_PRIMARY_TENANT_IDS: str = Field(default="", validation_alias="ARBITRATION_ENGINE_PRIMARY_TENANT_IDS")
    ARBITRATION_ENGINE_PRIMARY_PROJECT_IDS: str = Field(default="", validation_alias="ARBITRATION_ENGINE_PRIMARY_PROJECT_IDS")
    ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY: bool = Field(default=True, validation_alias="ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY")
    ARBITRATION_ENGINE_MIN_PLEADING_TYPE_SAMPLE: int = Field(default=1, ge=1, le=1000, validation_alias="ARBITRATION_ENGINE_MIN_PLEADING_TYPE_SAMPLE")
    ARBITRATION_ENGINE_ACCEPTANCE_WINDOW_RUNS: int = Field(default=500, ge=20, le=10000, validation_alias="ARBITRATION_ENGINE_ACCEPTANCE_WINDOW_RUNS")
    ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE: str = Field(default="active", validation_alias="ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE")
    ARBITRATION_ENGINE_V2_COMPATIBILITY_UNTIL: str = Field(default="", validation_alias="ARBITRATION_ENGINE_V2_COMPATIBILITY_UNTIL")
    ARBITRATION_ENGINE_MAX_CHECKPOINT_BYTES: int = Field(default=262144, ge=4096, validation_alias="ARBITRATION_ENGINE_MAX_CHECKPOINT_BYTES")
    ARBITRATION_ENGINE_CHECKPOINT_RETENTION_DAYS: int = Field(default=30, ge=1, validation_alias="ARBITRATION_ENGINE_CHECKPOINT_RETENTION_DAYS")
    ARBITRATION_ENGINE_RETRY_BUDGET: int = Field(default=3, ge=0, le=20, validation_alias="ARBITRATION_ENGINE_RETRY_BUDGET")
    ARBITRATION_ENGINE_MAX_REMEDIATION_CYCLES: int = Field(
        default=1,
        ge=0,
        le=3,
        validation_alias="ARBITRATION_ENGINE_MAX_REMEDIATION_CYCLES",
    )
    ARBITRATION_ENGINE_MIN_ACCEPTANCE_SAMPLE: int = Field(default=20, ge=1, le=10000, validation_alias="ARBITRATION_ENGINE_MIN_ACCEPTANCE_SAMPLE")
    ARBITRATION_ENGINE_MIN_SHADOW_PARITY_PERCENT: float = Field(default=99.0, ge=0, le=100, validation_alias="ARBITRATION_ENGINE_MIN_SHADOW_PARITY_PERCENT")
    ARBITRATION_ENGINE_MAX_FAILURE_RATE_PERCENT: float = Field(default=2.0, ge=0, le=100, validation_alias="ARBITRATION_ENGINE_MAX_FAILURE_RATE_PERCENT")
    ARBITRATION_ENGINE_MAX_FALLBACK_RATE_PERCENT: float = Field(default=5.0, ge=0, le=100, validation_alias="ARBITRATION_ENGINE_MAX_FALLBACK_RATE_PERCENT")
    ARBITRATION_ENGINE_MAX_PAUSE_HOURS: float = Field(default=72.0, ge=1, le=8760, validation_alias="ARBITRATION_ENGINE_MAX_PAUSE_HOURS")
    ARBITRATION_REVIEWER_ROLE_MATRIX: str = Field(default="", validation_alias="ARBITRATION_REVIEWER_ROLE_MATRIX")
    
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

    # SSO (generic OpenID Connect)
    OIDC_ENABLED: bool = Field(default=False, validation_alias="OIDC_ENABLED")
    OIDC_ISSUER: str = Field(default="", validation_alias="OIDC_ISSUER")
    OIDC_CLIENT_ID: str = Field(default="", validation_alias="OIDC_CLIENT_ID")
    OIDC_CLIENT_SECRET: str = Field(default="", validation_alias="OIDC_CLIENT_SECRET")
    OIDC_REDIRECT_URI: str = Field(default="", validation_alias="OIDC_REDIRECT_URI")
    OIDC_SCOPES: str = Field(default="openid email profile", validation_alias="OIDC_SCOPES")
    OIDC_ALLOWED_EMAIL_DOMAINS: str = Field(default="", validation_alias="OIDC_ALLOWED_EMAIL_DOMAINS")
    OIDC_AUTO_PROVISION: bool = Field(default=True, validation_alias="OIDC_AUTO_PROVISION")
    OIDC_DEFAULT_ROLE: str = Field(default="orguser", validation_alias="OIDC_DEFAULT_ROLE")
    OIDC_DEFAULT_ORG_ID: str = Field(default="", validation_alias="OIDC_DEFAULT_ORG_ID")
    OIDC_POST_LOGIN_REDIRECT: str = Field(default="/", validation_alias="OIDC_POST_LOGIN_REDIRECT")

    # Observability / distributed tracing (OpenTelemetry; opt-in)
    OTEL_ENABLED: bool = Field(default=False, validation_alias="OTEL_ENABLED")
    OTEL_SERVICE_NAME: str = Field(default="contraclaim-backend", validation_alias="OTEL_SERVICE_NAME")
    OTEL_EXPORTER_OTLP_ENDPOINT: str = Field(default="", validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT")

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
    RBAC_ENTITLEMENT_FAIL_OPEN: bool = Field(default=False, validation_alias="RBAC_ENTITLEMENT_FAIL_OPEN")

    # Contract upload/ingestion hard limits
    CONTRACT_UPLOAD_MAX_FILE_SIZE_MB: int = Field(default=50, validation_alias="CONTRACT_UPLOAD_MAX_FILE_SIZE_MB")
    CONTRACT_UPLOAD_MAX_CHUNK_SIZE_MB: int = Field(default=5, validation_alias="CONTRACT_UPLOAD_MAX_CHUNK_SIZE_MB")
    CONTRACT_UPLOAD_SESSION_TTL_SECONDS: int = Field(default=3600, validation_alias="CONTRACT_UPLOAD_SESSION_TTL_SECONDS")
    CONTRACT_UPLOAD_MAX_CONCURRENT_SESSIONS: int = Field(default=10, validation_alias="CONTRACT_UPLOAD_MAX_CONCURRENT_SESSIONS")
    DOCUMENT_PROCESSING_STALE_AFTER_SECONDS: int = Field(default=3600, ge=300, validation_alias="DOCUMENT_PROCESSING_STALE_AFTER_SECONDS")
    DOCUMENT_PROCESSING_HEARTBEAT_SECONDS: int = Field(default=30, ge=5, validation_alias="DOCUMENT_PROCESSING_HEARTBEAT_SECONDS")

    # Durable contract ingestion queue
    CONTRACT_QUEUE_ENABLED: bool = Field(default=True, validation_alias="CONTRACT_QUEUE_ENABLED")
    CONTRACT_QUEUE_REDIS_URL: Optional[str] = Field(default=None, validation_alias="CONTRACT_QUEUE_REDIS_URL")
    CONTRACT_QUEUE_NAME: str = Field(default="contract_ingest_queue", validation_alias="CONTRACT_QUEUE_NAME")
    CONTRACT_QUEUE_PROCESSING_NAME: str = Field(default="contract_ingest_processing", validation_alias="CONTRACT_QUEUE_PROCESSING_NAME")
    CONTRACT_QUEUE_DEADLETTER_NAME: str = Field(default="contract_ingest_deadletter", validation_alias="CONTRACT_QUEUE_DEADLETTER_NAME")
    CONTRACT_QUEUE_MAX_RETRIES: int = Field(default=3, validation_alias="CONTRACT_QUEUE_MAX_RETRIES")
    CONTRACT_QUEUE_WORKERS: int = Field(default=1, validation_alias="CONTRACT_QUEUE_WORKERS")
    CONTRACT_QUEUE_VISIBILITY_TIMEOUT_SECONDS: int = Field(default=1800, ge=60, validation_alias="CONTRACT_QUEUE_VISIBILITY_TIMEOUT_SECONDS")
    CONTRACT_QUEUE_HEARTBEAT_SECONDS: int = Field(default=30, ge=5, validation_alias="CONTRACT_QUEUE_HEARTBEAT_SECONDS")
    START_BACKGROUND_SERVICES: bool = Field(default=True, validation_alias="START_BACKGROUND_SERVICES")
    START_CONTRACT_QUEUE_WORKERS: bool = Field(default=True, validation_alias="START_CONTRACT_QUEUE_WORKERS")
    START_DRAFTING_QUEUE_WORKERS: bool = Field(default=False, validation_alias="START_DRAFTING_QUEUE_WORKERS")
    DRAFTING_QUEUE_ENABLED: bool = Field(default=False, validation_alias="DRAFTING_QUEUE_ENABLED")
    DRAFTING_QUEUE_REDIS_URL: Optional[str] = Field(default=None, validation_alias="DRAFTING_QUEUE_REDIS_URL")
    DRAFTING_QUEUE_NAME: str = Field(default="letter_drafting_queue", validation_alias="DRAFTING_QUEUE_NAME")
    DRAFTING_QUEUE_PROCESSING_NAME: str = Field(default="letter_drafting_processing", validation_alias="DRAFTING_QUEUE_PROCESSING_NAME")
    DRAFTING_QUEUE_DEADLETTER_NAME: str = Field(default="letter_drafting_deadletter", validation_alias="DRAFTING_QUEUE_DEADLETTER_NAME")
    DRAFTING_QUEUE_MAX_RETRIES: int = Field(default=3, ge=1, validation_alias="DRAFTING_QUEUE_MAX_RETRIES")
    DRAFTING_QUEUE_WORKERS: int = Field(default=1, ge=1, validation_alias="DRAFTING_QUEUE_WORKERS")
    DRAFTING_QUEUE_VISIBILITY_TIMEOUT_SECONDS: int = Field(default=1800, ge=60, validation_alias="DRAFTING_QUEUE_VISIBILITY_TIMEOUT_SECONDS")
    DRAFTING_QUEUE_HEARTBEAT_SECONDS: int = Field(default=30, ge=5, validation_alias="DRAFTING_QUEUE_HEARTBEAT_SECONDS")
    START_FILING_EXPORT_QUEUE_WORKERS: bool = Field(default=False, validation_alias="START_FILING_EXPORT_QUEUE_WORKERS")
    FILING_EXPORT_QUEUE_ENABLED: bool = Field(default=False, validation_alias="FILING_EXPORT_QUEUE_ENABLED")
    FILING_EXPORT_QUEUE_REDIS_URL: Optional[str] = Field(default=None, validation_alias="FILING_EXPORT_QUEUE_REDIS_URL")
    FILING_EXPORT_QUEUE_NAME: str = Field(default="arbitration_filing_export_queue", validation_alias="FILING_EXPORT_QUEUE_NAME")
    FILING_EXPORT_QUEUE_PROCESSING_NAME: str = Field(default="arbitration_filing_export_processing", validation_alias="FILING_EXPORT_QUEUE_PROCESSING_NAME")
    FILING_EXPORT_QUEUE_DEADLETTER_NAME: str = Field(default="arbitration_filing_export_deadletter", validation_alias="FILING_EXPORT_QUEUE_DEADLETTER_NAME")
    FILING_EXPORT_QUEUE_MAX_RETRIES: int = Field(default=3, ge=1, le=20, validation_alias="FILING_EXPORT_QUEUE_MAX_RETRIES")
    FILING_EXPORT_QUEUE_WORKERS: int = Field(default=2, ge=1, le=32, validation_alias="FILING_EXPORT_QUEUE_WORKERS")
    FILING_EXPORT_QUEUE_VISIBILITY_TIMEOUT_SECONDS: int = Field(default=300, ge=60, validation_alias="FILING_EXPORT_QUEUE_VISIBILITY_TIMEOUT_SECONDS")
    FILING_EXPORT_QUEUE_HEARTBEAT_SECONDS: int = Field(default=20, ge=5, validation_alias="FILING_EXPORT_QUEUE_HEARTBEAT_SECONDS")
    FILING_EXPORT_QUEUE_METADATA_TTL_SECONDS: int = Field(default=604800, ge=3600, validation_alias="FILING_EXPORT_QUEUE_METADATA_TTL_SECONDS")

    # Contract OCR / clause chunking controls. OCR is performed page/batch-wise
    # during contract ingestion so large PDFs do not require one monolithic
    # OCRmyPDF run before indexing can start.
    CONTRACT_OCR_BATCH_SIZE: int = Field(default=25, ge=1, le=100, validation_alias="CONTRACT_OCR_BATCH_SIZE")
    CONTRACT_OCR_MIN_TEXT_CHARS_PER_PAGE: int = Field(default=40, ge=0, validation_alias="CONTRACT_OCR_MIN_TEXT_CHARS_PER_PAGE")
    CONTRACT_TEXT_CLEANING_ENABLED: bool = Field(default=True, validation_alias="CONTRACT_TEXT_CLEANING_ENABLED")
    CONTRACT_AI_CHUNKING_ENABLED: bool = Field(default=False, validation_alias="CONTRACT_AI_CHUNKING_ENABLED")
    CONTRACT_AI_CHUNKING_MIN_CONFIDENCE: float = Field(default=0.70, ge=0.0, le=1.0, validation_alias="CONTRACT_AI_CHUNKING_MIN_CONFIDENCE")

    # H2: whether this process runs the APScheduler cron jobs. Default True keeps
    # single-process deploys working; jobs are leader-locked so it stays correct
    # even if several processes enable it. In a scaled deploy run it only on the
    # worker (RUN_SCHEDULER=true there, false on the web tier).
    RUN_SCHEDULER: bool = Field(default=True, validation_alias="RUN_SCHEDULER")
    SCHEDULER_LOCK_TTL_SECONDS: int = Field(default=3600, validation_alias="SCHEDULER_LOCK_TTL_SECONDS")

    # Contract appraisal generation (M1–M3). Generation runs in-process as a
    # managed background task: bounded concurrency, a hard timeout, and a reaper
    # that fails jobs left RUNNING by a crash/restart. Retrieval breadth is
    # tunable per deployment (clamped to the engine's own bounds: limit<=50,
    # iterations<=5) so large contracts can trade latency for coverage.
    CONTRACT_APPRAISAL_TIMEOUT_SECONDS: int = Field(default=1800, validation_alias="CONTRACT_APPRAISAL_TIMEOUT_SECONDS")
    CONTRACT_APPRAISAL_MAX_CONCURRENCY: int = Field(default=2, ge=1, validation_alias="CONTRACT_APPRAISAL_MAX_CONCURRENCY")
    CONTRACT_APPRAISAL_RETRIEVAL_LIMIT: int = Field(default=50, ge=1, le=50, validation_alias="CONTRACT_APPRAISAL_RETRIEVAL_LIMIT")
    CONTRACT_APPRAISAL_QA_MAX_ITERATIONS: int = Field(default=3, ge=1, le=5, validation_alias="CONTRACT_APPRAISAL_QA_MAX_ITERATIONS")

    # Cross-encoder reranking for contract retrieval. Disabled by default so
    # existing deployments keep the heuristic-only ordering; when enabled the
    # heuristic scorer remains the fallback and one input to the blended score.
    RERANKER_ENABLED: bool = Field(default=False, validation_alias="RERANKER_ENABLED")
    RERANKER_PROVIDER: str = Field(default="llm", validation_alias="RERANKER_PROVIDER")
    RERANKER_MODEL: str = Field(default="", validation_alias="RERANKER_MODEL")
    RERANKER_TOP_N: int = Field(default=20, ge=1, le=50, validation_alias="RERANKER_TOP_N")
    RERANKER_TIMEOUT_MS: int = Field(default=8000, ge=100, validation_alias="RERANKER_TIMEOUT_MS")
    RERANKER_WEIGHT: float = Field(default=0.5, ge=0.0, le=1.0, validation_alias="RERANKER_WEIGHT")

    # Deterministic AI output guardrails (citation coverage, injection scan).
    # GUARDRAIL_REJECT_UNSUPPORTED=false keeps the failure mode at
    # "requires_human_review" instead of replacing the answer outright.
    AI_GUARDRAILS_ENABLED: bool = Field(default=True, validation_alias="AI_GUARDRAILS_ENABLED")
    GUARDRAIL_MIN_CITATION_COVERAGE: float = Field(default=0.6, ge=0.0, le=1.0, validation_alias="GUARDRAIL_MIN_CITATION_COVERAGE")
    GUARDRAIL_REJECT_UNSUPPORTED: bool = Field(default=False, validation_alias="GUARDRAIL_REJECT_UNSUPPORTED")

    # Redaction / observability
    OBSERVABILITY_STORE_RAW_QUERIES: bool = Field(default=False, validation_alias="OBSERVABILITY_STORE_RAW_QUERIES")
    METRICS_ENABLED: bool = Field(default=True, validation_alias="METRICS_ENABLED")
    METRICS_TOKEN: Optional[str] = Field(default=None, validation_alias="METRICS_TOKEN")
    SLOW_REQUEST_THRESHOLD_MS: int = Field(default=7000, validation_alias="SLOW_REQUEST_THRESHOLD_MS")
    BACKUP_ROOT: str = Field(default="/var/backups/contractdms", validation_alias="BACKUP_ROOT")
    BACKUP_MAX_AGE_HOURS: int = Field(default=26, ge=1, validation_alias="BACKUP_MAX_AGE_HOURS")
    BACKUP_REQUIRED_IN_PRODUCTION: bool = Field(default=True, validation_alias="BACKUP_REQUIRED_IN_PRODUCTION")
    BACKUP_REQUIRED_VOLUME_LABELS: str = Field(
        default="backend-uploads,qdrant-data,falkordb-data,redis-data",
        validation_alias="BACKUP_REQUIRED_VOLUME_LABELS",
    )
    BACKUP_S3_BUCKET: str = Field(default="", validation_alias="BACKUP_S3_BUCKET")
    BACKUP_S3_PREFIX: str = Field(default="contraclaim/backups", validation_alias="BACKUP_S3_PREFIX")
    
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

        draft_engine_default = str(getattr(self, "DRAFT_ENGINE_DEFAULT", "v2") or "v2").lower()
        draft_rollout = str(getattr(self, "DRAFT_ENGINE_ROLLOUT_MODE", "off") or "off").lower()
        if draft_engine_default not in {"v2", "langgraph_v3"}:
            raise ValueError("DRAFT_ENGINE_DEFAULT must be v2 or langgraph_v3")
        if draft_rollout not in {"off", "shadow", "canary", "primary", "forced_v2"}:
            raise ValueError("DRAFT_ENGINE_ROLLOUT_MODE must be off, shadow, canary, primary, or forced_v2")
        arbitration_engine_default = str(
            getattr(self, "ARBITRATION_ENGINE_DEFAULT", "arbitration_v2") or "arbitration_v2"
        ).lower()
        arbitration_rollout = str(
            getattr(self, "ARBITRATION_ENGINE_ROLLOUT_MODE", "off") or "off"
        ).lower()
        if arbitration_engine_default not in {"arbitration_v2", "langgraph_v1"}:
            raise ValueError("ARBITRATION_ENGINE_DEFAULT must be arbitration_v2 or langgraph_v1")
        if arbitration_rollout not in {"off", "shadow", "canary", "primary", "forced_v2"}:
            raise ValueError("ARBITRATION_ENGINE_ROLLOUT_MODE must be off, shadow, canary, primary, or forced_v2")
        arbitration_v2_compatibility = str(
            getattr(self, "ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE", "active") or "active"
        ).lower()
        if arbitration_v2_compatibility not in {"active", "read_replay_only", "retired"}:
            raise ValueError(
                "ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE must be active, read_replay_only, or retired"
            )

        environment = str(getattr(self, "ENVIRONMENT", "development") or "development").lower()
        if environment == "production":
            production_errors: list[str] = []
            if draft_rollout == "primary" and not self.DRAFT_ENGINE_PRODUCTION_ACCEPTED:
                production_errors.append(
                    "DRAFT_ENGINE_PRODUCTION_ACCEPTED=true is required before primary LangGraph cutover"
                )
            if arbitration_rollout == "primary" and not self.ARBITRATION_ENGINE_PRODUCTION_ACCEPTED:
                production_errors.append(
                    "ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=true is required before primary arbitration LangGraph cutover"
                )
            if arbitration_rollout == "primary":
                if arbitration_engine_default != "langgraph_v1":
                    production_errors.append(
                        "ARBITRATION_ENGINE_DEFAULT=langgraph_v1 is required for primary arbitration rollout"
                    )
                receipt_id = str(self.ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID or "").strip()
                receipt_hash = str(self.ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256 or "").strip().lower()
                if not receipt_id or not re.fullmatch(r"[0-9a-f]{64}", receipt_hash):
                    production_errors.append(
                        "ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID and a 64-character "
                        "ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256 are required for primary rollout"
                    )
                primary_scoped = bool(
                    str(self.ARBITRATION_ENGINE_PRIMARY_TENANT_IDS or "").strip()
                    or str(self.ARBITRATION_ENGINE_PRIMARY_PROJECT_IDS or "").strip()
                    or int(self.ARBITRATION_ENGINE_PRIMARY_PERCENT or 0) > 0
                )
                if not primary_scoped:
                    production_errors.append(
                        "Primary arbitration rollout requires an explicit tenant/project allowlist or "
                        "ARBITRATION_ENGINE_PRIMARY_PERCENT greater than zero"
                    )
                compatibility_until = str(self.ARBITRATION_ENGINE_V2_COMPATIBILITY_UNTIL or "").strip()
                try:
                    compatibility_date = date.fromisoformat(compatibility_until)
                except ValueError:
                    compatibility_date = None
                if compatibility_date is None or compatibility_date < date.today():
                    production_errors.append(
                        "ARBITRATION_ENGINE_V2_COMPATIBILITY_UNTIL must be a current or future ISO date "
                        "during primary rollout"
                    )
                if not self.ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY:
                    production_errors.append(
                        "ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY=true is required for primary "
                        "arbitration rollout in production"
                    )
                if arbitration_v2_compatibility == "retired":
                    production_errors.append(
                        "ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE=retired is reserved until a separate "
                        "v2 deprecation decision is implemented and accepted"
                    )
                filing_export_redis = str(
                    self.FILING_EXPORT_QUEUE_REDIS_URL or self.APP_REDIS_URL or ""
                ).strip()
                if not self.FILING_EXPORT_QUEUE_ENABLED or not filing_export_redis:
                    production_errors.append(
                        "FILING_EXPORT_QUEUE_ENABLED=true and a Redis URL are required before primary "
                        "arbitration rollout"
                    )
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
            if bool(getattr(self, "OIDC_ENABLED", False)):
                missing_oidc = [
                    name
                    for name in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET", "OIDC_REDIRECT_URI")
                    if not str(getattr(self, name, "") or "").strip()
                ]
                if missing_oidc:
                    production_errors.append(
                        "OIDC is enabled but missing: " + ", ".join(missing_oidc)
                    )
            # Payments: with PAYMENT_PROVIDER=razorpay, an empty webhook secret is
            # the worst kind of misconfiguration — checkout works and customers
            # PAY, but every webhook fails signature verification (deny-by-
            # default), so their subscription never activates. Fail startup
            # instead of taking money for nothing.
            if str(getattr(self, "PAYMENT_PROVIDER", "noop") or "noop").lower() == "razorpay":
                missing_razorpay = [
                    name
                    for name in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET")
                    if not str(getattr(self, name, "") or "").strip()
                ]
                if missing_razorpay:
                    production_errors.append(
                        "PAYMENT_PROVIDER=razorpay requires: " + ", ".join(missing_razorpay)
                    )
            runtime_redis = (
                str(getattr(self, "RUNTIME_STATE_REDIS_URL", "") or "").strip()
                or str(getattr(self, "APP_REDIS_URL", "") or "").strip()
            )
            if not runtime_redis:
                production_errors.append("APP_REDIS_URL or RUNTIME_STATE_REDIS_URL is required in production")
            if self.METRICS_ENABLED and not str(self.METRICS_TOKEN or "").strip():
                production_errors.append("METRICS_TOKEN is required when METRICS_ENABLED=true in production")
            if self.BACKUP_REQUIRED_IN_PRODUCTION:
                if not str(self.BACKUP_ROOT or "").strip():
                    production_errors.append("BACKUP_ROOT is required when BACKUP_REQUIRED_IN_PRODUCTION=true")
                elif not os.path.isabs(str(self.BACKUP_ROOT)):
                    production_errors.append("BACKUP_ROOT must be an absolute path in production")
                if not str(self.BACKUP_S3_BUCKET or "").strip():
                    production_errors.append("BACKUP_S3_BUCKET is required when BACKUP_REQUIRED_IN_PRODUCTION=true")
            # P0-005: upload antivirus must be enabled and fail-closed for launch.
            if self.ANTIVIRUS_REQUIRED_IN_PRODUCTION and not self.ANTIVIRUS_ENABLED:
                production_errors.append(
                    "ANTIVIRUS_ENABLED must be true in production; set ANTIVIRUS_REQUIRED_IN_PRODUCTION=false only to explicitly accept the risk"
                )
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
