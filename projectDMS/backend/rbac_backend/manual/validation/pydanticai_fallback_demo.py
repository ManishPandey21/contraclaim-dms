#!/usr/bin/env python3
"""
Runtime verification for PydanticAI fallback behavior.

This script runs two scenarios using a real DocumentProcessor instance while stubbing
external dependencies (OCR, OpenAI, DB, File IO) to avoid network or DB access:

1) PydanticAI disabled/unavailable path:
   - Simulates environment where the agent is disabled and ensures legacy regex parsing is used.

2) PydanticAI enabled but agent fails:
   - Simulates an agent runtime failure (raises PydanticAIMetadataError) and verifies fallback.

Usage:
  python -u backend/rbac_backend/manual/validation/pydanticai_fallback_demo.py [PDF_PATH]

If PDF_PATH is not provided, defaults to:
  C:\\SaaS\\projectDMS\\GLM-SAM-KNPCC-05-UPMRC-OL-2025-4763.pdf
"""

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure backend is on sys.path so 'rbac_backend' can be imported when running from repo root
BASE_DIR = Path(__file__).resolve().parents[3]  # .../backend
sys.path.insert(0, str(BASE_DIR))

# Install dummy 'llama_index' modules if not available to avoid import-time failure
import types as _types
def _install_dummy_llama_index():
    try:
        import llama_index  # noqa: F401
        return
    except Exception:
        pass

    core_mod = _types.ModuleType("llama_index.core")
    class Document:
        def __init__(self, text: str = "", metadata=None, embedding=None):
            self.text = text
            self.metadata = metadata or {}
            self.embedding = embedding
    class VectorStoreIndex:
        @classmethod
        def from_vector_store(cls, vector_store):
            obj = cls()
            obj._vector_store = vector_store
            return obj
    core_mod.Document = Document
    core_mod.VectorStoreIndex = VectorStoreIndex

    emb_ns = _types.ModuleType("llama_index.embeddings")
    emb_openai_mod = _types.ModuleType("llama_index.embeddings.openai")
    class OpenAIEmbedding:
        def __init__(self, model=None, api_key=None):
            self.model = model
            self.api_key = api_key
        def get_text_embedding(self, text: str):
            return [0.0] * 10
        def get_text_embedding_batch(self, texts):
            return [[0.0] * 10 for _ in texts]
    emb_openai_mod.OpenAIEmbedding = OpenAIEmbedding

    vs_ns = _types.ModuleType("llama_index.vector_stores")
    vs_mongo_mod = _types.ModuleType("llama_index.vector_stores.mongodb")
    class MongoDBAtlasVectorSearch:
        def __init__(self, mongodb_client=None, db_name=None, collection_name=None, embed_model=None, collection=None):
            self._docs = []
        def add(self, nodes):
            self._docs.extend(nodes)
            return [f"node_{i}" for i in range(len(nodes))]
        def similarity_search(self, vector=None, limit=5, filters=None, alpha=0.5):
            return []
        def delete(self, ref_doc_id=None):
            return None
    vs_mongo_mod.MongoDBAtlasVectorSearch = MongoDBAtlasVectorSearch

    pkg = _types.ModuleType("llama_index")
    sys.modules["llama_index"] = pkg
    sys.modules["llama_index.core"] = core_mod
    sys.modules["llama_index.embeddings"] = emb_ns
    sys.modules["llama_index.embeddings.openai"] = emb_openai_mod
    sys.modules["llama_index.vector_stores"] = vs_ns
    sys.modules["llama_index.vector_stores.mongodb"] = vs_mongo_mod

_install_dummy_llama_index()

# Import from service modules
from rbac_backend.services.document_processor import create_document_processor, DocumentProcessingConfig
from rbac_backend.services.pydantic_ai_service import PydanticAIMetadataError


# -------------------------
# Dummy service stubs
# -------------------------
class DummyOCRService:
    async def process_pdf(self, input_path: Path):
        # Return the input path and a small OCR text to simulate presence
        return input_path, "DUMMY OCR TEXT: This is a test document containing basic metadata."


