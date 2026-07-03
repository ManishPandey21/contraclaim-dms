"""Phase 3 tests: SCC modification detection, Qdrant payload correctness,
and FalkorDB clause graph node/relationship creation.
"""

from __future__ import annotations

import pytest

from rbac_backend.services.contract_clause import (
    ClauseEmbeddingService,
    ClauseGraphService,
    ClauseStorageService,
    ModificationDetector,
    clause_node_id,
)


def _clause(**overrides):
    svc = ClauseStorageService(db=None)
    base = dict(
        org_id="org-A", project_id="proj-A", contract_id="ct-1", document_id="doc-1",
        clause_no="8.4", clause_title="Extension of Time", text="body",
        document_type="GCC",
    )
    base.update(overrides)
    return svc.build_record(**base)


# --------------------------------------------------------------------------- #
# SCC / addendum modification detection (req 19, 20)
# --------------------------------------------------------------------------- #
def test_detect_replace_modification():
    signals = ModificationDetector.detect(
        "Sub-Clause 8.4 is deleted and replaced by the following provision."
    )
    assert len(signals) == 1
    assert signals[0].base_clause_no == "8.4"
    assert signals[0].modification_type == "replace"
    assert signals[0].human_review_required is True


def test_detect_amend_and_add():
    amend = ModificationDetector.detect("Sub-Clause 4.2 is amended as follows.")
    assert amend[0].modification_type == "amend"
    assert amend[0].base_clause_no == "4.2"

    add = ModificationDetector.detect("Add the following at the end of Sub-Clause 13.3.")
    assert add[0].modification_type == "add"
    assert add[0].base_clause_no == "13.3"

    notw = ModificationDetector.detect("Notwithstanding GCC Clause 20.1, the following applies.")
    assert notw[0].modification_type == "amend"
    assert notw[0].base_clause_no == "20.1"


def test_no_modification_without_keyword():
    assert ModificationDetector.detect("Clause 8.4 sets out the time for completion.") == []


def test_build_modification_link_flags_human_review():
    sig = ModificationDetector.detect("Sub-Clause 8.4 is deleted.")[0]
    link = ModificationDetector.build_modification_link(
        signal=sig, applicable_clause_id="uid-scc-84", modifier_document_type="SCC",
    )
    assert link["base_clause_no"] == "8.4"
    assert link["base_document_type"] == "GCC"
    assert link["modifier_document_type"] == "SCC"
    assert link["modification_type"] == "delete"
    assert link["human_review_required"] is True


# --------------------------------------------------------------------------- #
# Qdrant payload correctness (req 16, 17)
# --------------------------------------------------------------------------- #
def test_qdrant_payload_contains_required_fields():
    clause = _clause(page_start=145, page_end=146, volume="Vol-2")
    payload = ClauseEmbeddingService.build_payload(clause)
    for key in (
        "org_id", "project_id", "contract_id", "document_id",
        "clause_no", "clause_title", "page_start", "page_end",
        "document_type", "is_current",
    ):
        assert key in payload
    assert payload["clause_no"] == "8.4"
    assert payload["document_type"] == "GCC"
    assert payload["page_start"] == 145
    assert payload["is_current"] is True
    assert payload["parent_clause_no"] == "8"
    assert payload["clause_path"] == ["8", "8.4"]


def test_embedding_text_uses_cleaned_text_with_context():
    clause = _clause(text="The Contractor shall be entitled to an extension.")
    text = ClauseEmbeddingService.build_embedding_text(clause)
    assert "Document: GCC" in text
    assert "Clause 8.4 - Extension of Time" in text
    assert "entitled to an extension" in text


def test_unauthorised_clause_not_embeddable():
    # No document_type -> incomplete_metadata -> not authorised for AI.
    clause = _clause(document_type=None)
    assert clause.is_authorised_for_ai is False
    assert ClauseEmbeddingService.is_embeddable(clause) is False


