from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Iterable, List

import pytest

from rbac_backend.models.contract_models import ContractSearchRequest
from rbac_backend.retrieval.models import ContractQARequest, SearchBackend, SearchFilters, SearchRequest, SearchResult
from rbac_backend.retrieval.service import RetrievalService
from rbac_backend.services.contract_service import ContractService


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    if "$or" in query:
        remaining = {key: value for key, value in query.items() if key != "$or"}
        return _matches(doc, remaining) and any(_matches(doc, item) for item in query["$or"])
    for key, expected in query.items():
        if isinstance(expected, dict):
            if "$in" in expected:
                if doc.get(key) not in expected["$in"]:
                    return False
                continue
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
    def __init__(self, docs: List[Dict[str, Any]], documents: List[Dict[str, Any]] | None = None) -> None:
        self.document_vectors = _Collection(docs)
        self.documents = _Collection(documents or [])


class _Embedding:
    async def embed(self, texts, model=None):
        return [[1.0] for _ in texts]


class _Vector:
    def is_healthy(self):
        return False


class _EmptyQdrantVector:
    def is_healthy(self):
        return True

    async def search(self, *_args, **_kwargs):
        return []


class _LLM:
    async def generate(self, prompt: str, max_tokens: int = 512, model=None):
        return "draft"


class _CitationLLM:
    async def generate(self, prompt: str, max_tokens: int = 512, model=None):
        if "Return JSON only" in prompt:
            return '{"issues":["sufficient"],"refinements":[]}'
        return "Clause 8.4 supports extension of time when the cited conditions are met [C1]."


class _GroupedCitationLLM:
    async def generate(self, prompt: str, max_tokens: int = 512, model=None):
        if "Return JSON only" in prompt:
            return '{"issues":["sufficient"],"refinements":[]}'
        return (
            "The conditions are completion in accordance with the Contract except minor outstanding work or defects, "
            "an application not earlier than 14 days before readiness, and the Engineer's issue or deemed issue of "
            "the Taking-Over Certificate [C1, C2]. "
            "Yes, the Employer can take over part of the Permanent Works through a Taking-Over Certificate for that "
            "part, and use before certification can deem that part taken over from the date of use [C2]."
        )


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
async def test_contract_search_falls_back_to_mongo_when_qdrant_returns_no_hits():
    db = _DB(
        [
            {
                "uploadType": "contract",
                "organization_id": "org-A",
                "project_id": "proj-A",
                "document_id": "doc-gcc",
                "chunk_id": "gcc-9.1-0",
                "chunk_index": 0,
                "clause_number": "9.1",
                "clause_title": "Taking Over Certificate",
                "section_heading": "Employer's Taking Over",
                "text": "The Works shall be taken over after completion and passing Tests on Completion.",
                "page_numbers": [52],
                "page_number": 52,
                "file_name": "Vol-2-GCC_and_SCC.pdf",
            }
        ],
        documents=[{"_id": "doc-gcc", "subject": "GCC Contract", "filename": "gcc.pdf"}],
    )
    service = RetrievalService(
        db=db,  # type: ignore[arg-type]
        embedding_client=_Embedding(),
        vector_client=_EmptyQdrantVector(),  # type: ignore[arg-type]
        llm_generator=_LLM(),  # type: ignore[arg-type]
        observability=_Observability(),  # type: ignore[arg-type]
    )

    response = await service.search(
        SearchRequest(
            query="Taking Over Certificate Tests on Completion",
            filters=SearchFilters(
                org_id="org-A",
                project_id="proj-A",
                document_id="doc-gcc",
                metadata={"uploadType": "contract"},
            ),
            backend=SearchBackend.AUTO,
        ),
        current_user=None,
        log_run=False,
    )

    assert response.backend_used == SearchBackend.MONGO
    assert response.results
    assert response.results[0].chunk_id == "gcc-9.1-0"


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


