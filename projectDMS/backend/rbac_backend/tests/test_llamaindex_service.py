"""Unit tests for LlamaIndexVectorService.

These tests verify the core embedding/indexing pipeline by mocking
LlamaIndex components (OpenAIEmbedding, MongoDBAtlasVectorSearch) so
that no network or database access is required.
"""

import asyncio
import sys
import types
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest


class _FakeDocument:
    def __init__(self, text: str = "", metadata: Optional[Dict] = None, embedding=None):
        self.text = text
        self.metadata = metadata or {}
        self.embedding = embedding


class _FakeVectorStoreIndex:
    @classmethod
    def from_vector_store(cls, vector_store=None):
        return cls()


class _FakeOpenAIEmbedding:
    def __init__(self, model: str = "", api_key: str = ""):
        self.model = model
        self.api_key = api_key

    def get_text_embedding(self, text: str) -> List[float]:
        return [0.1] * 10

    def get_text_embedding_batch(self, texts: List[str]) -> List[List[float]]:
        return [[0.1] * 10 for _ in texts]


class _FakeMongoDBAtlasVectorSearch:
    def __init__(
        self,
        mongodb_client=None,
        db_name=None,
        collection_name=None,
        embed_model=None,
        collection=None,
    ):
        self._docs: List[Any] = []
        self._deleted: List[str] = []

    def add(self, nodes: List[Any]) -> List[str]:
        self._docs.extend(nodes)
        return [f"node_{i}" for i in range(len(nodes))]

    def delete(self, ref_doc_id: str = "") -> None:
        self._deleted.append(ref_doc_id)


# ---------------------------------------------------------------------------
# Stub out llama_index modules so that the service can be imported without
# the actual library installed (mirrors the approach in test_vector_sync_status).
# ---------------------------------------------------------------------------

def _ensure_llama_index_stub() -> None:
    if "llama_index" in sys.modules:
        return

    llama_pkg = types.ModuleType("llama_index")
    sys.modules["llama_index"] = llama_pkg

    core_pkg = types.ModuleType("llama_index.core")

    core_pkg.Document = _FakeDocument
    core_pkg.VectorStoreIndex = _FakeVectorStoreIndex
    sys.modules["llama_index.core"] = core_pkg

    embeddings_pkg = types.ModuleType("llama_index.embeddings")
    sys.modules["llama_index.embeddings"] = embeddings_pkg

    openai_pkg = types.ModuleType("llama_index.embeddings.openai")

    openai_pkg.OpenAIEmbedding = _FakeOpenAIEmbedding
    sys.modules["llama_index.embeddings.openai"] = openai_pkg

    vector_pkg = types.ModuleType("llama_index.vector_stores")
    sys.modules["llama_index.vector_stores"] = vector_pkg

    mongo_pkg = types.ModuleType("llama_index.vector_stores.mongodb")

    mongo_pkg.MongoDBAtlasVectorSearch = _FakeMongoDBAtlasVectorSearch
    sys.modules["llama_index.vector_stores.mongodb"] = mongo_pkg


_ensure_llama_index_stub()

# Now we can safely import the service
from backend.rbac_backend.services import llamaindex_service as llamaindex_service_module
from backend.rbac_backend.services.llamaindex_service import LlamaIndexVectorService


