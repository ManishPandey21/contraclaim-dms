from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from redis import Redis
from redis.exceptions import RedisError

from ..core.config import settings

logger = logging.getLogger(__name__)


class FalkorGraphError(Exception):
    """Raised when FalkorDB operations fail."""


_NORMALIZE_PATTERN = re.compile(r"[^0-9a-zA-Z]+")

# A Cypher query is treated as a write unless it is provably read-only. Reads are
# routed to GRAPH.RO_QUERY (FalkorDB best practice: skips the write path and works
# on read-only replicas). The detector is deliberately conservative — only pure
# reads use RO_QUERY; anything with a write clause (incl. `CREATE INDEX` and the
# index procedures) stays on GRAPH.QUERY, because misclassifying a write as a read
# would make FalkorDB reject it.
_WRITE_CLAUSE_RE = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|REMOVE|DROP|FOREACH)\b"
    r"|createNodeIndex|createIndex|dropNodeIndex|dropIndex",
    re.IGNORECASE,
)


def _is_read_only_cypher(cypher: str) -> bool:
    """True when the Cypher contains no write clause (safe for GRAPH.RO_QUERY)."""
    return not _WRITE_CLAUSE_RE.search(cypher or "")


def normalize_letter_code(code: str) -> str:
    """Normalize a letter code so it can be used as the Falkor `normCode`."""
    if not code:
        return ""
    slug = _NORMALIZE_PATTERN.sub("-", code.strip().lower()).strip("-")
    return slug or "unknown"


@dataclass(slots=True)
class FalkorGraphConfig:
    host: str
    port: int
    graph_name: str
    password: Optional[str]
    enabled: bool
    cleanup: bool

    @classmethod
    def from_settings(cls) -> "FalkorGraphConfig":
        return cls(
            host=settings.FALKORDB_HOST,
            port=settings.FALKORDB_PORT,
            graph_name=settings.FALKORDB_GRAPH_NAME,
            password=settings.FALKORDB_PASSWORD,
            enabled=bool(settings.FALKORDB_ENABLED),
            cleanup=bool(settings.FALKORDB_CLEANUP_REFERENCES),
        )


