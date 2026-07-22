from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import numpy as np
import pytest
from langchain_core.embeddings import Embeddings

import rbac_backend.services.falkordb_vector_service as falkordb_module
import rbac_backend.services.openai_service as openai_module
from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.falkordb_vector_service import FalkorDBVectorService
from rbac_backend.services.langchain_vector_service import LangChainVectorService
from rbac_backend.services.openai_service import OpenAIService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.anyio("asyncio"),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _require_live_external_services(*names: str) -> None:
    if not _env_enabled("RUN_EXTERNAL_INTEGRATION_TESTS"):
        pytest.skip("Set RUN_EXTERNAL_INTEGRATION_TESTS=1 to run live external integration tests.")

    missing = [name for name in names if not os.getenv(name)]
    if missing:
        pytest.skip(f"Missing required integration environment variables: {', '.join(missing)}")


def _create_smoke_pdf(target: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    document = canvas.Canvas(str(target), pagesize=letter)
    document.setTitle("External integration smoke letter")
    document.setFont("Helvetica", 12)
    document.drawString(72, 740, "Date: 29-04-2026")
    document.drawString(72, 720, "Letter No.: INT-SMOKE-001")
    document.drawString(72, 700, "Subject: External OpenAI integration verification")
    document.drawString(72, 680, "Full content: Integration Smoke Letter")
    document.showPage()
    document.save()


class _FakeOpenAIFilesAPI:
    def __init__(self) -> None:
        self._uploads: Dict[str, bytes] = {}
        self.deleted_ids: list[str] = []

    async def create(self, *, file: tuple[str, bytes, str], purpose: str) -> Any:
        filename, payload, mime_type = file
        assert filename.endswith(".pdf")
        assert payload
        assert mime_type == "application/pdf"
        assert purpose == "user_data"

        file_id = f"file-{len(self._uploads) + 1}"
        self._uploads[file_id] = payload
        return SimpleNamespace(id=file_id)

    async def delete(self, file_id: str) -> Any:
        self.deleted_ids.append(file_id)
        self._uploads.pop(file_id, None)
        return SimpleNamespace(id=file_id, deleted=True)


class _FakeOpenAIChatCompletionsAPI:
    def __init__(self, files_api: _FakeOpenAIFilesAPI) -> None:
        self._files_api = files_api

    async def create(self, *, model: str, messages: list[dict[str, Any]], max_tokens: int, temperature: float) -> Any:
        assert model == "gpt-4o"
        # OpenAIService sends max(config.max_output_tokens, 4096)
        assert max_tokens == 4096
        assert temperature == 0.1

        file_id = messages[0]["content"][1]["file"]["file_id"]
        assert file_id in self._files_api._uploads

        content = [
            {"type": "output_text", "text": "Date: 29-04-2026"},
            {"type": "output_text", "text": "Letter No.: INT-SMOKE-001"},
            {"type": "output_text", "text": "Subject: External OpenAI integration verification"},
            {"type": "output_text", "text": "Full content: Integration Smoke Letter"},
        ]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content),
                )
            ]
        )


class _FakeOpenAIResponsesAPI:
    def __init__(self, files_api: _FakeOpenAIFilesAPI) -> None:
        self._files_api = files_api

    async def create(
        self,
        *,
        model: str,
        input: list[dict[str, Any]],
        max_output_tokens: int,
        temperature: float,
        store: bool,
    ) -> Any:
        assert model == "gpt-4o"
        assert max_output_tokens == 4096
        assert temperature == 0.1
        assert store is False
        assert input[0]["content"][0]["type"] == "input_text"
        file_part = input[0]["content"][1]
        assert file_part["type"] == "input_file"
        assert file_part["file_id"] in self._files_api._uploads
        return SimpleNamespace(
            output_text=(
                "Date: 29-04-2026\n"
                "Letter No.: INT-SMOKE-001\n"
                "Subject: External OpenAI integration verification\n"
                "Full content: Integration Smoke Letter"
            )
        )


class _FakeAsyncOpenAI:
    def __init__(self, *, api_key: str, timeout: float) -> None:
        assert api_key == "test-openai-key"
        assert timeout == 60.0
        files_api = _FakeOpenAIFilesAPI()
        self.files = files_api
        self.responses = _FakeOpenAIResponsesAPI(files_api)
        self.chat = SimpleNamespace(completions=_FakeOpenAIChatCompletionsAPI(files_api))


class _DeterministicEmbeddings(Embeddings):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.model = kwargs.get("model")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        lower = text.lower()
        return [
            1.0 if "alpha" in lower else 0.0,
            1.0 if "retention" in lower else 0.0,
            1.0 if "omega" in lower else 0.0,
            min(len(lower.split()) / 16.0, 1.0),
        ]


