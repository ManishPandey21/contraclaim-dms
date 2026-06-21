from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import pytest
from pymongo import ReplaceOne

from rbac_backend.ingestion.pipeline import IngestionPipeline
from rbac_backend.ingestion.models import IngestionJobCreate, IngestionOptions
from rbac_backend.observability.service import ObservabilityService
from rbac_backend.retrieval.models import (
    RagRequest,
    SearchBackend,
    SearchFilters,
    SearchRequest,
    SearchStrategy,
)
from rbac_backend.retrieval.service import RetrievalService


def _matches(doc: Dict[str, Any], criteria: Dict[str, Any]) -> bool:
    for key, value in criteria.items():
        candidate = doc.get(key)
        if isinstance(value, dict):
            if "$in" in value:
                if candidate not in value["$in"]:
                    return False
                continue
            if "$size" in value:
                if len(candidate or []) != value["$size"]:
                    return False
                continue
        if candidate != value:
            return False
    return True


class FakeCursor:
    def __init__(self, docs: Iterable[Dict[str, Any]], criteria: Optional[Dict[str, Any]] = None) -> None:
        self._docs = list(docs)
        self._criteria = criteria or {}
        self._limit: Optional[int] = None
        self._sort_field: Optional[str] = None
        self._sort_direction: int = 1

    def sort(self, field: str, direction: int = 1):
        self._sort_field = field
        self._sort_direction = direction
        return self

    def limit(self, limit: int):
        self._limit = limit
        return self

    def _materialize(self) -> List[Dict[str, Any]]:
        rows = [dict(doc) for doc in self._docs if _matches(doc, self._criteria)]
        if self._sort_field:
            reverse = self._sort_direction < 0
            rows.sort(key=lambda item: item.get(self._sort_field), reverse=reverse)
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        rows = self._materialize()
        if length is not None:
            rows = rows[:length]
        return rows

    def __aiter__(self):
        async def _iterate():
            for row in self._materialize():
                yield row

        return _iterate()


