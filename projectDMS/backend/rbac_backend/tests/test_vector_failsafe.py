"""Fail-visible retrieval: Qdrant outages must raise, tenancy must be required.

Regression tests for the silent-degradation incident class: an enabled-but-
failing Qdrant used to fall back to the (empty in production) in-memory index,
so outages presented as confident "no results". These tests pin the new
contract: enabled+failing raises VectorStoreUnavailableError (and increments
the failure metric), disabled stays on the in-memory index, and every query
must carry an org_id unless it explicitly opts into a global view.
"""

from __future__ import annotations

import pytest

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.retrieval.models import SearchBackend, SearchFilters, SearchRequest
from rbac_backend.retrieval.service import RetrievalService
from rbac_backend.retrieval.vector_client import (
    VectorClient,
    VectorScopeError,
    VectorStoreUnavailableError,
)
from rbac_backend.routers import health
from rbac_backend.services.observability import observability_registry


def _offline_client() -> VectorClient:
    config = DocumentProcessingConfig()
    config.qdrant_url = None  # disabled by config -> in-memory mode is legitimate
    return VectorClient(config)


def _failing_client() -> VectorClient:
    client = _offline_client()
    client.enabled = True  # enabled-but-broken: raising client stands in for Qdrant

    class _Boom:
        def __getattr__(self, name):
            def _fail(*_a, **_k):
                raise ConnectionError("qdrant down")

            return _fail

    class _Models:
        class Filter:
            def __init__(self, must=None):
                self.must = must

        class FieldCondition:
            def __init__(self, **kwargs):
                pass

        class MatchValue:
            def __init__(self, **kwargs):
                pass

        class MatchAny:
            def __init__(self, **kwargs):
                pass

    client._client = _Boom()
    client._qmodels = _Models()
    # Passes the upfront health probe, then fails in flight — the exact shape
    # of a Qdrant outage mid-request (or expiring credentials).
    client.is_healthy = lambda: True  # type: ignore[method-assign]
    return client


FILTERS = {"org_id": "org-A", "project_id": "proj-A"}


# --- enabled-but-failing raises ---------------------------------------------


@pytest.mark.asyncio
async def test_enabled_failing_search_raises_not_empty():
    before = observability_registry.snapshot()["vector_store_failure_total"]
    with pytest.raises(VectorStoreUnavailableError):
        await _failing_client().search([0.1, 0.2], filters=dict(FILTERS))
    after = observability_registry.snapshot()["vector_store_failure_total"]
    assert after == before + 1, "failure must be counted for alerting"


@pytest.mark.asyncio
async def test_enabled_failing_list_chunk_ids_raises():
    with pytest.raises(VectorStoreUnavailableError):
        await _failing_client().list_chunk_ids(dict(FILTERS))


@pytest.mark.asyncio
async def test_list_chunk_ids_returns_payload_chunk_id_from_qdrant_scroll():
    client = _offline_client()
    client.enabled = True

    class _Point:
        id = "uuid-point-id"
        payload = {"chunk_id": "legacy-chunk-id"}

    class _ScrollClient:
        def scroll(self, **kwargs):
            assert kwargs["with_payload"] is True
            return ([_Point()], None)

    class _Models:
        class Filter:
            def __init__(self, must=None):
                self.must = must

        class FieldCondition:
            def __init__(self, **kwargs):
                pass

        class MatchValue:
            def __init__(self, **kwargs):
                pass

        class MatchAny:
            def __init__(self, **kwargs):
                pass

    client._client = _ScrollClient()
    client._qmodels = _Models()

    assert await client.list_chunk_ids(dict(FILTERS)) == ["legacy-chunk-id"]


@pytest.mark.asyncio
async def test_list_chunk_ids_reads_nested_langchain_metadata():
    client = _offline_client()
    client.enabled = True

    class _Point:
        id = "uuid-point-id"
        payload = {"metadata": {"chunk_id": "langchain-chunk-id"}}

    class _ScrollClient:
        def __init__(self):
            self.filters = []

        def scroll(self, **kwargs):
            self.filters.append(kwargs["scroll_filter"])
            return ([_Point()], None)

    class _Models:
        class Filter:
            def __init__(self, must=None):
                self.must = must

        class FieldCondition:
            def __init__(self, **kwargs):
                self.key = kwargs["key"]

        class MatchValue:
            def __init__(self, **kwargs):
                pass

        class MatchAny:
            def __init__(self, **kwargs):
                pass

    scroll_client = _ScrollClient()
    client._client = scroll_client
    client._qmodels = _Models()

    assert await client.list_chunk_ids(dict(FILTERS)) == ["langchain-chunk-id"]
    assert len(scroll_client.filters) == 2
    nested_keys = [condition.key for condition in scroll_client.filters[1].must]
    assert nested_keys == ["metadata.org_id", "metadata.project_id"]


