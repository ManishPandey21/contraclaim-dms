import asyncio
import sys
import types
from types import SimpleNamespace
from typing import Any, Dict

import pytest

pytestmark = pytest.mark.anyio("asyncio")


def _ensure_llama_index_stub() -> None:
    if "llama_index" in sys.modules:
        return

    llama_pkg = types.ModuleType("llama_index")
    sys.modules["llama_index"] = llama_pkg

    core_pkg = types.ModuleType("llama_index.core")
    core_pkg.Document = object
    core_pkg.VectorStoreIndex = object
    sys.modules["llama_index.core"] = core_pkg

    embeddings_pkg = types.ModuleType("llama_index.embeddings")
    sys.modules["llama_index.embeddings"] = embeddings_pkg
    openai_pkg = types.ModuleType("llama_index.embeddings.openai")
    openai_pkg.OpenAIEmbedding = type("OpenAIEmbedding", (), {})
    sys.modules["llama_index.embeddings.openai"] = openai_pkg

    vector_pkg = types.ModuleType("llama_index.vector_stores")
    sys.modules["llama_index.vector_stores"] = vector_pkg
    mongo_pkg = types.ModuleType("llama_index.vector_stores.mongodb")
    mongo_pkg.MongoDBAtlasVectorSearch = type("MongoDBAtlasVectorSearch", (), {})
    sys.modules["llama_index.vector_stores.mongodb"] = mongo_pkg


_ensure_llama_index_stub()

from backend.rbac_backend.config.document_processing_config import DocumentProcessingConfig
from backend.rbac_backend.services.database_service import DatabaseService


class FakeStatusCollection:
    def __init__(self):
        self.last_query: Dict[str, Any] | None = None
        self.last_update: Dict[str, Any] | None = None
        self.upsert_count = 0

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        self.last_query = query
        self.last_update = update
        if upsert:
            self.upsert_count += 1


def test_update_vector_sync_status_upserts_payload():
    config = DocumentProcessingConfig()
    service = DatabaseService(config)
    fake_collection = FakeStatusCollection()
    fake_db = SimpleNamespace(vector_sync_status=fake_collection)

    asyncio.run(service._update_vector_sync_status(
        fake_db,
        document_id="doc-xyz",
        status="synced",
        mongo_chunks=5,
        qdrant_chunks=5,
        details="verification_ok",
    ))

    assert fake_collection.last_query == {"document_id": "doc-xyz"}
    assert fake_collection.last_update is not None
    payload = fake_collection.last_update["$set"]
    assert payload["sync_status"] == "synced"
    assert payload["mongo_chunks"] == 5
    assert payload["qdrant_chunks"] == 5
    assert payload["details"] == "verification_ok"
    assert "$setOnInsert" in fake_collection.last_update
    assert fake_collection.upsert_count == 1






