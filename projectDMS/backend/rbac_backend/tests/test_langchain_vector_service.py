import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from backend.rbac_backend.config.document_processing_config import DocumentProcessingConfig
from backend.rbac_backend.retrieval.correspondence_payload import build_correspondence_chunks
from backend.rbac_backend.retrieval.vector_client import VectorClient
from backend.rbac_backend.services.langchain_vector_service import LangChainVectorService


class _FakeEmbeddings:
    def embed_documents(self, texts):
        return [[float(len(text)), 1.0] for text in texts]


class _FakeVectorStore:
    embeddings = _FakeEmbeddings()


class _RecordingClient:
    def __init__(self) -> None:
        self.upserts = []

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)


@pytest.mark.asyncio
async def test_replace_document_writes_canonical_flat_points_with_uuid_ids():
    from qdrant_client.http import models as qmodels

    service = LangChainVectorService.__new__(LangChainVectorService)
    service._enabled = True
    service._vector_store = _FakeVectorStore()
    service._client = _RecordingClient()
    service._qdrant_models = qmodels
    service.config = SimpleNamespace(
        openai_embedding_model="text-embedding-test",
        qdrant_collection="documents",
        qdrant_vector_name=None,
    )

    async def fake_delete_document(*args, **kwargs):
        return True

    service.delete_document = fake_delete_document

    document_id = "6a4fca1d9001c718f215e9d3"
    [point] = build_correspondence_chunks(
        {"_id": document_id, "organization_id": "org-1", "project_id": "project-1"},
        ["Delay notice and retention clause text"],
        embedding_model="text-embedding-test",
    )

    written = await service.replace_document([point])

    assert written == 1
    [call] = service._client.upserts
    [stored] = call["points"]
    UUID(str(stored.id))
    # The application chunk id is kept in the payload; the point id is a UUID.
    assert str(stored.id) != point["chunk_id"]
    assert stored.payload["chunk_id"] == point["chunk_id"]
    assert stored.payload["qdrant_point_id"] == str(stored.id)
    # Flat, the shape the tenant-scoped readers filter on (DI-B1).
    assert stored.payload["org_id"] == "org-1"
    assert stored.payload["project_id"] == "project-1"
    assert "metadata" not in stored.payload


@pytest.mark.asyncio
async def test_delete_document_matches_flat_and_nested_payload_schemas():
    class _Client:
        def __init__(self) -> None:
            self.call = None

        def delete(self, **kwargs):
            self.call = kwargs

    class _Models:
        class MatchValue:
            def __init__(self, value):
                self.value = value

        class FieldCondition:
            def __init__(self, key, match):
                self.key = key
                self.match = match

        class Filter:
            def __init__(self, must=None, should=None):
                self.must = must
                self.should = should

        class FilterSelector:
            def __init__(self, filter):
                self.filter = filter

    service = LangChainVectorService.__new__(LangChainVectorService)
    service._enabled = True
    service._qdrant_models = _Models
    service._client = _Client()
    service.config = SimpleNamespace(qdrant_collection="documents")

    deleted = await service.delete_document(
        "document-1",
        organization_id="org-1",
        project_id="project-1",
    )

    assert deleted is True
    call = service._client.call
    assert call["wait"] is True
    variants = call["points_selector"].filter.should
    assert len(variants) == 4
    keys = {condition.key for variant in variants for condition in variant.must}
    assert {"document_id", "metadata.document_id"}.issubset(keys)
    assert {"org_id", "organization_id", "metadata.org_id", "metadata.organization_id"}.issubset(keys)


@pytest.mark.asyncio
async def test_canonical_payload_is_not_reconciled_as_chunks_and_is_deleted_by_document(monkeypatch):
    """Canonical letter points are hidden from chunks reconcilers and deleted by document."""
    from langchain_core.embeddings import Embeddings

    class _DeterministicEmbeddings(Embeddings):
        def __init__(self, *args, **kwargs):
            pass

        @staticmethod
        def _embed(text):
            return [
                1.0 if "retention" in text.lower() else 0.0,
                1.0 if "notice" in text.lower() else 0.0,
                0.0,
                1.0,
            ]

        def embed_documents(self, texts):
            return [self._embed(text) for text in texts]

        def embed_query(self, text):
            return self._embed(text)

    import langchain_openai

    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", _DeterministicEmbeddings)
    config = DocumentProcessingConfig()
    # Project settings may populate defaults during construction. Override the
    # isolated test instance afterwards so its collection and embedding shape
    # remain deterministic regardless of test order.
    config.openai_api_key = "test-key"
    config.qdrant_url = ":memory:"
    config.qdrant_collection = f"langchain-schema-{uuid4().hex}"
    config.qdrant_vector_size = 4
    config.qdrant_distance = "cosine"
    config.vector_store_enabled = True
    config.vector_dual_write_enabled = True
    service = LangChainVectorService(config)
    document_id = f"document-{uuid4().hex}"
    chunk_id = f"{document_id}-chunk-0"

    try:
        [point] = build_correspondence_chunks(
            {"_id": document_id, "organization_id": "org-1", "project_id": "project-1"},
            ["Retention notice content"],
            embedding_model="test-embedding",
        )
        chunk_id = point["chunk_id"]
        written = await service.replace_document([point])

        native_client = VectorClient.__new__(VectorClient)
        native_client.config = config
        native_client._client = service._client
        native_client._qmodels = service._qdrant_models
        native_client.enabled = True
        native_client.collection_name = config.qdrant_collection
        native_client._memory_index = []

        assert written == 1, service._init_error
        # The native chunks reconcilers must not list a letter's points: they
        # delete whatever `db.chunks` lacks, and letters have no chunks rows.
        assert await native_client.list_chunk_ids(
            {
                "org_id": "org-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "document_id": document_id,
            }
        ) == []
        stored, _ = await asyncio.to_thread(
            service._client.scroll,
            collection_name=config.qdrant_collection,
            limit=10,
            with_payload=True,
            with_vectors=False,
        )
        assert [point.payload["chunk_id"] for point in stored] == [chunk_id]
        assert await service.delete_document(
            document_id,
            organization_id="org-1",
            project_id="project-1",
        ) is True

        remaining, _ = await asyncio.to_thread(
            service._client.scroll,
            collection_name=config.qdrant_collection,
            limit=10,
            with_payload=True,
            with_vectors=False,
        )
        assert remaining == []
    finally:
        await asyncio.to_thread(service._client.delete_collection, config.qdrant_collection)
