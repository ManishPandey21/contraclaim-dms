"""Graph adapter abstraction for document relationship graph."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Sequence

import requests

try:
    from ..core.config import settings  # type: ignore
except Exception:  # pragma: no cover - defensive during tooling
    settings = None  # type: ignore

logger = logging.getLogger(__name__)


class GraphAdapterError(Exception):
    """Raised when graph operations fail."""


@dataclass(slots=True)
class GraphNode:
    """Represents a node in the knowledge graph."""

    node_id: str
    labels: Sequence[str] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)

    def payload(self) -> Dict[str, Any]:
        return {
            "id": self.node_id,
            "labels": list(self.labels),
            "properties": self.properties or {},
        }


@dataclass(slots=True)
class GraphEdge:
    """Represents a relationship between two nodes."""

    start_node: str
    end_node: str
    relationship: str
    properties: Dict[str, Any] = field(default_factory=dict)

    def payload(self) -> Dict[str, Any]:
        return {
            "start": self.start_node,
            "end": self.end_node,
            "type": self.relationship,
            "properties": self.properties or {},
        }


@dataclass(slots=True)
class GraphQueryResult:
    """Container for graph query responses."""

    nodes: Sequence[Dict[str, Any]]
    edges: Sequence[Dict[str, Any]]


@dataclass(slots=True)
class GraphConfig:
    """Runtime configuration for graph connectivity."""

    base_url: Optional[str] = None
    api_key: Optional[str] = None
    timeout_seconds: float = 10.0
    enabled: bool = False

    @classmethod
    def from_settings(cls) -> "GraphConfig":
        base_url: Optional[str] = None
        api_key: Optional[str] = None
        provider = "direct_falkor"
        graphiti_enabled = False
        if settings is not None:
            provider = str(getattr(settings, "GRAPH_PROVIDER", "direct_falkor") or "direct_falkor").lower()
            graphiti_enabled = bool(getattr(settings, "GRAPHITI_ENABLED", False))
            base_url = (
                getattr(settings, "GRAPHITI_BASE_URL", None)
                or getattr(settings, "GRAPHITI_API_URL", None)
            )
            api_key = getattr(settings, "GRAPHITI_API_KEY", None)
        enabled = provider == "graphiti" and graphiti_enabled and bool(base_url)
        return cls(base_url=base_url, api_key=api_key, enabled=enabled)


class GraphAdapter:
    """Thin abstraction over the underlying graph provider.

    The adapter hides Graphiti/Neo4j specifics and offers a minimal surface area
    for document services to describe graph operations.
    """

    def __init__(
        self,
        config: Optional[GraphConfig] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.config = config or GraphConfig.from_settings()
        self._session = session or requests.Session()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def upsert_node(self, node: GraphNode) -> Dict[str, Any]:
        """Create or update a node.

        Returns the persisted representation when available. If the graph
        layer is disabled the input payload is echoed back and a debug message
        is recorded so callers can continue without special casing.
        """
        if not self.config.enabled:
            logger.debug("GraphAdapter disabled; skipping node upsert for %s", node.node_id)
            return node.payload()

        endpoint = f"{self.config.base_url.rstrip('/')}/graph/nodes"
        try:
            response = self._session.post(
                endpoint,
                json=node.payload(),
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:  # pragma: no cover - network dependent
            logger.error("Graph node upsert failed for %s: %s", node.node_id, exc)
            raise GraphAdapterError(f"Failed to upsert node {node.node_id}") from exc

    def upsert_edge(self, edge: GraphEdge) -> Dict[str, Any]:
        """Create or update a relationship between two nodes."""
        if not self.config.enabled:
            logger.debug(
                "GraphAdapter disabled; skipping edge upsert %s -> %s", edge.start_node, edge.end_node
            )
            return edge.payload()

        endpoint = f"{self.config.base_url.rstrip('/')}/graph/edges"
        try:
            response = self._session.post(
                endpoint,
                json=edge.payload(),
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:  # pragma: no cover
            logger.error(
                "Graph edge upsert failed %s -[%s]-> %s: %s",
                edge.start_node,
                edge.relationship,
                edge.end_node,
                exc,
            )
            raise GraphAdapterError(
                f"Failed to upsert edge {edge.start_node}-[{edge.relationship}]->{edge.end_node}"
            ) from exc

    def neighbors(
        self,
        node_id: str,
        edge_types: Optional[Iterable[str]] = None,
        depth: int = 1,
    ) -> GraphQueryResult:
        """Traverse outward from a node returning neighbors within the depth."""
        if not self.config.enabled:
            logger.debug("GraphAdapter disabled; neighbors requested for %s", node_id)
            return GraphQueryResult(nodes=[], edges=[])

        params: Dict[str, Any] = {"node_id": node_id, "depth": max(depth, 1)}
        if edge_types:
            params["edge_types"] = list(edge_types)

        endpoint = f"{self.config.base_url.rstrip('/')}/graph/neighbors"
        try:
            response = self._session.get(
                endpoint,
                params=params,
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            return GraphQueryResult(
                nodes=payload.get("nodes", []),
                edges=payload.get("edges", []),
            )
        except requests.RequestException as exc:  # pragma: no cover
            logger.error("Graph neighbors query failed for %s: %s", node_id, exc)
            raise GraphAdapterError(f"Failed to fetch neighbors for {node_id}") from exc

    def query(
        self,
        pattern: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> GraphQueryResult:
        """Execute a pattern or DSL query with optional filters."""
        if not self.config.enabled:
            logger.debug(
                "GraphAdapter disabled; query skipped pattern=%s filters=%s", pattern, filters
            )
            return GraphQueryResult(nodes=[], edges=[])

        payload: Dict[str, Any] = {}
        if pattern:
            payload["pattern"] = pattern
        if filters:
            payload["filters"] = filters

        endpoint = f"{self.config.base_url.rstrip('/')}/graph/query"
        try:
            response = self._session.post(
                endpoint,
                json=payload,
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            return GraphQueryResult(
                nodes=data.get("nodes", []),
                edges=data.get("edges", []),
            )
        except requests.RequestException as exc:  # pragma: no cover
            logger.error("Graph query failed pattern=%s filters=%s: %s", pattern, filters, exc)
            raise GraphAdapterError("Graph query failed") from exc

    def query_letters_by_clause(
        self,
        clause_identifier: str,
        project_id: Optional[str] = None,
        include_eot_requests: bool = True,
    ) -> GraphQueryResult:
        """Retrieve letters that mention a clause, optionally filtered for EOT requests."""
        filters: Dict[str, Any] = {"clause": clause_identifier}
        if project_id:
            filters["project_id"] = project_id
        if include_eot_requests:
            filters.setdefault("topics", []).append("EOT")
        pattern = "letters_by_clause"
        return self.query(pattern=pattern, filters=filters)


    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers


__all__ = [
    "GraphAdapter",
    "GraphAdapterError",
    "GraphConfig",
    "GraphEdge",
    "GraphNode",
    "GraphQueryResult",
]
