from types import SimpleNamespace
from uuid import UUID

import pytest

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
