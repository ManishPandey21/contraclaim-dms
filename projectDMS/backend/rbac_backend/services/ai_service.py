from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher
from textwrap import shorten
from typing import Any, List, Optional, Tuple

from bson.objectid import ObjectId

from ..core.database import get_database
from ..models.ai_models import (
    AIAssistantStats,
    LangGraphDraftRequest,
    LangGraphDraftResponse,
    LangGraphNodeTrace,
    LetterDraftRequest,
    LetterDraftResponse,
    LetterSearchRequest,
    SimilarLetter,
    StrategyPlanRequest,
    StrategyPlanResponse,
    VectorSearchResponse,
)
from ..ai_workflows.langgraph import LetterDraftGraph
from ..core.security import _expand_object_ids, build_scope_query
from ..utils.error_handler import LetterError
from .letter_service import LetterService


class AIService:
    """Application-level orchestration for search and drafting primitives."""

    def __init__(self) -> None:
        self._letters = None

    async def _get_collection(self):
        if self._letters is None:
            db = await get_database()
            self._letters = db.letters
        return self._letters

    @staticmethod
    def _id_filter_values(value: Optional[Any]) -> List[Any]:
        if value in (None, ""):
            return []
        values: List[Any] = [str(value)]
        try:
            values.append(ObjectId(str(value)))
        except Exception:
            pass
        return values

    @classmethod
    def _scoped_query(
        cls,
        base_query: dict,
        *,
        organization_id: Optional[Any] = None,
        project_id: Optional[Any] = None,
    ) -> dict:
        scope_terms: List[dict] = []
        org_values = cls._id_filter_values(organization_id)
        if org_values:
            scope_terms.append({"organization_id": {"$in": org_values}})
        project_values = cls._id_filter_values(project_id)
        if project_values:
            scope_terms.append({"project_id": {"$in": project_values}})
        if not scope_terms:
            return base_query
        return {"$and": [base_query, *scope_terms]}

    @staticmethod
    def _letter_scope(
        current_user: Any,
        *,
        organization_id: Optional[Any] = None,
        project_id: Optional[Any] = None,
    ) -> dict:
        """The actor's canonical row visibility over `letters`.

        Authority comes from `build_scope_query` (the actor's entitlement); a
        supplied organisation/project only NARROWS inside it. The policy gate
        answers membership of the requested scope, not whether a letter id
        belongs to it, so every langgraph letter read and write must carry this
        predicate. `current_user` has no default: without an actor there is no
        scope, and the answer is "no letter", not "any letter".
        """
        if current_user is None:
            raise LetterError("Letter not found", 404)
        scope = build_scope_query(
            current_user,
            organization_id=str(organization_id) if organization_id else None,
            project_id=str(project_id) if project_id else None,
        )
        if not organization_id:
            return scope
        # The letter must also sit in the organisation the policy gate just
        # authorised. `build_scope_query` bounds the ContraClaim expert roles
        # by `projects` alone and ignores the organisation by design, so an
        # expert whose `projects` still lists another tenant's project could
        # otherwise act on that tenant's letter while subscription, entitlement,
        # allocation and metering were all evaluated for this one.
        org_clause = {"organization_id": {"$in": _expand_object_ids([str(organization_id)])}}
        return {"$and": [scope, org_clause]} if scope else org_clause

    @staticmethod
    async def _require_letter_in_scope(db: Any, letter_id: Any, scope: dict) -> None:
        """404 unless `letter_id` names a letter inside `scope`.

        Malformed, nonexistent and out-of-scope ids answer identically so the
        route cannot be used to probe for another tenant's letter ids.
        """
        try:
            letter_oid = ObjectId(str(letter_id))
        except Exception:
            raise LetterError("Letter not found", 404) from None
        id_clause = {"_id": letter_oid}
        query = {"$and": [id_clause, scope]} if scope else id_clause
        if not await db.letters.find_one(query, {"_id": 1}):
            raise LetterError("Letter not found", 404)

    async def resolve_letter_scope(
        self,
        letter_id: Any,
        current_user: Any,
        *,
        organization_id: Optional[Any] = None,
        project_id: Optional[Any] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """The TARGET letter's own organisation/project, for authorisation.

        The letter is located only inside the actor's canonical visibility
        (`_letter_scope`: entitlement, narrowed by any selected org / named
        project), and what comes back is the letter's stored scope - the only
        scope a policy decision about this letter may be made against. A
        requested, defaulted or `None` organisation is never a substitute: an
        expert with no organisation and a stale foreign project in `projects`
        otherwise had the policy evaluated for no organisation at all.

        A letter with no organisation cannot be attributed to a tenant, so only
        Super Admin may act on it. Every refusal is the same 404.
        """
        db = await get_database()
        scope = self._letter_scope(
            current_user, organization_id=organization_id, project_id=project_id
        )
        try:
            letter_oid = ObjectId(str(letter_id))
        except Exception:
            raise LetterError("Letter not found", 404) from None
        id_clause = {"_id": letter_oid}
        query = {"$and": [id_clause, scope]} if scope else id_clause
        letter = await db.letters.find_one(
            query, {"organization_id": 1, "project_id": 1}
        )
        if not letter:
            raise LetterError("Letter not found", 404)
        letter_org = letter.get("organization_id")
        letter_project = letter.get("project_id")
        roles = {str(r).lower() for r in (getattr(current_user, "roles", None) or [])}
        if not letter_org and "superadmin" not in roles:
            raise LetterError("Letter not found", 404)
        return (
            str(letter_org) if letter_org else None,
            str(letter_project) if letter_project else None,
        )

    async def _fetch_documents_by_ids(
        self,
        identifiers: List[str],
        *,
        organization_id: Optional[Any] = None,
        project_id: Optional[Any] = None,
    ) -> List[dict]:
        if not identifiers:
            return []

        db = await get_database()
        object_ids: List[ObjectId] = []
        for raw in identifiers:
            try:
                object_ids.append(ObjectId(raw))
            except Exception:
                continue

        if not object_ids:
            return []

        query = self._scoped_query(
            {"_id": {"$in": object_ids}},
            organization_id=organization_id,
            project_id=project_id,
        )
        cursor = db.documents.find(query)
        raw_docs = await cursor.to_list(length=None)
        # Ensure documents are dicts and add missing 'keywords' if absent to prevent AttributeError
        documents = []
        for doc in raw_docs:
            doc_dict = dict(doc)
            if 'keywords' not in doc_dict:
                doc_dict['keywords'] = []
            documents.append(doc_dict)
        return documents

    @staticmethod
    def _build_snippet(text: str | None, query: str, window: int = 220) -> str:
        if not text:
            return ""

        pattern = re.compile(re.escape(query), re.IGNORECASE)
        match = pattern.search(text)
        if not match:
            return shorten(text.strip(), width=window, placeholder="Ã¢â‚¬Â¦")

        start = max(match.start() - 80, 0)
        end = min(match.end() + 140, len(text))
        snippet = text[start:end].strip()
        if start > 0:
            snippet = "Ã¢â‚¬Â¦" + snippet
        if end < len(text):
            snippet = snippet + "Ã¢â‚¬Â¦"
        return snippet

    async def search_similar_letters(
        self,
        query: str,
        current_user: Any,
        limit: int,
        *,
        organization_id: Optional[Any] = None,
        project_id: Optional[Any] = None,
    ) -> VectorSearchResponse:
        collection = await self._get_collection()

        regex = {"$regex": re.escape(query), "$options": "i"}
        base_query = self._scoped_query(
            {
                "$or": [
                    {"subject": regex},
                    {"content": regex},
                    {"summary": regex},
                    {"keywords": regex},
                ]
            },
            organization_id=organization_id,
            project_id=project_id,
        )

        fetch_limit = max(limit * 4, limit)
        docs = await collection.find(base_query).sort("updated_at", -1).limit(fetch_limit).to_list(length=fetch_limit)

        results: List[SimilarLetter] = []
        for doc in docs:
            subject = doc.get("subject", "")
            summary = doc.get("summary") or doc.get("content") or doc.get("body") or ""
            keywords = doc.get("keywords") or []
            keyword_text = ", ".join(keywords)

            subject_score = SequenceMatcher(None, query.lower(), subject.lower()).ratio()
            keyword_score = SequenceMatcher(None, query.lower(), keyword_text.lower()).ratio()
            blended = SequenceMatcher(None, query.lower(), f"{subject} {summary}".lower()).ratio()
            score = min(subject_score * 0.6 + keyword_score * 0.2 + blended * 0.2 + 0.2, 1.0)

            snippet = self._build_snippet(summary or doc.get("content"), query)
            created_at = doc.get("created_at") or doc.get("createdAt")

            results.append(
                SimilarLetter(
                    letter_id=str(doc.get("_id")),
                    score=round(score, 3),
                    subject=subject or None,
                    snippet=snippet or None,
                    created_at=created_at,
                )
            )

        results.sort(key=lambda item: (-item.score, item.created_at or datetime.min))
        return VectorSearchResponse(results=results[:limit])

    async def generate_draft(
        self, request: LetterDraftRequest, current_user: Any
    ) -> LetterDraftResponse:
        sections: List[str] = []
        sections.append(f"Subject: {request.subject}")
        sections.append("")

        recipient_line = request.recipient.strip() if request.recipient else "valued stakeholder"
        sections.append(f"Dear {recipient_line},")
        sections.append("")

        if request.context:
            sections.append(request.context.strip())
            sections.append("")

        bullet_points: List[str] = []
        if request.points:
            for line in request.points.splitlines():
                cleaned = line.strip().lstrip("-Ã¢â‚¬Â¢*").strip()
                if cleaned:
                    bullet_points.append(cleaned)

        source_documents = await self._fetch_documents_by_ids(
            request.document_ids,
            organization_id=request.organization_id,
            project_id=request.project_id,
        )
        # `summary` is extraction-derived, so it goes through the publication
        # policy rather than being read raw - reading it directly is the exact
        # short-circuit that let a blocked document's text into a draft body.
        # `subject` is filing metadata and stays available so a blocked document
        # remains identifiable.
        from .publication_policy import authoritative_summary, is_consumable

        for doc in source_documents:
            summary = authoritative_summary(doc) or (
                doc.get("subject") if is_consumable(doc) else None
            )
            if summary:
                bullet_points.append(summary.strip())
            keywords = doc.get("keywords") or []
            if keywords:
                bullet_points.append("Key topics: " + ", ".join(keywords[:5]))

        deduped_points: List[str] = []
        seen_points: set[str] = set()
        for point in bullet_points:
            normalized = point.lower()
            if normalized not in seen_points:
                deduped_points.append(point)
                seen_points.add(normalized)

        if deduped_points:
            sections.append("Key Points:")
            sections.extend([f"- {point}" for point in deduped_points])
            sections.append("")

        sections.append("Please review the summary above and share any feedback or direction for the next revision.")
        sections.append("")

        author = (
            getattr(current_user, "full_name", None)
            or getattr(current_user, "username", None)
            or getattr(current_user, "email", None)
            or "ContractDMS Assistant"
        )
        sections.append(f"Regards,\n{author}")

        body = "\n".join(section for section in sections if section)
        return LetterDraftResponse(
            subject=request.subject,
            body=body,
            key_points=deduped_points,
        )

    async def get_stats(self, current_user: Any) -> AIAssistantStats:
        return AIAssistantStats(
            requests_last_24h=0,
            cached_hits_last_24h=0,
            average_latency_ms=0.0,
            success_rate=1.0,
            updated_at=datetime.utcnow(),
        )

    async def generate_draft_with_langgraph(
        self,
        request: LangGraphDraftRequest,
        current_user: Any,
        *,
        requested_project_id: Optional[str] = None,
    ) -> LangGraphDraftResponse:
        """`requested_project_id` is the project the CALLER named, if any. It
        narrows which letter is reachable; `request.project_id` may be a
        controller default (the actor's first project) and must not."""
        db = await get_database()
        scope = self._letter_scope(
            current_user,
            organization_id=request.organization_id,
            project_id=requested_project_id,
        )
        # The letter is authorised BEFORE the graph reads it: `load_state`
        # fetches by bare id and feeds subject/content into the prompt.
        await self._require_letter_in_scope(db, request.letter_id, scope)

        graph = LetterDraftGraph(self.generate_draft)
        graph_result = await graph.run(request, current_user)

        letter_service = LetterService(db)
        await letter_service.record_langgraph_result(
            request.letter_id, graph_result, created_by=current_user, scope=scope
        )

        trace_payload = [
            LangGraphNodeTrace(
                name=node.name,
                status=node.status,
                started_at=node.started_at,
                completed_at=node.completed_at,
                data=node.data,
            )
            for node in graph_result.trace
        ]

        return LangGraphDraftResponse(
            letter_id=graph_result.letter_id,
            run_id=graph_result.run_id,
            status=graph_result.status,
            plan=graph_result.plan,
            draft=graph_result.draft,
            warnings=list(graph_result.warnings),
            trace=trace_payload,
            summary_points=list(graph_result.summary_points),
            context_document_ids=list(graph_result.context_document_ids),
            context_documents=list(graph_result.context_documents),
            background_summary=list(graph_result.background_summary),
            graph_thread=list(graph_result.graph_thread),
            sources=list(graph_result.sources),
            reviewer_findings=list(graph_result.reviewer_findings),
            reviewer_blocking=graph_result.reviewer_blocking,
            started_at=graph_result.started_at,
            completed_at=graph_result.completed_at,
            tone_approach=graph_result.tone_approach,
            content_structure=graph_result.content_structure,
            specific_responses=list(graph_result.specific_responses),
            risk_mitigation=graph_result.risk_mitigation,
            desired_outcome=graph_result.desired_outcome,
            requirements_text=graph_result.requirements_text,
            context_text=graph_result.plan_context_text,
        )

    async def get_latest_langgraph_run(
        self,
        letter_id: str,
        current_user: Any,
        *,
        organization_id: Optional[Any] = None,
        project_id: Optional[Any] = None,
    ) -> Optional[LangGraphDraftResponse]:
        db = await get_database()
        scope = self._letter_scope(
            current_user, organization_id=organization_id, project_id=project_id
        )
        try:
            await self._require_letter_in_scope(db, letter_id, scope)
        except LetterError:
            return None
        letter_service = LetterService(db)
        snapshot = await letter_service.get_langgraph_snapshot(letter_id)
        if not snapshot:
            return None
        # Pydantic will coerce nested payloads appropriately
        return LangGraphDraftResponse(**snapshot)

    async def generate_strategy_plan(
        self,
        request: StrategyPlanRequest,
        current_user: Any,
        *,
        requested_project_id: Optional[str] = None,
    ) -> StrategyPlanResponse:
        """Run LangGraph in analysis-only mode to produce a structured strategy plan."""
        db = await get_database()
        scope = self._letter_scope(
            current_user,
            organization_id=request.organization_id,
            project_id=requested_project_id,
        )
        await self._require_letter_in_scope(db, request.letter_id, scope)

        composed_context = self._compose_role_context(request)
        langgraph_request = LangGraphDraftRequest(
            letter_id=request.letter_id,
            subject=request.subject,
            recipient=request.recipient,
            context=composed_context,
            points="\n".join(request.summary_points) if request.summary_points else request.points,
            document_ids=request.document_ids,
            use_vector_store=True,
            analysis_only=True,
            organization_id=request.organization_id,
            project_id=request.project_id,
        )
        graph = LetterDraftGraph(self.generate_draft)
        graph_result = await graph.run(langgraph_request, current_user)

        letter_service = LetterService(db)
        await letter_service.record_langgraph_result(
            request.letter_id, graph_result, created_by=current_user, scope=scope
        )

        trace_payload = [
            LangGraphNodeTrace(
                name=node.name,
                status=node.status,
                started_at=node.started_at,
                completed_at=node.completed_at,
                data=node.data,
            )
            for node in graph_result.trace
        ]

        completed_at = graph_result.completed_at or datetime.utcnow()
        return StrategyPlanResponse(
            letter_id=graph_result.letter_id,
            run_id=graph_result.run_id,
            status=graph_result.status,
            generated_at=completed_at,
            plan=graph_result.plan,
            tone_approach=graph_result.tone_approach or {},
            content_structure=graph_result.content_structure or {},
            specific_responses=list(graph_result.specific_responses),
            risk_mitigation=graph_result.risk_mitigation or {},
            desired_outcome=graph_result.desired_outcome or {},
            summary_points=list(graph_result.summary_points),
            background_summary=list(graph_result.background_summary),
            context_document_ids=list(graph_result.context_document_ids),
            context_documents=list(graph_result.context_documents),
            trace=trace_payload,
            warnings=list(graph_result.warnings),
        )

    @staticmethod
    def _compose_role_context(request: StrategyPlanRequest) -> str:
        """Combine three-way contexts with the selected role for LangGraph ingestion."""
        sections = [
            f"ROLE: {request.role}",
        ]
        if request.audience:
            sections.append(f"PRIMARY AUDIENCE: {request.audience}")
        if request.contractor_context:
            sections.append("CONTRACTOR CONTEXT:")
            sections.append(request.contractor_context)
        if request.engineer_context:
            sections.append("ENGINEER CONTEXT:")
            sections.append(request.engineer_context)
        if request.employer_context:
            sections.append("EMPLOYER CONTEXT:")
            sections.append(request.employer_context)
        if request.requirements:
            sections.append("CURRENT REQUIREMENTS / INPUT:")
            sections.append(request.requirements)
        elif request.context:
            sections.append("CURRENT REQUIREMENTS / INPUT:")
            sections.append(request.context)
        return "\n".join(section for section in sections if section)
