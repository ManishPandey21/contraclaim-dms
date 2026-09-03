from __future__ import annotations

import logging
import time
import uuid
from typing import Any, List, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..core.security import CurrentUser
from ..observability.service import ObservabilityService
from ..retrieval.generator import LLMGenerator
from ..retrieval.models import Citation, SearchFilters, SearchRequest
from ..retrieval.service import RetrievalService

from .models import AgentConversation, AgentMessage, AgentRequest, AgentResponse

logger = logging.getLogger(__name__)


class DraftingAgentService:
    """Conversation-aware drafting agent that reuses the retrieval service."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        retrieval_service: RetrievalService,
        llm_generator: LLMGenerator,
        observability: ObservabilityService,
    ):
        self.db = db
        self.retrieval = retrieval_service
        self.llm = llm_generator
        self.observability = observability

    async def run(
        self, request: AgentRequest, current_user: Optional[CurrentUser]
    ) -> AgentResponse:
        start = time.perf_counter()
        conversation_id = request.conversation_id or str(uuid.uuid4())
        # Called for the insert, not for the value: _ensure_conversation writes
        # the conversation row when it does not exist yet.
        await self._ensure_conversation(conversation_id, request, current_user)

        incoming_text = request.incoming_text or await self._load_letter_text(
            request.incoming_letter_id,
            org_id=request.org_id,
            project_id=request.project_id,
        )
        issues, questions = self._analyze_incoming(incoming_text, request.user_goal)

        filters = request.filters or SearchFilters(
            org_id=request.org_id, project_id=request.project_id
        )
        retrieval_req = SearchRequest(
            query=request.user_goal or incoming_text or "draft reply",
            strategy=request.strategy,
            limit=6,
            filters=filters,
            use_enriched_text=True,
        )
        search_resp = await self.retrieval.search(
            retrieval_req, current_user, log_run=False
        )

        draft_prompt = self._build_draft_prompt(
            incoming_text, request.user_goal, issues, search_resp.results
        )
        draft = await self.llm.generate(draft_prompt, max_tokens=700)

        citations = [
            Citation(
                document_id=res.document_id,
                chunk_id=res.chunk_id,
                page=res.page,
                score=res.score,
                snippet=res.snippet,
            )
            for res in search_resp.results
        ]

        message = AgentMessage(role="assistant", content=draft, citations=citations)
        await self._persist_message(conversation_id, message)

        timings = dict(search_resp.timings)
        timings["draft_ms"] = (time.perf_counter() - start) * 1000

        await self.observability.log_run(
            run_type="agent",
            org_id=request.org_id,
            project_id=request.project_id,
            strategy=request.strategy.value,
            query=request.user_goal or (incoming_text[:80] if incoming_text else ""),
            retrieved=[{"chunk_id": c.chunk_id, "score": c.score} for c in citations],
            breakdown_ms=timings,
            user_id=current_user.id if current_user else None,
        )

        return AgentResponse(
            conversation_id=conversation_id,
            questions_to_user=questions or None,
            issues_to_address=issues,
            draft_reply=draft,
            citations=citations,
            tool_traces=[],
            timings=timings,
        )

    async def _ensure_conversation(
        self,
        conversation_id: str,
        request: AgentRequest,
        current_user: Optional[CurrentUser],
    ) -> AgentConversation:
        existing = await self.db.agent_conversations.find_one(
            {"conversation_id": conversation_id}
        )
        if existing:
            return AgentConversation(**existing)
        participants = [current_user.id] if current_user else []
        convo = AgentConversation(
            conversation_id=conversation_id,
            org_id=request.org_id,
            project_id=request.project_id,
            participants=participants,
        )
        await self.db.agent_conversations.insert_one(
            convo.model_dump(by_alias=True, exclude_none=True)
        )
        return convo

    async def _persist_message(
        self, conversation_id: str, message: AgentMessage
    ) -> None:
        payload = message.model_dump(exclude_none=True)
        payload["conversation_id"] = conversation_id
        await self.db.agent_messages.insert_one(payload)
        await self.db.agent_conversations.update_one(
            {"conversation_id": conversation_id},
            {
                "$push": {"messages": payload},
                "$set": {"updated_at": payload["created_at"]},
            },
        )

    async def _load_letter_text(
        self,
        letter_id: Optional[str],
        *,
        org_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> str:
        if not letter_id:
            return ""
        # Both collections are ObjectId-keyed: document_service pops any
        # supplied _id before insert_one so Mongo generates one. Querying with
        # the raw string never matched, so this returned "" for every real
        # document - and the publication guard below never ran on anything.
        # Try both forms rather than relying on the string happening to work.
        candidates = [letter_id]
        try:
            from bson import ObjectId

            candidates.append(ObjectId(str(letter_id)))
        except Exception:
            pass

        # Tenant scope, applied UNCONDITIONALLY. This lookup had no scope at
        # all: the caller supplies incoming_letter_id and the route authorises
        # only the caller's own org, never re-checking that the id falls inside
        # it - so any authenticated user could name another tenant's document
        # and have its text folded into the draft prompt.
        #
        # Worse, the ObjectId fix above is what made that reachable. While the
        # raw string never matched, this failed closed by accident. Repairing a
        # lookup can activate code that was never exercised, which is exactly
        # why scope has to be applied here and not left to the caller.
        #
        # Scoping on None matches null/missing, so genuinely unscoped legacy
        # records keep working without ever crossing a tenant boundary.
        scope = {"organization_id": org_id, "project_id": project_id}

        doc = None
        for key in candidates:
            doc = await self.db.letters.find_one(
                {"_id": key, **scope}
            ) or await self.db.documents.find_one({"_id": key, **scope})
            if doc:
                break
        if not doc:
            return ""
        # Read through the publication policy, not the raw fields: a document
        # with unresolved extraction-quality findings must not reach drafting
        # just because its text is stored. See services/publication_policy.py.
        from ..services.publication_policy import authoritative_text

        return authoritative_text(doc)

    def _analyze_incoming(
        self, text: str, goal: Optional[str]
    ) -> Tuple[List[str], List[str]]:
        issues: List[str] = []
        questions: List[str] = []
        if goal:
            issues.append(f"User goal: {goal}")
        if text:
            issues.append("Summarize incoming letter obligations and deadlines.")
            questions.append(
                "Are there missing dates or reference numbers in the incoming letter?"
            )
        else:
            questions.append(
                "Please provide the incoming letter text to ground the draft."
            )
        return issues, questions

    def _build_draft_prompt(
        self,
        incoming_text: str,
        goal: Optional[str],
        issues: List[str],
        results: List[Any],
    ) -> str:
        context_snippets = "\n\n".join([r.snippet for r in results])
        goal_line = f"User goal: {goal}" if goal else "Goal: Provide a formal reply."
        issues_text = (
            "\n".join(f"- {issue}" for issue in issues) if issues else "None provided."
        )
        return (
            "You are drafting a formal contract letter reply. Use the retrieved context and keep statements factual.\n"
            f"{goal_line}\n\nIncoming letter:\n{incoming_text or 'Not provided'}\n\n"
            f"Issues to address:\n{issues_text}\n\nRetrieved context:\n{context_snippets}\n\n"
            "Draft a concise reply with clear paragraphs and cite document IDs when relevant."
        )
