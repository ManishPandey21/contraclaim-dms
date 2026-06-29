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


def test_entitlement_checks_fail_closed_by_default():
    assert Settings.model_fields["RBAC_ENTITLEMENT_FAIL_OPEN"].default is False


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
        BACKUP_S3_BUCKET="backup-bucket",
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
        BACKUP_S3_BUCKET="backup-bucket",
        ANTIVIRUS_ENABLED=True,
        CLAMAV_FAIL_OPEN=False,
    )

    settings.validate_runtime_configuration()


def test_production_validation_requires_antivirus_enabled():
    """P0-005: production must run with upload antivirus enabled by default."""
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
        BACKUP_S3_BUCKET="backup-bucket",
        ANTIVIRUS_ENABLED=False,
    )

    with pytest.raises(ValueError, match="ANTIVIRUS_ENABLED"):
        settings.validate_runtime_configuration()


def test_production_validation_rejects_antivirus_fail_open():
    """P0-005: enabled antivirus must fail closed (CLAMAV_FAIL_OPEN=false)."""
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
        BACKUP_S3_BUCKET="backup-bucket",
        ANTIVIRUS_ENABLED=True,
        CLAMAV_FAIL_OPEN=True,
    )

    with pytest.raises(ValueError, match="CLAMAV_FAIL_OPEN"):
        settings.validate_runtime_configuration()


def test_production_validation_allows_explicit_antivirus_opt_out():
    """The risk can be explicitly accepted, but only via a recorded override."""
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
        BACKUP_S3_BUCKET="backup-bucket",
        ANTIVIRUS_ENABLED=False,
        ANTIVIRUS_REQUIRED_IN_PRODUCTION=False,
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
        BACKUP_S3_BUCKET="backup-bucket",
    )

    with pytest.raises(ValueError, match="RBAC_ENTITLEMENT_FAIL_OPEN"):
        settings.validate_runtime_configuration()


def test_production_validation_rejects_insecure_auth_surface():
    settings = Settings(
        ENVIRONMENT="production",
        DATABASE_URL="mongodb://mongo1:27017,mongo2:27017/contraclaim?replicaSet=rs0",
        SECRET_KEY="short",
        AWS_ACCESS_KEY_ID="aws-key",
        AWS_SECRET_ACCESS_KEY="aws-secret",
        AWS_BUCKET_NAME="bucket",
        OPENAI_API_KEY="openai-key",
        SMTP_USERNAME="smtp-user",
        SMTP_PASSWORD="smtp-password",
        CORS_ORIGINS='["http://localhost:5173"]',
        LANGGRAPH_ENABLED=False,
        APP_REDIS_URL="redis://redis:6379/1",
        METRICS_TOKEN="metrics-token",
        ALLOW_DEV_HEADERS=True,
        RBAC_ENTITLEMENT_FAIL_OPEN=True,
        AUTH_COOKIE_SECURE=False,
        BACKUP_S3_BUCKET="backup-bucket",
    )

    with pytest.raises(ValueError) as exc:
        settings.validate_runtime_configuration()

    message = str(exc.value)
    assert "ALLOW_DEV_HEADERS" in message
    assert "RBAC_ENTITLEMENT_FAIL_OPEN" in message
    assert "SECRET_KEY" in message
    assert "AUTH_COOKIE_SECURE" in message
    assert "CORS_ORIGINS" in message


def test_production_validation_rejects_missing_backup_bucket_when_required():
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
        BACKUP_REQUIRED_IN_PRODUCTION=True,
        BACKUP_S3_BUCKET="",
    )

    with pytest.raises(ValueError, match="BACKUP_S3_BUCKET"):
        settings.validate_runtime_configuration()
