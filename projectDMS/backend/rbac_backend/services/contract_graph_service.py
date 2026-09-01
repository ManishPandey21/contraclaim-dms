from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .contract_graph_containment import stable_clause_node_id
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


class ContractGraphIdentityError(ValueError):
    """Raised when a document lacks the identity the project-scoped graph requires.

    Distinct from FalkorGraphError: this is a caller/domain problem (the
    document cannot be represented), not a transport or engine failure, and it
    must not be swallowed as a routine graph-unavailable warning.
    """


class ContractGraphService:
    """
    Minimal FalkorDB writer for contract documents.

    Nodes:
      - Document {doc_id, title, version}
      - Section {doc_id}
      - Clause {doc_id, clause_id, clause_number, title, text_content, page_number, is_active}
      Filename-derived `section_type`/`priority` are no longer written: they
      were never reprojected on a classification correction, and nothing in
      contract retrieval orders, filters or scores by them (DEBT-17).
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

        # Identity must be complete BEFORE any statement is emitted. FalkorDB
        # rejects `MERGE` with a null property value ("Cannot merge node using
        # null property value"), so a document with no project_id previously
        # died on the first MERGE and was swallowed as a generic warning by two
        # nested handlers - ingestion reported success for a document that is
        # entirely absent from the graph.
        #
        # Deliberately NOT solved with a sentinel project_id: a synthesized
        # value would become a shared identity key that later reads and cleanup
        # cannot distinguish from a real project, which is the ownership-
        # collision class this graph work exists to remove. An org-scoped
        # contract simply has no place in a project-scoped graph, and saying so
        # loudly beats writing it somewhere wrong.
        missing = [
            name
            for name, value in (
                ("doc_id", document.doc_id),
                ("organization_id", document.organization_id),
                ("project_id", document.project_id),
            )
            if not value
        ]
        if missing:
            raise ContractGraphIdentityError(
                f"Cannot ingest doc {document.doc_id!r} into the contract graph: "
                f"missing required identity {missing}. The graph is project-scoped; "
                "org-scoped documents are not representable in it."
            )

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
                "organization_id": document.organization_id,
                "project_id": document.project_id,
            }
            self.falkor._execute(
                """
                MERGE (s:Section {section_id: $section_id, doc_id: $doc_id, organization_id: $organization_id, project_id: $project_id})
                """,
                section_params,
            )
            self.falkor._execute(
                """
                MATCH (d:Document {doc_id: $doc_id, organization_id: $organization_id, project_id: $project_id})
                MATCH (s:Section {section_id: $section_id, organization_id: $organization_id, project_id: $project_id})
                MERGE (d)-[:HAS_SECTION]->(s)
                """,
                section_params,
            )

            # Upsert clauses
            for clause in clauses:
                # Identity excludes the classification (DEBT-03). Including it
                # stranded a node generation per correction: a reclassified
                # clause MERGEd as a new node beside the old one, and both
                # then answered queries.
                clause_node_id = stable_clause_node_id(
                    doc_id=document.doc_id, clause_id=clause.clause_id
                )
                clause_params = {
                    "clause_node_id": clause_node_id,
                    "doc_id": document.doc_id,
                    "clause_id": clause.clause_id,
                    "clause_number": clause.clause_number,
                    "title": clause.title,
                    "text_content": clause.text_content,
                    "page_number": clause.page_number,
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
                        c.is_active = $is_active
                    ON MATCH SET
                        c.clause_id = COALESCE($clause_id, c.clause_id),
                        c.clause_number = COALESCE($clause_number, c.clause_number),
                        c.title = COALESCE($title, c.title),
                        c.text_content = COALESCE($text_content, c.text_content),
                        c.page_number = COALESCE($page_number, c.page_number),
                        c.is_active = COALESCE($is_active, c.is_active)
                    """,
                    clause_params,
                )
                self.falkor._execute(
                    """
                    MATCH (s:Section {section_id: $section_id, organization_id: $organization_id, project_id: $project_id})
                    MATCH (c:Clause {clause_node_id: $clause_node_id, organization_id: $organization_id, project_id: $project_id})
                    MERGE (s)-[:HAS_CLAUSE]->(c)
                    """,
                    clause_params,
                )

        except FalkorGraphError as exc:
            logger.warning("Falkor graph upsert failed for doc %s: %s", document.doc_id, exc)
        except Exception:
            logger.exception("Unexpected error during Falkor contract graph ingestion for %s", document.doc_id)

    def candidate_document_ids(
        self,
        *,
        organization_id: Optional[str],
        project_id: Optional[str],
        seed_clause_numbers: List[str],
        seed_document_ids: Optional[List[str]] = None,
        raise_on_error: bool = False,
        document_limit: int = 500,
    ) -> List[str]:
        """The DOCUMENTS `find_related_clauses` could draw a related clause from.

        Exists so a caller can resolve canonical eligibility BEFORE the clause
        window is chosen, rather than filtering the window afterwards. Nothing
        deletes, deactivates or reprojects a `(:Clause)` node, so the residue of
        a hard-deleted or blocked contract keeps matching this traversal
        forever; with the fence applied afterwards, twelve dead clauses fill a
        three-row window and the live contract's clause never arrives (G29:
        suppression is material influence, not only disclosure).

        Bounded by DISTINCT DOCUMENT rather than by clause, which is the unit
        that actually needs a cap: a project has tens of contract documents and
        can have thousands of clauses. The predicates are deliberately the same
        ones `find_related_clauses` uses, minus the `LIMIT`, so the two cannot
        answer about different populations.
        """
        if not self.enabled or not seed_clause_numbers:
            return []
        seeds = [str(item).strip() for item in seed_clause_numbers if str(item).strip()]
        seed_docs = [str(item).strip() for item in (seed_document_ids or []) if str(item).strip()]
        if not seeds:
            return []
        params = {
            "organization_id": organization_id,
            "project_id": project_id,
            "seed_clause_numbers": seeds[:20],
            "seed_document_ids": seed_docs[:20],
            "has_seed_document_ids": bool(seed_docs),
            "document_limit": max(1, min(int(document_limit or 500), 2000)),
        }
        statements = (
            """
            MATCH (seed:Clause {organization_id: $organization_id, project_id: $project_id})
            WHERE seed.clause_number IN $seed_clause_numbers
              AND ($has_seed_document_ids = false OR seed.doc_id IN $seed_document_ids)
            MATCH (section:Section)-[:HAS_CLAUSE]->(seed)
            MATCH (section)-[:HAS_CLAUSE]->(related:Clause {organization_id: $organization_id, project_id: $project_id})
            WHERE related.is_active = true
            RETURN DISTINCT related.doc_id AS document_id
            LIMIT $document_limit
            """,
            """
            MATCH (related:Clause {organization_id: $organization_id, project_id: $project_id})
            WHERE related.is_active = true
              AND related.clause_number IN $seed_clause_numbers
              AND ($has_seed_document_ids = false OR NOT (related.doc_id IN $seed_document_ids))
            RETURN DISTINCT related.doc_id AS document_id
            LIMIT $document_limit
            """,
        )
        out: List[str] = []
        seen: set[str] = set()
        for cypher in statements:
            try:
                rows = self.falkor._parse_rows(self.falkor._execute(cypher, params, read_only=True))
            except Exception as exc:
                logger.debug("Contract graph candidate-document lookup failed: %s", exc)
                # A partial answer here would silently NARROW the eligible set,
                # which suppresses legitimate clauses - the same failure the
                # caller is trying to prevent. Report it instead.
                if raise_on_error:
                    raise
                return []
            for row in rows:
                document_id = str(row.get("document_id") or "").strip()
                if document_id and document_id not in seen:
                    seen.add(document_id)
                    out.append(document_id)
        return out

    def find_related_clauses(
        self,
        *,
        organization_id: Optional[str],
        project_id: Optional[str],
        seed_clause_numbers: List[str],
        seed_document_ids: Optional[List[str]] = None,
        eligible_document_ids: Optional[List[str]] = None,
        raise_on_error: bool = False,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Return graph-neighbor clauses for retrieval expansion.

        The current contract graph only models Document -> Section -> Clause.
        This query therefore returns two defensible related sets:
        - sibling clauses from sections containing a seed clause
        - clauses with the same number elsewhere in the scoped contract graph

        `eligible_document_ids` constrains the *related* clause rather than
        the seed, and is evaluated before `LIMIT`. `seed_document_ids` only
        ever bounded the seed, so related clauses could be drawn from any
        document in the organisation and project and spend the whole limit
        before an applicable clause was reached.

        Omitting it preserves the previous behaviour for the existing
        callers; the fail-closed evidence policy lives in
        `contract_graph_containment`, which ticket 12 switches on.
        """
        if not self.enabled or not seed_clause_numbers:
            return []
        seed_clause_numbers = [str(item).strip() for item in seed_clause_numbers if str(item).strip()]
        seed_document_ids = [str(item).strip() for item in (seed_document_ids or []) if str(item).strip()]
        # Deliberately not truncated the way the seed lists are: dropping an
        # eligible id would widen the answer rather than narrow it.
        eligible_ids = [str(item).strip() for item in (eligible_document_ids or []) if str(item).strip()]
        if not seed_clause_numbers:
            return []
        params = {
            "organization_id": organization_id,
            "project_id": project_id,
            "seed_clause_numbers": seed_clause_numbers[:20],
            "seed_document_ids": seed_document_ids[:20],
            "has_seed_document_ids": bool(seed_document_ids),
            "eligible_document_ids": eligible_ids,
            "has_eligible_document_ids": eligible_document_ids is not None,
            "limit": max(1, min(int(limit or 25), 100)),
        }
        cypher = """
        MATCH (seed:Clause {organization_id: $organization_id, project_id: $project_id})
        WHERE seed.clause_number IN $seed_clause_numbers
          AND ($has_seed_document_ids = false OR seed.doc_id IN $seed_document_ids)
        MATCH (section:Section)-[:HAS_CLAUSE]->(seed)
        MATCH (section)-[:HAS_CLAUSE]->(related:Clause {organization_id: $organization_id, project_id: $project_id})
        WHERE related.is_active = true
          AND ($has_eligible_document_ids = false OR related.doc_id IN $eligible_document_ids)
        RETURN related.doc_id AS document_id,
               related.clause_id AS clause_id,
               related.clause_number AS clause_number,
               related.title AS clause_title,
               related.text_content AS text,
               related.page_number AS page_number,
               related.section_type AS section_type,
               related.priority AS priority,
               related.clause_node_id AS clause_node_id,
               'section_sibling' AS graph_relation
        LIMIT $limit
        """
        try:
            rows = self.falkor._parse_rows(self.falkor._execute(cypher, params, read_only=True))
        except Exception as exc:
            logger.debug("Contract graph related-clause lookup failed: %s", exc)
            # Existing callers treat an outage as no expansion. The evidence
            # seam opts in to the raise so it can report a degraded answer
            # instead of one indistinguishable from 'nothing applies'.
            if raise_on_error:
                raise
            return []

        # Same-number matches are useful when SCC/GCC documents both carry the
        # relevant clause number but live in different sections/documents.
        same_number_cypher = """
        MATCH (related:Clause {organization_id: $organization_id, project_id: $project_id})
        WHERE related.is_active = true
          AND ($has_eligible_document_ids = false OR related.doc_id IN $eligible_document_ids)
          AND related.clause_number IN $seed_clause_numbers
          AND ($has_seed_document_ids = false OR NOT (related.doc_id IN $seed_document_ids))
        RETURN related.doc_id AS document_id,
               related.clause_id AS clause_id,
               related.clause_number AS clause_number,
               related.title AS clause_title,
               related.text_content AS text,
               related.page_number AS page_number,
               related.section_type AS section_type,
               related.priority AS priority,
               related.clause_node_id AS clause_node_id,
               'same_clause_number' AS graph_relation
        LIMIT $limit
        """
        try:
            rows.extend(self.falkor._parse_rows(self.falkor._execute(same_number_cypher, params, read_only=True)))
        except Exception as exc:
            logger.debug("Contract graph same-number lookup failed: %s", exc)
            if raise_on_error:
                raise

        seen: set[tuple[str, str, str]] = set()
        out: List[Dict[str, Any]] = []
        for row in rows:
            key = (str(row.get("document_id") or ""), str(row.get("clause_number") or ""), str(row.get("clause_node_id") or ""))
            if not key[0] or not key[1] or key in seen:
                continue
            seen.add(key)
            out.append(row)
            if len(out) >= params["limit"]:
                break
        return out
