"""Golden contract QA regression suite (deployment quality gate).

Runs the full contract QA pipeline — clause chunking -> clause vectors ->
hybrid retrieval -> heuristic + cross-encoder rerank -> citation enforcement ->
evidence ledger -> guardrails — against a small seeded GCC corpus using
deterministic fakes for embeddings, the drafting LLM, and the reranker backend
(no model downloads, no network). Each fixture case in
fixtures/golden_contract_qa_cases.json gates on:

- retrieval quality: required clause numbers must be cited, forbidden must not;
- citation quality: minimum citation coverage measured by the guardrail;
- unsupported-answer handling: expected guardrail verdict.

Set GOLDEN_QA_LIVE=1 to additionally run the live smoke test against real
Qdrant/OpenAI credentials (skipped by default and in CI).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from rbac_backend.retrieval.models import ContractQARequest, SearchFilters
from rbac_backend.retrieval.reranker import RerankerService
from rbac_backend.retrieval.service import RetrievalService
from rbac_backend.services.ai_guardrails import AIOutputGuardrailService
from rbac_backend.tests.test_contract_clause_retrieval_integration import (
    FakeDB,
    FakeEmbeddingClient,
    _index_document,
    _offline_vector_client,
    _seed_upload,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "golden_contract_qa_cases.json"
GOLDEN_CASES = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]


class ScriptedLLM:
    """Returns the case's scripted draft for the QA prompt; empty critique."""

    def __init__(self, answer: str):
        self.answer = answer

    async def generate(self, prompt, max_tokens=512, model=None):
        if "Contract Specialist" in prompt:
            return self.answer
        return "{}"  # critique step: no issues, no refinements


class KeywordRerankBackend:
    """Deterministic stand-in for a cross-encoder: term-overlap scoring."""

    async def score(self, query, passages):
        terms = [t for t in re.findall(r"[a-z]+", query.lower()) if len(t) > 3]
        if not terms:
            return [0.0 for _ in passages]
        return [
            min(1.0, sum(1 for t in terms if t in passage.lower()) / len(terms))
            for passage in passages
        ]


class _Obs:
    def __init__(self):
        self.runs = []

    async def log_run(self, **kwargs):
        self.runs.append(kwargs)


_corpus: dict = {}


async def _get_corpus():
    """Seed + clause-index the golden corpus once per test session."""
    if not _corpus:
        db = FakeDB()
        _seed_upload(db)
        vector_client = _offline_vector_client()
        await _index_document(db, vector_client)
        _corpus["db"] = db
        _corpus["vector_client"] = vector_client
    return _corpus["db"], _corpus["vector_client"]


async def _run_case(case: dict):
    db, vector_client = await _get_corpus()
    observability = _Obs()
    service = RetrievalService(
        db=db,
        embedding_client=FakeEmbeddingClient(),
        vector_client=vector_client,
        llm_generator=ScriptedLLM(case["fake_answer"]),
        observability=observability,
        reranker=RerankerService(KeywordRerankBackend(), enabled=True, weight=0.5),
        guardrails=AIOutputGuardrailService(),
    )
    request = ContractQARequest(
        query=case["question"],
        limit=case.get("limit", 1),
        require_citations=case.get("require_citations", True),
        max_iterations=2,
        filters=SearchFilters(
            org_id="org-A",
            project_id="proj-A",
            document_id="doc-1",
            metadata={"uploadType": "contract", "document_type": "contract"},
        ),
    )
    response = await service.contract_iterative_qa(request, current_user=None)
    return response, observability


@pytest.mark.asyncio
@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["id"] for c in GOLDEN_CASES])
async def test_golden_contract_qa_case(case):
    response, observability = await _run_case(case)
    cited_clauses = {c.clause_number for c in response.citations if c.clause_number}

    for clause in case["required_clauses"]:
        assert clause in cited_clauses, (
            f"required clause {clause} not cited; got {sorted(cited_clauses)}"
        )
    for clause in case["forbidden_clauses"]:
        assert clause not in cited_clauses, (
            f"forbidden clause {clause} was cited; got {sorted(cited_clauses)}"
        )

    for fact in case["expected_answer_facts"]:
        assert fact.lower() in response.answer.lower(), (
            f"expected fact {fact!r} missing from answer: {response.answer!r}"
        )

    assert response.guardrail is not None
    assert response.guardrail.verdict == case["expected_verdict"]
    coverage = response.guardrail.citation_coverage
    if coverage is not None:
        assert coverage >= case["min_citation_coverage"]
        if "max_citation_coverage" in case:
            assert coverage <= case["max_citation_coverage"]

    # Observability gate: coverage and score breakdown must be logged.
    run = observability.runs[-1]
    assert run["run_type"] == "contract_iterative_qa"
    if coverage is not None:
        assert run["counts"]["citation_coverage_pct"] == int(round(coverage * 100))


@pytest.mark.asyncio
async def test_golden_case_evidence_ledger_provenance():
    case = GOLDEN_CASES[0]
    response, _ = await _run_case(case)

    assert response.evidence_ledger, "evidence ledger must be populated"
    assert len(response.evidence_ledger) == len(response.citations)
    run_ids = {entry.run_id for entry in response.evidence_ledger}
    assert len(run_ids) == 1 and None not in run_ids
    for idx, entry in enumerate(response.evidence_ledger):
        assert entry.workflow == "contract_qa"
        assert entry.citation_label == f"C{idx + 1}"
        assert entry.organization_id == "org-A"
        assert entry.project_id == "proj-A"
        assert entry.source_hash, "source text must be hashed for auditability"
        assert entry.final_score is not None

    # Legacy citations are generated from the ledger, so they must agree.
    for entry, citation in zip(response.evidence_ledger, response.citations):
        assert entry.chunk_id == citation.chunk_id
        assert entry.clause_number == citation.clause_number


@pytest.mark.asyncio
async def test_reranker_scores_recorded_in_ledger():
    case = GOLDEN_CASES[0]
    response, _ = await _run_case(case)
    entry = response.evidence_ledger[0]
    assert entry.reranker_score is not None
    assert entry.lexical_score is not None
    assert entry.retrieval_score is not None


@pytest.mark.live_external_service
@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("GOLDEN_QA_LIVE"),
    reason="live golden QA run disabled by default; set GOLDEN_QA_LIVE=1 with real Qdrant/OpenAI credentials",
)
async def test_golden_contract_qa_live_smoke():
    """Optional live run against real Qdrant/OpenAI for pre-deploy validation."""
    from rbac_backend.core.database import get_database
    from rbac_backend.observability.service import ObservabilityService
    from rbac_backend.retrieval.dependencies import (
        get_embedding_client,
        get_llm_generator,
        get_vector_client,
    )

    db = await get_database()
    llm = get_llm_generator()
    service = RetrievalService(
        db=db,
        embedding_client=get_embedding_client(),
        vector_client=get_vector_client(),
        llm_generator=llm,
        observability=ObservabilityService(db),
        guardrails=AIOutputGuardrailService(),
    )
    case = GOLDEN_CASES[0]
    request = ContractQARequest(
        query=case["question"],
        limit=5,
        require_citations=True,
        filters=SearchFilters(
            org_id=os.environ["GOLDEN_QA_ORG_ID"],
            project_id=os.environ["GOLDEN_QA_PROJECT_ID"],
            metadata={"uploadType": "contract", "document_type": "contract"},
        ),
    )
    response = await service.contract_iterative_qa(request, current_user=None)
    assert response.answer
    assert response.guardrail is not None
