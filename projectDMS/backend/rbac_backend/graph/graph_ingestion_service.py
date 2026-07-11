import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Dict, Iterable, Optional, Sequence, List

from ..models.document_metadata import ParsedDocumentMetadata
from .graph_adapter import GraphAdapter, GraphAdapterError, GraphEdge, GraphNode
from ..services.falkor_graph_service import FalkorGraphService, normalize_letter_code

logger = logging.getLogger(__name__)


class GraphIngestionService:
    """Transforms processed document metadata into graph nodes and edges."""

    def __init__(
        self,
        adapter: Optional[GraphAdapter] = None,
        falkor: Optional[FalkorGraphService] = None,
    ) -> None:
        self.adapter = adapter or GraphAdapter()
        self.falkor = falkor or FalkorGraphService()

    @property
    def enabled(self) -> bool:
        return self.adapter.config.enabled

    async def ingest_document(
        self,
        document_id: str,
        document_data: Dict[str, Any],
        metadata: Optional[ParsedDocumentMetadata],
        metadata_source: str,
        upload_type: Optional[str] = None,
    ) -> None:
        """Ingest a processed document into the graph layer."""
        adapter_enabled = self.adapter.config.enabled

        try:
            await asyncio.to_thread(
                self._ingest_document_sync,
                document_id,
                document_data,
                metadata,
                metadata_source,
                upload_type,
                adapter_enabled,
            )
        except GraphAdapterError:
            logger.warning("Graph ingestion failed for document %s", document_id)
        except Exception:
            logger.exception("Unexpected error during graph ingestion for %s", document_id)

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _ingest_document_sync(
        self,
        document_id: str,
        document_data: Dict[str, Any],
        metadata: Optional[ParsedDocumentMetadata],
        metadata_source: str,
        upload_type: Optional[str],
        adapter_enabled: bool,
    ) -> None:
        if not adapter_enabled:
            logger.debug(
                "GraphAdapter disabled; skipping Graphiti upsert for %s but continuing Falkor sync",
                document_id,
            )

        doc_node_id = self._doc_node_id(document_id)
        if adapter_enabled:
            node_properties = self._build_document_properties(
                document_id=document_id,
                document=document_data,
                metadata=metadata,
                metadata_source=metadata_source,
                upload_type=upload_type,
            )

            self.adapter.upsert_node(
                GraphNode(node_id=doc_node_id, labels=["Letter"], properties=node_properties)
            )

            self._upsert_project_relationship(doc_node_id, document_data)
            self._upsert_reference_relationships(doc_node_id, document_data, metadata)
            self._upsert_clause_relationships(doc_node_id, metadata)
            self._upsert_document_lineage(doc_node_id, document_data)
            self._upsert_approval(doc_node_id, document_data, metadata)

        self.sync_document_to_falkor(document_id, document_data, metadata, upload_type)
        logger.info(
            "Stored document %s in FalkorDB graph (node_id=%s)",
            document_id,
            doc_node_id,
        )

    def _build_document_properties(
        self,
        document_id: str,
        document: Dict[str, Any],
        metadata: Optional[ParsedDocumentMetadata],
        metadata_source: str,
        upload_type: Optional[str],
    ) -> Dict[str, Any]:
        props: Dict[str, Any] = {
            "document_id": document_id,
            "letter_no": self._coalesce(
                metadata.letter_no if metadata else None,
                document.get("letterNo"),
            ),
            "subject": self._coalesce(metadata.subject if metadata else None, document.get("subject")),
            "metadata_source": metadata_source,
            "upload_type": upload_type or document.get("uploadType"),
            "project_id": document.get("project_id") or document.get("projectId"),
            "organization_id": document.get("organization_id") or document.get("organizationId"),
            "status": document.get("status"),
            "chain_head_id": document.get("chain_head_id"),
            "version": document.get("version"),
        }

        doc_date = self._coalesce(
            getattr(metadata, "date", None) if metadata else None,
            document.get("date"),
        )
        if doc_date:
            props["date"] = self._format_date(doc_date)

        if metadata:
            if metadata.summary:
                props["summary"] = metadata.summary
            if metadata.keywords:
                props["keywords"] = metadata.keywords
            if metadata.contractual_clauses:
                props["contractual_clauses"] = metadata.contractual_clauses
            if metadata.key_reply_points:
                props["key_reply_points"] = metadata.key_reply_points
            if metadata.additional_keywords:
                props["additional_keywords"] = metadata.additional_keywords
            if metadata.from_company:
                props["from_company"] = metadata.from_company
            if metadata.to_company:
                props["to_company"] = metadata.to_company
            for field in (
                "asset_type",
                "location",
                "specific_area",
                "chainage_from",
                "chainage_to",
                "work_type",
                "issue_nature",
                "claim_category",
                "alleged_responsibility",
                "priority",
                "linked_event_suggested",
                "reference_chain",
            ):
                value = getattr(metadata, field, None)
                if value:
                    props[field] = value
            if metadata.tags:
                props["extracted_tags"] = metadata.tags
            if metadata.sub_tags:
                props["extracted_subTags"] = metadata.sub_tags

        if document.get("tags"):
            props["tags"] = document.get("tags")
        for field in (
            "asset_type",
            "location",
            "specific_area",
            "chainage_from",
            "chainage_to",
            "work_type",
            "issue_nature",
            "claim_category",
            "alleged_responsibility",
            "priority",
            "linked_event_suggested",
            "reference_chain",
            "additional_keywords",
            "extracted_tags",
            "extracted_subTags",
        ):
            if field not in props and document.get(field):
                props[field] = document.get(field)
        if document.get("reference"):
            props["reference"] = document.get("reference")

        return {key: value for key, value in props.items() if value is not None}

    def _upsert_project_relationship(self, doc_node_id: str, document: Dict[str, Any]) -> None:
        project_id = document.get("project_id") or document.get("projectId")
        if not project_id:
            return

        project_node_id = self._project_node_id(str(project_id))
        project_node = GraphNode(
            node_id=project_node_id,
            labels=["Project"],
            properties={
                "project_id": str(project_id),
                "name": document.get("project_name") or document.get("projectName"),
            },
        )
        self.adapter.upsert_node(project_node)
        self.adapter.upsert_edge(
            GraphEdge(start_node=doc_node_id, end_node=project_node_id, relationship="BELONGS_TO_PROJECT")
        )

    def _upsert_reference_relationships(
        self,
        doc_node_id: str,
        document: Dict[str, Any],
        metadata: Optional[ParsedDocumentMetadata],
    ) -> None:
        references = set()
        doc_refs = document.get("reference") or []
        if isinstance(doc_refs, Sequence):
            references.update(self._normalize_collection(doc_refs))
        if metadata and metadata.references:
            references.update(self._normalize_collection(metadata.references))

        if not references:
            return

        for ref in references:
            ref_node_id = self._reference_node_id(ref)
            ref_node = GraphNode(
                node_id=ref_node_id,
                labels=["Letter"],
                properties={"letter_no": ref},
            )
            self.adapter.upsert_node(ref_node)
            self.adapter.upsert_edge(
                GraphEdge(start_node=doc_node_id, end_node=ref_node_id, relationship="REFERENCES")
            )

    def _upsert_clause_relationships(
        self,
        doc_node_id: str,
        metadata: Optional[ParsedDocumentMetadata],
    ) -> None:
        if not metadata or not metadata.contractual_clauses:
            return

        for clause in self._normalize_collection(metadata.contractual_clauses):
            clause_node_id = self._clause_node_id(clause)
            clause_node = GraphNode(
                node_id=clause_node_id,
                labels=["Clause"],
                properties={"identifier": clause},
            )
            self.adapter.upsert_node(clause_node)
            self.adapter.upsert_edge(
                GraphEdge(start_node=doc_node_id, end_node=clause_node_id, relationship="MENTIONS_CLAUSE")
            )

    def _upsert_document_lineage(self, doc_node_id: str, document: Dict[str, Any]) -> None:
        prev_id = document.get("previous_letter_id")
        if prev_id:
            prev_node_id = self._doc_node_id(str(prev_id))
            self.adapter.upsert_node(
                GraphNode(node_id=prev_node_id, labels=["Letter"], properties={"document_id": str(prev_id)})
            )
            self.adapter.upsert_edge(
                GraphEdge(start_node=doc_node_id, end_node=prev_node_id, relationship="REPLIES_TO")
            )
            self.adapter.upsert_edge(
                GraphEdge(start_node=doc_node_id, end_node=prev_node_id, relationship="SUPERSEDES")
            )

        chain_head = document.get("chain_head_id")
        if chain_head and str(chain_head) != doc_node_id:
            head_node_id = self._doc_node_id(str(chain_head))
            self.adapter.upsert_node(
                GraphNode(node_id=head_node_id, labels=["Letter"], properties={"document_id": str(chain_head)})
            )
            self.adapter.upsert_edge(
                GraphEdge(start_node=doc_node_id, end_node=head_node_id, relationship="VERSION_OF")
            )

    def _upsert_approval(
        self,
        doc_node_id: str,
        document: Dict[str, Any],
        metadata: Optional[ParsedDocumentMetadata],
    ) -> None:
        approver = (
            document.get("approvedBy")
            or document.get("approved_by")
            or (metadata.from_company if metadata and getattr(metadata, "from_company", None) else None)
        )
        if not approver:
            return

        approver_text = str(approver).strip()
        if not approver_text:
            return

        approver_node_id = self._party_node_id(approver_text)
        approver_node = GraphNode(
            node_id=approver_node_id,
            labels=["Party"],
            properties={"name": approver_text},
        )
        self.adapter.upsert_node(approver_node)
        self.adapter.upsert_edge(
            GraphEdge(start_node=doc_node_id, end_node=approver_node_id, relationship="APPROVED_BY")
        )

    # ------------------------------------------------------------------
    # utility helpers
    # ------------------------------------------------------------------
    def _doc_node_id(self, identifier: str) -> str:
        return f"doc:{identifier}"

    def _project_node_id(self, identifier: str) -> str:
        return f"project:{identifier}"

    def _clause_node_id(self, clause: str) -> str:
        return f"clause:{self._slug(clause)}"

    def _reference_node_id(self, reference: str) -> str:
        return f"letter-ref:{self._slug(reference)}"

    def _party_node_id(self, party: str) -> str:
        return f"party:{self._slug(party)}"

    def _format_date(self, value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    def _normalize_collection(self, values: Iterable[Any]) -> Sequence[str]:
        normalized: List[str] = []
        for value in values:
            if value is None:
                continue

            candidate = None
            if isinstance(value, dict):
                candidate = value.get("letterNo") or value.get("letter_no") or value.get("text") or value.get("reference")
            elif hasattr(value, "letter_no") or hasattr(value, "letterNo"):
                candidate = getattr(value, "letter_no", None) or getattr(value, "letterNo", None)

            if candidate:
                candidate_text = str(candidate).strip()
                if candidate_text:
                    normalized.append(candidate_text)
                    continue

            text = str(value).strip()
            if text:
                normalized.append(text)
        return normalized

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^0-9a-zA-Z]+", "-", value).strip("-")
        return slug.lower() or "unknown"

    def _coalesce(self, *values: Any) -> Optional[Any]:
        for value in values:
            if value not in (None, ""):
                return value
        return None

    # ------------------------------------------------------------------
    # FalkorDB integration
    # ------------------------------------------------------------------
    def sync_document_to_falkor(
        self,
        document_id: str,
        document: Dict[str, Any],
        metadata: Optional[ParsedDocumentMetadata],
        upload_type: Optional[str],
        *,
        raise_on_error: bool = False,
    ) -> None:
        if not self.falkor.enabled:
            return
        try:
            payload = self._build_falkor_payload(document_id, document, metadata, upload_type)
            if not payload:
                return
            letter, references = payload
            self.falkor.upsert_letter_with_refs(letter, references, cleanup=True)
        except Exception:
            logger.exception("Failed to sync document %s with FalkorDB", document_id)
            if raise_on_error:
                raise

    def _build_falkor_payload(
        self,
        document_id: str,
        document: Dict[str, Any],
        metadata: Optional[ParsedDocumentMetadata],
        upload_type: Optional[str],
    ) -> Optional[tuple[Dict[str, Any], List[Dict[str, Any]]]]:
        letter_code = self._coalesce(
            getattr(metadata, "letter_no", None) if metadata else None,
            document.get("letterNo"),
            document.get("letter_no"),
            document.get("code"),
            document_id,
        )
        if not letter_code:
            return None

        subject = self._coalesce(
            getattr(metadata, "subject", None) if metadata else None,
            document.get("subject"),
        )
        letter_date = self._coalesce(
            getattr(metadata, "date", None) if metadata else None,
            document.get("date"),
        )
        project = self._coalesce(
            document.get("project_name"),
            document.get("project"),
            document.get("projectId"),
            document.get("project_id"),
        )
        organization_id = self._coalesce(
            document.get("organization_id"),
            document.get("organizationId"),
        )
        project_id = self._coalesce(
            document.get("project_id"),
            document.get("projectId"),
        )
        direction = (
            (upload_type or document.get("uploadType") or "incoming")
            .strip()
            .lower()
        )
        if direction not in {"incoming", "outgoing"}:
            direction = "incoming"

        letter = {
            "code": str(letter_code),
            "normCode": normalize_letter_code(str(letter_code)),
            "direction": direction,
            "subject": subject,
            "date": letter_date,
            "organization_id": organization_id,
            "project_id": project_id,
            "project": project,
        }

        references: List[Dict[str, Any]] = []
        # Falkor relationships represent links resolved to documents in MongoDB.
        # Raw extracted metadata stays in `document.reference` for display and
        # retry, but must not create placeholder graph nodes for unavailable
        # letters.
        refs_source: List[Any] = []
        doc_links = document.get("references")
        if isinstance(doc_links, Sequence) and not isinstance(doc_links, (str, bytes)):
            refs_source.extend(doc_links)

        for ref in refs_source:
            code = None
            ref_type = "CITES"
            source_tag = "parser"
            if isinstance(ref, dict):
                code = (
                    ref.get("letterNo")
                    or ref.get("letter_no")
                    or ref.get("code")
                    or ref.get("text")
                    or ref.get("documentId")
                )
                ref_type = ref.get("type") or ref_type
                source_tag = ref.get("source") or ("manual" if ref.get("documentId") else "parser")
            elif hasattr(ref, "letter_no") or hasattr(ref, "letterNo"):
                code = getattr(ref, "letter_no", None) or getattr(ref, "letterNo", None)
                source_tag = getattr(ref, "source", None) or "parser"
            elif hasattr(ref, "documentId"):
                code = getattr(ref, "documentId")
                source_tag = getattr(ref, "source", None) or "manual"
            else:
                code = str(ref).strip()

            if not code:
                continue
            references.append(
                {
                    "code": str(code),
                    "type": "REPLIES_TO" if ref_type.upper() == "REPLIES_TO" else "CITES",
                    "source": source_tag,
                }
            )

        prev_id = document.get("previous_letter_id")
        if prev_id:
            references.append(
                {
                    "code": str(prev_id),
                    "type": "REPLIES_TO",
                    "source": "system",
                }
            )

        deduped: List[Dict[str, Any]] = []
        seen = set()
        for ref in references:
            norm = normalize_letter_code(ref.get("code", ""))
            if not norm:
                continue
            sig = (norm, ref.get("type", "CITES"))
            if sig in seen:
                continue
            ref["normCode"] = norm
            seen.add(sig)
            deduped.append(ref)

        return letter, deduped if deduped else []


__all__ = ["GraphIngestionService"]