@pytest.mark.asyncio
async def test_contract_qa_mongo_fallback_expands_complete_clause_and_rewrites_citations(monkeypatch):
    import rbac_backend.services.contract_graph_service as graph_module

    monkeypatch.setattr(graph_module, "ContractGraphService", lambda: SimpleNamespace(find_related_clauses=lambda **_kwargs: []))
    db = _DB(
        [
            {
                "uploadType": "contract",
                "document_type": "contract",
                "organization_id": "org-A",
                "project_id": "proj-A",
                "document_id": "doc-gcc",
                "upload_id": "upload-gcc",
                "clause_number": "8.4",
                "clause_title": "Extension of Time",
                "section_heading": "GCC",
                "clause_start_position": 100,
                "chunk_id": "gcc-8.4-0",
                "chunk_index": 0,
                "text": "GCC 8.4 entitles the Contractor to extension of time for Employer delay.",
                "text_enriched": "Clause 8.4: Extension of Time\nGCC 8.4 entitles the Contractor to extension of time for Employer delay.",
                "page_numbers": [5],
                "page_number": 5,
            },
            {
                "uploadType": "contract",
                "document_type": "contract",
                "organization_id": "org-A",
                "project_id": "proj-A",
                "document_id": "doc-gcc",
                "upload_id": "upload-gcc",
                "clause_number": "8.4",
                "clause_title": "Extension of Time",
                "section_heading": "GCC",
                "clause_start_position": 100,
                "chunk_id": "gcc-8.4-1",
                "chunk_index": 1,
                "text": "The Contractor must give notice and particulars under GCC 8.4.",
                "text_enriched": "Clause 8.4: Extension of Time\nThe Contractor must give notice and particulars under GCC 8.4.",
                "page_numbers": [6],
                "page_number": 6,
            },
        ],
        documents=[{"_id": "doc-gcc", "subject": "GCC Contract", "filename": "gcc.pdf"}],
    )
    service = RetrievalService(
        db=db,  # type: ignore[arg-type]
        embedding_client=_Embedding(),
        vector_client=_Vector(),  # type: ignore[arg-type]
        llm_generator=_CitationLLM(),  # type: ignore[arg-type]
        observability=_Observability(),  # type: ignore[arg-type]
    )

    response = await service.contract_iterative_qa(
        ContractQARequest(
            query="What does GCC 8.4 say about extension of time?",
            filters=SearchFilters(org_id="org-A", project_id="proj-A", metadata={"uploadType": "contract"}),
            max_iterations=1,
            require_citations=True,
        ),
        current_user=None,
    )

    assert "GCC>8.4" in response.answer
    assert response.citations
    assert response.citations[0].document_id == "doc-gcc"
    assert response.citations[0].clause_number == "8.4"
    assert response.citations[0].clause_title == "Extension of Time"
    assert response.citations[0].page_numbers == [5, 6]
    assert "notice and particulars" in response.citations[0].snippet
    assert response.trace[0].retrieved_ids == ["GCC>8.4"]


@pytest.mark.asyncio
async def test_contract_qa_returns_clean_answer_and_separate_sources(monkeypatch):
    import rbac_backend.services.contract_graph_service as graph_module

    monkeypatch.setattr(graph_module, "ContractGraphService", lambda: SimpleNamespace(find_related_clauses=lambda **_kwargs: []))
    db = _DB(
        [
            {
                "uploadType": "contract",
                "document_type": "contract",
                "organization_id": "org-A",
                "project_id": "proj-A",
                "document_id": "doc-gcc",
                "upload_id": "upload-gcc",
                "clause_number": "10.1",
                "clause_title": "Taking Over of the Works and Sections",
                "section_heading": "General Conditions",
                "clause_start_position": 100,
                "chunk_id": "gcc-10.1-0",
                "chunk_index": 0,
                "text": (
                    "The Works shall be taken over by the Employer when they have been completed in accordance "
                    "with the Contract, except for minor outstanding work and defects which will not substantially "
                    "affect use. The Contractor may apply for a Taking-Over Certificate not earlier than 14 days "
                    "before the Works will be complete and ready for taking over. The Engineer shall within 28 days "
                    "issue the Taking-Over Certificate stating the date of completion or reject the application."
                ),
                "page_numbers": [50],
                "page_number": 50,
            },
            {
                "uploadType": "contract",
                "document_type": "contract",
                "organization_id": "org-A",
                "project_id": "proj-A",
                "document_id": "doc-gcc",
                "upload_id": "upload-gcc",
                "clause_number": "10.2",
                "clause_title": "Taking Over of Parts of the Works",
                "section_heading": "General Conditions",
                "clause_start_position": 200,
                "chunk_id": "gcc-10.2-0",
                "chunk_index": 0,
                "text": (
                    "The Engineer may, at the sole discretion of the Employer, issue a Taking-Over Certificate "
                    "for any part of the Permanent Works. The Employer shall not use any part of the Works unless "
                    "and until the Engineer has issued a Taking-Over Certificate for this part; if the Employer "
                    "uses a part before certification, that part shall be deemed to have been taken over from the "
                    "date on which it is used."
                ),
                "page_numbers": [51],
                "page_number": 51,
            },
        ],
        documents=[{"_id": "doc-gcc", "subject": "GCC Contract", "filename": "gcc.pdf"}],
    )
    service = RetrievalService(
        db=db,  # type: ignore[arg-type]
        embedding_client=_Embedding(),
        vector_client=_Vector(),  # type: ignore[arg-type]
        llm_generator=_GroupedCitationLLM(),  # type: ignore[arg-type]
        observability=_Observability(),  # type: ignore[arg-type]
    )

    response = await service.contract_iterative_qa(
        ContractQARequest(
            query="What are the conditions for the issuance of a Taking Over Certificate? Can the employer issue part taking over?",
            filters=SearchFilters(org_id="org-A", project_id="proj-A", metadata={"uploadType": "contract"}),
            max_iterations=1,
        ),
        current_user=None,
    )

    assert "conditions are completion in accordance with the Contract" in response.answer
    assert "Employer can take over part" in response.answer
    assert "[C1, C2]" not in response.answer
    assert "General Conditions>10.1" not in response.answer
    assert "General Conditions>10.2" not in response.answer
    assert "p. 50" not in response.answer
    assert "chunk gcc-10.1-0" not in response.answer
    assert {citation.clause_number for citation in response.citations} == {"10.1", "10.2"}
    assert {citation.chunk_id for citation in response.citations} == {"gcc-10.1-0", "gcc-10.2-0"}
    assert response.citations[0].page_numbers
