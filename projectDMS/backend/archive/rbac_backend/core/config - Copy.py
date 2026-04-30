"""Configuration settings for the backend application."""

from __future__ import annotations

import logging
import os
from typing import List, Set

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _require(key: str) -> str:
    """Return the value of an environment variable or raise if missing."""
    value = os.getenv(key)
    if value is None or value.strip() == "":
        raise ValueError(f"{key} environment variable is required")
    return value.strip()


def _get_int(key: str) -> int:
    """Parse an integer environment variable."""
    try:
        return int(_require(key))
    except ValueError as exc:  # pragma: no cover - configuration error
        raise ValueError(f"{key} must be an integer") from exc


def _get_bool(key: str, default: bool = False) -> bool:
    """Parse a boolean environment variable."""
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split_list(key: str) -> List[str]:
    """Parse a comma-separated list from an environment variable."""
    raw = _require(key)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _split_set(key: str) -> Set[str]:
    """Parse a comma-separated list into a set."""
    return set(_split_list(key))


class Settings:
    """Application configuration loaded from environment variables."""

    def __init__(self) -> None:
        self.DATABASE_URL: str = _require("DATABASE_URL")
        self.SECRET_KEY: str = _require("SECRET_KEY")
        self.ALGORITHM: str = _require("ALGORITHM")
        self.ACCESS_TOKEN_EXPIRE_MINUTES: int = _get_int("ACCESS_TOKEN_EXPIRE_MINUTES")

        self.CORS_ORIGINS: List[str] = _split_list("CORS_ORIGINS")

        self.AWS_ACCESS_KEY_ID: str = _require("AWS_ACCESS_KEY_ID")
        self.AWS_SECRET_ACCESS_KEY: str = _require("AWS_SECRET_ACCESS_KEY")
        self.AWS_REGION: str = _require("AWS_REGION")
        self.AWS_BUCKET_NAME: str = _require("AWS_BUCKET_NAME")

        self.UPLOADS_DIR: str = _require("UPLOADS_DIR")
        self.SECURE_UPLOADS_DIR: str = _require("SECURE_UPLOADS_DIR")

        self.ALLOWED_DOCUMENT_MIMES: Set[str] = _split_set("ALLOWED_DOCUMENT_MIMES")
        self.ALLOWED_ENCLOSURE_MIMES: Set[str] = _split_set("ALLOWED_ENCLOSURE_MIMES")

        self.OPENAI_API_KEY: str = _require("OPENAI_API_KEY")
        self.ASSISTANT_ID: str = _require("ASSISTANT_ID")
        self.ASSISTANT_ID1: str = _require("ASSISTANT_ID1")

        self.PYDANTIC_AI_ENABLED: bool = _get_bool("PYDANTIC_AI_ENABLED", default=False)
        self.PYDANTIC_AI_MODEL: str | None = os.getenv("PYDANTIC_AI_MODEL")

        self.SMTP_HOST: str = _require("SMTP_HOST")
        self.SMTP_PORT: int = _get_int("SMTP_PORT")
        self.SMTP_USERNAME: str = _require("SMTP_USERNAME")
        self.SMTP_PASSWORD: str = _require("SMTP_PASSWORD")
        self.SMTP_FROM_EMAIL: str = _require("SMTP_FROM_EMAIL")


settings = Settings()
