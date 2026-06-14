from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rbac_backend.config.document_processing_config import (
    DocumentProcessingConfig,
    ParsedDocumentMetadata,
    ProcessingResult,
)
from rbac_backend.models.document_metadata import (
    ParsedDocumentMetadata as SharedParsedDocumentMetadata,
)
from rbac_backend.models.document_metadata import ProcessingResult as SharedProcessingResult


def _settings_stub(**overrides):
    defaults = {
        "DATABASE_URL": "mongodb://localhost:27017/contraclaim",
        "LOCAL_MONGODB_URI": None,
        "FALKORDB_URL": "redis://localhost:6380",
        "FALKORDB_HOST": "localhost",
        "FALKORDB_PORT": 6380,
        "FALKORDB_PASSWORD": None,
        "FALKORDB_GRAPH_NAME": "contraclaim",
        "FALKORDB_INDEX_NAME": "document_vectors",
        "FALKORDB_VECTOR_DIM": 1536,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_falkordb_components_override_default_url_from_settings(monkeypatch):
    from rbac_backend.core import config as core_config

    for key in (
        "FALKORDB_URL",
        "FALKORDB_HOST",
        "FALKORDB_PORT",
        "FALKORDB_PASSWORD",
        "LOCAL_MONGODB_URI",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(
        core_config,
        "settings",
        _settings_stub(
            FALKORDB_URL="redis://localhost:6380",
            FALKORDB_HOST="redis-prod",
            FALKORDB_PORT=6399,
        ),
    )

    config = DocumentProcessingConfig()

    assert config.falkordb_host == "redis-prod"
    assert config.falkordb_port == 6399
    assert config.falkordb_url == "redis://redis-prod:6399"


def test_config_creation_does_not_mutate_database_environment(monkeypatch):
    from rbac_backend.core import config as core_config

    mongo_uri = "mongodb://example:27017/config-test"
    monkeypatch.setattr(core_config, "settings", _settings_stub(DATABASE_URL=mongo_uri))

    before = {
        "DATABASE_URL": os.getenv("DATABASE_URL"),
        "MONGODB_URI": os.getenv("MONGODB_URI"),
    }

    config = DocumentProcessingConfig()

    after = {
        "DATABASE_URL": os.getenv("DATABASE_URL"),
        "MONGODB_URI": os.getenv("MONGODB_URI"),
    }

    assert config.mongo_uri == mongo_uri
    assert after == before


def test_invalid_qdrant_timeout_logs_warning(monkeypatch):
    from rbac_backend.core import config as core_config
    from rbac_backend.config import document_processing_config as config_module

    monkeypatch.setenv("QDRANT_TIMEOUT", "not-a-number")
    monkeypatch.setattr(core_config, "settings", _settings_stub(QDRANT_TIMEOUT=None))

    with patch.object(config_module.logger, "warning") as mock_warning:
        config = DocumentProcessingConfig()

    assert config.qdrant_timeout == 15.0
    mock_warning.assert_any_call(
        "Ignoring invalid %s=%r; using %s",
        "QDRANT_TIMEOUT",
        "not-a-number",
        15.0,
    )


def test_top_level_import_uses_absolute_settings_fallback(monkeypatch):
    module_path = Path(__file__).resolve().parents[1] / "config" / "document_processing_config.py"
    monkeypatch.syspath_prepend(str(module_path.parents[1]))

    top_level_core_config = importlib.import_module("core.config")
    monkeypatch.setattr(
        top_level_core_config,
        "settings",
        _settings_stub(
            DATABASE_URL="mongodb://top-level:27017/config-test",
            FALKORDB_URL="redis://localhost:6380",
            FALKORDB_HOST="redis-top-level",
            FALKORDB_PORT=6381,
        ),
    )

    module_name = "document_processing_config_top_level_test"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        config = module.DocumentProcessingConfig()
    finally:
        sys.modules.pop(module_name, None)

    assert config.mongo_uri == "mongodb://top-level:27017/config-test"
    assert config.falkordb_host == "redis-top-level"
    assert config.falkordb_port == 6381
    assert config.falkordb_url == "redis://redis-top-level:6381"


def test_document_processing_config_reexports_shared_models():
    assert ParsedDocumentMetadata is SharedParsedDocumentMetadata
    assert ProcessingResult is SharedProcessingResult


def test_shared_metadata_model_accepts_structured_references():
    metadata = ParsedDocumentMetadata(
        references=[
            "LTR-001",
            {"letter_no": "LTR-002", "date": "01-01-2026"},
        ]
    )
    result = ProcessingResult(
        success=True,
        metadata=metadata,
        metadata_source="pydantic_ai",
        metadata_debug={"trace_id": "abc123"},
    )

    assert metadata.references[0] == "LTR-001"
    assert metadata.references[1]["letter_no"] == "LTR-002"
    assert result.metadata_source == "pydantic_ai"
    assert result.metadata_debug == {"trace_id": "abc123"}


def test_legacy_document_processing_config1_shim_matches_primary_module():
    from rbac_backend.config.document_processing_config1 import (
        DocumentProcessingConfig as LegacyConfig,
        ParsedDocumentMetadata as LegacyParsedDocumentMetadata,
        ProcessingResult as LegacyProcessingResult,
        create_config as legacy_create_config,
    )

    legacy_config = legacy_create_config()

    assert isinstance(legacy_config, DocumentProcessingConfig)
    assert LegacyConfig is DocumentProcessingConfig
    assert LegacyParsedDocumentMetadata is ParsedDocumentMetadata
    assert LegacyProcessingResult is ProcessingResult


def test_config_adapter_shim_creates_primary_config():
    module_path = Path(__file__).resolve().parents[1] / "config" / "config-adapter.py"
    module_name = "config_adapter_shim_test"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        config = module.create_config()
    finally:
        sys.modules.pop(module_name, None)

    assert isinstance(config, DocumentProcessingConfig)
