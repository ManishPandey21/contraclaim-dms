"""FalkorDB read queries route to GRAPH.RO_QUERY (works on read-only replicas);
writes stay on GRAPH.QUERY. Guards the read/write classifier."""

import pytest

from rbac_backend.services.falkor_graph_service import (
    FalkorGraphConfig,
    FalkorGraphService,
    _is_read_only_cypher,
)


def _svc() -> FalkorGraphService:
    cfg = FalkorGraphConfig(host="x", port=6379, graph_name="g", password=None, enabled=True, cleanup=False)
    return FalkorGraphService(cfg)


class _FakeClient:
    def __init__(self, sink):
        self.sink = sink

    def execute_command(self, command, *args):
        self.sink.append(command)
        return []


def test_is_read_only_cypher_classifies_reads_and_writes():
    assert _is_read_only_cypher("MATCH (n:Letter) RETURN count(n)") is True
    # A property named createdAt must not be mistaken for a CREATE clause.
    assert _is_read_only_cypher("MATCH (n) WHERE n.createdAt > 0 RETURN n") is True
    assert _is_read_only_cypher("CREATE INDEX FOR (n:Letter) ON (n.date)") is False
    assert _is_read_only_cypher("MERGE (n:Letter {id:1}) SET n.x = 2") is False
    assert _is_read_only_cypher("MATCH (n) DETACH DELETE n") is False
    assert _is_read_only_cypher("CALL db.idx.fulltext.createNodeIndex('Letter','body')") is False


def test_execute_routes_reads_to_ro_query(monkeypatch):
    svc = _svc()
    captured: list[str] = []
    monkeypatch.setattr(svc, "_get_client", lambda: _FakeClient(captured))

    svc._execute("MATCH (n:Letter) RETURN count(n)")
    svc._execute("MERGE (n:Letter {id:1}) SET n.title = 'x'")
    svc._execute("CREATE INDEX FOR (n:Letter) ON (n.date)")

    assert captured == ["GRAPH.RO_QUERY", "GRAPH.QUERY", "GRAPH.QUERY"]


def test_execute_explicit_read_only_override(monkeypatch):
    svc = _svc()
    captured: list[str] = []
    monkeypatch.setattr(svc, "_get_client", lambda: _FakeClient(captured))

    svc._execute("MATCH (n) RETURN n", read_only=True)
    svc._execute("MATCH (n) RETURN n", read_only=False)

    assert captured == ["GRAPH.RO_QUERY", "GRAPH.QUERY"]


def test_falkor_param_serialization_preserves_lists_for_cypher_in():
    svc = _svc()

    params = svc._serialize_params(
        {
            "seed_clause_numbers": ["8.4", "20.1"],
            "nested": {"ids": [1, 2]},
        }
    )
    header = svc._prepend_params_header("MATCH (n) RETURN n", params)

    assert params["seed_clause_numbers"] == ["8.4", "20.1"]
    assert "seed_clause_numbers=[\"8.4\",\"20.1\"]" in header
    assert "nested={ids:[1,2]}" in header
