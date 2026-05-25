from __future__ import annotations

from datetime import datetime, timezone
from textwrap import shorten
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...models.letter import Letter
from ...models.letter_drafting import DraftContextBundle, DraftRunCreateRequest, SourceEvidence
from ...models.contract_models import ContractSearchRequest
from ...services.contract_service import ContractService
from ...services.conversation_service import ConversationService
from ...services.document_service import DocumentService
from ...services.falkor_graph_service import FalkorGraphService, normalize_letter_code


def condense_text(value: Optional[str], width: int = 600) -> Optional[str]:
    if not value:
        return None
    normalized = " ".join(str(value).split())
    if not normalized:
        return None
    if len(normalized) <= width:
        return normalized
    return shorten(normalized, width=width, placeholder="...")


class DraftContextBuilder:
    """Builds a scoped v2 context bundle and deterministic source ledger."""

    def __init__(
        self,
        document_service: DocumentService,
        conversation_service: ConversationService,
        contract_service: Optional[ContractService] = None,
        graph_service: Optional[FalkorGraphService] = None,
    ) -> None:
        self.document_service = document_service
        self.conversation_service = conversation_service
        self.contract_service = contract_service or ContractService()
        self.graph_service = graph_service or FalkorGraphService()

    async def build(
        self,
        letter: Letter,
        request: DraftRunCreateRequest,
        current_user: Any,
    ) -> Tuple[DraftContextBundle, List[SourceEvidence], List[str]]:
        warnings: List[str] = []
        org_id = str(getattr(letter, "organization_id", "") or "")
        project_id = str(getattr(letter, "project_id", "") or "")
        current_materials = self._current_materials(letter, request)
        sources: List[SourceEvidence] = []

        for idx, material in enumerate(current_materials, start=1):
            sources.append(
                SourceEvidence(
                    source_id=f"current:{idx}",
                    source_type="current_input",
                    allowed_use="fact",
                    organization_id=org_id,
                    project_id=project_id,
                    label=f"Current material {idx}",
                    text=material,
                    snippet=condense_text(material, 280),
                )
            )

        selected_document_ids, document_sources, comment_lines = await self._document_sources(
            letter, request, org_id, project_id, warnings
        )
        sources.extend(document_sources)

        clause_sources = await self._contract_clause_sources(
            letter, request, org_id, project_id, current_user, warnings
        )
        sources.extend(clause_sources)

        prior_ids, prior_sources = await self._prior_correspondence_sources(
            letter, request, current_user, org_id, project_id, warnings
        )
        sources.extend(prior_sources)

        graph_codes, graph_sources = self._graph_sources(letter, request, org_id, project_id, warnings)
        sources.extend(graph_sources)

        threshold_inputs = {
            "sender_profile": bool(request.role or getattr(letter, "strategy_role", None)),
            "letter_purpose": bool(request.subject or letter.subject),
            "intended_recipient": bool(request.recipient or letter.recipient),
            "key_issue_or_event": bool(current_materials),
            "main_factual_basis": bool(current_materials or document_sources or prior_sources),
        }

        bundle = DraftContextBundle(
            active_workspace={
                "organization_id": org_id or None,
                "project_id": project_id or None,
                "letter_id": str(letter.id),
                "letter_no": getattr(letter, "letter_no", None),
            },
            current_materials=current_materials,
            selected_document_ids=selected_document_ids,
            prior_correspondence_ids=prior_ids,
            graph_thread_codes=graph_codes,
            comments=comment_lines,
            threshold_inputs=threshold_inputs,
        )
        return bundle, self._dedupe_sources(sources), warnings

    def _current_materials(self, letter: Letter, request: DraftRunCreateRequest) -> List[str]:
        materials: List[str] = []
        for value in [
            request.purpose,
            request.requirements,
            request.points,
            request.desired_position,
            request.required_action,
            request.background_facts,
            request.trigger_event,
            "\n".join(request.clauses_to_consider),
            letter.content,
            getattr(letter, "contractor_context", None),
            getattr(letter, "engineer_context", None),
            getattr(letter, "employer_context", None),
        ]:
            condensed = condense_text(value, 1600)
            if condensed and condensed not in materials:
                materials.append(condensed)
        return materials

    async def _document_sources(
        self,
        letter: Letter,
        request: DraftRunCreateRequest,
        org_id: str,
        project_id: str,
        warnings: List[str],
    ) -> Tuple[List[str], List[SourceEvidence], List[str]]:
        doc_ids = list(dict.fromkeys([str(x) for x in request.document_ids if x]))
        if not doc_ids:
            doc_ids = [str(x) for x in (getattr(letter, "context_document_ids", []) or []) if x]
        docs = await self.document_service.get_documents_by_ids(doc_ids) if doc_ids else []
        selected_ids: List[str] = []
        sources: List[SourceEvidence] = []
        comments: List[str] = []
        for doc in docs:
            doc_id = str(getattr(doc, "id", "") or "")
            same_org = not org_id or str(getattr(doc, "organization_id", "")) == org_id
            same_project = not project_id or str(getattr(doc, "project_id", "") or "") == project_id
            if not same_org or not same_project:
                warnings.append(f"Skipped out-of-workspace document {doc_id}")
                continue
            selected_ids.append(doc_id)
            text = condense_text(
                getattr(doc, "summary", None)
                or getattr(doc, "full_text", None)
                or getattr(doc, "ocrText", None)
                or getattr(doc, "subject", None),
                1000,
            )
            sources.append(
                SourceEvidence(
                    source_id=f"document:{doc_id}",
                    source_type="context_document",
                    allowed_use="fact",
                    organization_id=org_id,
                    project_id=project_id,
                    label=getattr(doc, "subject", None) or getattr(doc, "letterNo", None) or "Context document",
                    text=text,
                    snippet=condense_text(text, 280),
                    document_id=doc_id,
                    metadata={
                        "letter_no": getattr(doc, "letterNo", None),
                        "upload_type": getattr(doc, "uploadType", None),
                    },
                )
            )
            try:
                for comment in (await self.document_service.get_comments(doc_id))[-5:]:
                    text_value = condense_text(comment.get("text"), 400) if isinstance(comment, dict) else None
                    if text_value:
                        comments.append(text_value)
                        sources.append(
                            SourceEvidence(
                                source_id=f"comment:{doc_id}:{len(comments)}",
                                source_type="comment",
                                allowed_use="comment",
                                organization_id=org_id,
                                project_id=project_id,
                                label="Document comment",
                                text=text_value,
                                snippet=condense_text(text_value, 200),
                                document_id=doc_id,
                            )
                        )
            except Exception as exc:
                warnings.append(f"Unable to load comments for {doc_id}: {exc}")
        return selected_ids, sources, comments

    async def _contract_clause_sources(
        self,
        letter: Letter,
        request: DraftRunCreateRequest,
        org_id: str,
        project_id: str,
        current_user: Any,
        warnings: List[str],
    ) -> List[SourceEvidence]:
        if not org_id or not project_id:
            return []
        query = " ".join(
            part
            for part in [
                request.subject or letter.subject,
                request.purpose,
                request.requirements,
                request.points,
                request.background_facts,
                request.trigger_event,
                " ".join(request.clauses_to_consider),
                letter.content,
            ]
            if part
        )[:800]
        if not query:
            return []
        try:
            response = await self.contract_service.search_contracts(
                ContractSearchRequest(
                    query=query,
                    organization_id=org_id,
                    project_id=project_id,
                    limit=5,
                    top_docs=3,
                    chunks_per_doc=2,
                    summarize=False,
                ),
                current_user,
            )
        except Exception as exc:
            warnings.append(f"Contract clause retrieval skipped: {exc}")
            return []
        sources: List[SourceEvidence] = []
        for idx, chunk in enumerate(response.results, start=1):
            clause_number = chunk.clause_number
            text = condense_text(chunk.text, 1000)
            sources.append(
                SourceEvidence(
                    source_id=f"clause:{chunk.document_id or chunk.upload_id}:{clause_number or idx}:{chunk.chunk_index or 0}",
                    source_type="contract_clause",
                    allowed_use="clause",
                    organization_id=org_id,
                    project_id=project_id,
                    label=f"{clause_number or 'Clause'} {chunk.clause_title or ''}".strip(),
                    text=text,
                    snippet=condense_text(text, 300),
                    document_id=str(chunk.document_id or chunk.upload_id or "") or None,
                    clause_number=clause_number,
                    clause_title=chunk.clause_title,
                    page_numbers=[int(p) for p in (chunk.page_numbers or ([chunk.page] if chunk.page else [])) if p],
                    score=chunk.score,
                    metadata={"file_name": chunk.file_name or chunk.source_filename},
                )
            )
        return sources

    async def _prior_correspondence_sources(
        self,
        letter: Letter,
        request: DraftRunCreateRequest,
        current_user: Any,
        org_id: str,
        project_id: str,
        warnings: List[str],
    ) -> Tuple[List[str], List[SourceEvidence]]:
        try:
            chain = await self.conversation_service.get_conversation_chain(
                str(letter.id), current_user=current_user
            )
        except Exception as exc:
            warnings.append(f"Prior correspondence unavailable: {exc}")
            return [], []
        exclude_codes = {normalize_letter_code(str(code)) for code in request.exclude_letter_codes or [] if code}
        prior_ids: List[str] = []
        sources: List[SourceEvidence] = []
        for entry in chain[-6:]:
            if not getattr(entry, "id", None) or str(entry.id) == str(letter.id):
                continue
            if normalize_letter_code(str(getattr(entry, "letter_no", "") or "")) in exclude_codes:
                continue
            if str(getattr(entry, "organization_id", "") or "") != org_id:
                continue
            if project_id and str(getattr(entry, "project_id", "") or "") != project_id:
                continue
            prior_ids.append(str(entry.id))
            text = condense_text(
                getattr(entry, "summary", None)
                or getattr(entry, "content", None)
                or getattr(entry, "subject", None),
                1000,
            )
            sources.append(
                SourceEvidence(
                    source_id=f"prior:{entry.id}",
                    source_type="prior_correspondence",
                    allowed_use="history_only",
                    organization_id=org_id,
                    project_id=project_id,
                    label=getattr(entry, "subject", None) or "Prior correspondence",
                    text=text,
                    snippet=condense_text(text, 280),
                    letter_id=str(entry.id),
                    metadata={
                        "letter_no": getattr(entry, "letter_no", None),
                        "date": self._date_label(getattr(entry, "date", None) or getattr(entry, "created_at", None)),
                    },
                )
            )
        return prior_ids, sources

    def _graph_sources(
        self,
        letter: Letter,
        request: DraftRunCreateRequest,
        org_id: str,
        project_id: str,
        warnings: List[str],
    ) -> Tuple[List[str], List[SourceEvidence]]:
        codes: List[str] = []
        sources: List[SourceEvidence] = []
        target_code = getattr(letter, "letter_no", None)
        nodes: Sequence[Dict[str, Any]] = []
        if self.graph_service.enabled and target_code:
            try:
                nodes = self.graph_service.get_thread(target_code, depth=6) or []
            except Exception as exc:
                warnings.append(f"Graph thread unavailable: {exc}")
        include_codes = {normalize_letter_code(str(code)) for code in request.include_letter_codes or [] if code}
        exclude_codes = {normalize_letter_code(str(code)) for code in request.exclude_letter_codes or [] if code}
        seen: set[str] = set()
        for node in nodes:
            code = node.get("normCode") or normalize_letter_code(str(node.get("code") or ""))
            if not code or code in exclude_codes or code in seen:
                continue
            seen.add(code)
            codes.append(code)
            sources.append(
                SourceEvidence(
                    source_id=f"graph:{code}",
                    source_type="graph_thread",
                    allowed_use="history_only",
                    organization_id=org_id,
                    project_id=project_id,
                    label=node.get("subject") or "Graph-linked letter",
                    snippet=node.get("subject"),
                    metadata=dict(node),
                )
            )
        for code in include_codes - seen - exclude_codes:
            codes.append(code)
            sources.append(
                SourceEvidence(
                    source_id=f"graph:{code}",
                    source_type="graph_thread",
                    allowed_use="history_only",
                    organization_id=org_id,
                    project_id=project_id,
                    label="Manually included graph-linked letter",
                    metadata={"normCode": code},
                )
            )
        return codes, sources

    @staticmethod
    def _date_label(value: Any) -> Optional[str]:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).date().isoformat()
        return str(value) if value else None

    @staticmethod
    def _dedupe_sources(sources: List[SourceEvidence]) -> List[SourceEvidence]:
        deduped: List[SourceEvidence] = []
        seen: set[str] = set()
        for source in sources:
            if source.source_id in seen:
                continue
            seen.add(source.source_id)
            deduped.append(source)
        return deduped
