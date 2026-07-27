"""Storage-sync monitor: actionable Qdrant hints + transition-based logging.

Guards the fix for the recurring opaque "Storage sync monitor detected issues:
... client_init_failed" warning that was logged identically every 5 minutes.
"""

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.routers.storage_sync import (
    WARN_REPEAT_SECONDS,
    _candidate_id_query,
    _qdrant_document_filter,
    _qdrant_hint,
    _should_emit_warning,
)


def _config(url, api_key):
    cfg = DocumentProcessingConfig()
    cfg.qdrant_url = url
    cfg.qdrant_api_key = api_key
    return cfg


def test_qdrant_hint_flags_insecure_url_with_api_key():
    hint = _qdrant_hint(_config("http://qdrant:6333", "secret"), "client_init_failed")
    assert hint and "https" in hint and "QDRANT_API_KEY" in hint


def test_qdrant_hint_explains_config_error():
    cfg = _config("http://qdrant.example.com:6333", "secret")
    hint = _qdrant_hint(cfg, "qdrant_config_error")

    assert hint and "https" in hint and "QDRANT_API_KEY" in hint


def test_qdrant_hint_quiet_when_config_is_fine():
    assert _qdrant_hint(_config("https://qdrant:6333", "secret"), "client_init_failed") is None
    assert _qdrant_hint(_config("http://qdrant:6333", None), "client_init_failed") is None


def test_qdrant_hint_for_missing_client():
    assert "qdrant-client" in (_qdrant_hint(_config("http://q", None), "qdrant_client_missing") or "")


def test_candidate_id_query_matches_objectid_or_string():
    query = _candidate_id_query("695b562ed72ae1818b362978")

    assert "$or" in query
    ids = [condition["_id"] for condition in query["$or"]]
    assert "695b562ed72ae1818b362978" in ids
    assert any(value.__class__.__name__ == "ObjectId" for value in ids)


def test_qdrant_document_filter_matches_native_and_langchain_layouts():
    class _Models:
        class MatchValue:
            def __init__(self, value):
                self.value = value

        class FieldCondition:
            def __init__(self, key, match):
                self.key = key
                self.match = match

        class Filter:
            def __init__(self, should=None):
                self.should = should

    result = _qdrant_document_filter(_Models, "document-1")

    assert [condition.key for condition in result.should] == [
        "document_id",
        "metadata.document_id",
    ]
    assert all(condition.match.value == "document-1" for condition in result.should)
def test_warning_only_on_change_then_heartbeat():
    sig = ("qdrant client_init_failed",)

    # First appearance → warn.
    assert _should_emit_warning(sig, None, now=100.0, last_warn_at=0.0) is True
    # Same issue set shortly after → stay quiet (DEBUG).
    assert _should_emit_warning(sig, sig, now=400.0, last_warn_at=100.0) is False
    # A changed issue set → warn again.
    assert _should_emit_warning(("new issue",), sig, now=500.0, last_warn_at=100.0) is True
    # Unchanged but past the heartbeat window → warn again so it isn't silent.
    assert _should_emit_warning(sig, sig, now=100.0 + WARN_REPEAT_SECONDS, last_warn_at=100.0) is True
