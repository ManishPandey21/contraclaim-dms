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


def _production_settings(**overrides):
    base = dict(
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
        BACKUP_ROOT="/var/backups/contractdms",
        BACKUP_S3_BUCKET="backups",
        ANTIVIRUS_ENABLED=True,
        CLAMAV_FAIL_OPEN=False,
    )
    base.update(overrides)
    return Settings(**base)


def test_production_razorpay_requires_webhook_secret():
    """A missing webhook secret means customers pay but never activate:
    signature verification is deny-by-default, so every activation webhook is
    rejected. Startup must refuse this configuration."""
    settings = _production_settings(
        PAYMENT_PROVIDER="razorpay",
        RAZORPAY_KEY_ID="rzp_live_key",
        RAZORPAY_KEY_SECRET="rzp_live_secret",
        RAZORPAY_WEBHOOK_SECRET="",
    )
    with pytest.raises(ValueError, match="RAZORPAY_WEBHOOK_SECRET"):
        settings.validate_runtime_configuration()


def test_production_razorpay_fully_configured_passes():
    settings = _production_settings(
        PAYMENT_PROVIDER="razorpay",
        RAZORPAY_KEY_ID="rzp_live_key",
        RAZORPAY_KEY_SECRET="rzp_live_secret",
        RAZORPAY_WEBHOOK_SECRET="whsec",
    )
    settings.validate_runtime_configuration()


def test_production_noop_provider_needs_no_razorpay_config():
    settings = _production_settings(PAYMENT_PROVIDER="noop")
    settings.validate_runtime_configuration()


def test_production_primary_arbitration_rollout_requires_phase6_cutover_controls():
    settings = _production_settings(
        ARBITRATION_ENGINE_DEFAULT="langgraph_v1",
        ARBITRATION_ENGINE_ROLLOUT_MODE="primary",
        ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=True,
        ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY=False,
    )

    with pytest.raises(ValueError) as exc:
        settings.validate_runtime_configuration()

    message = str(exc.value)
    assert "ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID" in message
    assert "explicit tenant/project allowlist" in message
    assert "ARBITRATION_ENGINE_V2_COMPATIBILITY_UNTIL" in message
    assert "ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY=true" in message


def test_production_primary_arbitration_rollout_accepts_complete_phase6_controls():
    settings = _production_settings(
        ARBITRATION_ENGINE_DEFAULT="langgraph_v1",
        ARBITRATION_ENGINE_ROLLOUT_MODE="primary",
        ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=True,
        ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID="phase6-acceptance-2026-07-22",
        ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256="a" * 64,
        ARBITRATION_ENGINE_PRIMARY_TENANT_IDS="accepted-tenant",
        ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY=True,
        ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE="active",
        ARBITRATION_ENGINE_V2_COMPATIBILITY_UNTIL="2099-12-31",
        FILING_EXPORT_QUEUE_ENABLED=True,
        FILING_EXPORT_QUEUE_REDIS_URL="redis://redis:6379/1",
    )

    settings.validate_runtime_configuration()


def test_production_primary_arbitration_rollout_rejects_unaccepted_v2_retirement():
    settings = _production_settings(
        ARBITRATION_ENGINE_DEFAULT="langgraph_v1",
        ARBITRATION_ENGINE_ROLLOUT_MODE="primary",
        ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=True,
        ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID="phase6-acceptance-2026-07-22",
        ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256="a" * 64,
        ARBITRATION_ENGINE_PRIMARY_PERCENT=100,
        ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY=True,
        ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE="retired",
        ARBITRATION_ENGINE_V2_COMPATIBILITY_UNTIL="2099-12-31",
        FILING_EXPORT_QUEUE_ENABLED=True,
        FILING_EXPORT_QUEUE_REDIS_URL="redis://redis:6379/1",
    )

    with pytest.raises(ValueError, match="separate v2 deprecation decision"):
        settings.validate_runtime_configuration()


def test_production_primary_arbitration_rollout_requires_durable_filing_export_queue():
    settings = _production_settings(
        ARBITRATION_ENGINE_DEFAULT="langgraph_v1",
        ARBITRATION_ENGINE_ROLLOUT_MODE="primary",
        ARBITRATION_ENGINE_PRODUCTION_ACCEPTED=True,
        ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_ID="phase6-acceptance-2026-07-22",
        ARBITRATION_ENGINE_ACCEPTANCE_RECEIPT_SHA256="a" * 64,
        ARBITRATION_ENGINE_PRIMARY_PERCENT=1,
        ARBITRATION_ENGINE_PRIMARY_REQUIRE_HEALTH_READY=True,
        ARBITRATION_ENGINE_V2_COMPATIBILITY_MODE="active",
        ARBITRATION_ENGINE_V2_COMPATIBILITY_UNTIL="2099-12-31",
        FILING_EXPORT_QUEUE_ENABLED=False,
    )

    with pytest.raises(ValueError, match="FILING_EXPORT_QUEUE_ENABLED=true"):
        settings.validate_runtime_configuration()