class FakeCollection:
    def __init__(self) -> None:
        self.docs: List[Dict[str, Any]] = []

    async def insert_one(self, doc: Dict[str, Any]):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("_id"))

    async def insert_many(self, docs: List[Dict[str, Any]]):
        for doc in docs:
            self.docs.append(dict(doc))
        return SimpleNamespace(inserted_ids=[doc.get("_id") for doc in docs])

    async def find_one(self, criteria: Dict[str, Any]):
        for doc in self.docs:
            if _matches(doc, criteria):
                return dict(doc)
        return None

    async def update_one(self, criteria: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        for doc in self.docs:
            if _matches(doc, criteria):
                if "$set" in update:
                    doc.update(update["$set"])
                if "$push" in update:
                    for key, value in update["$push"].items():
                        doc.setdefault(key, []).append(value)
                if "$setOnInsert" in update:
                    for key, value in update["$setOnInsert"].items():
                        doc.setdefault(key, value)
                return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)

        if upsert:
            new_doc = dict(criteria)
            if "$setOnInsert" in update:
                new_doc.update(update["$setOnInsert"])
            if "$set" in update:
                new_doc.update(update["$set"])
            self.docs.append(new_doc)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=new_doc.get("_id"))

        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)

    async def bulk_write(self, ops, ordered: bool = False):
        for op in ops:
            if isinstance(op, ReplaceOne):
                replaced = False
                for index, doc in enumerate(self.docs):
                    if _matches(doc, op._filter):
                        self.docs[index] = dict(op._doc)
                        replaced = True
                        break
                if not replaced and op._upsert:
                    self.docs.append(dict(op._doc))
        return SimpleNamespace()

    async def delete_many(self, criteria: Dict[str, Any]):
        before = len(self.docs)
        self.docs = [doc for doc in self.docs if not _matches(doc, criteria)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    def find(self, criteria: Dict[str, Any]):
        return FakeCursor(self.docs, criteria)

    async def aggregate(self, pipeline):
        for doc in self.docs:
            yield dict(doc)

    async def count_documents(self, criteria):
        return sum(1 for doc in self.docs if _matches(doc, criteria))


class FakeDB:
    def __init__(self):
        self.ingestion_jobs = FakeCollection()
        self.documents = FakeCollection()
        self.chunks = FakeCollection()
        self.rag_runs = FakeCollection()
        self.agent_conversations = FakeCollection()
        self.agent_messages = FakeCollection()
        self.vector_sync_status = FakeCollection()


class StubEmbedding:
    async def embed(self, texts: List[str], model: Optional[str] = None) -> List[List[float]]:
        return [[float(len(text))] for text in texts]


class StubVector:
    def __init__(self):
        self.writes: List[Dict[str, Any]] = []
        self.config = SimpleNamespace(qdrant_vector_size=64)
        self.enabled = True

    async def upsert(self, vectors: List[List[float]], chunks: List[Dict[str, Any]], namespace: Optional[str] = None) -> int:
        for vector, chunk in zip(vectors, chunks):
            self.writes.append({"vector": vector, "chunk": chunk})
        return len(chunks)

    async def list_chunk_ids(self, filters: Dict[str, Any], namespace: Optional[str] = None, limit: int = 1000):
        return [
            entry["chunk"]["chunk_id"]
            for entry in self.writes
            if all(entry["chunk"].get(key) == value for key, value in filters.items() if value is not None)
        ]

    async def delete(self, chunk_ids: List[str], namespace: Optional[str] = None) -> int:
        before = len(self.writes)
        self.writes = [entry for entry in self.writes if entry["chunk"]["chunk_id"] not in chunk_ids]
        return before - len(self.writes)

    def is_healthy(self) -> bool:
        return self.enabled

    async def search(self, query_vector: List[float], filters: Dict[str, Any], limit: int = 5, namespace: Optional[str] = None):
        base_score = query_vector[0] if query_vector else 0.0
        payload = {
            "document_id": "doc-1",
            "chunk_id": "chunk-1",
            "page": 1,
            "text": "sample",
            "text_enriched": "sample enriched",
            "tags": [],
        }
        return [{"score": base_score, "payload": payload, "id": payload["chunk_id"]}]


class StubLLM:
    async def generate(self, prompt: str, max_tokens: int = 512, model: Optional[str] = None) -> str:
        return f"draft:{prompt[:40]}"


async def test_ingestion_pipeline_deduplication():
    fake_db = FakeDB()
    observability = ObservabilityService(fake_db)  # type: ignore[arg-type]
    pipeline = IngestionPipeline(
        fake_db,
        embedding_client=StubEmbedding(),
        vector_client=StubVector(),
        observability_service=observability,
    )  # type: ignore[arg-type]

    fake_db.documents.docs.append({"_id": "doc-123", "full_text": "Alpha Beta Gamma" * 5})

    job = await pipeline.create_job(
        IngestionJobCreate(org_id="org-1", project_id="proj-1", document_id="doc-123", options=IngestionOptions())
    )
    await pipeline.process_job(job.id)
    assert fake_db.chunks.docs, "Chunks should be created on first ingestion"

    job2 = await pipeline.create_job(
        IngestionJobCreate(org_id="org-1", project_id="proj-1", document_id="doc-123", options=IngestionOptions())
    )
    await pipeline.process_job(job2.id)
    stored_job = await pipeline.get_job(job2.id)
    assert stored_job is not None
    assert stored_job.deduped is True
    assert stored_job.stage.value == "done"


async def test_search_response_shape():
    fake_db = FakeDB()
    observability = ObservabilityService(fake_db)  # type: ignore[arg-type]
    retrieval = RetrievalService(
        db=fake_db,  # type: ignore[arg-type]
        embedding_client=StubEmbedding(),
        vector_client=StubVector(),
        llm_generator=StubLLM(),  # type: ignore[arg-type]
        observability=observability,
    )

    filters = SearchFilters(org_id="org-1", project_id="proj-1")
    request = SearchRequest(query="timeline", strategy=SearchStrategy.VANILLA, limit=3, filters=filters)
    fake_db.documents.docs.append({"_id": "doc-1", "subject": "Doc Subject", "letterNo": "L-1"})
    response = await retrieval.search(request, current_user=None)

    assert response.results, "Search should return at least one result"
    assert response.strategy_used == SearchStrategy.VANILLA
    first = response.results[0]
    assert first.document_id and first.chunk_id


async def test_search_backend_fallback_to_mongo_when_qdrant_unhealthy():
    fake_db = FakeDB()
    fake_db.documents.docs.append({"_id": "doc-1", "subject": "Doc Subject", "letterNo": "L-1"})
    fake_db.chunks.docs.append(
        {
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "org_id": "org-1",
            "project_id": "proj-1",
            "text_original": "timeline item",
        }
    )
    observability = ObservabilityService(fake_db)  # type: ignore[arg-type]
    vector = StubVector()
    vector.enabled = False
    retrieval = RetrievalService(
        db=fake_db,  # type: ignore[arg-type]
        embedding_client=StubEmbedding(),
        vector_client=vector,
        llm_generator=StubLLM(),  # type: ignore[arg-type]
        observability=observability,
    )
    filters = SearchFilters(org_id="org-1", project_id="proj-1")
    request = SearchRequest(
        query="timeline",
        strategy=SearchStrategy.VANILLA,
        limit=1,
        filters=filters,
        backend=SearchBackend.AUTO,
    )
    response = await retrieval.search(request, current_user=None)
    assert response.backend_used == SearchBackend.MONGO
    assert response.results, "Mongo fallback should return results"


class RaisingVector(StubVector):
    """Passes the up-front health check but fails mid-query (e.g. timeout)."""

    def is_healthy(self) -> bool:
        return True

    async def search(self, *args, **kwargs):
        raise RuntimeError("qdrant timeout")


def _seed_one_chunk(fake_db: "FakeDB") -> None:
    fake_db.documents.docs.append({"_id": "doc-1", "subject": "Doc Subject", "letterNo": "L-1"})
    fake_db.chunks.docs.append(
        {
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "org_id": "org-1",
            "project_id": "proj-1",
            "text_original": "timeline item",
        }
    )


async def test_search_falls_back_to_mongo_when_qdrant_raises_midquery():
    # Qdrant is healthy at backend-resolution time but throws during the query.
    # The request must degrade to the Mongo failsafe, not surface a 500.
    fake_db = FakeDB()
    _seed_one_chunk(fake_db)
    observability = ObservabilityService(fake_db)  # type: ignore[arg-type]
    retrieval = RetrievalService(
        db=fake_db,  # type: ignore[arg-type]
        embedding_client=StubEmbedding(),
        vector_client=RaisingVector(),
        llm_generator=StubLLM(),  # type: ignore[arg-type]
        observability=observability,
    )
    filters = SearchFilters(org_id="org-1", project_id="proj-1")
    request = SearchRequest(
        query="timeline",
        strategy=SearchStrategy.VANILLA,
        limit=1,
        filters=filters,
        backend=SearchBackend.AUTO,
    )
    response = await retrieval.search(request, current_user=None)  # must not raise
    assert response.backend_used == SearchBackend.MONGO
    assert response.results, "Mongo failsafe should still return results"


async def test_rag_survives_qdrant_midquery_failure():
    # rag() retrieves via the same path, so a Qdrant in-flight failure must still
    # produce an answer through the Mongo failsafe instead of erroring.
    fake_db = FakeDB()
    _seed_one_chunk(fake_db)
    observability = ObservabilityService(fake_db)  # type: ignore[arg-type]
    retrieval = RetrievalService(
        db=fake_db,  # type: ignore[arg-type]
        embedding_client=StubEmbedding(),
        vector_client=RaisingVector(),
        llm_generator=StubLLM(),  # type: ignore[arg-type]
        observability=observability,
    )
    filters = SearchFilters(org_id="org-1", project_id="proj-1")
    request = RagRequest(query="timeline", strategy=SearchStrategy.VANILLA, limit=1, filters=filters)
    resp = await retrieval.rag(request, current_user=None)  # must not raise
    assert resp.answer, "RAG should produce an answer via the Mongo failsafe"


async def test_rag_citations_include_doc_meta():
    fake_db = FakeDB()
    fake_db.documents.docs.append({"_id": "doc-1", "subject": "Doc Subject", "letterNo": "L-1"})
    observability = ObservabilityService(fake_db)  # type: ignore[arg-type]
    retrieval = RetrievalService(
        db=fake_db,  # type: ignore[arg-type]
        embedding_client=StubEmbedding(),
        vector_client=StubVector(),
        llm_generator=StubLLM(),  # type: ignore[arg-type]
        observability=observability,
    )
    filters = SearchFilters(org_id="org-1", project_id="proj-1")
    request = RagRequest(query="timeline", strategy=SearchStrategy.VANILLA, limit=1, filters=filters)
    resp = await retrieval.rag(request, current_user=None)
    assert resp.citations, "RAG should return citations"
    assert resp.citations[0].document_title == "Doc Subject"
    assert resp.citations[0].letter_no == "L-1"
