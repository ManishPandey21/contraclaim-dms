"""Unified evidence ledger: adapters from every workflow shape + legacy regen."""

from __future__ import annotations

import hashlib

from rbac_backend.models.evidence_ledger import EvidenceLedgerEntry
from rbac_backend.models.letter_drafting import SourceEvidence
from rbac_backend.retrieval.models import Citation, SearchResult


def _search_result(**overrides) -> SearchResult:
    payload = {
        "text": "The Contractor shall be entitled to an extension of time.",
        "clause_number": "8.4",
        "clause_title": "Extension of Time for Completion",
        "section_heading": "GENERAL PROVISIONS",
        "page_numbers": [12, 13],
        "file_name": "Vol-2-GCC.pdf",
        "scores": {"base_score": 0.8, "heuristic_score": 1.1, "reranker_score": 0.9, "final_score": 0.95},
    }
    payload.update(overrides.pop("payload", {}))
    defaults = dict(document_id="doc-1", chunk_id="ch-1", score=0.8, page=12, snippet="entitled to an extension", payload=payload)
    defaults.update(overrides)
    return SearchResult(**defaults)


def test_from_search_result_maps_scores_and_clause_fields():
    entry = EvidenceLedgerEntry.from_search_result(
        _search_result(), workflow="contract_qa", run_id="run-1",
        organization_id="org-A", project_id="proj-A", citation_label="C1",
    )
    assert entry.workflow == "contract_qa"
    assert entry.run_id == "run-1"
    assert entry.source_type == "contract_clause"
    assert entry.clause_number == "8.4"
    assert entry.page_numbers == [12, 13]
    assert entry.citation_label == "C1"
    assert entry.retrieval_score == 0.8
    assert entry.reranker_score == 0.9
    assert entry.final_score == 0.95
    assert entry.metadata["file_name"] == "Vol-2-GCC.pdf"


def test_from_search_result_hashes_underlying_text():
    entry = EvidenceLedgerEntry.from_search_result(_search_result(), workflow="contract_qa")
    expected = hashlib.sha256(
        "The Contractor shall be entitled to an extension of time.".encode("utf-8")
    ).hexdigest()
    assert entry.source_hash == expected


def test_from_search_result_without_scores_falls_back_to_result_score():
    res = _search_result(payload={"scores": {}})
    entry = EvidenceLedgerEntry.from_search_result(res, workflow="contract_qa")
    assert entry.retrieval_score == 0.8
    assert entry.final_score == 0.8
    assert entry.reranker_score is None


def test_from_search_result_non_clause_payload_is_document_chunk():
    res = SearchResult(document_id="d", chunk_id="c", score=0.5, snippet="hello", payload={"text": "hello"})
    entry = EvidenceLedgerEntry.from_search_result(res, workflow="contract_qa")
    assert entry.source_type == "document_chunk"


def test_from_citation_round_trip_regenerates_equivalent_citation():
    citation = Citation(
        document_id="doc-1", chunk_id="ch-1", page=12, score=0.7,
        snippet="entitled to an extension", document_title="GCC Volume 2",
        letter_no=None, file_name="Vol-2-GCC.pdf", clause_number="8.4",
        clause_title="Extension of Time", section_heading="GENERAL", page_numbers=[12],
    )
    entry = EvidenceLedgerEntry.from_citation(citation, workflow="contract_qa", citation_label="C2")
    regenerated = entry.to_citation()
    assert regenerated.document_id == citation.document_id
    assert regenerated.chunk_id == citation.chunk_id
    assert regenerated.clause_number == citation.clause_number
    assert regenerated.snippet == citation.snippet
    assert regenerated.file_name == citation.file_name
    assert regenerated.document_title == citation.document_title
    assert regenerated.page == 12
    assert regenerated.score == citation.score


def test_from_source_evidence_preserves_allowed_use_and_hash():
    evidence = SourceEvidence(
        source_id="sev-1", source_type="contract_clause", allowed_use="clause",
        organization_id="org-A", project_id="proj-A", label="GCC 8.4",
        text="full clause text", snippet="clause text", document_id="doc-1",
        clause_number="8.4", page_numbers=[12], score=0.9, source_hash="abc123",
    )
    entry = EvidenceLedgerEntry.from_source_evidence(evidence, run_id="run-9")
    assert entry.workflow == "letter_drafting"
    assert entry.allowed_use == "clause"
    assert entry.source_hash == "abc123"  # existing hash kept, not recomputed
    assert entry.citation_label == "GCC 8.4"
    assert entry.organization_id == "org-A"


def test_from_source_evidence_computes_hash_when_missing():
    evidence = SourceEvidence(
        source_id="sev-2", source_type="context_document", allowed_use="fact",
        label="Doc", text="some evidence text",
    )
    entry = EvidenceLedgerEntry.from_source_evidence(evidence)
    assert entry.source_hash == hashlib.sha256(b"some evidence text").hexdigest()


def test_page_numbers_cleaned_of_junk_values():
    res = _search_result(payload={"page_numbers": [3, "4", "n/a", None]})
    entry = EvidenceLedgerEntry.from_search_result(res, workflow="contract_qa")
    assert entry.page_numbers == [3, 4]