@pytest.mark.asyncio
async def test_disabled_client_still_serves_in_memory_index():
    client = _offline_client()
    await client.upsert(
        [[1.0, 0.0]],
        [{"org_id": "org-A", "project_id": "proj-A", "chunk_id": "c1", "text": "alpha", "document_id": "d1"}],
    )
    results = await client.search([1.0, 0.0], filters=dict(FILTERS))
    assert [r["payload"]["chunk_id"] for r in results] == ["c1"]


# --- tenancy enforcement -----------------------------------------------------


@pytest.mark.asyncio
async def test_search_without_org_id_is_refused():
    with pytest.raises(VectorScopeError):
        await _offline_client().search([0.1], filters={"project_id": "proj-A"})


@pytest.mark.asyncio
async def test_list_chunk_ids_without_org_id_is_refused():
    with pytest.raises(VectorScopeError):
        await _offline_client().list_chunk_ids({"document_id": "d1"})


@pytest.mark.asyncio
async def test_allow_global_opts_into_cross_org_query():
    results = await _offline_client().search([0.1], filters={"org_id": None}, allow_global=True)
    assert results == []


# --- retrieval service engages the Mongo failsafe ----------------------------


class _FakeEmbeddings:
    async def embed(self, texts, model=None):
        return [[0.1, 0.2] for _ in texts]


class _Obs:
    async def log_run(self, **kwargs):
        return None


@pytest.mark.asyncio
async def test_qdrant_outage_falls_back_to_mongo_not_empty_200():
    service = RetrievalService(
        db=None,
        embedding_client=_FakeEmbeddings(),
        vector_client=_failing_client(),
        llm_generator=None,
        observability=_Obs(),
    )
    sentinel = [{"score": 0.5, "payload": {"document_id": "d1", "chunk_id": "m1", "text": "from mongo"}}]

    async def _mongo(request):
        return sentinel

    service._search_mongo = _mongo
    service._fetch_documents_meta = lambda ids: _empty_meta()

    request = SearchRequest(
        query="anything",
        limit=3,
        backend=SearchBackend.AUTO,
        filters=SearchFilters(org_id="org-A", project_id="proj-A"),
    )
    response = await service.search(request, current_user=None, log_run=False)
    assert response.backend_used == SearchBackend.MONGO
    assert [r.chunk_id for r in response.results] == ["m1"]


async def _empty_meta():
    return {}


# --- degraded dependency health ----------------------------------------------


@pytest.mark.asyncio
async def test_degraded_checks_never_raise_and_are_recorded(monkeypatch):
    async def _boom():
        raise ConnectionError("down")

    async def _fine():
        return {"status": "ok"}

    monkeypatch.setattr(health, "_check_qdrant", _boom)
    monkeypatch.setattr(health, "_check_falkordb", _fine)
    monkeypatch.setattr(health, "_check_clamav", _fine)

    results = await health._degraded_dependency_checks(include_details=False)
    assert results["qdrant"]["status"] == "degraded"
    assert results["qdrant"]["error"] == "check failed"  # details hidden without token
    assert results["falkordb"]["status"] == "ok"
    snapshot = observability_registry.snapshot()
    assert snapshot["dependency_health"]["qdrant"] == 0.0
    assert snapshot["dependency_health"]["falkordb"] == 1.0


@pytest.mark.asyncio
async def test_disabled_dependencies_report_skipped(monkeypatch):
    monkeypatch.setattr(health.settings, "FALKORDB_ENABLED", False)
    monkeypatch.setattr(health.settings, "ANTIVIRUS_ENABLED", False)
    assert (await health._check_falkordb())["status"] == "skipped"
    assert (await health._check_clamav())["status"] == "skipped"


def test_prometheus_render_includes_new_metrics():
    text = observability_registry.render_prometheus()
    assert "contractdms_vector_store_failures_total" in text
    assert "contractdms_dependency_up" in text
