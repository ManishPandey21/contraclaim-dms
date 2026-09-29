"""Shared harness for the correspondence vector payload contract (DI-B1).

Real code under test: ``DocumentService.process_document_async`` (the worker
seam single and bulk uploads are queued onto), ``DocumentProcessor`` on both
pipelines, ``DatabaseService`` (the one correspondence vector writer),
``LangChainVectorService``, ``VectorClient`` and ``RetrievalService``.

System-boundary fakes only: Mongo (an in-process collection double), OCR, the
LLM extraction reply, the Mongo-side LlamaIndex embedder, and the embedding
model (a deterministic bag-of-words hash, so similarity is meaningful and
reproducible). Qdrant is real: ``qdrant-client`` local mode by default, and a
disposable Qdrant server when ``CORRESPONDENCE_QDRANT_TEST_URL`` is set.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
import uuid
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import pytest
from bson import ObjectId

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.core.security import CurrentUser
from rbac_backend.models.document_metadata import ParsedDocumentMetadata
from rbac_backend.retrieval.models import SearchFilters, SearchRequest
from rbac_backend.retrieval.service import RetrievalService
from rbac_backend.retrieval.vector_client import VectorClient
from rbac_backend.services.database_service import DatabaseService
from rbac_backend.services.document_processor import DocumentProcessor
from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.langchain_vector_service import LangChainVectorService
from rbac_backend.tests.test_document_references import FakeCollection, FakeCursor

#: Opt-in real Qdrant server. Never defaulted: a default would silently point
#: the suite at whichever developer container happens to own :6333.
QDRANT_SERVER_ENV = "CORRESPONDENCE_QDRANT_TEST_URL"
DIMENSIONS = 32

ADVISORY_TEXT = "ADVISORY-REPLY-POINT reserve rights on prolongation cost"


# --- embeddings ----------------------------------------------------------------


def embed_text(text: str) -> List[float]:
    """Hashed bag of words: shared words => high cosine, deterministic."""
    vector = [0.0] * DIMENSIONS
    for token in re.findall(r"[a-z0-9]+", (text or "").lower()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        vector[digest[0] % DIMENSIONS] += 1.0
    vector[-1] += 0.05  # never a zero vector
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def install_deterministic_langchain_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_core.embeddings import Embeddings

    class _Embeddings(Embeddings):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def embed_documents(self, texts: List[str]) -> List[List[float]]:
            return [embed_text(text) for text in texts]

        def embed_query(self, text: str) -> List[float]:
            return embed_text(text)

    import langchain_openai

    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", _Embeddings)


class DeterministicEmbeddingClient:
    async def embed(self, texts: List[str], model: Optional[str] = None) -> List[List[float]]:
        return [embed_text(text) for text in texts]


# --- Mongo double ----------------------------------------------------------------


def _normal(value: Any) -> Any:
    return str(value) if isinstance(value, ObjectId) else value


def _field_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict) and any(str(k).startswith("$") for k in expected):
        if "$in" in expected and _normal(actual) not in {_normal(v) for v in expected["$in"]}:
            return False
        if "$nin" in expected and _normal(actual) in {_normal(v) for v in expected["$nin"]}:
            return False
        if "$ne" in expected and _normal(actual) == _normal(expected["$ne"]):
            return False
        if "$exists" in expected and (actual is not None) != bool(expected["$exists"]):
            return False
        for op, compare in (
            ("$lte", lambda a, b: a <= b),
            ("$lt", lambda a, b: a < b),
            ("$gte", lambda a, b: a >= b),
            ("$gt", lambda a, b: a > b),
        ):
            if op in expected:
                try:
                    if actual is None or not compare(actual, expected[op]):
                        return False
                except TypeError:
                    return False
        return True
    return _normal(actual) == _normal(expected)


def _matches(document: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in (query or {}).items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in expected):
                return False
            continue
        if key == "$and":
            if not all(_matches(document, branch) for branch in expected):
                return False
            continue
        if not _field_matches(document.get(key), expected):
            return False
    return True


class Collection(FakeCollection):
    """`FakeCollection` plus the generic reads/deletes the vector writer uses."""

    def find(self, filter: Dict[str, Any], *_args: Any, **_kwargs: Any) -> FakeCursor:
        return FakeCursor(doc for doc in self._docs.values() if _matches(doc, filter or {}))

    async def find_one(self, filter: Dict[str, Any], *_args: Any, **_kwargs: Any):
        for doc in self._docs.values():
            if _matches(doc, filter or {}):
                return doc
        return None

    async def delete_many(self, filter: Dict[str, Any]) -> SimpleNamespace:
        doomed = [key for key, doc in self._docs.items() if _matches(doc, filter or {})]
        for key in doomed:
            del self._docs[key]
        return SimpleNamespace(deleted_count=len(doomed))

    async def delete_one(self, filter: Dict[str, Any]) -> SimpleNamespace:
        for key, doc in list(self._docs.items()):
            if _matches(doc, filter or {}):
                del self._docs[key]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def count_documents(self, filter: Dict[str, Any], *_args: Any, **_kwargs: Any) -> int:
        return sum(1 for doc in self._docs.values() if _matches(doc, filter or {}))

    async def bulk_write(self, operations: Any, *args: Any, **kwargs: Any) -> SimpleNamespace:
        """pymongo ReplaceOne / UpdateOne, which the unified page store issues."""
        for operation in operations or []:
            query = getattr(operation, "_filter", None) or {}
            document = getattr(operation, "_doc", None) or {}
            upsert = bool(getattr(operation, "_upsert", False))
            if any(str(key).startswith("$") for key in document):
                await self.update_one(query, document, upsert=upsert)
                continue
            existing = await self.find_one(query)
            replacement = dict(document)
            if existing is not None:
                replacement["_id"] = existing["_id"]
                self._docs[self._key(existing["_id"])] = replacement
            elif upsert:
                await self.insert_one(replacement)
        return SimpleNamespace(acknowledged=True)

    async def find_one_and_update(self, filter, update, *args: Any, **kwargs: Any):
        found = await self.find_one(filter)
        if found is None:
            return None
        await self.update_one({"_id": found["_id"]}, update)
        return await self.find_one({"_id": found["_id"]})

    async def update_one(self, filter, update, upsert: bool = False):
        found = await self.find_one(filter)
        if found is None:
            return await super().update_one(filter, update, upsert=upsert)
        for key in (update.get("$unset") or {}):
            found.pop(key, None)
        for key, amount in (update.get("$inc") or {}).items():
            found[key] = (found.get(key) or 0) + amount
        rest = {k: v for k, v in update.items() if k not in ("$unset", "$inc")}
        if rest:
            return await super().update_one({"_id": found["_id"]}, rest, upsert=False)
        return SimpleNamespace(matched_count=1, modified_count=1)


class Database:
    def __init__(self, documents: Iterable[Dict[str, Any]] = ()) -> None:
        self._collections: Dict[str, Collection] = {"documents": Collection(documents)}

    def __getattr__(self, name: str) -> Collection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._collections.setdefault(name, Collection())

    def __getitem__(self, name: str) -> Collection:
        return getattr(self, name)


def correspondence_document(
    *,
    organization_id: Any,
    project_id: Any,
    letter_no: str,
    subject: str,
    upload_type: str = "incoming",
    **overrides: Any,
) -> Dict[str, Any]:
    """A document row as the upload route leaves it: stored, queued, no text yet."""
    now = __import__("datetime").datetime.utcnow()
    row: Dict[str, Any] = {
        "_id": ObjectId(),
        "organization_id": organization_id,
        "project_id": project_id,
        "filename": f"{letter_no.replace('/', '-')}.pdf",
        "filepath_local": "",
        "filepath_s3": "",
        "filetype": "application/pdf",
        "filesize": 1024,
        "uploadType": upload_type,
        "letterNo": letter_no,
        "date": now,
        "subject": subject,
        "tags": [],
        "subTags": [],
        "status": "active",
        "ocrEnabled": True,
        "reference": [],
        "references": [],
        "referencedBy": [],
        "createdAt": now,
        "updatedAt": now,
        "createdBy": "tester@example.com",
        "version": "1.0",
        "enclosures": [],
    }
    row.update(overrides)
    return row


# --- Qdrant -------------------------------------------------------------------


QDRANT_BACKENDS = [
    "local",
    pytest.param(
        "server",
        marks=pytest.mark.skipif(
            not os.environ.get(QDRANT_SERVER_ENV),
            reason=f"{QDRANT_SERVER_ENV} not set (disposable real Qdrant)",
        ),
    ),
]


class QdrantHarness:
    """One collection, the real writer service and the real reader client."""

    def __init__(self, backend: str, monkeypatch: pytest.MonkeyPatch) -> None:
        install_deterministic_langchain_embeddings(monkeypatch)
        self.backend = backend
        config = DocumentProcessingConfig()
        config.openai_api_key = "test-key"
        config.qdrant_url = os.environ[QDRANT_SERVER_ENV] if backend == "server" else ":memory:"
        config.qdrant_api_key = None
        config.qdrant_collection = f"phaseb-corr-{uuid.uuid4().hex[:12]}"
        config.qdrant_vector_size = DIMENSIONS
        config.qdrant_distance = "cosine"
        config.qdrant_vector_name = None
        config.vector_store_enabled = True
        config.vector_dual_write_enabled = True
        self.config = config
        self.writer = LangChainVectorService(config)
        assert self.writer.enabled, self.writer._init_error
        if backend == "server":
            self.reader = VectorClient(config)
        else:
            reader = VectorClient.__new__(VectorClient)
            reader.config = config
            reader._client = self.writer._client
            reader._qmodels = self.writer._qdrant_models
            reader.enabled = True
            reader.collection_name = config.qdrant_collection
            reader._memory_index = []
            self.reader = reader
        assert self.reader.enabled

    @property
    def client(self):
        return self.writer._client

    def scroll(self) -> List[Any]:
        points, _ = self.client.scroll(
            collection_name=self.config.qdrant_collection,
            limit=1000,
            with_payload=True,
            with_vectors=False,
        )
        return list(points)

    def drop(self) -> None:
        try:
            self.client.delete_collection(self.config.qdrant_collection)
        except Exception:  # pragma: no cover - cleanup best effort
            pass


def retrieval_service(db: Database, harness: QdrantHarness) -> RetrievalService:
    return RetrievalService(
        db=db,
        embedding_client=DeterministicEmbeddingClient(),
        vector_client=harness.reader,
        llm_generator=None,
        observability=None,
    )


def user_for(org_id: str, project_id: str) -> CurrentUser:
    return CurrentUser(
        id=str(ObjectId()),
        username="reader",
        email="reader@example.com",
        roles=["projectuser"],
        organization_id=org_id,
        projects=[project_id],
    )


async def scoped_search(
    db: Database,
    harness: QdrantHarness,
    query: str,
    *,
    org_id: str,
    project_id: str,
    metadata: Optional[Dict[str, Any]] = None,
    limit: int = 8,
):
    request = SearchRequest(
        query=query,
        limit=limit,
        filters=SearchFilters(org_id=org_id, project_id=project_id, metadata=metadata or {}),
    )
    return await retrieval_service(db, harness).search(
        request, user_for(org_id, project_id), log_run=False
    )


# --- the processing pipeline ----------------------------------------------------


class _MongoVectorModel:
    """The LlamaIndex Mongo-side embedder (writes `document_vectors`)."""

    embedding_model_name = "phaseb-test-model"

    async def delete_vectors(self, _refs: Any) -> None:
        return None

    async def index_chunks(self, payloads: List[Dict[str, Any]], persist: bool = False):
        return [
            {
                "metadata": deepcopy(payload["metadata"]),
                "vector_ref": f"ref-{index}-{payload['chunk_id']}",
                "embedding": embed_text(payload["text"]),
                "text": payload["text"],
            }
            for index, payload in enumerate(payloads)
        ]


def database_service(db: Database, harness: QdrantHarness) -> DatabaseService:
    service = DatabaseService(harness.config)
    service._langchain_vector_service = harness.writer
    service._langchain_service_initialized = True
    service._vector_service = _MongoVectorModel()

    async def _get_database():
        return db

    service.get_database = _get_database  # type: ignore[method-assign]
    return service


class _LegacyOcr:
    def __init__(self, text: str) -> None:
        self.text = text

    async def process_pdf(self, input_path: Path):
        return Path(input_path), self.text

    async def process_pdf_pagewise(self, *args: Any, **kwargs: Any):
        raise AssertionError("legacy_v0 must not run the unified extractor")


class _OpenAI:
    def __init__(self, report: str = "REPORT") -> None:
        self.report = report

    async def process_text(self, text: str, *, filename: str, include_full_content: bool = True) -> str:
        return self.report

    async def upload_file(self, path: str) -> str:
        return "file-1"

    async def process_document(self, file_id: str) -> str:
        return self.report

    async def cleanup_file(self, file_id: str) -> None:
        return None


class _Parser:
    """The LLM report parse. It returns reply advice too, as the real one does."""

    def __init__(self, metadata: ParsedDocumentMetadata) -> None:
        self.metadata = metadata

    def parse_extraction_report(self, content: str) -> ParsedDocumentMetadata:
        return self.metadata.model_copy(deep=True)


async def _async_none(*args: Any, **kwargs: Any) -> None:
    return None


def build_processor(
    db: Database,
    harness: QdrantHarness,
    *,
    body: str,
    metadata: ParsedDocumentMetadata,
    pipeline: str,
    report: str = "REPORT",
    ocr_text: Optional[str] = None,
) -> DocumentProcessor:
    from rbac_backend.services.extraction.quality.gate import ExtractionQualityGate
    from rbac_backend.services.extraction.source_kind import SourceKindRouter
    from rbac_backend.services.ocr_service import OCRService

    processor = DocumentProcessor.__new__(DocumentProcessor)
    if pipeline == "legacy_v0":
        processor.ocr_service = _LegacyOcr(body if ocr_text is None else ocr_text)
    else:
        ocr_config = DocumentProcessingConfig()
        ocr_config.ocr_enabled = True
        ocr_config.contract_ocr_min_text_chars_per_page = 40
        ocr = OCRService.__new__(OCRService)
        ocr.config = ocr_config
        ocr._ocr_available = False
        processor.ocr_service = ocr
    processor.openai_service = _OpenAI(report)
    processor.text_service = _Parser(metadata)
    processor.file_service = SimpleNamespace(save_summary=_async_none)
    processor.database_service = database_service(db, harness)
    processor.pydantic_ai_service = SimpleNamespace(is_enabled=False, extract_metadata=None)
    processor.quality_gate = ExtractionQualityGate()
    processor.fallback_ladder = None
    processor.fallback_max_pages_per_document = 0
    processor.intervention_ledger = None
    processor.source_kind_router = SourceKindRouter
    processor.image_ocr_runner = object()
    processor.resolve_fallback_ladder = lambda kind, db=None: None
    processor.config = SimpleNamespace(
        max_file_size_mb=50,
        ocr_language="eng",
        use_pydantic_ai=False,
        openai_api_key=None,
        process_dir="/processed",
    )
    return processor


def letter_metadata(*, letter_no: str, subject: str, body: str) -> ParsedDocumentMetadata:
    return ParsedDocumentMetadata(
        letter_no=letter_no,
        subject=subject,
        from_company="Contractor Ltd",
        to_company="Employer Authority",
        summary=f"Letter {letter_no} about {subject}",
        keywords=["notice", "delay"],
        full_content=body,
        key_reply_points=[ADVISORY_TEXT],
    )


async def upload_and_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    db: Database,
    harness: QdrantHarness,
    row: Dict[str, Any],
    *,
    body: str,
    pipeline: str = "legacy_v0",
    report: str = "REPORT",
    ocr_text: Optional[str] = None,
    metadata: Optional[ParsedDocumentMetadata] = None,
) -> DocumentProcessor:
    """Run the queued-job worker exactly as a single or bulk upload does."""
    from rbac_backend.tests.fixtures.pdf_builders import build_text_pdf

    await db.documents.insert_one(row)
    source = build_text_pdf(tmp_path / f"{row['_id']}.pdf", text=body)
    row["filepath_local"] = str(source)
    job_id = f"job-{row['_id']}"
    await db.document_processing_jobs.insert_one(
        {
            "_id": job_id,
            "document_id": str(row["_id"]),
            "status": "processing",
            "pipeline_version": pipeline,
            "attempts": 1,
            "source_mime": "application/pdf",
        }
    )
    processor = build_processor(
        db,
        harness,
        body=body,
        metadata=metadata
        or letter_metadata(letter_no=row["letterNo"], subject=row["subject"], body=body),
        report=report,
        ocr_text=ocr_text,
        pipeline=pipeline,
    )
    monkeypatch.setattr(
        "rbac_backend.services.document_service.create_document_processor",
        lambda: processor,
    )
    service = DocumentService(db)
    monkeypatch.setattr(service.graph_ingestion, "ingest_document", _async_none)
    monkeypatch.setattr(service.graph_ingestion, "sync_document_to_falkor", lambda **_k: None)
    await service.process_document_async(
        str(row["_id"]),
        file_path=str(source),
        organization_id=str(row["organization_id"]),
        project_id=str(row["project_id"] or ""),
        upload_type=row["uploadType"],
        job_id=job_id,
    )
    return processor


def run(coro):
    return asyncio.run(coro)
