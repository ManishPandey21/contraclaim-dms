"""`search()` must not re-fetch document metadata it never reads.

`RetrievalService.search` used to run

    doc_meta = await self._fetch_documents_meta(candidate_ids)

and then never read `doc_meta`. It was left behind when containment moved from
"strip the blocked document's title" to "drop the blocked result entirely":
`_blocked_document_ids` resolves the documents it needs itself, so the earlier
lookup became a second Mongo round trip per search whose result was discarded.
ruff reported it as F841 the moment the hook's file scope was repaired.

Removing a query is a behaviour change until it is shown not to be one, so this
pins both halves: the fetch is gone, and containment still drops the blocked
result rather than merely relabelling it.
"""

from __future__ import annotations

import pytest

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.retrieval.models import SearchBackend, SearchFilters, SearchRequest
from rbac_backend.retrieval.service import RetrievalService
from rbac_backend.retrieval.vector_client import VectorClient


class _FakeEmbeddings:
    async def embed(self, texts, model=None):
        return [[0.1, 0.2] for _ in texts]


class _Obs:
    async def log_run(self, **kwargs):
        return None


def _disabled_vector_client() -> VectorClient:
    config = DocumentProcessingConfig()
    config.qdrant_url = None  # disabled by config: the Mongo path is legitimate
    return VectorClient(config)


def _service() -> RetrievalService:
    return RetrievalService(
        db=None,
        embedding_client=_FakeEmbeddings(),
        vector_client=_disabled_vector_client(),
        llm_generator=None,
        observability=_Obs(),
    )


def _two_hits():
    return [
        {"score": 0.9, "payload": {"document_id": "d1", "chunk_id": "m1", "text": "allowed"}},
        {"score": 0.8, "payload": {"document_id": "d2", "chunk_id": "m2", "text": "blocked"}},
    ]


def _request() -> SearchRequest:
    return SearchRequest(
        query="anything",
        limit=5,
        backend=SearchBackend.AUTO,
        filters=SearchFilters(org_id="org-A", project_id="proj-A"),
    )


@pytest.mark.asyncio
async def test_search_does_not_fetch_document_metadata_it_never_reads():
    service = _service()
    calls: list[list[str]] = []

    async def _mongo(request):
        return _two_hits()

    async def _tripwire(ids):
        calls.append(list(ids))
        return {}

    async def _blocked(ids):
        return {"d2"}

    service._search_mongo = _mongo
    service._fetch_documents_meta = _tripwire
    service._blocked_document_ids = _blocked

    response = await service.search(_request(), current_user=None, log_run=False)

    assert calls == [], (
        f"search() fetched document metadata it does not read: {calls}. "
        f"Containment resolves its own documents in _blocked_document_ids."
    )
    # Behaviour that must survive the removal: the blocked document is dropped
    # whole, not returned with its title stripped.
    assert [r.chunk_id for r in response.results] == ["m1"]
    assert all(r.document_id != "d2" for r in response.results)
    assert all("blocked" not in (r.snippet or "") for r in response.results)


@pytest.mark.asyncio
async def test_search_still_returns_every_unblocked_result():
    service = _service()

    async def _mongo(request):
        return _two_hits()

    async def _blocked(ids):
        return set()

    service._search_mongo = _mongo
    service._blocked_document_ids = _blocked

    response = await service.search(_request(), current_user=None, log_run=False)

    assert [r.chunk_id for r in response.results] == ["m1", "m2"]
