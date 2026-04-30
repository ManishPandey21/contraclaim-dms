import pytest

from rbac_backend.core.config import Settings


def test_settings_reject_empty_critical_fields():
    with pytest.raises(ValueError):
        Settings(
            DATABASE_URL=" ",
            SECRET_KEY="secret",
            AWS_ACCESS_KEY_ID="key",
            AWS_SECRET_ACCESS_KEY="secret",
            AWS_BUCKET_NAME="bucket",
            OPENAI_API_KEY="openai",
            SMTP_USERNAME="user",
            SMTP_PASSWORD="password",
        )


def test_validate_runtime_configuration_accepts_non_placeholder_values():
    settings = Settings(
        DATABASE_URL="mongodb://localhost:27017/test",
        SECRET_KEY="real-secret-key",
        AWS_ACCESS_KEY_ID="aws-key",
        AWS_SECRET_ACCESS_KEY="aws-secret",
        AWS_BUCKET_NAME="bucket",
        OPENAI_API_KEY="openai-key",
        SMTP_USERNAME="smtp-user",
        SMTP_PASSWORD="smtp-password",
    )

    settings.validate_runtime_configuration()


def test_validate_runtime_configuration_rejects_placeholder_values():
    settings = Settings(
        DATABASE_URL="mongodb://localhost:27017/test",
        SECRET_KEY="SECRET_KEY",
        AWS_ACCESS_KEY_ID="aws-key",
        AWS_SECRET_ACCESS_KEY="aws-secret",
        AWS_BUCKET_NAME="bucket",
        OPENAI_API_KEY="openai-key",
        SMTP_USERNAME="smtp-user",
        SMTP_PASSWORD="smtp-password",
    )

    with pytest.raises(ValueError, match="placeholder critical settings"):
        settings.validate_runtime_configuration()
