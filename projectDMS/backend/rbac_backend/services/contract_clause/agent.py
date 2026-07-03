"""Contract Clause Chunking Agent orchestrator (Phase 2).

Ties the pipeline together for one contract document:
scope+authorize -> page-wise text (reused OCR) -> clean -> detect clause
boundaries + hierarchy (reused ClauseExtractor) -> long-clause splitting ->
table linking -> validate -> idempotent save -> processing-run audit log.

The pure building/linking logic is decoupled from IO so it is unit-testable
without a database; ``process_document`` wires it to the real stores.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ...models.contract_clause import ClauseProcessingRun, ContractClause
from .modification_detector import ModificationDetector
from .storage_service import ClauseStorageService

logger = logging.getLogger(__name__)

_TABLE_CAPTION_RE = re.compile(
    r"^[ \t]*(table|schedule|annexure|appendix)\b[^\n]{0,120}",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class DetectedClause:
    """Normalised clause detected from the document (extractor-agnostic).

    ``text`` holds the exact raw wording; ``cleaned_text`` the noise-stripped
    text used for embeddings. ``char_start``/``char_end`` are offsets in the
    assembled document text for source-viewer jumps (req 3).
    """

    clause_no: Optional[str]
    clause_title: Optional[str] = None
    clause_type: str = "clause"
    text: str = ""
    cleaned_text: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    confidence: str = "high"
    extraction_method: str = "text"

    @property
    def is_section(self) -> bool:
        return not (self.clause_no and str(self.clause_no).strip())


@dataclass
class DetectedTable:
    """A table block to be stored separately and linked to a clause (req 15).

    For structured tables (e.g. BOQ) ``table_type``/``columns``/``rows`` carry
    the parsed data so the table is not stored as a blind blob.
    """

    page_no: int
    table_title: Optional[str] = None
    text: str = ""
    table_type: Optional[str] = None
    columns: List[str] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class DocumentScope:
    org_id: str
    project_id: str
    contract_id: str
    document_id: str
    document_title: Optional[str] = None
    document_type: Optional[str] = None
    volume: Optional[str] = None
    revision: Optional[str] = None
    source_file_path: Optional[str] = None
    source_pdf_url: Optional[str] = None


@dataclass
class ClauseProcessingSummary:
    document_id: str
    total_pages: int = 0
    clauses_detected: int = 0
    section_chunks: int = 0
    tables_detected: int = 0
    low_confidence_chunks: int = 0
    duplicates_detected: int = 0
    modifications_detected: int = 0
    human_review_required: bool = False
    records_written: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


class ClauseChunkingAgent:
    RUN_COLLECTION = "clause_processing_runs"

    def __init__(
        self,
        db: Any,
        storage_service: Optional[ClauseStorageService] = None,
        policy_service: Any = None,
        max_clause_chars: int = 4000,
        embedding_service: Any = None,
        graph_service: Any = None,
    ) -> None:
        self.db = db
        self.storage = storage_service or ClauseStorageService(db, policy_service)
        self.policy_service = policy_service
        self.max_clause_chars = max_clause_chars
        # Phase 3 sinks (optional; best-effort when configured).
        self.embedding_service = embedding_service
        self.graph_service = graph_service

    # ------------------------------------------------------------------ #
    # Pure helpers (unit-testable)
    # ------------------------------------------------------------------ #
    def split_text(self, text: str, max_len: Optional[int] = None) -> List[str]:
        """Paragraph-aware split of a long clause into parts <= max_len."""
        max_len = max_len or self.max_clause_chars
        body = (text or "").strip()
        if not body:
            return [""]
        if len(body) <= max_len:
            return [body]
        parts: List[str] = []
        current: List[str] = []
        length = 0
        for para in re.split(r"\n\n+", body):
            para = para.strip()
            if not para:
                continue
            if length + len(para) > max_len and current:
                parts.append("\n\n".join(current))
                current, length = [], 0
            if len(para) > max_len:
                for i in range(0, len(para), max_len):
                    parts.append(para[i : i + max_len])
            else:
                current.append(para)
                length += len(para)
        if current:
            parts.append("\n\n".join(current))
        return parts or [body]

    @staticmethod
    def link_table_to_clause(
        table: DetectedTable, clauses: List[DetectedClause]
    ) -> Optional[str]:
        """Nearest clause for a table: the clause on/just before the table's page (req 15)."""
        candidates = [c for c in clauses if c.clause_no and c.page_start is not None]
        if not candidates:
            return None
        preceding = [c for c in candidates if (c.page_start or 0) <= table.page_no]
        if preceding:
            return max(preceding, key=lambda c: c.page_start or 0).clause_no
        # No clause starts before the table -> nearest by page distance.
        return min(candidates, key=lambda c: abs((c.page_start or 0) - table.page_no)).clause_no

    @staticmethod
    def detect_tables(pages: List[Dict[str, Any]]) -> List[DetectedTable]:
        """Caption-based table detection from cleaned page text (heuristic, req 15).

        Each page dict has ``page_no`` and ``cleaned_text``/``text``. A line that
        starts with Table/Schedule/Annexure/Appendix begins a table block.
        """
        tables: List[DetectedTable] = []
        for page in pages:
            page_no = int(page.get("page_no") or page.get("page_number") or 0)
            text = page.get("cleaned_text") or page.get("text") or ""
            for match in _TABLE_CAPTION_RE.finditer(text):
                caption = match.group(0).strip()
                start = match.end()
                block = text[start : start + 1500].strip()
                tables.append(
                    DetectedTable(page_no=page_no, table_title=caption[:200], text=block)
                )
        return tables

    def build_records(
        self,
        scope: DocumentScope,
        clauses: List[DetectedClause],
        tables: Optional[List[DetectedTable]] = None,
    ) -> Tuple[List[ContractClause], ClauseProcessingSummary]:
        """Map detected clauses/tables into ContractClause records (pure)."""
        tables = tables or []
        summary = ClauseProcessingSummary(document_id=scope.document_id)
        records: List[ContractClause] = []
        chunk_index = 0

        # Pre-count occurrences per clause number so repeated numbers get a
        # stable duplicate ordinal + status (req #2).
        group_counts: Dict[str, int] = {}
        for clause in clauses:
            if clause.clause_no:
                key = ClauseStorageService.normalize_clause_no(clause.clause_no) or clause.clause_no
                group_counts[key] = group_counts.get(key, 0) + 1
        running: Dict[str, int] = {}

        for clause in clauses:
            ordinal, dup_status = 1, "unique"
            if clause.clause_no:
                key = ClauseStorageService.normalize_clause_no(clause.clause_no) or clause.clause_no
                running[key] = running.get(key, 0) + 1
                ordinal = running[key]
                dup_status = "duplicate" if group_counts.get(key, 0) > 1 else "unique"

            raw_text = clause.text or ""
            cleaned_source = clause.cleaned_text if clause.cleaned_text is not None else clause.text
            parts = self.split_text(cleaned_source)
            total = len(parts)
            # Preserve exact raw wording per part: when a distinct raw text is
            # provided, split it in parallel and pair 1:1 with the cleaned parts
            # if the split aligns; otherwise fall back to the cleaned slice.
            raw_parts: List[str] = []
            if raw_text and raw_text != cleaned_source:
                candidate = self.split_text(raw_text)
                if len(candidate) == total:
                    raw_parts = candidate
            for part_no, part in enumerate(parts, start=1):
                if clause.is_section:
                    chunk_type = "section_chunk"
                elif total > 1:
                    chunk_type = "clause_part"
                else:
                    chunk_type = "clause"
                if raw_parts:
                    text_value = raw_parts[part_no - 1]   # exact raw slice per part
                elif total == 1 and raw_text:
                    text_value = raw_text                 # exact raw for un-split clause
                else:
                    text_value = part                     # safe fallback (raw==cleaned or misaligned)
                record = self.storage.build_record(
                    org_id=scope.org_id,
                    project_id=scope.project_id,
                    contract_id=scope.contract_id,
                    document_id=scope.document_id,
                    clause_no=clause.clause_no,
                    clause_title=clause.clause_title,
                    text=text_value,
                    cleaned_text=part,
                    chunk_type=chunk_type,
                    chunk_index=chunk_index,
                    chunk_part=part_no,
                    chunk_total=total,
                    page_start=clause.page_start,
                    page_end=clause.page_end,
                    char_start=clause.char_start,
                    char_end=clause.char_end,
                    duplicate_ordinal=ordinal,
                    duplicate_status=dup_status,
                    document_title=scope.document_title,
                    document_type=scope.document_type,
                    volume=scope.volume,
                    revision=scope.revision,
                    confidence=clause.confidence,  # type: ignore[arg-type]
                    extraction_method=clause.extraction_method,  # type: ignore[arg-type]
                    source_file_path=scope.source_file_path,
                    source_pdf_url=scope.source_pdf_url,
                )
                records.append(record)
                chunk_index += 1
                if record.quality_status != "validated":
                    summary.low_confidence_chunks += 1
                if record.human_review_required:
                    summary.human_review_required = True

            if clause.is_section:
                summary.section_chunks += 1
            else:
                summary.clauses_detected += 1

        # Tables: separate chunks linked to the nearest clause.
        for table in tables:
            linked_no = self.link_table_to_clause(table, clauses)
            record = self.storage.build_record(
                org_id=scope.org_id,
                project_id=scope.project_id,
                contract_id=scope.contract_id,
                document_id=scope.document_id,
                clause_no=None,
                clause_title=table.table_title,
                text=table.text,
                cleaned_text=table.text,
                chunk_type="table",
                chunk_index=chunk_index,
                chunk_part=1,
                chunk_total=1,
                page_start=table.page_no,
                page_end=table.page_no,
                document_title=scope.document_title,
                document_type=scope.document_type,
                volume=scope.volume,
                revision=scope.revision,
                linked_clause_no=linked_no,
                table_title=table.table_title,
                table_type=table.table_type,
                table_columns=table.columns,
                table_rows=table.rows,
                source_file_path=scope.source_file_path,
                source_pdf_url=scope.source_pdf_url,
            )
            records.append(record)
            chunk_index += 1
            summary.tables_detected += 1

        summary.duplicates_detected = sum(1 for count in group_counts.values() if count > 1)
        return records, summary

    # ------------------------------------------------------------------ #
    # Orchestration (IO)
    # ------------------------------------------------------------------ #
    async def process(
        self,
        current_user: Any,
        scope: DocumentScope,
        clauses: List[DetectedClause],
        tables: Optional[List[DetectedTable]] = None,
        total_pages: int = 0,
    ) -> ClauseProcessingSummary:
        """Authorize, build, save idempotently, and write the audit run."""
        await self.storage.authorize_processing_run(
            current_user,
            org_id=scope.org_id,
            project_id=scope.project_id,
            contract_id=scope.contract_id,
            document_id=scope.document_id,
        )
        records, summary = self.build_records(scope, clauses, tables)
        summary.total_pages = total_pages

        try:
            await self.storage.ensure_indexes()
        except Exception as exc:  # pragma: no cover - index creation is best-effort
            logger.warning("clause index creation skipped: %s", exc)

        summary.records_written = await self.storage.save_clauses(records)

        # Phase 3: detect SCC/addendum modifications to base (GCC) clauses.
        modification_links = self._detect_modifications(records)
        summary.modifications_detected = len(modification_links)
        if modification_links:
            summary.human_review_required = True

        # Phase 3: embed authorised clauses (cleaned text) into Qdrant (best-effort).
        if self.embedding_service is not None:
            try:
                emb = await self.embedding_service.index_clauses(records)
                summary.records_written["embedded"] = emb.get("embedded", 0)
            except Exception as exc:  # pragma: no cover - external service
                logger.warning("clause embedding failed: %s", exc)
                summary.errors.append(f"embedding_failed: {exc}")

        # Phase 3: sync the clause graph to FalkorDB (best-effort).
        if self.graph_service is not None:
            try:
                await self.graph_service.sync(records, modification_links)
            except Exception as exc:  # pragma: no cover - external service
                logger.warning("clause graph sync failed: %s", exc)
                summary.errors.append(f"graph_sync_failed: {exc}")

        await self._write_run(current_user, scope, summary)
        return summary

    def _detect_modifications(self, records: List[ContractClause]) -> List[Dict[str, Any]]:
        """Modification links from SCC/addendum clauses to base GCC clauses (req 19).

        GCC is the base document type, so its own clauses are not treated as
        modifiers. Deduplicated per (modifier clause, base clause, type).
        """
        links: List[Dict[str, Any]] = []
        seen: set = set()
        for record in records:
            if record.chunk_type == "table":
                continue
            if (record.document_type or "").upper() == "GCC":
                continue
            text = record.cleaned_text or record.text or ""
            for signal in ModificationDetector.detect(text):
                dedupe = (record.clause_uid, signal.base_clause_no, signal.modification_type)
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                links.append(
                    ModificationDetector.build_modification_link(
                        signal=signal,
                        applicable_clause_id=record.clause_uid,
                        modifier_document_type=record.document_type,
                        base_document_type="GCC",
                    )
                )
        return links

    async def _write_run(
        self, current_user: Any, scope: DocumentScope, summary: ClauseProcessingSummary
    ) -> None:
        run = ClauseProcessingRun(
            document_id=scope.document_id,
            user_id=getattr(current_user, "id", None),
            org_id=scope.org_id,
            project_id=scope.project_id,
            contract_id=scope.contract_id,
            clauses_detected=summary.clauses_detected,
            section_chunks=summary.section_chunks,
            tables_detected=summary.tables_detected,
            low_confidence_chunks=summary.low_confidence_chunks,
            duplicates_detected=summary.duplicates_detected,
            modifications_detected=summary.modifications_detected,
            human_review_required=summary.human_review_required,
            errors=summary.errors,
            created_at=datetime.utcnow(),
        )
        doc = run.model_dump(by_alias=True)
        doc.pop("_id", None)
        await self.db[self.RUN_COLLECTION].insert_one(doc)

    async def process_document(self, current_user: Any, document_id: str) -> ClauseProcessingSummary:
        """Integration entry: load the document + stored OCR pages, run the
        existing ClauseExtractor, then process clause-wise."""
        from bson import ObjectId

        lookups: List[Any] = [document_id]
        try:
            lookups.append(ObjectId(document_id))
        except Exception:
            pass
        document = None
        for key in lookups:
            document = await self.db.documents.find_one({"_id": key})
            if document:
                break
        if not document:
            raise ValueError(f"Contract document {document_id} not found")

        scope = DocumentScope(
            org_id=str(document.get("organization_id") or ""),
            project_id=str(document.get("project_id") or ""),
            contract_id=str(document.get("contract_id") or document.get("_id") or document_id),
            document_id=str(document.get("_id") or document_id),
            document_title=document.get("filename") or document.get("title"),
            document_type=(document.get("contract_categories") or [None])[0] or document.get("document_type"),
            volume=document.get("volume"),
            revision=document.get("revision"),
            source_file_path=document.get("filepath_local") or document.get("file_path"),
        )

        self._ensure_phase3_services()
        pages = await self._load_ocr_pages(scope.document_id)
        cleaned_text, page_index = self._assemble_pages(pages)
        clauses = self._detect_clauses(cleaned_text, page_index)
        tables = self.detect_tables(pages) + self.detect_boq_tables(pages)
        return await self.process(current_user, scope, clauses, tables, total_pages=len(pages))

    @staticmethod
    def detect_boq_tables(pages: List[Dict[str, Any]]) -> List[DetectedTable]:
        """Detect + structure Bill of Quantities tables (req 15)."""
        from .boq_detector import detect_boq_tables as _detect_boq

        detected: List[DetectedTable] = []
        for boq in _detect_boq(pages):
            rows = [
                {
                    "item_no": r.item_no,
                    "description": r.description,
                    "unit": r.unit,
                    "quantity": r.quantity,
                    "rate": r.rate,
                    "amount": r.amount,
                }
                for r in boq.rows
            ]
            detected.append(
                DetectedTable(
                    page_no=boq.page_no,
                    table_title=boq.header or f"Bill of Quantities (page {boq.page_no})",
                    text=boq.text,
                    table_type="boq",
                    columns=boq.columns,
                    rows=rows,
                )
            )
        return detected

    def _ensure_phase3_services(self) -> None:
        """Lazily wire real Qdrant + FalkorDB sinks for the live pipeline."""
        if self.embedding_service is None:
            try:
                from ...retrieval.dependencies import get_embedding_client, get_vector_client
                from .embedding_service import ClauseEmbeddingService

                self.embedding_service = ClauseEmbeddingService(
                    self.db, get_embedding_client(), get_vector_client()
                )
            except Exception as exc:  # pragma: no cover - optional deps
                logger.warning("clause embedding service unavailable: %s", exc)
        if self.graph_service is None:
            try:
                from ..falkor_graph_service import FalkorGraphService
                from .graph_service import ClauseGraphService

                falkor = FalkorGraphService()
                if getattr(falkor, "enabled", False):
                    self.graph_service = ClauseGraphService(falkor=falkor)
            except Exception as exc:  # pragma: no cover - optional deps
                logger.warning("clause graph service unavailable: %s", exc)

    async def _load_ocr_pages(self, document_id: str) -> List[Dict[str, Any]]:
        cursor = self.db.contract_ocr_pages.find({"document_id": document_id})
        pages = [page async for page in cursor]
        pages.sort(key=lambda p: int(p.get("page_number") or p.get("page_no") or 0))
        return [
            {
                "page_no": int(p.get("page_number") or p.get("page_no") or 0),
                "cleaned_text": p.get("cleaned_text") or "",
                "text": p.get("raw_text") or "",
                "ocr_used": (p.get("status") or "").startswith("ocr"),
            }
            for p in pages
        ]

    @staticmethod
    def _assemble_pages(pages: List[Dict[str, Any]]) -> Tuple[str, List[Tuple[int, int, int]]]:
        """Concatenate cleaned page text; return (text, [(start, end, page_no)])."""
        chunks: List[str] = []
        index: List[Tuple[int, int, int]] = []
        offset = 0
        for page in pages:
            text = page.get("cleaned_text") or page.get("text") or ""
            start = offset
            chunks.append(text)
            offset += len(text) + 2  # account for the "\n\n" join
            index.append((start, offset, page["page_no"]))
        return "\n\n".join(chunks), index

    def _detect_clauses(
        self, cleaned_text: str, page_index: List[Tuple[int, int, int]]
    ) -> List[DetectedClause]:
        from ..contracts_ingest import ClauseExtractor
        from .subitem_detector import detect_subitems

        extractor = ClauseExtractor()
        detected: List[DetectedClause] = []
        for clause in extractor.extract_clauses(cleaned_text):
            page_start = self._page_for_offset(clause.start_position, page_index)
            page_end = self._page_for_offset(clause.end_position, page_index) or page_start
            confidence = "high"
            if getattr(clause, "ai_confidence", None) is not None and clause.ai_confidence < 0.7:
                confidence = "medium"

            base_no = clause.clause_number or None
            subitems = detect_subitems(clause.clause_text, base_no) if base_no else []

            if subitems:
                # Parent keeps the lead-in text (before the first sub-item); each
                # (a)/(i) item becomes a child record for granular grounding.
                lead_in = clause.clause_text[: subitems[0].char_start].strip()
                parent_text = lead_in or (clause.clause_title or clause.clause_text[:200])
                detected.append(
                    DetectedClause(
                        clause_no=base_no,
                        clause_title=clause.clause_title or None,
                        clause_type=clause.clause_type or "clause",
                        text=parent_text,
                        cleaned_text=parent_text,
                        page_start=page_start,
                        page_end=page_end,
                        char_start=clause.start_position,
                        char_end=clause.start_position + len(parent_text),
                        confidence=confidence,
                        extraction_method="text",
                    )
                )
                for item in subitems:
                    s_start = clause.start_position + item.char_start
                    s_end = clause.start_position + item.char_end
                    detected.append(
                        DetectedClause(
                            clause_no=item.clause_no,
                            clause_title=None,
                            clause_type="sub_item",
                            text=item.text,
                            cleaned_text=item.text,
                            page_start=self._page_for_offset(s_start, page_index),
                            page_end=self._page_for_offset(s_end, page_index),
                            char_start=s_start,
                            char_end=s_end,
                            confidence=confidence,
                            extraction_method="text",
                        )
                    )
            else:
                detected.append(
                    DetectedClause(
                        clause_no=base_no,
                        clause_title=clause.clause_title or None,
                        clause_type=clause.clause_type or "clause",
                        text=clause.clause_text,
                        cleaned_text=clause.clause_text,
                        page_start=page_start,
                        page_end=page_end,
                        char_start=clause.start_position,
                        char_end=clause.end_position,
                        confidence=confidence,
                        extraction_method="text",
                    )
                )
        return detected

    @staticmethod
    def _page_for_offset(
        offset: Optional[int], page_index: List[Tuple[int, int, int]]
    ) -> Optional[int]:
        if offset is None:
            return None
        for start, end, page_no in page_index:
            if start <= offset < end:
                return page_no
        return page_index[-1][2] if page_index else None


__all__ = [
    "ClauseChunkingAgent",
    "DetectedClause",
    "DetectedTable",
    "DocumentScope",
    "ClauseProcessingSummary",
]
