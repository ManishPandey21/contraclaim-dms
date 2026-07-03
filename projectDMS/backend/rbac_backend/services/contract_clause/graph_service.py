"""Build the clause graph in FalkorDB (req 18).

Nodes: Contract, ContractDocument, Clause.
Relationships: Contract-HAS_DOCUMENT->Document, Document-HAS_CLAUSE->Clause,
Clause-HAS_SUBCLAUSE->Clause, Clause-MODIFIED_BY->Clause, Clause-SUPERSEDED_BY->Clause.

The graph *model* is built purely (unit-testable); ``sync`` executes MERGE
statements through an injected executor (the FalkorDB client, or a fake).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ...models.contract_clause import ContractClause

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphNode:
    label: str
    key: str
    props: Tuple[Tuple[str, Any], ...] = ()

    def props_dict(self) -> Dict[str, Any]:
        return dict(self.props)


@dataclass(frozen=True)
class GraphEdge:
    from_key: str
    rel: str
    to_key: str


@dataclass
class GraphModel:
    nodes: List[GraphNode] = field(default_factory=list)
    edges: List[GraphEdge] = field(default_factory=list)

    def has_edge(self, rel: str, from_key: Optional[str] = None, to_key: Optional[str] = None) -> bool:
        return any(
            e.rel == rel
            and (from_key is None or e.from_key == from_key)
            and (to_key is None or e.to_key == to_key)
            for e in self.edges
        )

    def node_keys(self, label: str) -> List[str]:
        return [n.key for n in self.nodes if n.label == label]


def clause_node_id(contract_id: str, document_type: Optional[str], clause_no: str) -> str:
    return f"{contract_id}:{(document_type or 'DOC')}:{clause_no}"


class ClauseGraphService:
    def __init__(self, falkor: Any = None) -> None:
        self.falkor = falkor  # object exposing ._execute(cypher, params)

    def build_graph(
        self,
        clauses: List[ContractClause],
        modification_links: Optional[List[Dict[str, Any]]] = None,
    ) -> GraphModel:
        modification_links = modification_links or []
        model = GraphModel()
        node_index: Dict[Tuple[str, str], GraphNode] = {}
        edge_set: set = set()

        def add_node(node: GraphNode) -> None:
            k = (node.label, node.key)
            if k not in node_index:
                node_index[k] = node
                model.nodes.append(node)

        def add_edge(edge: GraphEdge) -> None:
            k = (edge.from_key, edge.rel, edge.to_key)
            if k not in edge_set:
                edge_set.add(k)
                model.edges.append(edge)

        # Represent each real clause once (records may be split into parts).
        clause_ids_by_no: Dict[str, str] = {}
        uid_to_node: Dict[str, str] = {}
        for clause in clauses:
            if not clause.clause_no or clause.chunk_type == "table":
                continue
            node_id = clause_node_id(clause.contract_id, clause.document_type, clause.clause_no)
            uid_to_node[clause.clause_uid] = node_id
            if node_id in clause_ids_by_no:
                continue
            clause_ids_by_no[node_id] = clause.clause_no

            contract_key = clause.contract_id
            document_key = clause.document_id
            add_node(GraphNode("Contract", contract_key, (
                ("contract_id", clause.contract_id),
                ("organization_id", clause.org_id),
                ("project_id", clause.project_id),
            )))
            add_node(GraphNode("ContractDocument", document_key, (
                ("document_id", clause.document_id),
                ("contract_id", clause.contract_id),
                ("title", clause.document_title),
                ("document_type", clause.document_type),
                ("organization_id", clause.org_id),
                ("project_id", clause.project_id),
            )))
            add_node(GraphNode("Clause", node_id, (
                ("clause_node_id", node_id),
                ("clause_id", clause.clause_uid),
                ("clause_no", clause.clause_no),
                ("title", clause.clause_title),
                ("document_type", clause.document_type),
                ("page_start", clause.page_start),
                ("page_end", clause.page_end),
                ("contract_id", clause.contract_id),
                ("is_current", clause.is_current),
            )))
            add_edge(GraphEdge(contract_key, "HAS_DOCUMENT", document_key))
            add_edge(GraphEdge(document_key, "HAS_CLAUSE", node_id))

        # Parent-child sub-clause edges (within the same document_type).
        for clause in clauses:
            if not clause.clause_no or not clause.parent_clause_no or clause.chunk_type == "table":
                continue
            child_id = clause_node_id(clause.contract_id, clause.document_type, clause.clause_no)
            parent_id = clause_node_id(clause.contract_id, clause.document_type, clause.parent_clause_no)
            if parent_id in clause_ids_by_no and child_id in clause_ids_by_no:
                add_edge(GraphEdge(parent_id, "HAS_SUBCLAUSE", child_id))

        # Modification / supersession edges from detected SCC->GCC links.
        for link in modification_links:
            modifier_uid = link.get("applicable_clause_id")
            modifier_node = uid_to_node.get(modifier_uid)
            if not modifier_node:
                continue
            base_no = link.get("base_clause_no")
            base_type = link.get("base_document_type") or "GCC"
            # Base clause may live in another (GCC) document; contract_id comes
            # from the modifier clause node.
            contract_id = modifier_node.split(":", 1)[0]
            base_node = clause_node_id(contract_id, base_type, base_no)
            add_node(GraphNode("Clause", base_node, (
                ("clause_node_id", base_node),
                ("clause_no", base_no),
                ("document_type", base_type),
                ("contract_id", contract_id),
            )))
            add_edge(GraphEdge(base_node, "MODIFIED_BY", modifier_node))
            if link.get("modification_type") in {"replace", "delete"}:
                add_edge(GraphEdge(base_node, "SUPERSEDED_BY", modifier_node))
        return model

    @staticmethod
    def to_statements(model: GraphModel) -> List[Tuple[str, Dict[str, Any]]]:
        statements: List[Tuple[str, Dict[str, Any]]] = []
        for node in model.nodes:
            statements.append((
                f"MERGE (n:{node.label} {{key: $key}}) SET n += $props",
                {"key": node.key, "props": node.props_dict()},
            ))
        for edge in model.edges:
            statements.append((
                f"MATCH (a {{key: $from}}) MATCH (b {{key: $to}}) MERGE (a)-[:{edge.rel}]->(b)",
                {"from": edge.from_key, "to": edge.to_key},
            ))
        return statements

    async def sync(
        self,
        clauses: List[ContractClause],
        modification_links: Optional[List[Dict[str, Any]]] = None,
        executor: Optional[Callable[..., Any]] = None,
    ) -> int:
        """Build and execute the clause graph. Returns statements executed."""
        model = self.build_graph(clauses, modification_links)
        run = executor or (self.falkor._execute if self.falkor is not None else None)
        if run is None:
            return 0
        statements = self.to_statements(model)
        for cypher, params in statements:
            result = run(cypher, params)
            if hasattr(result, "__await__"):
                await result
        return len(statements)


__all__ = ["ClauseGraphService", "GraphModel", "GraphNode", "GraphEdge", "clause_node_id"]
