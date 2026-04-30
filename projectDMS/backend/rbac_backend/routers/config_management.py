# Centralized Configuration Management
"""
Centralized, secure configuration management with validation and type safety.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from functools import lru_cache
import logging

from pydantic import BaseSettings, Field, field_validator, model_validator, SettingsConfigDict

logger = logging.getLogger(__name__)


class SecuritySettings(BaseSettings):
    """Security-related configuration."""

    model_config = SettingsConfigDict(env_prefix="SECURITY_")

    JWT_SECRET_KEY: str = Field(..., env="JWT_SECRET_KEY")
    JWT_ALGORITHM: str = Field("HS256", env="JWT_ALGORITHM")
    JWT_EXPIRATION_HOURS: int = Field(24, env="JWT_EXPIRATION_HOURS", ge=1, le=168)

    CORS_ORIGINS: List[str] = Field(["http://localhost:3000"], env="CORS_ORIGINS")
    CORS_ALLOW_CREDENTIALS: bool = Field(True, env="CORS_ALLOW_CREDENTIALS")

    SECURITY_HEADERS_ENABLED: bool = Field(True, env="SECURITY_HEADERS_ENABLED")

    GLOBAL_RATE_LIMIT_REQUESTS: int = Field(1000, env="GLOBAL_RATE_LIMIT_REQUESTS", ge=1)
    GLOBAL_RATE_LIMIT_WINDOW: int = Field(3600, env="GLOBAL_RATE_LIMIT_WINDOW", ge=60)
    USER_RATE_LIMIT_REQUESTS: int = Field(100, env="USER_RATE_LIMIT_REQUESTS", ge=1)
    USER_RATE_LIMIT_WINDOW: int = Field(3600, env="USER_RATE_LIMIT_WINDOW", ge=60)

    MAX_FILE_SIZE: int = Field(50 * 1024 * 1024, env="MAX_FILE_SIZE", ge=1024)
    MAX_FILES_PER_UPLOAD: int = Field(10, env="MAX_FILES_PER_UPLOAD", ge=1, le=100)
    QUARANTINE_SUSPICIOUS_FILES: bool = Field(True, env="QUARANTINE_SUSPICIOUS_FILES")

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


class DatabaseSettings(BaseSettings):
    """Database configuration."""

    model_config = SettingsConfigDict(env_prefix="DB_")

    MONGODB_URL: str = Field(..., env="MONGODB_URL")
    DATABASE_NAME: str = Field("contraclaim", env="DATABASE_NAME")

    MIN_CONNECTIONS: int = Field(1, env="DB_MIN_CONNECTIONS", ge=1)
    MAX_CONNECTIONS: int = Field(20, env="DB_MAX_CONNECTIONS", ge=1, le=100)
    CONNECTION_TIMEOUT: int = Field(30, env="DB_CONNECTION_TIMEOUT", ge=5)

    DEFAULT_PAGE_SIZE: int = Field(50, env="DB_DEFAULT_PAGE_SIZE", ge=1, le=1000)
    MAX_PAGE_SIZE: int = Field(1000, env="DB_MAX_PAGE_SIZE", ge=1, le=10000)
    QUERY_TIMEOUT: int = Field(30, env="DB_QUERY_TIMEOUT", ge=1)


class FileStorageSettings(BaseSettings):
    """File storage and handling configuration."""

    model_config = SettingsConfigDict(env_prefix="STORAGE_")

    UPLOADS_DIR: str = Field("uploads", env="UPLOADS_DIR")
    SECURE_UPLOADS_DIR: str = Field("secure_uploads", env="SECURE_UPLOADS_DIR")
    TEMP_DIR: str = Field("temp", env="TEMP_DIR")

    AWS_ACCESS_KEY_ID: Optional[str] = Field(None, env="AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY: Optional[str] = Field(None, env="AWS_SECRET_ACCESS_KEY")
    AWS_REGION: str = Field("us-east-1", env="AWS_REGION")
    AWS_BUCKET_NAME: Optional[str] = Field(None, env="AWS_BUCKET_NAME")
    S3_ENABLED: bool = Field(False, env="S3_ENABLED")
    S3_PRESIGNED_URL_EXPIRY: int = Field(1800, env="S3_PRESIGNED_URL_EXPIRY", ge=300)

    ALLOWED_DOCUMENT_MIMES: Set[str] = {"application/pdf"}
    ALLOWED_ENCLOSURE_MIMES: Set[str] = {"application/pdf", "image/png", "image/jpeg"}
    ALLOWED_CONTRACT_MIMES: Set[str] = {"application/pdf"}

    ENABLE_VIRUS_SCANNING: bool = Field(False, env="ENABLE_VIRUS_SCANNING")
    ENABLE_FILE_DEDUPLICATION: bool = Field(True, env="ENABLE_FILE_DEDUPLICATION")
    AUTO_CLEANUP_TEMP_FILES: bool = Field(True, env="AUTO_CLEANUP_TEMP_FILES")
    TEMP_FILE_RETENTION_HOURS: int = Field(24, env="TEMP_FILE_RETENTION_HOURS", ge=1)

    @field_validator("UPLOADS_DIR", "SECURE_UPLOADS_DIR", "TEMP_DIR")
    @classmethod
    def ensure_absolute_path(cls, value: str) -> str:
        return str(Path(value).resolve())


class AISettings(BaseSettings):
    """AI service configuration."""

    model_config = SettingsConfigDict(env_prefix="AI_")

    OPENAI_API_KEY: str = Field(..., env="OPENAI_API_KEY")
    OPENAI_MODEL_CHAT: str = Field("gpt-4-1106-preview", env="OPENAI_MODEL_CHAT")
    OPENAI_MODEL_EMBEDDING: str = Field("text-embedding-3-small", env="OPENAI_MODEL_EMBEDDING")
    OPENAI_MODEL_RESPONSES: str = Field("gpt-4.1", env="OPENAI_MODEL_RESPONSES")

    OPENAI_CALLS_PER_MINUTE: int = Field(30, env="OPENAI_CALLS_PER_MINUTE", ge=1)
    OPENAI_CALLS_PER_DAY: int = Field(1000, env="OPENAI_CALLS_PER_DAY", ge=1)
    OPENAI_TIMEOUT: int = Field(60, env="OPENAI_TIMEOUT", ge=5)

    SIMILARITY_THRESHOLD: float = Field(0.3, env="SIMILARITY_THRESHOLD", ge=0.0, le=1.0)
    MAX_SIMILAR_LETTERS: int = Field(5, env="MAX_SIMILAR_LETTERS", ge=1, le=50)
    VECTOR_STORE_ENABLED: bool = Field(False, env="VECTOR_STORE_ENABLED")

    EMBEDDING_CACHE_SIZE: int = Field(1000, env="EMBEDDING_CACHE_SIZE", ge=100)
    EMBEDDING_CACHE_TTL: int = Field(3600, env="EMBEDDING_CACHE_TTL", ge=300)
    SEARCH_CACHE_SIZE: int = Field(500, env="SEARCH_CACHE_SIZE", ge=50)
    SEARCH_CACHE_TTL: int = Field(1800, env="SEARCH_CACHE_TTL", ge=300)

    ENABLE_CONTENT_FILTERING: bool = Field(True, env="ENABLE_CONTENT_FILTERING")
    MAX_PROMPT_LENGTH: int = Field(8000, env="MAX_PROMPT_LENGTH", ge=1000)
    MAX_CONTEXT_LENGTH: int = Field(5000, env="MAX_CONTEXT_LENGTH", ge=500)


class OCRSettings(BaseSettings):
    """OCR processing configuration."""

    model_config = SettingsConfigDict(env_prefix="OCR_")

    OCR_ENABLED: bool = Field(True, env="OCR_ENABLED")
    OCR_LANGUAGE: str = Field("eng", env="OCR_LANGUAGE")
    OCR_TIMEOUT: int = Field(300, env="OCR_TIMEOUT", ge=30)

    OCR_DPI: int = Field(300, env="OCR_DPI", ge=150, le=600)
    OCR_PREPROCESS: bool = Field(True, env="OCR_PREPROCESS")
    OCR_DESKEW: bool = Field(True, env="OCR_DESKEW")
    OCR_DENOISE: bool = Field(True, env="OCR_DENOISE")


class LoggingSettings(BaseSettings):
    """Logging configuration."""

    model_config = SettingsConfigDict(env_prefix="LOG_")

    LOG_LEVEL: str = Field("INFO", env="LOG_LEVEL")
    LOG_FORMAT: str = Field("structured", env="LOG_FORMAT")
    LOG_TO_FILE: bool = Field(False, env="LOG_TO_FILE")
    LOG_FILE_PATH: str = Field("logs/app.log", env="LOG_FILE_PATH")
    LOG_ROTATION_SIZE: str = Field("100MB", env="LOG_ROTATION_SIZE")
    LOG_RETENTION_DAYS: int = Field(30, env="LOG_RETENTION_DAYS", ge=1)

    LOG_SENSITIVE_DATA: bool = Field(False, env="LOG_SENSITIVE_DATA")
    MASK_SENSITIVE_FIELDS: bool = Field(True, env="MASK_SENSITIVE_FIELDS")

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in valid_levels:
            raise ValueError(f"LOG_LEVEL must be one of {valid_levels}")
        return upper


class ApplicationSettings(BaseSettings):
    """Main application configuration."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    APP_NAME: str = Field("ContraClaim API", env="APP_NAME")
    APP_VERSION: str = Field("2.0.0", env="APP_VERSION")
    APP_DESCRIPTION: str = Field("Contract Management API", env="APP_DESCRIPTION")

    HOST: str = Field("0.0.0.0", env="HOST")
    PORT: int = Field(8000, env="PORT", ge=1024, le=65535)
    DEBUG: bool = Field(False, env="DEBUG")
    ENVIRONMENT: str = Field("production", env="ENVIRONMENT")

    API_PREFIX: str = Field("/api", env="API_PREFIX")
    DOCS_URL: Optional[str] = Field("/docs", env="DOCS_URL")
    REDOC_URL: Optional[str] = Field("/redoc", env="REDOC_URL")
    OPENAPI_URL: Optional[str] = Field("/openapi.json", env="OPENAPI_URL")

    ENABLE_METRICS: bool = Field(True, env="ENABLE_METRICS")
    ENABLE_HEALTH_CHECKS: bool = Field(True, env="ENABLE_HEALTH_CHECKS")
    ENABLE_API_DOCS: bool = Field(True, env="ENABLE_API_DOCS")

    WORKER_PROCESSES: int = Field(1, env="WORKER_PROCESSES", ge=1)
    WORKER_TIMEOUT: int = Field(120, env="WORKER_TIMEOUT", ge=30)
    KEEPALIVE_TIMEOUT: int = Field(2, env="KEEPALIVE_TIMEOUT", ge=1)

    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        valid_envs = {"development", "staging", "production"}
        lower = value.lower()
        if lower not in valid_envs:
            raise ValueError(f"ENVIRONMENT must be one of {valid_envs}")
        return lower

    @model_validator(mode="after")
    def disable_docs_in_production(self):
        if self.ENVIRONMENT == "production" and not self.ENABLE_API_DOCS:
            self.DOCS_URL = None
            self.REDOC_URL = None
            self.OPENAPI_URL = None
        return self