class FalkorGraphService:
    """High-level helper for FalkorDB graph operations."""

    def __init__(self, config: Optional[FalkorGraphConfig] = None) -> None:
        self.config = config or FalkorGraphConfig.from_settings()
        self._client: Optional[Redis] = None
        self._schema_ensured: bool = False

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def cleanup_enabled(self) -> bool:
        return self.config.cleanup

    def ensure_schema(self) -> None:
        """Ensure required schema constraints and indexes exist."""
        if not self.enabled or self._schema_ensured:
            return

        try:
            # Try to create indexes, but ignore if they already exist
            index_queries = [
                "CREATE INDEX FOR (n:Letter) ON (n.date)",
                "CREATE INDEX FOR (n:Letter) ON (n.direction)",
                "CREATE INDEX FOR (n:Letter) ON (n.organization_id)",
                "CREATE INDEX FOR (n:Letter) ON (n.project_id)",
            ]

            for cypher in index_queries:
                try:
                    self._execute(cypher, suppress_error_log=True)
                    logger.debug("Created index: %s", cypher)
                except FalkorGraphError as e:
                    if "already exists" in str(e).lower() or "already indexed" in str(e).lower():
                        logger.debug("Index already exists: %s", cypher)
                    else:
                        logger.warning("Failed to create index %s: %s", cypher, str(e))

            self._schema_ensured = True
            logger.info("FalkorDB schema setup completed for graph '%s'", self.config.graph_name)

        except Exception as e:
            logger.warning("Schema setup completed with some issues: %s. Service will continue.", str(e))
            self._schema_ensured = True  # Mark as ensured to prevent repeated attempts

    def upsert_letter_with_refs(
        self,
        letter: Dict[str, Any],
        references: Sequence[Dict[str, Any]],
        *,
        cleanup: Optional[bool] = None,
    ) -> None:
        """Create/Update a letter node and its reference edges."""
        if not self.enabled:
            logger.debug("FalkorDB disabled; skipping letter upsert for %s", letter.get("code"))
            return

        norm_code = normalize_letter_code(letter.get("normCode") or letter.get("code", ""))
        if not norm_code:
            logger.debug("Skipping Falkor upsert without valid normCode: %s", letter)
            return

        # Prepare letter payload with proper null handling
        current_time = datetime.now().isoformat()
        formatted_date = self._format_date(letter.get("date"))
        
        # Build payload with explicit None handling for FalkorDB
        payload = {
            "normCode": norm_code,
            "code": letter.get("code") or norm_code,
            "direction": letter.get("direction") or "incoming",
            "subject": letter.get("subject") or "",
            "date": formatted_date if formatted_date else None,  # Explicit None for FalkorDB
            # Prefer explicit org/project ids; fall back to legacy project/code fields
            "organization_id": letter.get("organization_id"),
            "project_id": letter.get("project_id"),
            "project": letter.get("project") or letter.get("project_id") or "KNPCC-11",
            "createdAt": current_time,
            "lastUpdated": current_time
        }

        ref_list: List[Dict[str, Any]] = []
        for ref in references or []:
            code = ref.get("code") or ref.get("letterNo") or ref.get("normCode")
            norm = normalize_letter_code(ref.get("normCode") or code or "")
            if not norm or norm == norm_code:
                continue

            ref_entry = {
                "code": code or norm,
                "normCode": norm,
                "type": ref.get("type") or "CITES",
                "source": ref.get("source") or "parser",
            }
            ref_list.append(ref_entry)

        try:
            # Try to ensure schema, but continue even if it fails
            try:
                self.ensure_schema()
            except Exception as e:
                logger.debug("Schema creation had issues but continuing: %s", str(e))

            # Use a simpler upsert query with explicit NULL handling
            upsert_query = """
            MERGE (src:Letter {normCode: $normCode})
            ON CREATE SET 
                src.code = $code,
                src.direction = $direction,
                src.subject = $subject,
                src.date = $date,
                src.organization_id = $organization_id,
                src.project_id = $project_id,
                src.project = $project,
                src.createdAt = $createdAt,
                src.lastUpdated = $lastUpdated
            ON MATCH SET 
                src.code = $code,
                src.direction = $direction, 
                src.subject = $subject,
                src.date = COALESCE($date, src.date),
                src.organization_id = COALESCE($organization_id, src.organization_id),
                src.project_id = COALESCE($project_id, src.project_id),
                src.project = $project,
                src.lastUpdated = $lastUpdated
            """

            # Execute the main upsert
            self._execute(upsert_query, payload)

            # Process references if any exist
            if ref_list:
                for ref in ref_list:
                    try:
                        # Create or update the target letter
                        target_payload = {
                            "normCode": ref["normCode"],
                            "code": ref["code"],
                            "createdAt": current_time,
                            "lastUpdated": current_time
                        }
                        
                        target_query = """
                        MERGE (dst:Letter {normCode: $normCode})
                        ON CREATE SET 
                            dst.code = $code,
                            dst.direction = 'unknown',
                            dst.createdAt = $createdAt,
                            dst.lastUpdated = $lastUpdated
                        ON MATCH SET 
                            dst.code = $code,
                            dst.lastUpdated = $lastUpdated
                        """
                        self._execute(target_query, target_payload)

                        # Create the relationship with current timestamp
                        rel_payload = {
                            "srcNorm": norm_code,
                            "dstNorm": ref["normCode"],
                            "source": ref["source"],
                            "createdAt": current_time,
                            "updatedAt": current_time
                        }
                        
                        if ref["type"] == "REPLIES_TO":
                            rel_query = """
                            MATCH (src:Letter {normCode: $srcNorm}), (dst:Letter {normCode: $dstNorm})
                            MERGE (src)-[e:REPLIES_TO]->(dst)
                            ON CREATE SET e.source = $source, e.createdAt = $createdAt
                            SET e.updatedAt = $updatedAt
                            """
                        else:
                            rel_query = """
                            MATCH (src:Letter {normCode: $srcNorm}), (dst:Letter {normCode: $dstNorm})
                            MERGE (src)-[e:CITES]->(dst)
                            ON CREATE SET e.source = $source, e.createdAt = $createdAt
                            SET e.updatedAt = $updatedAt
                            """
                        
                        self._execute(rel_query, rel_payload)
                    except FalkorGraphError as e:
                        logger.warning("Failed to process reference %s: %s", ref["normCode"], str(e))
                        continue

            # Reconcile only stale Mongo-owned relationships. Keeping desired
            # edges in place makes repeated syncs idempotent and preserves their
            # original creation metadata instead of deleting/recreating them.
            if (cleanup if cleanup is not None else self.cleanup_enabled):
                desired_cites = [
                    ref["normCode"] for ref in ref_list if ref["type"] != "REPLIES_TO"
                ]
                desired_replies = [
                    ref["normCode"] for ref in ref_list if ref["type"] == "REPLIES_TO"
                ]
                for relationship, desired in (
                    ("CITES", desired_cites),
                    ("REPLIES_TO", desired_replies),
                ):
                    try:
                        self._execute(
                            (
                                f"MATCH (src:Letter {{normCode: $normCode}})-[e:{relationship}]->"
                                "(dst:Letter) "
                                "WHERE (e.source IS NULL OR e.source IN ['parser', 'manual']) "
                                "AND NOT (dst.normCode IN $desiredNormCodes) DELETE e"
                            ),
                            {
                                "normCode": norm_code,
                                "desiredNormCodes": desired,
                            },
                        )
                    except FalkorGraphError as e:
                        logger.warning(
                            "Cleanup of stale %s relationships failed: %s",
                            relationship,
                            str(e),
                        )

            logger.info(
                "FalkorDB upsert successful for normCode=%s (references=%s)",
                norm_code,
                len(ref_list),
            )

        except FalkorGraphError:
            logger.exception("Failed to upsert letter %s into FalkorDB", norm_code)
            raise

    def get_letter(self, norm_code: str) -> Optional[Dict[str, Any]]:
        """Return a single letter node by its normalized code."""
        entries = self.get_thread(norm_code, depth=0)
        if not entries:
            return None
        return entries[0]

    def get_thread(self, norm_code: str, depth: int = 4) -> List[Dict[str, Any]]:
        """
        Return all letters within `depth` hops of the given letter, ordered by date.
        
        FIXED: Handle depth=0 case separately to avoid invalid path expressions.
        FalkorDB requires: minimum_hops <= maximum_hops in path expressions.
        When depth=0, we only want the root node with no neighbors.
        """
        if not self.enabled:
            return []

        norm = normalize_letter_code(norm_code)
        if not norm:
            return []

        depth = max(depth, 0)
        
        # FIXED: Special case for depth=0 - only return the root node
        if depth == 0:
            result = self._execute(
                "MATCH (letter:Letter {normCode:$norm}) RETURN DISTINCT letter.normCode AS normCode, letter.code AS code, letter.direction AS direction, letter.subject AS subject, toString(letter.date) AS date, letter.project AS project, toString(letter.createdAt) AS createdAt",
                {"norm": norm},
            )
        else:
            # For depth > 0, include root and neighbors
            result = self._execute(
                f"""
                MATCH (root:Letter {{normCode:$norm}})
                OPTIONAL MATCH (root)-[:CITES|REPLIES_TO*1..{depth}]-(neighbor:Letter)
                WITH root, collect(DISTINCT neighbor) AS neighbors
                UNWIND ([root] + neighbors) AS letter
                RETURN DISTINCT
                    letter.normCode AS normCode,
                    letter.code AS code,
                    letter.direction AS direction,
                    letter.subject AS subject,
                    toString(letter.date) AS date,
                    letter.project AS project,
                    toString(letter.createdAt) AS createdAt
                ORDER BY date ASC
                """,
                {"norm": norm},
            )

        return self._parse_rows(result)

    def get_neighbors(self, norm_code: str, direction: str) -> List[Dict[str, Any]]:
        """Fetch incoming or outgoing neighbours for a letter."""
        if not self.enabled:
            return []

        norm = normalize_letter_code(norm_code)
        if not norm:
            return []

        if direction == "outgoing":
            query = "MATCH (:Letter {normCode:$norm})-[:CITES|REPLIES_TO]->(dst:Letter) RETURN DISTINCT dst.normCode AS normCode, dst.code AS code, dst.direction AS direction, toString(dst.date) AS date ORDER BY date ASC"
        else:
            query = "MATCH (src:Letter)-[:CITES|REPLIES_TO]->(:Letter {normCode:$norm}) RETURN DISTINCT src.normCode AS normCode, src.code AS code, src.direction AS direction, toString(src.date) AS date ORDER BY date ASC"

        return self._parse_rows(self._execute(query, {"norm": norm}))

    def debug_get_letter_raw(self, norm_code: str) -> Optional[Dict[str, Any]]:
        """
        DEBUG: Fetch raw letter node without any filtering or ordering.
        Returns all properties exactly as stored in FalkorDB.
        Useful for diagnosing why queries return empty results.
        """
        if not self.enabled:
            return None

        norm = normalize_letter_code(norm_code)
        if not norm:
            return None

        result = self._execute(
            "MATCH (letter:Letter {normCode:$norm}) RETURN letter",
            {"norm": norm},
        )

        rows = self._parse_rows(result)
        if not rows:
            return None
        
        row = rows[0]
        if "letter" in row:
            return row["letter"]
        return row

    def debug_count_letters(self) -> int:
        """DEBUG: Count total Letter nodes in the graph."""
        if not self.enabled:
            return 0

        result = self._execute("MATCH (n:Letter) RETURN count(n) AS count")
        rows = self._parse_rows(result)
        if rows:
            return int(rows[0].get("count", 0))
        return 0

    def debug_list_all_letters(self, limit: int = 10) -> List[Dict[str, Any]]:
        """DEBUG: List all Letter nodes (limited to avoid huge results)."""
        if not self.enabled:
            return []

        result = self._execute(
            f"MATCH (letter:Letter) RETURN letter.normCode AS normCode, letter.code AS code, letter.subject AS subject LIMIT {limit}"
        )

        return self._parse_rows(result)

    def _get_client(self) -> Redis:
        """Get or create Redis client connection."""
        if self._client is None:
            if not self.enabled:
                raise FalkorGraphError("FalkorDB client requested while service disabled")

            try:
                self._client = Redis(
                    host=self.config.host,
                    port=self.config.port,
                    password=self.config.password,
                    decode_responses=True,
                    socket_timeout=5,
                )
                self._client.ping()
            except RedisError as exc:
                raise FalkorGraphError(f"Cannot connect to FalkorDB: {exc}") from exc

        return self._client

    def _execute(
        self,
        cypher: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        suppress_error_log: bool = False,
        read_only: Optional[bool] = None,
    ) -> Any:
        """
        Execute a Cypher query against FalkorDB.
        
        CRITICAL CONSTRAINTS:
        - Only ONE Cypher statement per call (no multiple statements with ;)
        - FalkorDB does not support parameterized variable-length paths
        - For variable-length paths: minimum_hops must be <= maximum_hops
        - When querying with depth=0, only match the root node (no path traversal)
        """
        client = self._get_client()

        cypher_stripped = cypher.strip()

        # Count semicolons to detect multiple statements
        semicolon_count = cypher_stripped.count(";")
        if semicolon_count > 1:
            raise FalkorGraphError(
                "Only one Cypher statement allowed per query. "
                f"Found {semicolon_count} semicolons. Query: {cypher_stripped[:100]}..."
            )

        serialized_params: Optional[Dict[str, Any]] = None
        query_to_execute = cypher_stripped

        try:
            if params:
                serialized_params = self._serialize_params(params)
                logger.debug(
                    "Executing FalkorDB query: %s with params: %s",
                    cypher_stripped[:200],
                    serialized_params,
                )
                query_to_execute = self._prepend_params_header(
                    cypher_stripped,
                    serialized_params,
                )
            else:
                logger.debug("Executing FalkorDB query: %s", cypher_stripped[:200])

            # Reads use GRAPH.RO_QUERY (works on read-only replicas, skips the
            # write path); writes use GRAPH.QUERY. Detection is based on the
            # original Cypher (the params header never adds write clauses).
            use_read_only = read_only if read_only is not None else _is_read_only_cypher(cypher_stripped)
            command = "GRAPH.RO_QUERY" if use_read_only else "GRAPH.QUERY"
            response = client.execute_command(
                command,
                self.config.graph_name,
                query_to_execute,
                "--compact",
            )
            return response
        except RedisError as exc:
            # Enhanced error logging for debugging
            log_params = serialized_params if serialized_params is not None else params
            if suppress_error_log:
                logger.debug(
                    "FalkorDB query expected failure suppressed. Query: %s, Params: %s, Error: %s",
                    cypher_stripped[:500],
                    log_params,
                    str(exc),
                )
            else:
                logger.error(
                    "FalkorDB query failed. Query: %s, Params: %s, Error: %s",
                    cypher_stripped[:500],
                    log_params,
                    str(exc),
                )
            raise FalkorGraphError(f"FalkorDB query failed: {exc}") from exc

    def _serialize_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure all parameters are properly serialized for FalkorDB."""
        serialized = {}
        for key, value in params.items():
            if value is None:
                serialized[key] = None
            elif isinstance(value, (datetime, date)):
                serialized[key] = value.isoformat()
            elif isinstance(value, (str, int, float, bool)):
                serialized[key] = value
            elif isinstance(value, (list, tuple, set)):
                serialized[key] = [self._serialize_param_value(item) for item in value]
            elif isinstance(value, dict):
                serialized[key] = {
                    str(item_key): self._serialize_param_value(item_value)
                    for item_key, item_value in value.items()
                }
            else:
                serialized[key] = str(value)
        return serialized

    def _serialize_param_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple, set)):
            return [self._serialize_param_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(item_key): self._serialize_param_value(item_value)
                for item_key, item_value in value.items()
            }
        return str(value)

    def _prepend_params_header(self, cypher: str, params: Dict[str, Any]) -> str:
        """Prefix Cypher text with a CYPHER params header."""
        if not params:
            return cypher

        assignments = []
        for key, value in params.items():
            assignments.append(f"{key}={self._stringify_param_value(value)}")

        header = "CYPHER " + " ".join(assignments) + " "
        return header + cypher

    def _stringify_param_value(self, value: Any) -> str:
        """Stringify parameter values according to Cypher syntax."""
        if isinstance(value, bytes):
            value = value.decode()

        if isinstance(value, str):
            return self._quote_cypher_string(value)
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (list, tuple, set)):
            return "[" + ",".join(self._stringify_param_value(v) for v in value) + "]"
        if isinstance(value, dict):
            inner = ",".join(
                f"{k}:{self._stringify_param_value(v)}" for k, v in value.items()
            )
            return "{" + inner + "}"
        return str(value)

    @staticmethod
    def _quote_cypher_string(value: str) -> str:
        """Escape and quote string values for Cypher."""
        if value == "":
            return '""'
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _parse_rows(self, response: Any) -> List[Dict[str, Any]]:
        """Parse FalkorDB response into list of dictionaries."""
        if not response or len(response) < 2:
            return []
        header, rows = response[0], response[1]
        if not header or not rows:
            return []

        # FalkorDB returns typed values when called with --compact:
        # header: [[type_code, "column_name"], ...]
        # rows:   [[[type_code, value], ...], ...]
        if header and isinstance(header[0], (list, tuple)) and len(header[0]) == 2:
            column_names = [h[1] if isinstance(h, (list, tuple)) and len(h) == 2 else h for h in header]
            parsed_rows: List[Dict[str, Any]] = []
            for row in rows:
                parsed_row: Dict[str, Any] = {}
                for col, cell in zip(column_names, row):
                    if isinstance(cell, (list, tuple)) and len(cell) >= 2:
                        parsed_row[col] = cell[1]
                    else:
                        parsed_row[col] = cell
                parsed_rows.append(parsed_row)
            return parsed_rows

        return [dict(zip(header, row)) for row in rows]

    @staticmethod
    def _format_date(value: Any) -> Optional[str]:
        """Format date value to ISO string (YYYY-MM-DD)."""
        if value is None:
            return None

        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()

        text = str(value).strip()
        if not text:
            return None

        try:
            parsed = datetime.fromisoformat(text)
            return parsed.date().isoformat()
        except ValueError:
            pass

        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue

        return text


__all__ = [
    "FalkorGraphService",
    "FalkorGraphError",
    "normalize_letter_code",
]
