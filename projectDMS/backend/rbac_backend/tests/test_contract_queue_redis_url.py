"""The contract ingest queue must use the app's Redis, never FalkorDB."""

import pytest

from rbac_backend.core.config import settings
from rbac_backend.services.contract_ingest_queue import ContractIngestQueue


def test_redis_url_prefers_contract_queue_url(monkeypatch):
    monkeypatch.setattr(settings, "CONTRACT_QUEUE_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(settings, "APP_REDIS_URL", "redis://localhost:6379/1")
    assert ContractIngestQueue().redis_url == "redis://localhost:6379/0"


def test_redis_url_falls_back_to_app_redis(monkeypatch):
    monkeypatch.setattr(settings, "CONTRACT_QUEUE_REDIS_URL", "")
    monkeypatch.setattr(settings, "APP_REDIS_URL", "redis://localhost:6379/1")
    assert ContractIngestQueue().redis_url == "redis://localhost:6379/1"


def test_redis_url_never_falls_back_to_falkordb(monkeypatch):
    monkeypatch.setattr(settings, "CONTRACT_QUEUE_REDIS_URL", "")
    monkeypatch.setattr(settings, "APP_REDIS_URL", "")
    monkeypatch.setattr(settings, "FALKORDB_URL", "redis://localhost:6380")
    with pytest.raises(RuntimeError):
        _ = ContractIngestQueue().redis_url
