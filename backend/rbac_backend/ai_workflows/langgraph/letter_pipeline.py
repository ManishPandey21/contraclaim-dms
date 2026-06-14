"""
Simplified LangGraph-inspired pipeline for letter drafting.

The implementation mirrors a node-based workflow without importing the
external LangGraph dependency, keeping execution deterministic and testable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from textwrap import shorten
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, TYPE_CHECKING

from ...core.database import get_database
from ...models.ai_models import (
    DraftReviewFinding,
    DraftSource,
    LangGraphDraftRequest,
    LetterDraftRequest,
    LetterDraftResponse,
)
from ...services.document_service import DocumentService
from ...models.document import Document
from ...services.letter_service import LetterService
from ...services.contract_service import ContractService
from ...services.conversation_service import ConversationService
from ...services.workflow import workflow_engine
from ...services.falkor_graph_service import FalkorGraphService, normalize_letter_code
from ...models.contract_models import ContractSearchRequest
from ...retrieval.dependencies import get_embedding_client, get_llm_generator, get_vector_client
from ...retrieval.models import SearchFilters, SearchRequest, SearchStrategy
from ...retrieval.service import RetrievalService
from ...observability.service import ObservabilityService
from ...services.llm_config_service import (
    DEFAULT_DRAFT_PROMPT_TEMPLATE,
    DEFAULT_PLAN_PROMPT_TEMPLATE,
    LLMConfigService,
)
from ...retrieval.generator import LLMGenerator
from ...config.document_processing_config import DocumentProcessingConfig
from ..tools import coerce_bullets, pick_non_empty, summarise_points

if TYPE_CHECKING:
    from ...models.letter import Letter


DraftCallable = Callable[[LetterDraftRequest, Any], Awaitable[LetterDraftResponse]]


@dataclass
class LetterGraphNodeTrace:
    """Lightweight record of each node execution."""

    name: str
    status: str
    started_at: datetime
    completed_at: datetime
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "data": self.data,
        }


@dataclass
class LetterGraphResult:
    """Outcome snapshot for a LangGraph run."""

    letter_id: str
    run_id: str
    plan: str
    draft: LetterDraftResponse
    status: str
    warnings: List[str]
    trace: List[LetterGraphNodeTrace]
    started_at: datetime
    completed_at: datetime
    summary_points: List[str] = field(default_factory=list)
    context_document_ids: List[str] = field(default_factory=list)
    context_documents: List[Dict[str, Any]] = field(default_factory=list)
    background_summary: List[Dict[str, Any]] = field(default_factory=list)
    graph_thread: List[Dict[str, Any]] = field(default_factory=list)
    sources: List[DraftSource] = field(default_factory=list)
    reviewer_findings: List[DraftReviewFinding] = field(default_factory=list)
    reviewer_blocking: bool = False
    tone_approach: Optional[Dict[str, Any]] = None
    content_structure: Optional[Dict[str, Any]] = None
    specific_responses: List[Dict[str, Any]] = field(default_factory=list)
    risk_mitigation: Optional[Dict[str, Any]] = None
    desired_outcome: Optional[Dict[str, Any]] = None
    requirements_text: Optional[str] = None
    plan_context_text: Optional[str] = None

    def trace_payload(self) -> List[Dict[str, Any]]:
        return [node.to_dict() for node in self.trace]

    def to_storage_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "draft_plan": self.plan,
            "draft_output": self.draft.body,
            "graph_status": self.status,
            "graph_started_at": self.started_at,
            "graph_completed_at": self.completed_at,
            "graph_warnings": list(self.warnings),
            "draft_trace": self.trace_payload(),
            "summary_points": list(self.summary_points),
            "context_document_ids": list(self.context_document_ids),
            "context_documents": list(self.context_documents),
            "background_summary": list(self.background_summary),
            "graph_thread": list(self.graph_thread),
            "draft_sources": [source.model_dump() for source in self.sources],
            "reviewer_findings": [finding.model_dump() for finding in self.reviewer_findings],
            "reviewer_blocking": self.reviewer_blocking,
            "strategy_outline": {
                "tone_approach": self.tone_approach,
                "content_structure": self.content_structure,
                "specific_responses": self.specific_responses,
                "risk_mitigation": self.risk_mitigation,
                "desired_outcome": self.desired_outcome,
            },
            "requirements_text": self.requirements_text,
            "plan_context_text": self.plan_context_text,
        }


class LetterDraftGraph:
    """Executes a deterministic sequence of nodes representing the drafting workflow."""

    def __init__(self, draft_callback: DraftCallable):
        self._draft_callback = draft_callback

    async def run(
        self,
        request: LangGraphDraftRequest,
        current_user: Any,
    ) -> LetterGraphResult:
        if not request.letter_id:
            raise ValueError("letter_id is required for LangGraph drafting")

        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        trace: List[LetterGraphNodeTrace] = []
        warnings: List[str] = []

        db = await get_database()
        letter_service = LetterService(db)
        document_service = DocumentService(db)
        conversation_service = ConversationService(letter_service)
        falkor_service = FalkorGraphService()
        llm_config_service = LLMConfigService(db)
        llm_config = await llm_config_service.get_config()
        drafter_model = llm_config.drafter_model
        reviewer_model = llm_config.reviewer_model
        draft_prompt_template = llm_config.draft_prompt_template or DEFAULT_DRAFT_PROMPT_TEMPLATE
        llm_generator = LLMGenerator(DocumentProcessingConfig(openai_model=drafter_model))
        plan_model = llm_config.plan_model or reviewer_model
        plan_prompt_template = llm_config.plan_prompt_template or DEFAULT_PLAN_PROMPT_TEMPLATE
        plan_generator = LLMGenerator(DocumentProcessingConfig(openai_model=plan_model))

        letter: Optional["Letter"] = None
        related_documents: List[Dict[str, Any]] = []
        document_comments: Dict[str, List[Dict[str, Any]]] = {}
        conversation_chain: List["Letter"] = []
        graph_thread: List[Dict[str, Any]] = []
        selected_document_ids: List[str] = []
        selected_documents: List[Dict[str, Any]] = []
        background_summary: List[Dict[str, Any]] = []
        draft_sources: List[DraftSource] = []
        reviewer_findings: List[DraftReviewFinding] = []
        reviewer_blocking: bool = False
        source_context_lines: List[str] = []
        source_items: List[Dict[str, Any]] = []
        retrieval_service: Optional[RetrievalService] = None
        contract_service = ContractService()
        plan_context_text: str = ""
        requirements_text: str = ""
        selected_graph_nodes: List[Dict[str, Any]] = []

        def _condense_text(value: Optional[str], width: int = 240) -> Optional[str]:
            if not value:
                return None
            normalized = " ".join(str(value).split())
            if not normalized:
                return None
            if len(normalized) <= width:
                return normalized
            return shorten(normalized, width=width, placeholder="...")

        def _build_retrieval_query() -> str:
            pieces: List[str] = []
            if request.subject:
                pieces.append(request.subject)
            if request.points:
                pieces.append(request.points)
            if request.context:
                pieces.append(request.context)
            if letter and getattr(letter, "content", None):
                pieces.append(str(letter.content))
            text = " ".join(piece.strip() for piece in pieces if piece and str(piece).strip())
            if not text:
                return request.subject or "contract correspondence"
            collapsed = " ".join(text.split())
            return collapsed[:800]

        async def _get_retrieval_service() -> RetrievalService:
            nonlocal retrieval_service
            if retrieval_service is None:
                observability = ObservabilityService(db)
                retrieval_service = RetrievalService(
                    db=db,
                    embedding_client=get_embedding_client(),
                    vector_client=get_vector_client(),
                    llm_generator=get_llm_generator(),
                    observability=observability,
                )
            return retrieval_service

        # --- Node: load_state -------------------------------------------------
        async def load_state() -> Dict[str, Any]:
            nonlocal letter
            letter = await letter_service.get_letter(request.letter_id)
            if not letter:
                raise ValueError(f"Letter {request.letter_id} not found")
            return {
                "letter_id": request.letter_id,
                "status": letter.status,
                "subject": letter.subject,
                "project_id": letter.project_id,
            }

        await self._exec_node("load_state", load_state, trace, warnings)

        # --- Node: collect_context -------------------------------------------
        async def collect_context() -> Dict[str, Any]:
            nonlocal related_documents, conversation_chain, document_comments
            nonlocal selected_document_ids, selected_documents, graph_thread
            if not letter:
                return {}

            letter_org = str(getattr(letter, "organization_id", "") or "") or str(
                request.organization_id or ""
            )
            letter_project = str(getattr(letter, "project_id", "") or "") or str(
                request.project_id or ""
            )

            preferred_ids: List[str] = []

            def add_preferred(candidate: Optional[str]) -> None:
                if not candidate:
                    return
                text = str(candidate)
                if text and text not in preferred_ids:
                    preferred_ids.append(text)

            for candidate in request.document_ids or []:
                add_preferred(candidate)

            stored_ids = getattr(letter, "context_document_ids", []) or []
            for candidate in stored_ids:
                add_preferred(candidate)

            if not preferred_ids and request.use_vector_store and letter_org and letter_project:
                try:
                    retrieval_query = _build_retrieval_query()
                    service = await _get_retrieval_service()
                    search_req = SearchRequest(
                        query=retrieval_query,
                        strategy=SearchStrategy.VANILLA,
                        limit=6,
                        filters=SearchFilters(org_id=letter_org, project_id=letter_project),
                        use_enriched_text=True,
                    )
                    search_resp = await service.search(search_req, current_user, log_run=False)
                    for result in search_resp.results:
                        add_preferred(result.document_id)
                    if search_resp.results:
                        warnings.append(
                            "Context documents were auto-selected via semantic retrieval."
                        )
                except Exception as exc:
                    warnings.append(f"semantic_context: {exc}")

            if not preferred_ids:
                fallback_documents = await self._fetch_related_documents(
                    document_service, letter.letter_no
                )
                for record in fallback_documents:
                    doc_id = str(record.get("_id") or record.get("id") or "")
                    if doc_id:
                        add_preferred(doc_id)
                if fallback_documents:
                    warnings.append(
                        "No curated context documents supplied; used linked documents matching the letter number."
                    )

            selected_document_ids = preferred_ids

            documents = await document_service.get_documents_by_ids(selected_document_ids)
            document_index: Dict[str, Document] = {
                str(doc.id): doc for doc in documents
            }

            missing_ids = [doc_id for doc_id in selected_document_ids if doc_id not in document_index]
            if missing_ids:
                warnings.append(
                    "Missing documents were skipped: " + ", ".join(missing_ids)
                )
            selected_document_ids = [
                doc_id for doc_id in selected_document_ids if doc_id in document_index
            ]

            def in_scope(document: Document) -> bool:
                same_org = not letter_org or str(document.organization_id) == letter_org
                same_project = (
                    not letter_project
                    or str(getattr(document, "project_id", "") or "") == letter_project
                )
                return same_org and same_project

            filtered_documents: List[Document] = []
            scope_invalid: List[str] = []
            for doc_id in selected_document_ids:
                doc = document_index.get(doc_id)
                if not doc:
                    continue
                if in_scope(doc):
                    filtered_documents.append(doc)
                else:
                    scope_invalid.append(doc_id)

            if scope_invalid:
                warnings.append(
                    "Context documents outside the letter scope were ignored: "
                    + ", ".join(scope_invalid)
                )

            if not filtered_documents:
                fallback_documents = await self._fetch_related_documents(
                    document_service, letter.letter_no
                )
                fallback_ids: List[str] = []
                for record in fallback_documents:
                    doc_id = str(record.get("_id") or record.get("id") or "")
                    if doc_id:
                        fallback_ids.append(doc_id)
                if fallback_ids:
                    documents = await document_service.get_documents_by_ids(fallback_ids)
                    document_index = {str(doc.id): doc for doc in documents}
                    filtered_documents = [doc for doc in documents if in_scope(doc)]
                    if filtered_documents:
                        warnings.append(
                            "Fell back to documents sharing the letter number because validated context was unavailable."
                        )
                        selected_document_ids = [str(doc.id) for doc in filtered_documents]
                    else:
                        selected_document_ids = []
                        document_index = {}
                else:
                    selected_document_ids = []
                    document_index = {}
            else:
                selected_document_ids = [str(doc.id) for doc in filtered_documents]
                document_index = {str(doc.id): doc for doc in filtered_documents}

            related_documents = []
            document_comments = {}
            for doc in filtered_documents:
                doc_id = str(doc.id)
                try:
                    comments = await document_service.get_comments(doc_id)
                except Exception as exc:
                    warnings.append(f"collect_context: comment load failed for {doc_id}: {exc}")
                    comments = []
                if comments:
                    document_comments[doc_id] = comments
                related_documents.append(
                    {
                        "id": doc_id,
                        "letterNo": doc.letterNo,
                        "subject": doc.subject,
                        "uploadType": doc.uploadType,
                        "summary": doc.summary,
                        "keywords": doc.keywords or [],
                        "date": doc.date.isoformat() if doc.date else None,
                    }
                )
            selected_documents = related_documents

            conversation_chain = await conversation_service.get_conversation_chain(
                request.letter_id, current_user=current_user
            )

            if falkor_service.enabled:
                target_code = (
                    getattr(letter, "letter_no", None)
                    or getattr(getattr(letter, "reference", None), "reference_number", None)
                )
                if target_code:
                    try:
                        graph_thread = falkor_service.get_thread(target_code, depth=6)
                    except Exception as exc:
                        warnings.append(f"graph_context: {exc}")
                        graph_thread = []

            # Apply include/exclude linked letter codes against graph thread and conversation chain
            include_codes = {normalize_letter_code(str(code)) for code in request.include_letter_codes or [] if code}
            exclude_codes = {normalize_letter_code(str(code)) for code in request.exclude_letter_codes or [] if code}

            filtered_thread: List[Dict[str, Any]] = []
            seen_norms: Set[str] = set()
            for node in graph_thread or []:
                code = node.get("normCode") or normalize_letter_code(str(node.get("code") or ""))
                if not code:
                    continue
                if code in exclude_codes:
                    continue
                if code in seen_norms:
                    continue
                seen_norms.add(code)
                filtered_thread.append(node)

            for code in include_codes:
                if code and code not in seen_norms:
                    filtered_thread.append(
                        {
                            "code": code,
                            "normCode": code,
                            "subject": "Linked letter (manual)",
                            "direction": "unknown",
                        }
                    )
                    seen_norms.add(code)

            graph_thread = filtered_thread
            selected_graph_nodes = filtered_thread

            def _norm_letter_code(value: Any) -> str:
                try:
                    return normalize_letter_code(str(value))
                except Exception:
                    return ""

            if exclude_codes:
                conversation_chain = [
                    entry
                    for entry in conversation_chain
                    if _norm_letter_code(getattr(entry, "letter_no", None)) not in exclude_codes
                ]

            return {
                "documents": list(related_documents),
                "documents_with_comments": [
                    {
                        "id": doc_id,
                        "comment_count": len(comments),
                    }
                    for doc_id, comments in document_comments.items()
                ],
                "conversation_depth": len(conversation_chain),
                "graph_thread": list(graph_thread),
                "graph_thread_nodes": len(graph_thread),
            }

        await self._exec_node("collect_context", collect_context, trace, warnings)

        # --- Node: retrieve_sources -----------------------------------------
        async def retrieve_sources() -> Dict[str, Any]:
            nonlocal draft_sources, source_context_lines, source_items
            if not letter:
                return {}

            try:
                draft_sources = []
                source_context_lines = []
                source_items = []
                now_iso = datetime.now(timezone.utc).isoformat()

                letter_org = str(getattr(letter, "organization_id", "") or "") or str(
                    request.organization_id or ""
                )
                letter_project = str(getattr(letter, "project_id", "") or "") or str(
                    request.project_id or ""
                )

                retrieval_query = _build_retrieval_query()

                # Contract clause retrieval
                clause_count = 0
                if request.use_vector_store and letter_org and letter_project:
                    try:
                        contract_req = ContractSearchRequest(
                            query=retrieval_query,
                            organization_id=letter_org,
                            project_id=letter_project,
                            limit=6,
                            top_docs=4,
                            chunks_per_doc=2,
                            summarize=False,
                        )
                        contract_resp = await contract_service.search_contracts(
                            contract_req, current_user
                        )
                        for chunk in contract_resp.results:
                            clause_number = chunk.clause_number or "Clause"
                            clause_title = chunk.clause_title or ""
                            label = f"{clause_number} {clause_title}".strip()
                            snippet = _condense_text(chunk.text, 280)
                            doc_id = chunk.document_id or chunk.upload_id
                            page_numbers = (
                                chunk.page_numbers
                                if chunk.page_numbers
                                else ([chunk.page] if chunk.page else [])
                            )
                            source_id = (
                                f"{doc_id or 'contract'}::{clause_number}::{chunk.chunk_index or 0}"
                            )
                            draft_sources.append(
                                DraftSource(
                                    id=source_id,
                                    source_type="contract_clause",
                                    label=label or "Contract clause",
                                    snippet=snippet,
                                    document_id=str(doc_id) if doc_id else None,
                                    clause_number=chunk.clause_number,
                                    clause_title=chunk.clause_title,
                                    page_numbers=[int(p) for p in page_numbers if p],
                                    score=chunk.score,
                                    metadata={
                                        "upload_id": chunk.upload_id,
                                        "file_name": chunk.file_name or chunk.source_filename,
                                        "file_path": chunk.file_path or chunk.source_file,
                                    },
                                )
                            )
                            context_line = f"{label}: {snippet}" if snippet else label
                            if context_line:
                                source_context_lines.append(context_line)
                            source_items.append(
                                {
                                    "id": source_id,
                                    "text": context_line or label or "Contract clause",
                                    "documents": [],
                                    "type": "clause",
                                    "generated_at": now_iso,
                                }
                            )
                        clause_count = len(contract_resp.results)
                    except Exception as exc:
                        warnings.append(f"contract_retrieval: {exc}")

                # Correspondence sources from vector/keyword retrieval, conversation chain, and related letters
                letter_sources = []
                seen_ids: Set[str] = set()

                if letter_org and letter_project:
                    try:
                        service = await _get_retrieval_service()
                        hybrid_req = SearchRequest(
                            query=retrieval_query,
                            strategy=SearchStrategy.RAG_FUSION,
                            limit=6,
                            filters=SearchFilters(
                                org_id=letter_org,
                                project_id=letter_project,
                                metadata={"doc_type": "letter"},
                            ),
                            use_enriched_text=True,
                        )
                        hybrid_resp = await service.search(hybrid_req, current_user, log_run=False)
                        for res in hybrid_resp.results:
                            doc_id = str(res.document_id or "")
                            if not doc_id or doc_id in seen_ids:
                                continue
                            seen_ids.add(doc_id)
                            payload = res.payload or {}
                            label = (
                                payload.get("title")
                                or payload.get("subject")
                                or payload.get("letterNo")
                                or "Related letter"
                            )
                            letter_no = payload.get("letterNo")
                            if letter_no:
                                label = f"{label} ({letter_no})"
                            snippet = _condense_text(res.snippet, 280)
                            page_value = payload.get("page")
                            letter_sources.append(
                                DraftSource(
                                    id=f"{doc_id}::chunk::{payload.get('chunk_id') or res.chunk_id}",
                                    source_type="letter",
                                    label=label,
                                    snippet=snippet,
                                    letter_id=doc_id,
                                    metadata={
                                        "letter_no": letter_no,
                                        "page": page_value,
                                        "chunk_id": payload.get("chunk_id") or res.chunk_id,
                                    },
                                )
                            )
                            if snippet:
                                source_context_lines.append(f"{label}: {snippet}")
                                source_items.append(
                                    {
                                        "id": f"{doc_id}::{payload.get('chunk_id') or res.chunk_id}",
                                        "text": f"{label}: {snippet}",
                                        "documents": [doc_id],
                                        "type": "letter",
                                        "generated_at": now_iso,
                                    }
                                )
                    except Exception as exc:
                        warnings.append(f"letter_hybrid_retrieval: {exc}")
                for entry in conversation_chain:
                    entry_id = getattr(entry, "id", None)
                    if not entry_id:
                        continue
                    if letter and entry_id == getattr(letter, "id", None):
                        continue
                    if entry_id in seen_ids:
                        continue
                    seen_ids.add(entry_id)
                    summary_source = pick_non_empty(
                        [
                            getattr(entry, "summary", None),
                            getattr(entry, "content", None),
                            getattr(entry, "subject", None),
                        ]
                    )
                    snippet = _condense_text(summary_source, 280)
                    letter_no = getattr(entry, "letter_no", None)
                    label = getattr(entry, "subject", None) or "Related letter"
                    if letter_no:
                        label = f"{label} ({letter_no})"
                    date_value = getattr(entry, "date", None)
                    date_label = date_value.isoformat() if isinstance(date_value, datetime) else date_value
                    letter_sources.append(
                        DraftSource(
                            id=str(entry_id),
                            source_type="letter",
                            label=label,
                            snippet=snippet,
                            letter_id=str(entry_id),
                            metadata={
                                "letter_no": letter_no,
                                "date": date_label,
                            },
                        )
                    )
                    if snippet:
                        source_context_lines.append(f"{label}: {snippet}")
                        source_items.append(
                            {
                                "id": str(entry_id),
                                "text": f"{label}: {snippet}",
                                "documents": [],
                                "type": "letter",
                                "generated_at": now_iso,
                            }
                        )

                if not letter_sources and letter_org:
                    try:
                        related = await letter_service.get_letters(
                            limit=5,
                            filters={
                                "organization_id": letter_org,
                                "project_id": letter_project or None,
                            },
                            search_query=request.subject or getattr(letter, "subject", ""),
                        )
                        for entry in related:
                            if not entry or not getattr(entry, "id", None):
                                continue
                            entry_id = str(entry.id)
                            if entry_id in seen_ids:
                                continue
                            seen_ids.add(entry_id)
                            summary_source = pick_non_empty(
                                [
                                    getattr(entry, "summary", None),
                                    getattr(entry, "content", None),
                                    getattr(entry, "subject", None),
                                ]
                            )
                            snippet = _condense_text(summary_source, 280)
                            label = getattr(entry, "subject", None) or "Related letter"
                            date_value = getattr(entry, "date", None)
                            date_label = date_value.isoformat() if isinstance(date_value, datetime) else date_value
                            letter_sources.append(
                                DraftSource(
                                    id=entry_id,
                                    source_type="letter",
                                    label=label,
                                    snippet=snippet,
                                    letter_id=entry_id,
                                    metadata={
                                        "letter_no": getattr(entry, "letter_no", None),
                                        "date": date_label,
                                    },
                                )
                            )
                            if snippet:
                                source_context_lines.append(f"{label}: {snippet}")
                                source_items.append(
                                    {
                                        "id": entry_id,
                                        "text": f"{label}: {snippet}",
                                        "documents": [],
                                        "type": "letter",
                                        "generated_at": now_iso,
                                    }
                                )
                    except Exception as exc:
                        warnings.append(f"letter_retrieval: {exc}")

                # Context document sources (for curated selections)
                for doc in selected_documents:
                    doc_id = str(doc.get("id") or "")
                    if not doc_id:
                        continue
                    label = pick_non_empty([doc.get("subject"), doc.get("letterNo")]) or "Context document"
                    snippet = _condense_text(doc.get("summary"), 280)
                    source_id = f"{doc_id}::context"
                    draft_sources.append(
                        DraftSource(
                            id=source_id,
                            source_type="context_document",
                            label=label,
                            snippet=snippet,
                            document_id=doc_id,
                            metadata={
                                "upload_type": doc.get("uploadType"),
                                "keywords": doc.get("keywords") or [],
                            },
                        )
                    )
                    if snippet:
                        source_context_lines.append(f"{label}: {snippet}")
                        source_items.append(
                            {
                                "id": source_id,
                                "text": f"{label}: {snippet}",
                                "documents": [doc_id],
                                "type": "context_document",
                                "generated_at": now_iso,
                            }
                        )

                draft_sources.extend(letter_sources)
                return {
                    "clause_sources": clause_count,
                    "letter_sources": len(letter_sources),
                    "total_sources": len(draft_sources),
                }
            except Exception as exc:
                warnings.append(f"source_retrieval: {exc}")
                return {"clause_sources": 0, "letter_sources": 0, "total_sources": 0}

        await self._exec_node("retrieve_sources", retrieve_sources, trace, warnings)

        # --- Node: plan_response ---------------------------------------------
        summary_points: List[str] = []

        async def plan_response() -> Dict[str, Any]:
            nonlocal summary_points, background_summary, plan_context_text, requirements_text
            subject = letter.subject if letter else request.subject
            recipient = (letter.recipient if letter else request.recipient) or "the counterparty"

            keyword_points = (letter.keywords or []) if letter else []
            additional_context: List[str] = []
            if request.points:
                additional_context.extend(
                    line.strip()
                    for line in request.points.splitlines()
                    if line.strip()
                )
            if request.context:
                additional_context.append(
                    f"Context provided by drafter: {request.context.strip()}"
                )
            if source_context_lines:
                additional_context.extend(source_context_lines)

            comment_items: List[Dict[str, Any]] = []
            if document_comments and selected_documents:
                for doc in selected_documents:
                    doc_id = str(doc.get("id") or "")
                    if not doc_id:
                        continue
                    comments_for_doc = document_comments.get(doc_id, [])
                    if not comments_for_doc:
                        continue
                    doc_label = (
                        doc.get("subject")
                        or doc.get("letterNo")
                        or doc_id
                    )
                    for comment in comments_for_doc[-5:]:
                        text = (comment.get("text") or "").strip()
                        if not text:
                            continue
                        snippet = text if len(text) <= 280 else f"{text[:277]}…"
                        author_name = (
                            comment.get("authorName")
                            or comment.get("author")
                            or comment.get("authorEmail")
                            or comment.get("authorId")
                            or "Reviewer"
                        )
                        created_at = comment.get("createdAt")
                        created_label: Optional[str] = None
                        if isinstance(created_at, datetime):
                            created_label = created_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
                        elif isinstance(created_at, str) and created_at:
                            created_label = created_at[:10]
                        context_line = f"Comment from {author_name}"
                        if created_label:
                            context_line += f" on {created_label}"
                        context_line += f" about '{doc_label}': {snippet}"
                        additional_context.append(context_line)
                        comment_items.append(
                            {
                                "id": str(uuid.uuid4()),
                                "text": context_line,
                                "documents": [doc_id],
                                "type": "comment",
                            }
                        )

            # Incorporate role-specific contexts if available
            contractor_ctx = getattr(letter, "contractor_context", None)
            engineer_ctx = getattr(letter, "engineer_context", None)
            employer_ctx = getattr(letter, "employer_context", None)
            for ctx in [contractor_ctx, engineer_ctx, employer_ctx]:
                if ctx:
                    additional_context.append(ctx)

            base_summary_points = summarise_points(subject, keyword_points, additional_context)

            def build_previous_context_lines() -> List[str]:
                if not conversation_chain:
                    return []
                candidates = [
                    entry
                    for entry in conversation_chain
                    if not letter or entry.id != getattr(letter, "id", None)
                ]
                if not candidates:
                    return []
                lines: List[str] = []
                for entry in candidates[-5:]:
                    summary_source = pick_non_empty(
                        [
                            getattr(entry, "summary", None),
                            getattr(entry, "content", None),
                            getattr(entry, "subject", None),
                        ]
                    )
                    snippet = _condense_text(summary_source) or "Summary unavailable"
                    letter_no = getattr(entry, "letter_no", None)
                    subject_label = getattr(entry, "subject", None) or "Previous correspondence"
                    date_value = getattr(entry, "date", None) or getattr(entry, "created_at", None)
                    if isinstance(date_value, datetime):
                        date_label = date_value.strftime("%Y-%m-%d")
                    else:
                        date_label = "Undated"
                    reference_label = f"{subject_label}"
                    if letter_no:
                        reference_label += f" ({letter_no})"
                    lines.append(f"{date_label} - {reference_label}: {snippet}")
                return lines

            def build_document_context_lines() -> List[str]:
                lines: List[str] = []
                for doc in selected_documents[:5]:
                    ref = pick_non_empty([doc.get("letterNo"), doc.get("subject")]) or "Context document"
                    snippet = _condense_text(doc.get("summary")) or ""
                    if snippet:
                        lines.append(f"{ref}: {snippet}")
                    else:
                        lines.append(ref)
                return lines

            previous_context_lines = build_previous_context_lines()
            document_context_lines = build_document_context_lines()
            context_block_lines = previous_context_lines + document_context_lines
            context_block = (
                "\n".join(context_block_lines)
                if context_block_lines
                else "No previous letters or linked documents were available for context."
            )

            requirement_lines: List[str] = []
            if request.context:
                condensed = _condense_text(request.context, 320)
                if condensed:
                    requirement_lines.append(condensed)
            if request.points:
                requirement_lines.append(request.points.strip())
            elif letter and letter.content:
                fallback_req = _condense_text(letter.content, 320)
                if fallback_req:
                    requirement_lines.append(fallback_req)
            requirements_block = (
                "\n".join(line for line in requirement_lines if line)
                if requirement_lines
                else "No explicit requirements provided; seek clarification from the requesting team."
            )
            plan_context_text = context_block
            requirements_text = requirements_block

            combined_analyser = f"{requirements_block}\n{context_block}".lower()
            if any(keyword in combined_analyser for keyword in ("breach", "default", "penalty", "non-compliance", "escalate")):
                overall_tone = "Firm and compliance-focused"
            elif any(keyword in combined_analyser for keyword in ("collaborat", "support", "clarify", "coordinate")):
                overall_tone = "Cooperative and solution-oriented"
            elif any(keyword in combined_analyser for keyword in ("urgent", "immediate", "delay", "overdue")):
                overall_tone = "Firm with urgency on timelines"
            else:
                overall_tone = "Professional and neutral with fact-led narration"

            tone_section = {
                "overall_tone": overall_tone,
                "key_messaging_strategy": f"Anchor the response on '{subject}' while maintaining continuity with earlier correspondence.",
                "relationship_management_approach": "Maintain transparency, cite evidence, and invite constructive engagement without conceding contractual rights.",
            }

            fallback_points = [
                "Reconfirm the objective and cite last confirmed commitments.",
                "Address each outstanding issue with evidence from prior letters.",
                "Highlight contractual expectations and consequences for non-compliance.",
                "Outline the requested action, timeline, and supporting documents.",
                "Propose the follow-up mechanism (meeting, written confirmation, or escalation).",
            ]
            key_points_order = base_summary_points[:5] or fallback_points
            summary_points = list(key_points_order)

            contractual_references: List[str] = []
            for doc in selected_documents:
                summary = doc.get("summary") or ""
                if "clause" in summary.lower():
                    snippet = _condense_text(summary, 160) or summary.strip()
                    if snippet:
                        contractual_references.append(snippet)
                elif doc.get("letterNo"):
                    contractual_references.append(
                        f"Refer to letter {doc['letterNo']} ({doc.get('subject') or 'context document'})"
                    )
                if len(contractual_references) >= 5:
                    break
            if draft_sources:
                for source in draft_sources:
                    if source.source_type != "contract_clause":
                        continue
                    clause_label = source.clause_number or ""
                    clause_title = source.clause_title or ""
                    label = f"Clause {clause_label} {clause_title}".strip()
                    if label and label not in contractual_references:
                        contractual_references.append(label)
                    if len(contractual_references) >= 7:
                        break
            if not contractual_references:
                contractual_references.append(
                    "Reiterate relevant clauses from the primary contract and any issued deviation notices."
                )

            latest_reference = previous_context_lines[-1] if previous_context_lines else "the latest correspondence"
            opening_strategy = (
                f"Acknowledge receipt of {latest_reference} and restate the purpose of '{subject}'."
            )
            closing_approach = (
                f"Close by confirming the expected response from {recipient}, reference attached evidence, and outline escalation if timelines slip."
            )
            content_section = {
                "opening_strategy": opening_strategy,
                "key_points_order": key_points_order,
                "contractual_references": contractual_references,
                "closing_approach": closing_approach,
            }

            evidence_refs = [
                pick_non_empty([doc.get("letterNo"), doc.get("subject")]) or doc.get("id") or "Context document"
                for doc in selected_documents
            ]
            if not evidence_refs:
                evidence_refs = ["Conversation history", "Project records"]

            specific_responses: List[Dict[str, Any]] = []
            basis_cycle = contractual_references or ["Reiterate contractual baseline obligations."]
            for idx, point in enumerate(summary_points[:5] or fallback_points[:3]):
                evidence = evidence_refs[idx % len(evidence_refs)]
                contractual_basis = basis_cycle[idx % len(basis_cycle)]
                specific_responses.append(
                    {
                        "contractor_point": point,
                        "response_strategy": f"Address '{point}' with facts from the collected context and restate the project's position.",
                        "evidence_references": [evidence] if evidence else [],
                        "contractual_basis": contractual_basis,
                    }
                )

            risk_section = {
                "legal_risks": [
                    "Avoid implying waiver of core contractual rights without proper approval.",
                    "Ensure any commitment aligns with documented clauses and authority thresholds.",
                ],
                "relationship_risks": [
                    "Balance firmness with acknowledgement of the counterpart's constraints.",
                    "Prevent tone escalation by referencing shared project goals.",
                ],
                "project_impact_considerations": [
                    "Track schedule impact and communicate delays to stakeholders promptly.",
                    "Monitor financial exposure (advances, penalties, or claims) resulting from the decision.",
                ],
            }
            if contractual_references:
                risk_section["legal_risks"].append(
                    f"Quote {contractual_references[0]} precisely to avoid misinterpretation."
                )

            desired_outcome = {
                "immediate_action": f"Obtain written acknowledgement from {recipient} agreeing to the outlined next steps for '{subject}'.",
                "next_steps": [
                    "Share the strategic plan with internal reviewers and capture their comments.",
                    "Schedule a clarification call (if needed) to walk through outstanding points.",
                    "Track action items in the workflow tool with accountable owners.",
                ],
                "fallback_positions": [
                    "Escalate unresolved items to the project steering committee.",
                    "Offer phased compliance or conditional approvals if commercially acceptable.",
                ],
            }

            structured_lines: List[str] = []
            structured_lines.append("1. **TONE & APPROACH**")
            structured_lines.append(f"   - Overall tone: {tone_section['overall_tone']}")
            structured_lines.append(f"   - Key messaging strategy: {tone_section['key_messaging_strategy']}")
            structured_lines.append(f"   - Relationship management approach: {tone_section['relationship_management_approach']}")
            structured_lines.append("")
            structured_lines.append("2. **CONTENT STRUCTURE**")
            structured_lines.append(f"   - Opening paragraph strategy: {content_section['opening_strategy']}")
            if content_section["key_points_order"]:
                structured_lines.append("   - Key points to address (in order):")
                for idx, point in enumerate(content_section["key_points_order"], start=1):
                    structured_lines.append(f"     {idx}. {point}")
            if content_section["contractual_references"]:
                structured_lines.append("   - Contractual references to include:")
                for ref in content_section["contractual_references"]:
                    structured_lines.append(f"     - {ref}")
            structured_lines.append(f"   - Closing approach: {content_section['closing_approach']}")
            structured_lines.append("")
            structured_lines.append("3. **SPECIFIC RESPONSES** to contractor's points")
            for response in specific_responses:
                structured_lines.append(f"   - Point: {response['contractor_point']}")
                structured_lines.append(f"     - Response strategy: {response['response_strategy']}")
                if response["evidence_references"]:
                    refs_line = ", ".join(response["evidence_references"])
                    structured_lines.append(f"     - Evidence/references: {refs_line}")
                structured_lines.append(f"     - Contractual basis: {response['contractual_basis']}")
            structured_lines.append("")
            structured_lines.append("4. **RISK MITIGATION**")
            structured_lines.append("   - Legal/contractual risks to avoid:")
            for risk in risk_section["legal_risks"]:
                structured_lines.append(f"     - {risk}")
            structured_lines.append("   - Relationship risks to manage:")
            for risk in risk_section["relationship_risks"]:
                structured_lines.append(f"     - {risk}")
            structured_lines.append("   - Project impact considerations:")
            for risk in risk_section["project_impact_considerations"]:
                structured_lines.append(f"     - {risk}")
            structured_lines.append("")
            structured_lines.append("5. **DESIRED OUTCOME**")
            structured_lines.append(f"   - Immediate action required: {desired_outcome['immediate_action']}")
            structured_lines.append("   - Next steps:")
            for step in desired_outcome["next_steps"]:
                structured_lines.append(f"     - {step}")
            structured_lines.append("   - Fallback positions:")
            for fallback in desired_outcome["fallback_positions"]:
                structured_lines.append(f"     - {fallback}")

            plan_body = "\n".join(structured_lines)
            plan_text = plan_body

            # If user provided a plan override, short-circuit here
            if request.plan_override:
                plan_text = request.plan_override.strip()
            else:
                # Attempt LLM-based plan (Grok or configured model) using the structured context
                llm_plan_prompt = plan_prompt_template.format(
                    subject=subject,
                    recipient=recipient,
                    role=(getattr(letter, "strategy_role", None) or "contractor"),
                    contractor_context=contractor_ctx or "N/A",
                    engineer_context=engineer_ctx or "N/A",
                    employer_context=employer_ctx or "N/A",
                    requirements=requirements_block,
                    linked_letters=", ".join(
                        [str(node.get("code") or node.get("normCode")) for node in selected_graph_nodes]
                    )
                    or "None",
                    sources="\n".join(source_context_lines[:6]) or "None",
                )
                try:
                    llm_plan = await plan_generator.generate(
                        llm_plan_prompt, max_tokens=1800, model=plan_model
                    )
                    if llm_plan:
                        plan_text = llm_plan
                except Exception as exc:  # pragma: no cover - defensive
                    warnings.append(f"plan_llm: {exc}")

            document_items: List[Dict[str, Any]] = []
            now_iso = datetime.now(timezone.utc).isoformat()
            for doc in selected_documents:
                doc_text = doc.get("summary") or doc.get("subject") or doc.get("letterNo")
                if not doc_text:
                    continue
                document_items.append(
                    {
                        "id": str(uuid.uuid4()),
                        "text": doc_text,
                        "documents": [doc.get("id")],
                        "type": "document",
                        "generated_at": now_iso,
                    }
                )

            summary_items = [
                {
                    "id": str(uuid.uuid4()),
                    "text": point,
                    "documents": [doc.get("id") for doc in selected_documents if doc.get("id")],
                    "type": "summary",
                    "generated_at": now_iso,
                }
                for point in summary_points
            ]

            party_items: List[Dict[str, Any]] = []
            role_contexts = [
                ("contractor", contractor_ctx),
                ("engineer", engineer_ctx),
                ("employer", employer_ctx),
            ]
            for role, ctx in role_contexts:
                condensed = _condense_text(ctx, 200)
                if condensed:
                    summary_points.append(f"{role.title()} view: {condensed}")
            for role, ctx in role_contexts:
                condensed = _condense_text(ctx, 280)
                if not condensed:
                    continue
                party_items.append(
                    {
                        "id": str(uuid.uuid4()),
                        "text": f"{role.title()} perspective: {condensed}",
                        "type": "party_context",
                        "generated_at": now_iso,
                    }
                )

            thread_items: List[Dict[str, Any]] = []
            for node in graph_thread or []:
                code = node.get("normCode") or node.get("code")
                subject_label = node.get("subject") or "Linked letter"
                text = f"{code}: {subject_label}" if code else subject_label
                thread_items.append(
                    {
                        "id": str(uuid.uuid4()),
                        "text": text,
                        "type": "graph_thread",
                        "documents": [],
                        "generated_at": now_iso,
                    }
                )

            background_summary = (
                party_items
                + document_items
                + comment_items
                + source_items
                + thread_items
                + summary_items
            )
            return {
                "plan": plan_text,
                "tone_approach": tone_section,
                "content_structure": content_section,
                "specific_responses": specific_responses,
                "risk_mitigation": risk_section,
                "desired_outcome": desired_outcome,
                "context_text": context_block,
                "requirements_text": requirements_block,
            }

        plan_payload = await self._exec_node(
            "plan_response", plan_response, trace, warnings
        )
        plan = plan_payload.get("plan", "")
        tone_approach = plan_payload.get("tone_approach")
        content_structure = plan_payload.get("content_structure")
        specific_responses = plan_payload.get("specific_responses") or []
        risk_mitigation = plan_payload.get("risk_mitigation")
        desired_outcome = plan_payload.get("desired_outcome")
        plan_context_text = plan_payload.get("context_text") or ""
        requirements_text = plan_payload.get("requirements_text") or ""

        if request.analysis_only:
            completed_at = datetime.now(timezone.utc)
            draft = LetterDraftResponse(
                subject=request.subject,
                body="",
                key_points=[],
            )
            return LetterGraphResult(
                letter_id=request.letter_id,
                run_id=run_id,
                plan=plan,
                draft=draft,
                status="analysis_only",
                warnings=warnings,
                trace=trace,
                started_at=started_at,
                completed_at=completed_at,
                summary_points=summary_points,
                graph_thread=list(graph_thread),
                context_document_ids=list(selected_document_ids),
                context_documents=list(selected_documents),
                background_summary=list(background_summary),
                sources=list(draft_sources),
                reviewer_findings=list(reviewer_findings),
                reviewer_blocking=reviewer_blocking,
                tone_approach=tone_approach,
                content_structure=content_structure,
                specific_responses=list(specific_responses),
                risk_mitigation=risk_mitigation,
                desired_outcome=desired_outcome,
                requirements_text=requirements_text,
                plan_context_text=plan_context_text,
            )

        # --- Node: draft_letter ----------------------------------------------
        def _format_sources_block(sources: List[DraftSource]) -> str:
            if not sources:
                return ""
            lines: List[str] = []
            for idx, source in enumerate(sources, start=1):
                source_type = source.source_type.replace("_", " ")
                label = source.label or source_type
                snippet = source.snippet or ""
                clause = source.clause_number or ""
                prefix = f"[S{idx}] {source_type}: {label}"
                if clause:
                    prefix += f" (Clause {clause})"
                if snippet:
                    prefix += f": {snippet}"
                lines.append(prefix.strip())
            return "\n".join(lines[:20])

        def _normalise_sender_profile(value: Optional[str]) -> str:
            if not value:
                return "Contractor"
            lowered = str(value).strip().lower()
            if lowered.startswith("engineer"):
                return "Engineer"
            if lowered.startswith("employer"):
                return "Employer"
            return "Contractor"

        def _profile_patterns(profile: str) -> str:
            if profile == "Employer":
                return (
                    "Posture: authoritative, governance-oriented, compliance-focused.\n"
                    "Typical flow: reference to the matter; clear decision or instruction; contractual basis; "
                    "expectations on time, quality, and process; consequences of non-compliance.\n"
                    "Tone: direct, procedural, risk-allocating, controlling yet reasonable; avoid aggressive language."
                )
            if profile == "Engineer":
                return (
                    "Posture: neutral, procedurally precise, analytically structured.\n"
                    "Typical flow: background and submissions received; contractual analysis; reasoning; determination or decision.\n"
                    "Tone: impartial, transparent, rooted in contractual criteria; distinguish submissions from determinations."
                )
            return (
                "Posture: protective, commercially aware, entitlement-focused.\n"
                "Typical flow: factual chronology; contractual basis; impact description; entitlement position; reservation of rights.\n"
                "Tone: firm and forward-looking on entitlements without overstating certainty; use without prejudice, "
                "reserves all rights, and subject to further particulars where supported."
            )

        def _active_workspace_label() -> str:
            org_id = (getattr(letter, "organization_id", None) if letter else None) or request.organization_id
            project_id = (getattr(letter, "project_id", None) if letter else None) or request.project_id
            letter_no = getattr(letter, "letter_no", None) if letter else None
            parts = [
                f"organization={org_id or 'not provided'}",
                f"project={project_id or 'not provided'}",
                f"letter_id={request.letter_id}",
            ]
            if letter_no:
                parts.append(f"letter_no={letter_no}")
            return "; ".join(parts)

        def _format_prior_correspondence() -> str:
            if not conversation_chain:
                return "No prior same-workspace correspondence was available."
            candidates = [
                entry
                for entry in conversation_chain
                if not letter or entry.id != getattr(letter, "id", None)
            ]
            if not candidates:
                return "No prior same-workspace correspondence was available."
            lines: List[str] = []
            for idx, entry in enumerate(candidates[-5:], start=1):
                summary_source = pick_non_empty(
                    [
                        getattr(entry, "summary", None),
                        getattr(entry, "content", None),
                        getattr(entry, "subject", None),
                    ]
                )
                snippet = _condense_text(summary_source, 360) or "Summary unavailable"
                letter_no = getattr(entry, "letter_no", None)
                subject_label = getattr(entry, "subject", None) or "Previous correspondence"
                date_value = getattr(entry, "date", None) or getattr(entry, "created_at", None)
                if isinstance(date_value, datetime):
                    date_label = date_value.strftime("%Y-%m-%d")
                else:
                    date_label = str(date_value or "undated")
                reference_label = subject_label
                if letter_no:
                    reference_label += f" ({letter_no})"
                lines.append(f"[P{idx}] {date_label} - {reference_label}: {snippet}")
            return "\n".join(lines)

        async def draft_letter() -> Dict[str, Any]:
            augmented_context = request.context or ""
            plan_block = f"\n\nDrafting Plan:\n{plan}".strip()
            if plan_block not in augmented_context:
                augmented_context = f"{augmented_context}\n\n{plan_block}".strip()

            combined_points = request.points or ""
            if source_context_lines:
                sources_block = "\n".join(
                    f"- {line}" for line in source_context_lines[:8]
                )
                if sources_block:
                    combined_points = (
                        f"{combined_points}\n{sources_block}".strip()
                        if combined_points
                        else sources_block
                    )
                    augmented_context = (
                        f"{augmented_context}\n\nSources:\n{sources_block}".strip()
                        if augmented_context
                        else f"Sources:\n{sources_block}"
                    )

            sources_block_full = _format_sources_block(draft_sources)
            prompt_context = augmented_context.strip() or requirements_text
            sender_profile = _normalise_sender_profile(
                getattr(letter, "strategy_role", None) if letter else None
            )
            if sender_profile == "Engineer" and getattr(letter, "strategy_recipient", None):
                sender_profile = (
                    f"{sender_profile} (recipient focus: {getattr(letter, 'strategy_recipient')})"
                )
            prompt_payload = {
                "subject": request.subject,
                "recipient": request.recipient or "the counterparty",
                "sender_profile": sender_profile,
                "active_contract_workspace": _active_workspace_label(),
                "plan": plan or plan_context_text or "Use the provided context to outline the response.",
                "requirements": prompt_context or "No explicit requirements provided.",
                "sources": sources_block_full or "No sources available; highlight need for evidence.",
                "prior_correspondence": _format_prior_correspondence(),
                "profile_patterns": _profile_patterns(sender_profile.split(" ", 1)[0]),
            }

            try:
                prompt_text = draft_prompt_template.format(**prompt_payload)
            except Exception:
                prompt_text = f"{draft_prompt_template}\n\n{prompt_payload}"

            llm_body = ""
            try:
                llm_body = await llm_generator.generate(
                    prompt_text,
                    max_tokens=1200,
                    model=drafter_model,
                )
            except Exception as exc:  # pragma: no cover - defensive
                warnings.append(f"drafter_llm: {exc}")

            if not llm_body:
                draft_request = LetterDraftRequest(
                    subject=request.subject,
                    recipient=request.recipient,
                    context=augmented_context,
                    points=combined_points or None,
                    user_id=request.user_id,
                    organization_id=request.organization_id,
                    project_id=request.project_id,
                    document_ids=selected_document_ids or request.document_ids,
                    use_vector_store=request.use_vector_store,
                )
                fallback = await self._draft_callback(draft_request, current_user)
                llm_body = fallback.body

            return {
                "subject": request.subject,
                "body": llm_body,
                "key_points": summary_points or [],
            }

        draft_payload = await self._exec_node(
            "draft_letter", draft_letter, trace, warnings
        )
        draft = LetterDraftResponse(
            subject=draft_payload.get("subject", request.subject),
            body=draft_payload.get("body", ""),
            key_points=coerce_bullets(draft_payload.get("key_points", [])),
        )

        # --- Node: review_draft ---------------------------------------------
        reviewer_blocking = False

        async def review_draft() -> Dict[str, Any]:
            nonlocal reviewer_findings, reviewer_blocking
            reviewer_findings = []
            reviewer_blocking = False

            if not draft.body:
                return {"finding_count": 0, "blocking": False}

            clause_pattern = re.compile(
                r"\b(?:clause|section|sub-clause|subclause|article)\s+([0-9A-Za-z.\-()]+)",
                re.IGNORECASE,
            )
            mentioned_clauses = {
                match.group(1).lower().strip()
                for match in clause_pattern.finditer(draft.body)
                if match.group(1)
            }

            source_clauses = {
                (source.clause_number or "").lower().strip()
                for source in draft_sources
                if source.source_type == "contract_clause" and source.clause_number
            }

            missing = sorted([c for c in mentioned_clauses if c and c not in source_clauses])
            if missing:
                reviewer_findings.append(
                    DraftReviewFinding(
                        level="error",
                        message="Draft references clauses that were not retrieved in sources.",
                        evidence=", ".join(missing[:8]),
                    )
                )

            if not draft_sources:
                reviewer_findings.append(
                    DraftReviewFinding(
                        level="error",
                        message="No sources were retrieved for this draft; verify factual grounding before review.",
                    )
                )

            if re.search(
                r"<clause|\[clause|\[confirm:|\[to be inserted by user:|\[position conflict:|tbd|to be confirmed",
                draft.body,
                re.IGNORECASE,
            ):
                reviewer_findings.append(
                    DraftReviewFinding(
                        level="warning",
                        message="Draft contains placeholders or unconfirmed markers that need review.",
                    )
                )

            sources_text = _format_sources_block(draft_sources) or "No sources available."
            reviewer_prompt = (
                "You are a senior reviewer. Given the draft letter and the evidence list, flag issues.\n"
                "Output one finding per line in the form LEVEL|message|evidence, where LEVEL is WARNING or ERROR.\n"
                "Focus on factual grounding, missing citations, risky commitments, and tone misalignment.\n\n"
                f"Draft:\n{draft.body}\n\nSources:\n{sources_text}"
            )
            try:
                reviewer_response = await llm_generator.generate(
                    reviewer_prompt, max_tokens=512, model=reviewer_model
                )
                for raw_line in reviewer_response.splitlines():
                    parts = [p.strip() for p in raw_line.split("|") if p.strip()]
                    if len(parts) < 2:
                        continue
                    level_raw = parts[0].lower()
                    if level_raw not in {"warning", "error"}:
                        continue
                    message = parts[1]
                    evidence = parts[2] if len(parts) > 2 else None
                    reviewer_findings.append(
                        DraftReviewFinding(
                            level="error" if level_raw == "error" else "warning",
                            message=message or "Unspecified reviewer note",
                            evidence=evidence or None,
                        )
                    )
            except Exception as exc:  # pragma: no cover - defensive
                warnings.append(f"reviewer_llm: {exc}")

            reviewer_blocking = any(f.level == "error" for f in reviewer_findings)
            for finding in reviewer_findings:
                warnings.append(f"reviewer: {finding.level} - {finding.message}")
            return {
                "finding_count": len(reviewer_findings),
                "blocking": reviewer_blocking,
            }

        await self._exec_node("review_draft", review_draft, trace, warnings)

        # --- Node: validate_and_route ----------------------------------------
        async def validate_and_route() -> Dict[str, Any]:
            if not letter:
                return {"status": "unknown"}

            status = letter.status or "Draft"
            if workflow_engine.is_terminal(status):
                warnings.append(
                    f"Letter is in terminal status '{status}'. Review before applying the draft."
                )
                return {"status": "needs_attention"}

            if reviewer_blocking:
                return {"status": "needs_attention"}

            valid_destinations = workflow_engine.get_valid_transitions(status)
            return {"status": "ready_for_review", "next": valid_destinations}

        validation_payload = await self._exec_node(
            "validate_and_route", validate_and_route, trace, warnings
        )
        status = validation_payload.get("status", "ready_for_review")

        if falkor_service.enabled and letter:
            agent_refs: List[Dict[str, Any]] = []
            seen_norms: Set[str] = set()
            for doc in selected_documents:
                code = doc.get("letterNo") or doc.get("letter_no") or doc.get("reference_number")
                if code:
                    norm_code = normalize_letter_code(str(code))
                    if norm_code in seen_norms:
                        continue
                    seen_norms.add(norm_code)
                    agent_refs.append(
                        {
                            "code": str(code),
                            "normCode": norm_code,
                            "type": "CITES",
                            "source": "agent",
                            "metadata": {
                                "context_document_id": doc.get("id"),
                                "generated_by": "langgraph_letter_pipeline",
                            },
                        }
                    )

            primary_code = (
                getattr(letter, "letter_no", None)
                or getattr(getattr(letter, "reference", None), "reference_number", None)
            )

            if primary_code:
                base_letter = {
                    "code": str(primary_code),
                    "normCode": normalize_letter_code(str(primary_code)),
                    "direction": "outgoing",
                    "subject": letter.subject,
                    "date": letter.date.isoformat() if getattr(letter, "date", None) else datetime.now(timezone.utc).date().isoformat(),
                    "project": letter.project_id,
                }
                try:
                    if agent_refs:
                        falkor_service.upsert_letter_with_refs(base_letter, agent_refs, cleanup=False)
                    refreshed = falkor_service.get_thread(primary_code, depth=6)
                    if refreshed:
                        graph_thread = refreshed
                except Exception as exc:
                    warnings.append(f"falkor_sync: {exc}")

        # --- Finalise ---------------------------------------------------------
        completed_at = datetime.now(timezone.utc)
        return LetterGraphResult(
            letter_id=request.letter_id,
            run_id=run_id,
            plan=plan,
            draft=draft,
            status=status,
            warnings=warnings,
            trace=trace,
            started_at=started_at,
            completed_at=completed_at,
            summary_points=summary_points,
            context_document_ids=list(selected_document_ids),
            context_documents=list(selected_documents),
            background_summary=list(background_summary),
            graph_thread=list(graph_thread),
            sources=list(draft_sources),
            reviewer_findings=list(reviewer_findings),
            reviewer_blocking=reviewer_blocking,
            tone_approach=tone_approach,
            content_structure=content_structure,
            specific_responses=list(specific_responses),
            risk_mitigation=risk_mitigation,
            desired_outcome=desired_outcome,
            requirements_text=requirements_text,
            plan_context_text=plan_context_text,
        )

    async def _exec_node(
        self,
        name: str,
        func: Callable[[], Awaitable[Dict[str, Any]]],
        trace: List[LetterGraphNodeTrace],
        warnings: List[str],
    ) -> Dict[str, Any]:
        node_started = datetime.now(timezone.utc)
        status = "success"
        payload: Dict[str, Any]
        try:
            payload = await func()
        except Exception as exc:  # pragma: no cover - defensive guardrail
            status = "error"
            warnings.append(f"{name}: {exc}")
            payload = {}
            raise
        finally:
            node_completed = datetime.now(timezone.utc)
            trace.append(
                LetterGraphNodeTrace(
                    name=name,
                    status=status,
                    started_at=node_started,
                    completed_at=node_completed,
                    data=payload,
                )
            )
        return payload

    async def _fetch_related_documents(
        self,
        document_service: DocumentService,
        letter_no: Optional[str],
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Retrieve documents bearing the same letter number for quick context."""
        if not letter_no:
            return []
        try:
            db = await document_service._get_db()  # type: ignore[attr-defined]
        except AttributeError:
            # Fallback to accessing the internal db attribute for older service signatures.
            db = document_service.db  # type: ignore[attr-defined]
        if db is None:
            return []
        cursor = db.documents.find({"letterNo": letter_no}).limit(limit)
        return await cursor.to_list(length=limit)
