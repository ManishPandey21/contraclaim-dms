"""RAG-quality unit tests (Phase 2: M1 lexical scoring, M2 context budgeting)."""

from __future__ import annotations

from rbac_backend.retrieval.models import SearchResult
from rbac_backend.retrieval.service import RetrievalService


def _svc() -> RetrievalService:
    return RetrievalService(
        db=None, embedding_client=None, vector_client=None, llm_generator=None, observability=None
    )


def _result(text: str, cid: str = "c") -> SearchResult:
    return SearchResult(document_id="d", chunk_id=cid, score=1.0, snippet=text[:50], payload={"text": text})


# --- M1: _lexical_score ----------------------------------------------------


def test_lexical_score_ranks_phrase_over_partial_over_none():
    full = RetrievalService._lexical_score("delay notice clause", "the delay notice clause was served")
    partial = RetrievalService._lexical_score("delay notice clause", "a delay occurred today")
    none = RetrievalService._lexical_score("delay notice clause", "unrelated payment schedule")
    assert full > partial > none
    assert none == 0.0


def test_lexical_score_empty_text_is_zero():
    assert RetrievalService._lexical_score("anything", "") == 0.0


def test_lexical_score_no_query_terms_is_floor():
    assert RetrievalService._lexical_score("", "some document text") == 0.1


def test_lexical_score_frequency_increases_score():
    once = RetrievalService._lexical_score("variation", "a variation was raised")
    many = RetrievalService._lexical_score("variation", "variation variation variation variation")
    assert many > once


# --- M2: _assemble_context -------------------------------------------------


def test_assemble_context_includes_all_within_budget():
    ctx = _svc()._assemble_context([_result("alpha alpha", "1"), _result("beta beta", "2")], char_budget=10000)
    assert "alpha" in ctx and "beta" in ctx


def test_assemble_context_respects_budget_and_stops():
    big = "x" * 5000
    ctx = _svc()._assemble_context([_result(big, "1"), _result("LATER_CHUNK", "2")], char_budget=1000)
    assert len(ctx) <= 1000
    assert "LATER_CHUNK" not in ctx


def test_assemble_context_preserves_rank_order():
    ctx = _svc()._assemble_context([_result("FIRST", "1"), _result("SECOND", "2")], char_budget=10000)
    assert ctx.index("FIRST") < ctx.index("SECOND")


def test_assemble_context_skips_empty_chunks():
    ctx = _svc()._assemble_context([_result("", "1"), _result("real content", "2")], char_budget=10000)
    assert "real content" in ctx
