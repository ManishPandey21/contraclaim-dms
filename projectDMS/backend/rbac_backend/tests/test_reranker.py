"""RerankerService: blending, fallback, feature flag, score observability."""

from __future__ import annotations

import asyncio

import pytest

from rbac_backend.retrieval.models import SearchResult
from rbac_backend.retrieval.reranker import LLMRerankerBackend, RerankerService


class FakeBackend:
    """Deterministic backend: scores by keyword hit, records calls."""

    def __init__(self, scores=None, delay=0.0, error=None):
        self.scores = scores
        self.delay = delay
        self.error = error
        self.calls = []

    async def score(self, query, passages):
        self.calls.append((query, list(passages)))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        if self.scores is not None:
            return self.scores[: len(passages)]
        return [1.0 if any(t in p.lower() for t in query.lower().split()) else 0.0 for p in passages]


def _result(cid: str, text: str, score: float) -> SearchResult:
    return SearchResult(
        document_id="d", chunk_id=cid, score=score, snippet=text[:80],
        payload={"text": text, "scores": {"heuristic_score": score}},
    )


@pytest.mark.asyncio
async def test_disabled_reranker_returns_input_unchanged():
    service = RerankerService(FakeBackend(), enabled=False)
    results = [_result("a", "alpha", 1.0), _result("b", "beta", 0.5)]
    assert await service.rerank("beta", results) == results


@pytest.mark.asyncio
async def test_reranker_promotes_semantically_relevant_result():
    # Heuristic order puts the irrelevant chunk first; reranker flips it.
    results = [
        _result("wrong", "payment schedule terms", 2.0),
        _result("right", "extension of time entitlement", 0.5),
    ]
    service = RerankerService(FakeBackend(), enabled=True, weight=0.8)
    ranked = await service.rerank("extension time", results)
    assert [r.chunk_id for r in ranked] == ["right", "wrong"]


@pytest.mark.asyncio
async def test_reranker_annotates_all_four_scores():
    results = [_result("a", "extension of time", 1.5)]
    service = RerankerService(FakeBackend(), enabled=True, weight=0.5)
    ranked = await service.rerank("extension", results)
    scores = ranked[0].payload["scores"]
    assert scores["base_score"] == 1.5
    assert scores["lexical_score"] == 1.5  # heuristic score carried through
    assert scores["reranker_score"] == 1.0
    assert 0.0 <= scores["final_score"] <= 1.0


@pytest.mark.asyncio
async def test_backend_failure_falls_back_to_heuristic_order():
    results = [_result("a", "alpha", 2.0), _result("b", "beta", 1.0)]
    service = RerankerService(FakeBackend(error=RuntimeError("boom")), enabled=True)
    ranked = await service.rerank("beta", results)
    assert [r.chunk_id for r in ranked] == ["a", "b"]


@pytest.mark.asyncio
async def test_timeout_falls_back_to_heuristic_order():
    results = [_result("a", "alpha", 2.0), _result("b", "beta", 1.0)]
    service = RerankerService(FakeBackend(delay=0.2), enabled=True, timeout_ms=10)
    ranked = await service.rerank("beta", results)
    assert [r.chunk_id for r in ranked] == ["a", "b"]


@pytest.mark.asyncio
async def test_top_n_limits_rerank_window_and_keeps_tail():
    backend = FakeBackend()
    results = [
        _result("a", "payment", 3.0),
        _result("b", "extension of time", 2.0),
        _result("c", "tail stays put", 1.0),
    ]
    service = RerankerService(backend, enabled=True, top_n=2, weight=1.0)
    ranked = await service.rerank("extension", results)
    assert len(backend.calls[0][1]) == 2  # only the head was scored
    assert ranked[-1].chunk_id == "c"  # tail untouched
    assert ranked[0].chunk_id == "b"


@pytest.mark.asyncio
async def test_backend_value_error_falls_back():
    results = [_result("a", "alpha", 2.0), _result("b", "beta", 1.0)]
    service = RerankerService(FakeBackend(error=ValueError("wrong length")), enabled=True)
    ranked = await service.rerank("beta", results)
    assert [r.chunk_id for r in ranked] == ["a", "b"]


@pytest.mark.asyncio
async def test_llm_backend_parses_json_array():
    class _Gen:
        async def generate(self, prompt, max_tokens=512, model=None):
            return "Here are the scores: [0.2, 0.9]"

    backend = LLMRerankerBackend(_Gen())
    assert await backend.score("q", ["p1", "p2"]) == [0.2, 0.9]


@pytest.mark.asyncio
async def test_llm_backend_raises_on_garbage_output():
    class _Gen:
        async def generate(self, prompt, max_tokens=512, model=None):
            return "no scores here"

    backend = LLMRerankerBackend(_Gen())
    with pytest.raises(ValueError):
        await backend.score("q", ["p1"])


def test_from_settings_respects_flag():
    class _Settings:
        RERANKER_ENABLED = False
        RERANKER_PROVIDER = "llm"
        RERANKER_MODEL = ""
        RERANKER_TOP_N = 10
        RERANKER_TIMEOUT_MS = 5000
        RERANKER_WEIGHT = 0.4

    service = RerankerService.from_settings(_Settings(), llm_generator=object())
    assert service.enabled is False
    assert service.top_n == 10
    assert service.weight == 0.4
