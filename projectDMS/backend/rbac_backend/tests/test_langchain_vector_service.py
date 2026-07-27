import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from backend.rbac_backend.config.document_processing_config import DocumentProcessingConfig
from backend.rbac_backend.retrieval.vector_client import VectorClient
from backend.rbac_backend.services.langchain_vector_service import LangChainVectorService


class _FakeVectorStore:
    def __init__(self) -> None:
        self.calls = []

    def add_texts(self, *, texts, metadatas, ids):
        self.calls.append({"texts": texts, "metadatas": metadatas, "ids": ids})


@pytest.mark.asyncio
async def test_replace_document_uses_uuid_point_ids_and_preserves_chunk_id():
    service = LangChainVectorService.__new__(LangChainVectorService)
    service._enabled = True
    service._vector_store = _FakeVectorStore()
    service.config = SimpleNamespace(
        openai_embedding_model="text-embedding-test",
        qdrant_collection="documents",
    )

    async def fake_delete_document(*args, **kwargs):
        return True

    service.delete_document = fake_delete_document

    document_id = "6a4fca1d9001c718f215e9d3"
    legacy_chunk_id = f"{document_id}-31f19f1f79f844317014c5d1"

    written = await service.replace_document(
        [
            {
                "text": "Delay notice and retention clause text",
                "checksum": "checksum-1",
                "chunk_id": legacy_chunk_id,
                "metadata": {
                    "document_id": document_id,
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "chunk_id": legacy_chunk_id,
                    "chunk_index": 0,
                },
            }
        ]
    )

    assert written == 1
    call = service._vector_store.calls[0]
    [point_id] = call["ids"]
    UUID(point_id)
    assert point_id != legacy_chunk_id
    assert call["metadatas"][0]["chunk_id"] == legacy_chunk_id
    assert call["metadatas"][0]["qdrant_point_id"] == point_id


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
async def test_langchain_payload_is_reconciled_and_deleted_by_native_client(monkeypatch):
    """Reconciliation must recognize the metadata envelope written by LangChain."""
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
        written = await service.replace_document(
            [
                {
                    "text": "Retention notice content",
                    "checksum": "checksum-1",
                    "chunk_id": chunk_id,
                    "metadata": {
                        "document_id": document_id,
                        "org_id": "org-1",
                        "organization_id": "org-1",
                        "project_id": "project-1",
                        "chunk_id": chunk_id,
                        "chunk_index": 0,
                    },
                }
            ]
        )

        native_client = VectorClient.__new__(VectorClient)
        native_client.config = config
        native_client._client = service._client
        native_client._qmodels = service._qdrant_models
        native_client.enabled = True
        native_client.collection_name = config.qdrant_collection
        native_client._memory_index = []

        assert written == 1, service._init_error
        assert await native_client.list_chunk_ids(
            {
                "org_id": "org-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "document_id": document_id,
            }
        ) == [chunk_id]
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
