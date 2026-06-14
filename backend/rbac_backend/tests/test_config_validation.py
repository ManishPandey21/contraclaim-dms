import pytest

from rbac_backend.core.config import Settings


def test_settings_do_not_embed_secret_placeholders_as_defaults():
    sensitive_defaults = {
        "SECRET_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_BUCKET_NAME",
        "OPENAI_API_KEY",
        "ASSISTANT_ID",
        "ASSISTANT_ID1",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM_EMAIL",
        "CONTACT_RECIPIENT_EMAIL",
    }

    for field_name in sensitive_defaults:
        assert Settings.model_fields[field_name].default == ""


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


def test_production_validation_rejects_standalone_mongodb():
    settings = Settings(
        ENVIRONMENT="production",
        DATABASE_URL="mongodb://mongo.example.internal:27017/contraclaim",
        SECRET_KEY="x" * 32,
        AWS_ACCESS_KEY_ID="aws-key",
        AWS_SECRET_ACCESS_KEY="aws-secret",
        AWS_BUCKET_NAME="bucket",
        OPENAI_API_KEY="openai-key",
        SMTP_USERNAME="smtp-user",
        SMTP_PASSWORD="smtp-password",
        CORS_ORIGINS='["https://app.contraclaim.com"]',
        LANGGRAPH_ENABLED=False,
        APP_REDIS_URL="redis://redis:6379/1",
        METRICS_TOKEN="metrics-token",
        AUTH_COOKIE_SECURE=True,
    )

    with pytest.raises(ValueError, match="replica set"):
        settings.validate_runtime_configuration()


def test_production_validation_accepts_replicaset_mongodb():
    settings = Settings(
        ENVIRONMENT="production",
        DATABASE_URL="mongodb://mongo1:27017,mongo2:27017/contraclaim?replicaSet=rs0",
        SECRET_KEY="x" * 32,
        AWS_ACCESS_KEY_ID="aws-key",
        AWS_SECRET_ACCESS_KEY="aws-secret",
        AWS_BUCKET_NAME="bucket",
        OPENAI_API_KEY="openai-key",
        SMTP_USERNAME="smtp-user",
        SMTP_PASSWORD="smtp-password",
        CORS_ORIGINS='["https://app.contraclaim.com"]',
        LANGGRAPH_ENABLED=False,
        APP_REDIS_URL="redis://redis:6379/1",
        METRICS_TOKEN="metrics-token",
        RBAC_ENTITLEMENT_FAIL_OPEN=False,
        AUTH_COOKIE_SECURE=True,
    )

    settings.validate_runtime_configuration()


def test_production_validation_rejects_entitlement_fail_open():
    settings = Settings(
        ENVIRONMENT="production",
        DATABASE_URL="mongodb://mongo1:27017,mongo2:27017/contraclaim?replicaSet=rs0",
        SECRET_KEY="x" * 32,
        AWS_ACCESS_KEY_ID="aws-key",
        AWS_SECRET_ACCESS_KEY="aws-secret",
        AWS_BUCKET_NAME="bucket",
        OPENAI_API_KEY="openai-key",
        SMTP_USERNAME="smtp-user",
        SMTP_PASSWORD="smtp-password",
        CORS_ORIGINS='["https://app.contraclaim.com"]',
        LANGGRAPH_ENABLED=False,
        APP_REDIS_URL="redis://redis:6379/1",
        METRICS_TOKEN="metrics-token",
        RBAC_ENTITLEMENT_FAIL_OPEN=True,
        AUTH_COOKIE_SECURE=True,
    )

    with pytest.raises(ValueError, match="RBAC_ENTITLEMENT_FAIL_OPEN"):
        settings.validate_runtime_configuration()