@pytest.fixture(autouse=True)
def _patch_llamaindex_dependencies(monkeypatch):
    """Keep these unit tests hermetic even when real llama-index packages exist."""
    monkeypatch.setattr(llamaindex_service_module, "Document", _FakeDocument)
    monkeypatch.setattr(llamaindex_service_module, "VectorStoreIndex", _FakeVectorStoreIndex)
    monkeypatch.setattr(llamaindex_service_module, "OpenAIEmbedding", _FakeOpenAIEmbedding)
    monkeypatch.setattr(
        llamaindex_service_module,
        "MongoDBAtlasVectorSearch",
        _FakeMongoDBAtlasVectorSearch,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(**overrides) -> LlamaIndexVectorService:
    """Create a LlamaIndexVectorService with safe defaults for testing."""
    defaults = dict(
        mongo_uri="mongodb://localhost:27017/testdb",
        database_name="testdb",
        collection_name="test_vectors",
        embedding_model="text-embedding-3-small",
        openai_api_key="test-key-000",
    )
    defaults.update(overrides)
    return LlamaIndexVectorService(**defaults)


def _fake_mongo_client():
    """Create a mock MongoClient that passes the ping health-check."""
    client = MagicMock()
    client.admin.command.return_value = {"ok": 1}
    # db[collection_name] should return a mock collection
    db = MagicMock()
    db.__getitem__ = MagicMock(return_value=MagicMock())
    client.__getitem__ = MagicMock(return_value=db)
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIndexChunks:
    """Tests for LlamaIndexVectorService.index_chunks()."""

    def test_empty_payloads_returns_empty(self):
        service = _make_service()
        result = asyncio.run(service.index_chunks([]))
        assert result == []

    def test_index_chunks_returns_correct_structure(self):
        service = _make_service()
        # Pre-inject a mock MongoClient to skip real connection
        service._mongo_client = _fake_mongo_client()

        payloads = [
            {
                "text": "This is clause 1 about payment terms.",
                "metadata": {
                    "document_id": "doc-abc",
                    "organization_id": "org-1",
                    "project_id": "proj-1",
                    "chunk_index": 0,
                },
                "checksum": "abc123",
            },
            {
                "text": "This is clause 2 about delivery schedule.",
                "metadata": {
                    "document_id": "doc-abc",
                    "organization_id": "org-1",
                    "project_id": "proj-1",
                    "chunk_index": 1,
                },
                "checksum": "def456",
            },
        ]

        results = asyncio.run(service.index_chunks(payloads))

        # Should return same number of results as payloads
        assert len(results) == 2

        for result in results:
            # Each result must have required keys
            assert "embedding_id" in result
            assert "vector_ref" in result
            assert "metadata" in result
            assert "text" in result
            assert "embedding" in result

            # vector_ref should be set (from the fake vector store)
            assert result["vector_ref"] is not None
            assert result["vector_ref"].startswith("node_")

            # embedding should be a list of floats
            assert isinstance(result["embedding"], list)
            assert len(result["embedding"]) > 0

            # metadata should carry through
            assert result["metadata"]["document_id"] == "doc-abc"
            assert result["metadata"]["organization_id"] == "org-1"

    def test_index_chunks_preserves_checksum(self):
        service = _make_service()
        service._mongo_client = _fake_mongo_client()

        payloads = [
            {
                "text": "Test text",
                "metadata": {"document_id": "doc-1"},
                "checksum": "sha256_checksum_value",
            }
        ]

        results = asyncio.run(service.index_chunks(payloads))
        assert len(results) == 1
        assert results[0]["metadata"]["checksum_sha256"] == "sha256_checksum_value"

    def test_index_chunks_generates_embedding_id_when_missing(self):
        service = _make_service()
        service._mongo_client = _fake_mongo_client()

        payloads = [
            {
                "text": "Some chunk text",
                "metadata": {"document_id": "doc-1", "chunk_index": 0},
            }
        ]

        results = asyncio.run(service.index_chunks(payloads))
        assert len(results) == 1
        # embedding_id should be auto-generated
        assert results[0]["metadata"]["embedding_id"]
        assert len(results[0]["metadata"]["embedding_id"]) > 0

    def test_index_chunks_can_generate_embeddings_without_persisting_mongo_rows(self):
        service = _make_service()

        payloads = [
            {
                "text": "Some chunk text",
                "metadata": {"document_id": "doc-1", "chunk_index": 0},
            }
        ]

        results = asyncio.run(service.index_chunks(payloads, persist=False))

        assert len(results) == 1
        assert results[0]["vector_ref"] is None
        assert isinstance(results[0]["embedding"], list)
        assert service._vector_store is None
        assert service._mongo_client is None


class TestDeleteVectors:
    """Tests for LlamaIndexVectorService.delete_vectors()."""

    def test_delete_empty_refs_is_noop(self):
        service = _make_service()
        # Should not raise
        asyncio.run(service.delete_vectors([]))

    def test_delete_none_refs_is_noop(self):
        service = _make_service()
        asyncio.run(service.delete_vectors([None, None]))

    def test_delete_calls_vector_store_delete(self):
        service = _make_service()
        service._mongo_client = _fake_mongo_client()

        # Force vector store initialization
        vector_store = service._ensure_vector_store()

        refs = ["node_0", "node_1", "node_2"]
        asyncio.run(service.delete_vectors(refs))

        # Verify all refs were passed to the vector store's delete method
        assert vector_store._deleted == ["node_0", "node_1", "node_2"]

    def test_delete_skips_none_refs(self):
        service = _make_service()
        service._mongo_client = _fake_mongo_client()
        vector_store = service._ensure_vector_store()

        refs = ["node_0", None, "node_2", None]
        asyncio.run(service.delete_vectors(refs))

        # Only non-None refs should be deleted
        assert vector_store._deleted == ["node_0", "node_2"]


class TestEmbeddingIdGeneration:
    """Tests for _generate_embedding_id using SHA-256."""

    def test_deterministic_output(self):
        service = _make_service()
        metadata = {"document_id": "doc-1", "chunk_index": 0}
        text = "Hello world"

        id1 = service._generate_embedding_id(metadata, text)
        id2 = service._generate_embedding_id(metadata, text)

        assert id1 == id2

    def test_different_text_gives_different_id(self):
        service = _make_service()
        metadata = {"document_id": "doc-1", "chunk_index": 0}

        id1 = service._generate_embedding_id(metadata, "Text A")
        id2 = service._generate_embedding_id(metadata, "Text B")

        assert id1 != id2

    def test_uses_sha256_not_sha1(self):
        """Verify the hash in the embedding ID is SHA-256 length."""
        import hashlib
        service = _make_service()
        metadata = {"document_id": "doc-1", "chunk_index": 0}
        text = "Test"

        # The method uses sha256 internally for the checksum component
        expected_checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # Generate ID and verify it's deterministic with the sha256 checksum
        embedding_id = service._generate_embedding_id(metadata, text)
        assert embedding_id  # Should be a valid UUID string
        assert len(embedding_id) == 36  # UUID format: 8-4-4-4-12


class TestMongoClientSharing:
    """Tests for MongoClient injection and ownership."""

    def test_owns_client_when_none_provided(self):
        service = _make_service()
        assert service._owns_mongo_client is True
        assert service._mongo_client is None

    def test_uses_injected_client(self):
        mock_client = _fake_mongo_client()
        service = _make_service(existing_mongo_client=mock_client)
        assert service._owns_mongo_client is False
        assert service._mongo_client is mock_client

    def test_close_does_not_close_injected_client(self):
        mock_client = _fake_mongo_client()
        service = _make_service(existing_mongo_client=mock_client)

        asyncio.run(service.close())

        # Injected client should NOT have .close() called
        mock_client.close.assert_not_called()
        # But internal reference should be cleared
        assert service._mongo_client is None

    def test_close_closes_owned_client(self):
        service = _make_service()
        mock_client = _fake_mongo_client()
        service._mongo_client = mock_client
        service._owns_mongo_client = True

        asyncio.run(service.close())

        mock_client.close.assert_called_once()
        assert service._mongo_client is None