class DummyOpenAIService:
    async def upload_file(self, file_path: str, max_retries: int = 3) -> str:
        return "file_dummy_123"

    async def process_document(self, file_id: str) -> str:
        # Return a representative extraction report that the legacy regex parser can parse
        return """
        1) Date: 2024-01-15
        2) Letter No.: TEST-001
        3) From (Company): Test Company A
        4) To (Company): Test Company B
        5) Subject: Test Document
        6) References:
           - REF-001
           - REF-002
        7) Summary:
           - This is a test summary line 1
           - This is a test summary line 2
        8) Key Words: test, document, sample
        9) Contractual Clauses: clause1, clause2
        10) Full content: This is the full content of the test document.
        """

    async def create_embeddings(self, texts):
        return [[0.0] * 10 for _ in texts]

    async def cleanup_file(self, file_id: str) -> None:
        return None


class DummyDatabaseService:
    async def save_document_data(
        self,
        *,
        document_id: Optional[str],
        file_path: str,
        parsed_metadata,
        full_text: str,
        embedding_text: str
    ) -> int:
        # Simulate chunk creation count
        return 0

    async def close_connection(self) -> None:
        return None


class DummyFileService:
    async def save_summary(self, extracted_content: str, original_path: str, path_structure: str, upload_type: str):
        return None


class DummyPydanticAIDisabled:
    @property
    def is_enabled(self) -> bool:
        return False


class DummyPydanticAIError:
    @property
    def is_enabled(self) -> bool:
        return True

    async def extract_metadata(self, document_text: str, *, context: Optional[Dict[str, Any]] = None):
        raise PydanticAIMetadataError("Simulated agent failure")


# -------------------------
# Helpers
# -------------------------
from dataclasses import is_dataclass
def _normalize(obj):
    # Dataclasses
    if is_dataclass(obj):
        return {k: _normalize(v) for k, v in asdict(obj).items()}
    # Pydantic v2
    if hasattr(obj, "model_dump") and callable(getattr(obj, "model_dump")):
        try:
            return obj.model_dump()
        except Exception:
            pass
    # Pydantic v1
    if hasattr(obj, "dict") and callable(getattr(obj, "dict")):
        try:
            return obj.dict()
        except Exception:
            pass
    # Path
    if isinstance(obj, Path):
        return str(obj)
    # Containers
    if isinstance(obj, (list, tuple, set)):
        return [_normalize(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _normalize(v) for k, v in obj.items()}
    # Primitives or unknowns
    return obj


async def run_case(pdf_path: str, agent_mode: str) -> Dict[str, Any]:
    """
    Run one scenario of the processor with stubbed services.

    agent_mode:
      - "disabled": PydanticAI disabled path
      - "error": PydanticAI enabled but agent fails path
    """
    config = DocumentProcessingConfig()
    # provide a dummy API key so DocumentProcessor/OpenAIService constructor succeeds; we stub it immediately after
    config.openai_api_key = "DUMMY"

    processor = create_document_processor(config)

    # Stub external services to avoid network and DB dependencies
    processor.ocr_service = DummyOCRService()
    processor.openai_service = DummyOpenAIService()
    processor.database_service = DummyDatabaseService()
    processor.file_service = DummyFileService()

    # Configure PydanticAI behavior
    if agent_mode == "disabled":
        processor.pydantic_ai_service = DummyPydanticAIDisabled()
    elif agent_mode == "error":
        processor.pydantic_ai_service = DummyPydanticAIError()
    else:
        raise ValueError("agent_mode must be 'disabled' or 'error'")

    result = await processor.process_document(
        pdf_path=pdf_path,
        path_structure="acme-proj/2025/10/",
        upload_type="incoming",
        document_id=None,
    )

    # Convert dataclass result to dict for printing
    out = _normalize(result)
    return out


async def main():
    default_pdf = r"C:\SaaS\projectDMS\GLM-SAM-KNPCC-05-UPMRC-OL-2025-4763.pdf"
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else default_pdf

    if not Path(pdf_path).exists():
        print(json.dumps({"error": f"PDF not found at {pdf_path}"}))
        sys.exit(1)

    print("Running PydanticAI disabled/unavailable scenario...")
    disabled_out = await run_case(pdf_path, "disabled")
    print(json.dumps({"scenario": "disabled", "result": disabled_out}, indent=2, ensure_ascii=False))

    print("\nRunning PydanticAI enabled but agent failure scenario...")
    error_out = await run_case(pdf_path, "error")
    print(json.dumps({"scenario": "agent_error", "result": error_out}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
