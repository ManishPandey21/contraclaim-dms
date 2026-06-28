from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Iterable, List

import pytest

from rbac_backend.models.contract_models import ContractSearchRequest
from rbac_backend.retrieval.models import ContractQARequest, SearchFilters, SearchResult
from rbac_backend.retrieval.service import RetrievalService
from rbac_backend.services.contract_service import ContractService


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    if "$or" in query:
        return any(_matches(doc, item) for item in query["$or"])
    for key, expected in query.items():
        if doc.get(key) != expected:
            return False
    return True


class _Cursor:
    def __init__(self, docs: Iterable[Dict[str, Any]]) -> None:
        self.docs = [dict(doc) for doc in docs]

    def sort(self, key, direction=1):
        if isinstance(key, list):
            for field, item_direction in reversed(key):
                self.docs.sort(key=lambda item: item.get(field) or 0, reverse=item_direction < 0)
        else:
            self.docs.sort(key=lambda item: item.get(key) or 0, reverse=direction < 0)
        return self

    def limit(self, limit: int):
        self.docs = self.docs[:limit]
        return self

    async def to_list(self, length=None):
        return self.docs if length is None else self.docs[:length]

    def __aiter__(self):
        async def _iterate():
            for doc in self.docs:
                yield doc

        return _iterate()


class _Collection:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self.docs = docs

    def find(self, query: Dict[str, Any]):
        return _Cursor([doc for doc in self.docs if _matches(doc, query)])


class _DB:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self.document_vectors = _Collection(docs)


class _Embedding:
    async def embed(self, texts, model=None):
        return [[1.0] for _ in texts]


class _Vector:
    def is_healthy(self):
        return False


class _LLM:
    async def generate(self, prompt: str, max_tokens: int = 512, model=None):
        return "draft"


class _Observability:
    async def log_run(self, **_kwargs):
        return None


class _Graph:
    def find_related_clauses(self, **_kwargs):
        return [
            {
                "document_id": "doc-scc",
                "clause_id": "scc-8.4",
                "clause_number": "8.4",
                "clause_title": "SCC amendment to EOT",
                "text": "SCC 8.4 modifies the GCC extension of time clause.",
                "page_number": 7,
                "section_type": "SCC",
                "graph_relation": "same_clause_number",
            }
        ]


@pytest.mark.asyncio
async def test_contract_qa_augments_retrieval_with_graph_related_clauses(monkeypatch):
    import rbac_backend.services.contract_graph_service as graph_module

    monkeypatch.setattr(graph_module, "ContractGraphService", lambda: _Graph())
    db = _DB(
        [
            {
                "uploadType": "contract",
                "document_id": "doc-scc",
                "clause_number": "8.4",
                "clause_title": "SCC amendment to EOT",
                "chunk_id": "scc-chunk-1",
                "chunk_index": 0,
                "text": "SCC 8.4 modifies the GCC extension of time clause.",
                "page_numbers": [7],
            }
        ]
    )
    service = RetrievalService(
        db=db,  # type: ignore[arg-type]
        embedding_client=_Embedding(),
        vector_client=_Vector(),  # type: ignore[arg-type]
        llm_generator=_LLM(),  # type: ignore[arg-type]
        observability=_Observability(),  # type: ignore[arg-type]
    )
    seed = SearchResult(
        document_id="doc-gcc",
        chunk_id="gcc-chunk-1",
        score=1.0,
        snippet="GCC 8.4 extension of time",
        payload={"uploadType": "contract", "document_id": "doc-gcc", "clause_number": "8.4", "text": "GCC 8.4 extension of time"},
    )
    request = ContractQARequest(
        query="How does GCC 8.4 work?",
        filters=SearchFilters(org_id="org-A", project_id="proj-A", metadata={"uploadType": "contract"}),
    )

    expanded = await service._augment_with_contract_graph_results([seed], request, limit=5)

    assert len(expanded) == 2
    graph_result = expanded[1]
    assert graph_result.document_id == "doc-scc"
    assert graph_result.payload["graph_expanded"] is True
    assert graph_result.payload["graph_relation"] == "same_clause_number"
    assert graph_result.payload["clause_number"] == "8.4"


@pytest.mark.asyncio
async def test_contract_search_assembles_graph_candidates_into_clause_chunks():
    service = ContractService()
    db = _DB(
        [
            {
                "uploadType": "contract",
                "document_id": "doc-scc",
                "upload_id": "up-1",
                "clause_number": "8.4",
                "clause_title": "SCC amendment to EOT",
                "chunk_id": "scc-chunk-1",
                "chunk_index": 0,
                "text": "SCC 8.4 modifies the GCC extension of time clause.",
                "page_numbers": [7],
            }
        ]
    )

    rows, total, has_more = await service._assemble_results(
        db.document_vectors,
        lexical_candidates=[],
        vector_candidates=[],
        graph_candidates=[{"document_id": "doc-scc", "clause_number": "8.4", "score": 0.75, "graph_relation": "same_clause_number"}],
        page_size=10,
        skip_count=0,
    )

    assert total == 1
    assert has_more is False
    assert rows[0]["document_id"] == "doc-scc"
    assert rows[0]["clause_number"] == "8.4"
    assert rows[0]["text"].startswith("SCC 8.4")


def test_contract_search_extracts_graph_clause_seeds():
    service = ContractService()
    request = ContractSearchRequest(query="How does GCC 8.4 affect EOT?", organization_id="org-A", project_id="proj-A")

    assert service._extract_clause_numbers_for_graph(request) == ["8.4"]
