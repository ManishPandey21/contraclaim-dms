from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from .falkor_graph_service import FalkorGraphService, FalkorGraphError

logger = logging.getLogger(__name__)


@dataclass
class ClauseGraphPayload:
    """Normalized clause payload for graph upsert."""

    clause_id: str
    clause_number: str
    title: Optional[str]
    text_content: Optional[str]
    page_number: Optional[int]
    section_type: Optional[str]
    priority: int
    is_active: bool


@dataclass
class DocumentGraphPayload:
    doc_id: str
    title: str
    version: Optional[str]
    organization_id: Optional[str]
    project_id: Optional[str]
    section_type: Optional[str]
    priority: int


class ContractGraphService:
    """
    Minimal FalkorDB writer for contract documents.

    Nodes:
      - Document {doc_id, title, version}
      - Section {doc_id, type, priority}
      - Clause {doc_id, clause_id, clause_number, title, text_content, page_number, section_type, priority, is_active}
    Edges:
      - (Document)-[:HAS_SECTION]->(Section)
      - (Section)-[:HAS_CLAUSE]->(Clause)
    """

    def __init__(self, falkor: Optional[FalkorGraphService] = None) -> None:
        self.falkor = falkor or FalkorGraphService()

    @property
    def enabled(self) -> bool:
        return bool(self.falkor.enabled)

    def ensure_schema(self) -> None:
        """Create indexes for contract nodes (idempotent)."""
        if not self.enabled:
            return
        try:
            for cypher in [
                "CREATE INDEX IF NOT EXISTS FOR (d:Document) ON (d.doc_id)",
                "CREATE INDEX IF NOT EXISTS FOR (s:Section) ON (s.section_id)",
                "CREATE INDEX IF NOT EXISTS FOR (c:Clause) ON (c.clause_node_id)",
            ]:
                try:
                    self.falkor._execute(cypher, suppress_error_log=True)
                except FalkorGraphError as exc:
                    logger.debug("Index creation skipped/failed (%s): %s", cypher, exc)
        except Exception as exc:
            logger.warning("Contract graph schema setup encountered issues: %s", exc)

    def upsert_contract_graph(
        self,
        document: DocumentGraphPayload,
        clauses: List[ClauseGraphPayload],
    ) -> None:
        """Upsert document/section/clauses into FalkorDB."""
        if not self.enabled:
            return

        self.ensure_schema()

        section_type = (document.section_type or "contract").upper()
        section_id = f"{document.doc_id}:{section_type}"

        try:
            # Upsert Document node
            doc_params = {
                "doc_id": document.doc_id,
                "title": document.title,
                "version": document.version,
                "organization_id": document.organization_id,
                "project_id": document.project_id,
            }
            self.falkor._execute(
                """
                MERGE (d:Document {doc_id: $doc_id, organization_id: $organization_id, project_id: $project_id})
                ON CREATE SET d.title = $title, d.version = $version
                ON MATCH SET d.title = COALESCE($title, d.title), d.version = COALESCE($version, d.version)
                """,
                doc_params,
            )

            # Upsert Section node and edge
            section_params = {
                "section_id": section_id,
                "doc_id": document.doc_id,
                "type": section_type,
                "priority": document.priority,
                "organization_id": document.organization_id,
                "project_id": document.project_id,
            }
            self.falkor._execute(
                """
                MERGE (s:Section {section_id: $section_id, doc_id: $doc_id, organization_id: $organization_id, project_id: $project_id})
                ON CREATE SET s.type = $type, s.priority = $priority
                ON MATCH SET s.type = COALESCE($type, s.type), s.priority = COALESCE($priority, s.priority)
                """,
                section_params,
            )
            self.falkor._execute(
                """
                MATCH (d:Document {doc_id: $doc_id, organization_id: $organization_id, project_id: $project_id})
                MATCH (s:Section {section_id: $section_id})
                MERGE (d)-[:HAS_SECTION]->(s)
                """,
                section_params,
            )

            # Upsert clauses
            for clause in clauses:
                clause_node_id = f"{document.doc_id}:{section_type}:{clause.clause_id}"
                clause_params = {
                    "clause_node_id": clause_node_id,
                    "doc_id": document.doc_id,
                    "clause_id": clause.clause_id,
                    "clause_number": clause.clause_number,
                    "title": clause.title,
                    "text_content": clause.text_content,
                    "page_number": clause.page_number,
                    "section_type": clause.section_type or section_type,
                    "priority": clause.priority,
                    "is_active": bool(clause.is_active),
                    "organization_id": document.organization_id,
                    "project_id": document.project_id,
                    "section_id": section_id,
                }
                self.falkor._execute(
                    """
                    MERGE (c:Clause {clause_node_id: $clause_node_id, doc_id: $doc_id, organization_id: $organization_id, project_id: $project_id})
                    ON CREATE SET 
                        c.clause_id = $clause_id,
                        c.clause_number = $clause_number,
                        c.title = $title,
                        c.text_content = $text_content,
                        c.page_number = $page_number,
                        c.section_type = $section_type,
                        c.priority = $priority,
                        c.is_active = $is_active
                    ON MATCH SET
                        c.clause_id = COALESCE($clause_id, c.clause_id),
                        c.clause_number = COALESCE($clause_number, c.clause_number),
                        c.title = COALESCE($title, c.title),
                        c.text_content = COALESCE($text_content, c.text_content),
                        c.page_number = COALESCE($page_number, c.page_number),
                        c.section_type = COALESCE($section_type, c.section_type),
                        c.priority = COALESCE($priority, c.priority),
                        c.is_active = COALESCE($is_active, c.is_active)
                    """,
                    clause_params,
                )
                self.falkor._execute(
                    """
                    MATCH (s:Section {section_id: $section_id})
                    MATCH (c:Clause {clause_node_id: $clause_node_id})
                    MERGE (s)-[:HAS_CLAUSE]->(c)
                    """,
                    clause_params,
                )

        except FalkorGraphError as exc:
            logger.warning("Falkor graph upsert failed for doc %s: %s", document.doc_id, exc)
        except Exception:
            logger.exception("Unexpected error during Falkor contract graph ingestion for %s", document.doc_id)