class _FakeSearchIndex:
    def __init__(self, client: "_FakeRedisClient", index_name: str) -> None:
        self._client = client
        self._index_name = index_name

    def info(self) -> dict[str, Any]:
        if self._index_name not in self._client.indexes:
            raise RuntimeError("Index does not exist")
        return {"index_name": self._index_name}

    def create_index(self, *, fields: Any, definition: Any) -> None:
        self._client.indexes.add(self._index_name)

    def dropindex(self, delete_documents: bool = False, dd: bool = False) -> None:
        self._client.indexes.discard(self._index_name)
        if delete_documents or dd:
            prefix = f"{self._index_name}:"
            for key in [name for name in self._client.hashes if name.startswith(prefix)]:
                self._client.hashes.pop(key, None)


class _FakeRedisClient:
    def __init__(self) -> None:
        self.hashes: Dict[str, Dict[bytes, bytes]] = {}
        self.indexes: set[str] = set()

    def ft(self, index_name: str) -> _FakeSearchIndex:
        return _FakeSearchIndex(self, index_name)

    def hset(self, key: str, *, mapping: Dict[str, Any]) -> int:
        encoded: Dict[bytes, bytes] = {}
        for field, value in mapping.items():
            field_bytes = field.encode("utf-8") if isinstance(field, str) else bytes(field)
            if isinstance(value, bytes):
                value_bytes = value
            elif isinstance(value, str):
                value_bytes = value.encode("utf-8")
            else:
                value_bytes = str(value).encode("utf-8")
            encoded[field_bytes] = value_bytes
        self.hashes[key] = encoded
        return len(encoded)

    def hgetall(self, key: str) -> Dict[bytes, bytes]:
        return dict(self.hashes.get(key, {}))

    def delete(self, key: str) -> int:
        return 1 if self.hashes.pop(key, None) is not None else 0


@pytest.mark.external_service
async def test_openai_service_document_round_trip_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openai_module, "AsyncOpenAI", _FakeAsyncOpenAI)

    pdf_path = tmp_path / "openai-contract-smoke.pdf"
    _create_smoke_pdf(pdf_path)

    config = DocumentProcessingConfig(
        openai_api_key="test-openai-key",
        openai_model="gpt-4o",
        openai_timeout=60.0,
    )
    service = OpenAIService(config)

    file_id = await service.upload_file(str(pdf_path))
    assert file_id == "file-1"

    extracted = await service.process_document(file_id)
    assert "INT-SMOKE-001" in extracted
    assert "Integration Smoke Letter" in extracted

    await service.cleanup_file(file_id)
    assert file_id not in service._client.files._uploads
    assert service._client.files.deleted_ids == [file_id]


@pytest.mark.external_service
async def test_qdrant_langchain_vector_round_trip_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    import langchain_openai

    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", _DeterministicEmbeddings)

    collection_name = f"itest_{uuid.uuid4().hex}"
    document_id = f"doc-{uuid.uuid4().hex}"

    config = DocumentProcessingConfig(openai_api_key="test-openai-key")
    config.openai_embedding_model = "test-embedding-model"
    config.vector_store_enabled = True
    config.vector_dual_write_enabled = True
    config.qdrant_url = ":memory:"
    config.qdrant_collection = collection_name
    config.qdrant_vector_size = 4
    config.qdrant_distance = "cosine"
    config.qdrant_timeout = 5.0

    service = LangChainVectorService(config)
    if not service.enabled:
        pytest.fail(f"LangChainVectorService did not initialize: {getattr(service, '_init_error', None)}")

    payloads = [
        {
            "text": (
                "This integration smoke document references the alpha retention clause "
                "and the omega fallback obligation for vector-search validation."
            ),
            "metadata": {
                "document_id": document_id,
                "chunk_index": 0,
            },
        }
    ]

    try:
        written = await service.replace_document(payloads)
        assert written == 1

        results = []
        for _ in range(3):
            results = await service.similarity_search("alpha retention clause", top_k=1)
            if results:
                break
            await asyncio.sleep(0.2)

        assert results
        assert any(result["metadata"].get("document_id") == document_id for result in results)
        assert results[0]["text"]
    finally:
        client = getattr(service, "_client", None)
        if client is not None:
            await asyncio.to_thread(client.delete_collection, collection_name)


@pytest.mark.external_service
async def test_falkordb_vector_service_round_trip_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeRedisClient()

    class _FakeRedisFactory:
        @staticmethod
        def from_url(url: str) -> _FakeRedisClient:
            assert url == "memory://falkor"
            return fake_client

    monkeypatch.setattr(falkordb_module, "Redis", _FakeRedisFactory)

    index_name = f"itest_{uuid.uuid4().hex[:12]}"
    document_id = f"doc-{uuid.uuid4().hex}"

    config = DocumentProcessingConfig(falkordb_enabled=True)
    config.falkordb_index_name = index_name
    config.falkordb_vector_dim = 4
    config.falkordb_url = "memory://falkor"
    service = FalkorDBVectorService(config)
    key = f"{service.index_name}:{document_id}:0"

    payloads = [
        {
            "text": "Redis integration smoke payload",
            "vector": np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32),
            "metadata": {
                "document_id": document_id,
                "chunk_index": 0,
            },
        }
    ]

    saved = await service.save_data(payloads)
    assert saved == 1

    stored = service.client.hgetall(key)
    assert stored
    assert stored.get(b"text") == b"Redis integration smoke payload"
    assert stored.get(b"document_id") == document_id.encode("utf-8")