class Settings:
    """Aggregated settings container."""

    def __init__(self):
        self.app = ApplicationSettings()
        self.security = SecuritySettings()
        self.database = DatabaseSettings()
        self.storage = FileStorageSettings()
        self.ai = AISettings()
        self.ocr = OCRSettings()
        self.logging = LoggingSettings()

    def validate_configuration(self) -> List[str]:
        issues: List[str] = []

        if self.storage.S3_ENABLED:
            if not all([self.storage.AWS_ACCESS_KEY_ID, self.storage.AWS_SECRET_ACCESS_KEY, self.storage.AWS_BUCKET_NAME]):
                issues.append("S3 enabled but AWS credentials or bucket name missing")

        if self.ai.VECTOR_STORE_ENABLED and not self.ai.OPENAI_API_KEY:
            issues.append("Vector store enabled but OpenAI API key missing")

        for dir_path in [self.storage.UPLOADS_DIR, self.storage.SECURE_UPLOADS_DIR, self.storage.TEMP_DIR]:
            try:
                Path(dir_path).mkdir(parents=True, exist_ok=True)
                test_file = Path(dir_path) / ".write_test"
                test_file.write_text("test")
                test_file.unlink()
            except Exception as exc:
                issues.append(f"Directory {dir_path} not writable: {exc}")

        if self.ai.MAX_SIMILAR_LETTERS > 20:
            issues.append("MAX_SIMILAR_LETTERS should not exceed 20 for performance")

        if self.ai.EMBEDDING_CACHE_SIZE < 100:
            issues.append("EMBEDDING_CACHE_SIZE too small, may impact performance")

        return issues

    def get_openai_models(self) -> Dict[str, str]:
        return {
            "chat": self.ai.OPENAI_MODEL_CHAT,
            "embedding": self.ai.OPENAI_MODEL_EMBEDDING,
            "responses": self.ai.OPENAI_MODEL_RESPONSES,
        }


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()

    issues = settings.validate_configuration()
    if issues:
        logger.warning(f"Configuration issues detected: {issues}")
        if settings.app.ENVIRONMENT == "production":
            raise ValueError(f"Critical configuration issues in production: {issues}")

    return settings


settings = get_settings()