@pytest.mark.asyncio
async def test_index_clauses_embeds_only_authorised():
    class FakeEmbed:
        def __init__(self):
            self.calls = []
        async def embed(self, texts):
            self.calls.append(texts)
            return [[0.1] * 4 for _ in texts]

    class FakeVector:
        def __init__(self):
            self.upserts = []
        async def upsert(self, vectors, chunks, namespace=None):
            self.upserts.append((vectors, chunks, namespace))
            return len(chunks)

    embed, vector = FakeEmbed(), FakeVector()
    svc = ClauseEmbeddingService(db=None, embedding_client=embed, vector_client=vector)
    clauses = [_clause(clause_no="8.4"), _clause(clause_no="9.1", document_type=None)]
    result = await svc.index_clauses(clauses)

    assert result == {"embedded": 1, "skipped": 1}
    # Only the authorised clause was embedded, and via cleaned-text context.
    assert len(embed.calls[0]) == 1
    assert vector.upserts[0][2] == "contract_clauses"
    assert vector.upserts[0][1][0]["chunk_id"] == clauses[0].clause_uid


# --------------------------------------------------------------------------- #
# FalkorDB clause graph (req 18)
# --------------------------------------------------------------------------- #
def test_graph_nodes_and_has_clause_edges():
    clauses = [
        _clause(clause_no="8", clause_title="General"),
        _clause(clause_no="8.4", clause_title="EOT"),
    ]
    model = ClauseGraphService().build_graph(clauses)

    assert "ct-1" in model.node_keys("Contract")
    assert "doc-1" in model.node_keys("ContractDocument")
    assert model.has_edge("HAS_DOCUMENT", "ct-1", "doc-1")

    node_84 = clause_node_id("ct-1", "GCC", "8.4")
    node_8 = clause_node_id("ct-1", "GCC", "8")
    assert model.has_edge("HAS_CLAUSE", "doc-1", node_84)
    # Parent-child sub-clause edge.
    assert model.has_edge("HAS_SUBCLAUSE", node_8, node_84)


def test_graph_dedupes_clause_parts():
    parts = [
        _clause(clause_no="8.4", chunk_type="clause_part", chunk_part=1, chunk_total=2, chunk_index=0),
        _clause(clause_no="8.4", chunk_type="clause_part", chunk_part=2, chunk_total=2, chunk_index=1),
    ]
    model = ClauseGraphService().build_graph(parts)
    clause_nodes = model.node_keys("Clause")
    assert clause_nodes.count(clause_node_id("ct-1", "GCC", "8.4")) == 1


def test_graph_modification_edges():
    scc_clause = _clause(
        contract_id="ct-1", document_id="doc-scc", clause_no="8.4",
        document_type="SCC", text="Sub-Clause 8.4 is deleted and replaced by...",
    )
    sig = ModificationDetector.detect(scc_clause.text)[0]
    link = ModificationDetector.build_modification_link(
        signal=sig, applicable_clause_id=scc_clause.clause_uid,
        modifier_document_type="SCC", base_document_type="GCC",
    )
    model = ClauseGraphService().build_graph([scc_clause], [link])

    base_node = clause_node_id("ct-1", "GCC", "8.4")
    scc_node = clause_node_id("ct-1", "SCC", "8.4")
    assert model.has_edge("MODIFIED_BY", base_node, scc_node)
    # "replace" also supersedes the base clause.
    assert model.has_edge("SUPERSEDED_BY", base_node, scc_node)


@pytest.mark.asyncio
async def test_graph_sync_executes_statements():
    executed = []

    async def executor(cypher, params):
        executed.append((cypher, params))

    clauses = [_clause(clause_no="8", clause_title="General"), _clause(clause_no="8.4")]
    count = await ClauseGraphService().sync(clauses, executor=executor)
    assert count == len(executed) > 0
    assert any("MERGE" in c for c, _ in executed)