@pytest.mark.external_service
@pytest.mark.live_external_service
async def test_openai_service_document_round_trip_live(tmp_path: Path) -> None:
    _require_live_external_services("OPENAI_API_KEY")

    pdf_path = tmp_path / "openai-live-smoke.pdf"
    _create_smoke_pdf(pdf_path)

    config = DocumentProcessingConfig(
        openai_api_key=os.environ["OPENAI_API_KEY"],
        openai_model=os.getenv("OPENAI_INTEGRATION_MODEL", "gpt-4o"),
        openai_timeout=float(os.getenv("OPENAI_INTEGRATION_TIMEOUT", "60")),
    )
    service = OpenAIService(config)

    file_id = await service.upload_file(str(pdf_path))
    assert file_id

    try:
        extracted = await service.process_document(file_id)
    finally:
        await service.cleanup_file(file_id)

    assert extracted.strip()
    assert "INT-SMOKE-001" in extracted or "Integration Smoke Letter" in extracted


@pytest.mark.external_service
@pytest.mark.live_external_service
async def test_qdrant_langchain_vector_round_trip_live() -> None:
    _require_live_external_services("OPENAI_API_KEY", "QDRANT_URL")

    collection_name = f"itest_{uuid.uuid4().hex}"
    document_id = f"doc-{uuid.uuid4().hex}"

    config = DocumentProcessingConfig(openai_api_key=os.environ["OPENAI_API_KEY"])
    config.openai_embedding_model = os.getenv("QDRANT_INTEGRATION_EMBEDDING_MODEL", "text-embedding-3-small")
    config.vector_store_enabled = True
    config.vector_dual_write_enabled = True
    config.qdrant_url = os.environ["QDRANT_URL"]
    config.qdrant_api_key = os.getenv("QDRANT_API_KEY")
    config.qdrant_collection = collection_name
    config.qdrant_vector_size = int(os.getenv("QDRANT_INTEGRATION_VECTOR_SIZE", "1536"))
    config.qdrant_distance = os.getenv("QDRANT_INTEGRATION_DISTANCE", "cosine")
    config.qdrant_timeout = float(os.getenv("QDRANT_INTEGRATION_TIMEOUT", "15"))

    service = LangChainVectorService(config)
    if not service.enabled:
        pytest.fail(f"LangChainVectorService did not initialize: {getattr(service, '_init_error', None)}")

    payloads = [
        {
            "text": (
                "This integration smoke document references the alpha retention clause "
                "and the omega fallback obligation for vector-search validation."
            ),
            "metadata": {
                "document_id": document_id,
                "chunk_index": 0,
            },
        }
    ]

    try:
        written = await service.replace_document(payloads)
        assert written == 1

        results = []
        for _ in range(3):
            results = await service.similarity_search("alpha retention clause", top_k=1)
            if results:
                break
            await asyncio.sleep(1)

        assert results
        assert any(result["metadata"].get("document_id") == document_id for result in results)
    finally:
        client = getattr(service, "_client", None)
        if client is not None:
            await asyncio.to_thread(client.delete_collection, collection_name)


@pytest.mark.external_service
@pytest.mark.live_external_service
async def test_falkordb_vector_service_round_trip_live() -> None:
    _require_live_external_services("FALKORDB_URL")

    index_name = f"itest_{uuid.uuid4().hex[:12]}"
    document_id = f"doc-{uuid.uuid4().hex}"

    config = DocumentProcessingConfig(falkordb_enabled=True)
    config.falkordb_index_name = index_name
    config.falkordb_vector_dim = 4
    config.falkordb_url = os.environ["FALKORDB_URL"]
    service = FalkorDBVectorService(config)
    key = f"{service.index_name}:{document_id}:0"

    payloads = [
        {
            "text": "Redis integration smoke payload",
            "vector": np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32),
            "metadata": {
                "document_id": document_id,
                "chunk_index": 0,
            },
        }
    ]

    try:
        saved = await service.save_data(payloads)
        assert saved == 1

        stored = service.client.hgetall(key)
        assert stored
        assert stored.get(b"text") == b"Redis integration smoke payload"
        assert stored.get(b"document_id") == document_id.encode("utf-8")
    finally:
        try:
            service.client.delete(key)
        except Exception:
            pass
        try:
            service.client.ft(index_name).dropindex(delete_documents=True)
        except TypeError:
            try:
                service.client.ft(index_name).dropindex(dd=True)
            except Exception:
                pass
        except Exception:
            pass
