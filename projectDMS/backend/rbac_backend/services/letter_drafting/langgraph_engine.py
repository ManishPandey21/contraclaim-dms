"""Official LangGraph v3 drafting runtime.

The runtime owns graph orchestration and MongoDB checkpoints. Existing v2
agents/services remain the domain implementations and are called from graph
nodes in later phases; the engine never redirects into the experimental
``services/langgraph`` sidecar.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional, TypedDict
from urllib.parse import urlparse

from fastapi import HTTPException
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pymongo import MongoClient

from ...core.config import settings
from ...core.database import get_database
from ...models.letter_drafting import (
    DraftRun,
    DraftRunAccepted,
    DraftRunCancelRequest,
    DraftRunCreateRequest,
    DraftRunResumeRequest,
    DraftRunStateResponse,
    DraftExecutionEffect,
    ForceV2FallbackRequest,
)
from .drafting_queue import get_drafting_queue
from .context import DraftContextBuilder
from .repository import DraftRunRepository
from ...services.conversation_service import ConversationService
from ...services.document_service import DocumentService
from .service import DraftRunService
from .user_direction import UserDirectionAgent


class DraftGraphState(TypedDict, total=False):
    """Versioned, deliberately minimal persisted graph state."""

    run_id: str
    letter_id: str
    state_schema_version: int
    execution_status: str
    next_action: str
    input_snapshot_id: str
    context_snapshot_id: str
    cancellation_requested: bool
    correspondence_reviewed: bool
    evidence_collected: bool
    questions_required: bool
    domain_execution_status: str
    domain_next_action: str


def build_drafting_graph(
    *,
    checkpointer: Optional[BaseCheckpointSaver] = None,
):
    """Build the v3 orchestration skeleton with an official StateGraph.

    Phase 4–6 add evidence, interrupt and approval nodes to this graph. The
    initial node writes only opaque identifiers/statuses, keeping checkpoints
    free of raw source text and draft content.
    """

    def collect_evidence(state: DraftGraphState) -> Dict[str, Any]:
        # Scoped retrieval runs before this synchronous graph invocation. This
        # node records only its completion marker and snapshot IDs, so it can
        # safely participate in Python 3.10 interrupt/checkpoint execution.
        return {"evidence_collected": bool(state.get("context_snapshot_id"))}

    def review_correspondence(state: DraftGraphState) -> Dict[str, Any]:
        # The handler supplies only a boolean/count-derived result. Raw prior
        # correspondence remains in the immutable evidence snapshot, never in
        # the graph checkpoint.
        return {"correspondence_reviewed": True}

    def user_direction_gate(state: DraftGraphState) -> Dict[str, Any]:
        if not state.get("questions_required"):
            return {}
        # The durable run holds question text and answers.  The checkpointer
        # sees IDs only, so it never retains raw user directions.
        interrupt({"kind": "user_direction", "run_id": state["run_id"]})
        return {"execution_status": "awaiting_strategy_confirmation", "next_action": "confirm_strategy"}

    def strategy_gate(state: DraftGraphState) -> Dict[str, Any]:
        interrupt({"kind": "strategy_confirmation", "run_id": state["run_id"]})
        return {"execution_status": "running", "next_action": "poll"}

    def prepare_execution(state: DraftGraphState) -> Dict[str, Any]:
        if state.get("cancellation_requested"):
            return {"execution_status": "cancelled", "next_action": "cancelled"}
        return {
            "execution_status": state.get("domain_execution_status", "completed"),
            "next_action": state.get("domain_next_action", "approve"),
        }

    def draft_generation(state: DraftGraphState) -> Dict[str, Any]:
        return {}

    def validation_review(state: DraftGraphState) -> Dict[str, Any]:
        return {}

    def legal_risk_review(state: DraftGraphState) -> Dict[str, Any]:
        return {}

    def approval_gate(state: DraftGraphState) -> Dict[str, Any]:
        return {}

    graph = StateGraph(DraftGraphState)
    graph.add_node("collect_evidence", collect_evidence)
    graph.add_node("review_correspondence", review_correspondence)
    graph.add_node("user_direction_gate", user_direction_gate)
    graph.add_node("strategy_gate", strategy_gate)
    graph.add_node("draft_generation", draft_generation)
    graph.add_node("validation_review", validation_review)
    graph.add_node("legal_risk_review", legal_risk_review)
    graph.add_node("approval_gate", approval_gate)
    graph.add_node("prepare_execution", prepare_execution)
    graph.add_edge(START, "collect_evidence")
    graph.add_edge("collect_evidence", "review_correspondence")
    graph.add_edge("review_correspondence", "user_direction_gate")
    graph.add_edge("user_direction_gate", "strategy_gate")
    graph.add_edge("strategy_gate", "draft_generation")
    graph.add_edge("draft_generation", "validation_review")
    graph.add_edge("validation_review", "legal_risk_review")
    graph.add_edge("legal_risk_review", "approval_gate")
    graph.add_edge("approval_gate", "prepare_execution")
    graph.add_edge("prepare_execution", END)
    return graph.compile(checkpointer=checkpointer)


class MongoDraftCheckpointStore:
    """Synchronous driver adapter required by langgraph-checkpoint-mongodb 0.4."""

    def __init__(self) -> None:
        database_url = str(settings.DATABASE_URL or "").strip()
        if not database_url:
            raise RuntimeError("DATABASE_URL is required for LangGraph checkpoints")
        parsed = urlparse(database_url)
        database_name = (parsed.path or "").strip("/") or "contraclaim"
        self.client = MongoClient(database_url, serverSelectionTimeoutMS=5000)
        self.saver = MongoDBSaver(
            self.client,
            db_name=database_name,
            checkpoint_collection_name="letter_draft_langgraph_checkpoints",
            writes_collection_name="letter_draft_langgraph_checkpoint_writes",
            ttl=int(settings.DRAFT_ENGINE_CHECKPOINT_RETENTION_DAYS) * 86400,
        )

    def close(self) -> None:
        self.client.close()


def _redacted_checkpoint_values(values: Dict[str, Any]) -> Dict[str, Any]:
    """Return diagnostic shape/hashes only; checkpoint history is ops-only."""
    redacted: Dict[str, Any] = {}
    for key, value in (values or {}).items():
        if key in {"run_id", "letter_id", "state_schema_version", "execution_status", "next_action"}:
            redacted[key] = value
            continue
        encoded = json.dumps(value, sort_keys=True, default=str)
        redacted[key] = {"redacted": True, "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}
    return redacted


class LangGraphDraftingEngine:
    name = "langgraph_v3"
    version = "1.2.9"

    def __init__(self, db: Any, *, queue: Any = None, checkpointer: Optional[BaseCheckpointSaver] = None) -> None:
        self.db = db
        self.repository = DraftRunRepository(db)
        self.queue = queue or get_drafting_queue()
        self.checkpointer = checkpointer

    async def create(
        self,
        letter_id: str,
        payload: DraftRunCreateRequest,
        current_user: Any,
        *,
        idempotency_key: Optional[str],
        request_hash: str,
    ) -> DraftRunAccepted:
        if not self.queue.enabled:
            raise HTTPException(status_code=503, detail="Dedicated LangGraph drafting queue is disabled")

        legacy = DraftRunService(self.db)
        letter = await legacy._load_and_authorize(
            letter_id, current_user, "write", drafting_permission="drafting.draft.create"
        )
        if idempotency_key:
            existing = await self.repository.get_by_idempotency_key(letter_id, idempotency_key)
            if existing:
                if existing.request_hash and existing.request_hash != request_hash:
                    raise HTTPException(status_code=409, detail="Idempotency-Key was already used with a different drafting request")
                return self._accepted(existing)

        started = datetime.now(timezone.utc)
        run = DraftRun(
            run_id=str(uuid.uuid4()),
            letter_id=str(letter_id),
            draft_type=payload.draft_type,
            mode=payload.mode,
            letter_category=payload.letter_category,
            contract_package=payload.contract_package,
            role=legacy._resolve_role(letter, payload),
            recipient_focus=payload.recipient_focus or getattr(letter, "strategy_recipient", None),
            inputs=legacy._inputs_payload(letter, payload),
            status="queued",
            engine="langgraph_v3",
            engine_version=self.version,
            graph_version=str(settings.DRAFT_ENGINE_GRAPH_VERSION),
            state_schema_version=int(settings.DRAFT_ENGINE_STATE_SCHEMA_VERSION),
            thread_id=str(uuid.uuid4()),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            execution_status="queued",
            next_action="poll",
            started_at=started,
            updated_at=started,
            created_by=legacy._user_id(current_user),
        )
        # The worker creates evidence snapshots after scoped retrieval.  Do not
        # create an empty immutable ledger before the evidence node runs.
        stored = await self.repository.create(run)
        await self.repository.append_event(
            stored.letter_id,
            stored.run_id,
            "created",
            actor_user_id=legacy._user_id(current_user),
            status=stored.status,
            payload={"mode": stored.mode, "draft_type": stored.draft_type, "engine": self.name},
        )
        if stored.run_id != run.run_id:
            return self._accepted(stored)
        try:
            job_id = await self.queue.enqueue(
                run.run_id,
                {"run_id": run.run_id, "letter_id": run.letter_id, "thread_id": run.thread_id},
            )
        except Exception:
            await self.repository.update_fields(
                run.letter_id,
                run.run_id,
                {"status": "failed", "execution_status": "failed", "next_action": "none"},
            )
            raise
        stored = await self.repository.update_fields(run.letter_id, run.run_id, {"queue_job_id": job_id}) or stored
        return self._accepted(stored)

    @staticmethod
    def _accepted(run: DraftRun) -> DraftRunAccepted:
        return DraftRunAccepted(
            run_id=run.run_id,
            letter_id=run.letter_id,
            engine="langgraph_v3",
            execution_status=run.execution_status,
            next_action=run.next_action,
            state_version=run.state_version,
            poll_url=f"/api/letters/{run.letter_id}/drafting/runs/{run.run_id}/state",
        )

    async def get_state(self, letter_id: str, run_id: str, current_user: Any) -> DraftRunStateResponse:
        legacy = DraftRunService(self.db)
        run = await legacy.get_run(letter_id, run_id, current_user)
        return DraftRunStateResponse(
            run_id=run.run_id,
            letter_id=run.letter_id,
            engine=run.engine,
            execution_status=run.execution_status,
            next_action=run.next_action,
            state_version=run.state_version,
            last_checkpoint_id=run.last_checkpoint_id,
            cancellation_requested_at=run.cancellation_requested_at,
            updated_at=run.updated_at,
            probing_questions=run.probing_questions,
        )

    async def resume(
        self, letter_id: str, run_id: str, payload: DraftRunResumeRequest, current_user: Any
    ) -> DraftRunStateResponse:
        legacy = DraftRunService(self.db)
        await legacy._load_and_authorize(letter_id, current_user, "write", drafting_permission="drafting.draft.create")
        run = await self.repository.get(letter_id, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Draft run not found")
        if run.engine != "langgraph_v3":
            raise HTTPException(status_code=409, detail="Only LangGraph v3 runs can be resumed")
        if run.execution_status in {"completed", "failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="Terminal draft runs cannot be resumed")
        resume_gate = run.next_action
        if run.next_action == "answer_questions":
            required = {question.question_id: question for question in run.probing_questions if question.required}
            submitted = {answer.question_id: answer for answer in payload.answers if answer.question_id}
            unknown = sorted(set(submitted) - set(required))
            missing = sorted(set(required) - set(submitted))
            if unknown or missing:
                raise HTTPException(
                    status_code=422,
                    detail={"unknown_question_ids": unknown, "missing_required_question_ids": missing},
                )
            stale = sorted(
                answer.question_id
                for answer in payload.answers
                if answer.question_id in required
                and answer.question_version is not None
                and answer.question_version != required[answer.question_id].question_version
            )
            if stale:
                raise HTTPException(status_code=409, detail={"stale_question_ids": stale})
            answers = list(payload.answers)
        elif run.next_action == "confirm_strategy":
            if not payload.strategy_approved:
                raise HTTPException(status_code=422, detail="strategy_approved=true is required to continue")
            answers = list(run.user_directions)
        else:
            raise HTTPException(status_code=409, detail="Draft run is not waiting for resumable user input")
        directions = UserDirectionAgent.format_directions(answers, payload.directions)
        inputs = dict(run.inputs)
        if directions:
            inputs["user_direction"] = directions
        updated = await self.repository.compare_and_set_execution(
            letter_id,
            run_id,
            payload.expected_state_version,
            {
                "user_directions": answers,
                "inputs": inputs,
                "status": "queued",
                "execution_status": "queued",
                "next_action": "poll",
                "resumed_at": datetime.now(timezone.utc),
                "cancellation_requested_at": None,
                "cancellation_reason": None,
            },
        )
        if not updated:
            raise HTTPException(status_code=409, detail="Draft state changed; refresh and retry")
        await self.queue.enqueue(
            updated.run_id,
            {
                "run_id": updated.run_id,
                "letter_id": updated.letter_id,
                "thread_id": updated.thread_id,
                "resume": True,
                "resume_gate": resume_gate,
            },
        )
        return await self.get_state(letter_id, run_id, current_user)

    async def cancel(
        self, letter_id: str, run_id: str, payload: DraftRunCancelRequest, current_user: Any
    ) -> DraftRunStateResponse:
        legacy = DraftRunService(self.db)
        await legacy._load_and_authorize(letter_id, current_user, "write", drafting_permission="drafting.draft.create")
        existing = await self.repository.get(letter_id, run_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Draft run not found")
        if existing.execution_status in {"completed", "failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="Terminal draft runs cannot be cancelled")
        is_active_worker = existing.execution_status in {"queued", "running"}
        updated = await self.repository.compare_and_set_execution(
            letter_id,
            run_id,
            payload.expected_state_version,
            {
                "status": "cancel_requested" if is_active_worker else "cancelled",
                "execution_status": "cancel_requested" if is_active_worker else "cancelled",
                "next_action": "poll" if is_active_worker else "cancelled",
                "cancellation_requested_at": datetime.now(timezone.utc),
                "cancellation_reason": payload.reason,
            },
        )
        if not updated:
            raise HTTPException(status_code=409, detail="Draft state changed; refresh and retry")
        await self.repository.append_event(letter_id, run_id, "cancel_requested", actor_user_id=legacy._user_id(current_user))
        return await self.get_state(letter_id, run_id, current_user)

    async def force_v2_fallback(
        self,
        letter_id: str,
        run_id: str,
        payload: ForceV2FallbackRequest,
        current_user: Any,
    ) -> DraftRun:
        """Create a linked v2 fallback only before any irreversible effect."""
        legacy = DraftRunService(self.db)
        await legacy._load_and_authorize(
            letter_id, current_user, "write", drafting_permission="drafting.workflow.force_v2"
        )
        original = await self.repository.get(letter_id, run_id)
        if not original:
            raise HTTPException(status_code=404, detail="Draft run not found")
        if original.engine != "langgraph_v3":
            raise HTTPException(status_code=409, detail="Only LangGraph v3 runs can use the v2 fallback")
        if original.status in {"approved", "exported", "issued"} or await self.repository.has_completed_effects(run_id):
            raise HTTPException(status_code=409, detail="Fallback is not eligible after approval or an external effect")
        request_fields = set(DraftRunCreateRequest.model_fields)
        request_payload = {key: value for key, value in (original.inputs or {}).items() if key in request_fields}
        request_payload["mode"] = original.mode
        if original.inputs.get("user_direction"):
            request_payload["points"] = "\n\n".join(
                part for part in (str(request_payload.get("points") or ""), str(original.inputs["user_direction"])) if part
            )
        request = DraftRunCreateRequest(**request_payload)
        transitioned = await self.repository.compare_and_set_execution(
            letter_id,
            run_id,
            payload.expected_state_version,
            {
                "status": "cancel_requested",
                "execution_status": "cancel_requested",
                "next_action": "poll",
                "fallback_reason": payload.reason,
                "cancellation_requested_at": datetime.now(timezone.utc),
            },
        )
        if not transitioned:
            raise HTTPException(status_code=409, detail="Draft state changed; refresh and retry")
        fallback = await legacy.create_run(
            letter_id,
            request,
            current_user,
            engine_metadata={
                "engine": "v2",
                "engine_version": "v2-fallback",
                "fallback_of_run_id": original.run_id,
                "fallback_reason": payload.reason,
            },
        )
        await self.repository.append_event(
            letter_id,
            run_id,
            "fallback_started",
            actor_user_id=legacy._user_id(current_user),
            status=transitioned.status,
            payload={"fallback_run_id": fallback.run_id, "reason": payload.reason},
        )
        return fallback

    async def list_checkpoints(self, run_id: str, *, limit: int = 50) -> list[Dict[str, Any]]:
        run = await self.repository.get_by_run_id(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Draft run not found")
        if run.engine != "langgraph_v3" or not run.thread_id:
            return []
        store = MongoDraftCheckpointStore()
        try:
            config = {"configurable": {"thread_id": run.thread_id}}
            results: list[Dict[str, Any]] = []
            async for item in store.saver.alist(config, limit=max(1, min(int(limit), 100))):
                checkpoint = item.checkpoint or {}
                results.append(
                    {
                        "checkpoint_id": str(item.config.get("configurable", {}).get("checkpoint_id", "")),
                        "run_id": run.run_id,
                        "thread_id": run.thread_id,
                        "node": ",".join((item.metadata or {}).get("writes", {}).keys()) or "unknown",
                        "state_schema_version": run.state_schema_version,
                        "created_at": item.metadata.get("created_at") or datetime.now(timezone.utc),
                        "redacted_state": _redacted_checkpoint_values(checkpoint.get("channel_values", {})),
                    }
                )
            return results
        finally:
            await asyncio.to_thread(store.close)

    async def collect_evidence(self, state: DraftGraphState) -> Dict[str, Any]:
        """Graph node: retrieve scoped evidence and create its immutable ledger."""
        run = await self.repository.get_by_run_id(str(state["run_id"]))
        if not run:
            raise RuntimeError("Draft run disappeared before evidence collection")
        if run.input_snapshot_id and run.context_snapshot_id:
            return {
                "input_snapshot_id": run.input_snapshot_id,
                "context_snapshot_id": run.context_snapshot_id,
                "questions_required": bool(run.probing_questions),
                "execution_status": run.execution_status,
                "next_action": run.next_action,
            }
        letter = await DraftRunService(self.db).letter_service.get_letter(run.letter_id)
        if not letter:
            raise RuntimeError("Draft letter disappeared before evidence collection")
        request_payload = dict(run.inputs or {})
        request_payload["mode"] = run.mode
        request = DraftRunCreateRequest(**request_payload)
        legacy = DraftRunService(self.db)
        worker_principal = SimpleNamespace(
            id=run.created_by,
            organization_id=getattr(letter, "organization_id", None),
            project_id=getattr(letter, "project_id", None),
        )
        builder = DraftContextBuilder(
            document_service=DocumentService(self.db),
            conversation_service=ConversationService(legacy.letter_service),
            db=self.db,
        )
        context, sources, warnings = await builder.build(letter, request, worker_principal)
        sources = await legacy._add_governance_comment_context(
            run.letter_id, context, sources, current_run_id=run.run_id
        )
        sources = legacy._with_source_hashes(sources)
        incoming_analysis = None
        questions = []
        if request.draft_type == "reply":
            incoming_analysis = await legacy.incoming_analyzer.analyze(letter, request, DocumentService(self.db))
            missing_inputs = [key for key, present in context.threshold_inputs.items() if not present]
            questions = UserDirectionAgent.build_questions(incoming_analysis, request, missing_inputs)
        updated = await self.repository.update_fields(
            run.letter_id,
            run.run_id,
            {
                "context_bundle": context,
                "sources": sources,
                "warnings": list(run.warnings) + warnings,
                "incoming_analysis": incoming_analysis,
                "probing_questions": questions,
            },
        ) or run
        snapshot_fields = await self.repository.create_immutable_snapshots(updated)
        updated = await self.repository.update_fields(run.letter_id, run.run_id, snapshot_fields) or updated
        return {
            "input_snapshot_id": updated.input_snapshot_id or "",
            "context_snapshot_id": updated.context_snapshot_id or "",
            "questions_required": bool(questions),
            "execution_status": "awaiting_user_direction" if questions else "awaiting_strategy_confirmation",
            "next_action": "answer_questions" if questions else "confirm_strategy",
        }

    async def execute_confirmed_domain_pipeline(self, run: DraftRun) -> DraftRun:
        """Run the proven domain pipeline once after the v3 human gates.

        The v2 implementation is retained as an explicit, auditable domain
        adapter during migration. LangGraph owns sequencing/checkpoints; the
        adapter provides the already-tested planner, generator, validator and
        legal-risk reviewer until their internals are fully split into nodes.
        """
        effect_key = f"{run.run_id}:domain_generation"
        existing_effect = await self.repository.get_effect(effect_key)
        if existing_effect and existing_effect.status == "completed":
            latest = await self.repository.get(run.letter_id, run.run_id)
            return latest or run
        effect = await self.repository.record_effect(
            DraftExecutionEffect(
                effect_id=str(uuid.uuid4()),
                effect_key=effect_key,
                run_id=run.run_id,
                effect_type="domain_generation",
                payload_hash=hashlib.sha256((run.input_snapshot_hash or run.request_hash or run.run_id).encode("utf-8")).hexdigest(),
            )
        )
        if effect.status == "completed":
            latest = await self.repository.get(run.letter_id, run.run_id)
            return latest or run

        legacy = DraftRunService(self.db)
        letter = await legacy.letter_service.get_letter(run.letter_id)
        if not letter:
            raise RuntimeError("Draft letter disappeared before domain generation")
        request_payload = dict(run.inputs or {})
        request_payload["mode"] = run.mode
        if request_payload.get("user_direction"):
            points = str(request_payload.get("points") or "").strip()
            request_payload["points"] = "\n\n".join(part for part in (points, request_payload["user_direction"]) if part)
        request = DraftRunCreateRequest(**request_payload)

        async def _already_authorized(*_args: Any, **_kwargs: Any):
            return letter

        legacy._load_and_authorize = _already_authorized  # type: ignore[method-assign]
        worker_principal = SimpleNamespace(
            id=run.created_by,
            organization_id=getattr(letter, "organization_id", None),
            project_id=getattr(letter, "project_id", None),
        )
        generated = await legacy.create_run(
            run.letter_id,
            request,
            worker_principal,
            engine_metadata={"engine": "v2", "engine_version": "v2-domain-adapter", "parent_run_id": run.run_id},
        )
        fields = {
            "incoming_analysis": generated.incoming_analysis,
            "planning_sheet": generated.planning_sheet,
            "reply_matrix": generated.reply_matrix,
            "sources": generated.sources,
            "context_bundle": generated.context_bundle,
            "plan": generated.plan,
            "draft_artifact": generated.draft_artifact,
            "source_integrity_summary": generated.source_integrity_summary,
            "validation_report": generated.validation_report,
            "legal_risk_report": generated.legal_risk_report,
            "cyclic_trace": generated.cyclic_trace,
            "assertion_support": generated.assertion_support,
            "confidence_scores": generated.confidence_scores,
            "iteration_count": generated.iteration_count,
            "warnings": list(run.warnings) + list(generated.warnings),
            "status": generated.status,
            "execution_status": "failed" if generated.status == "failed" else "completed",
            "next_action": "none" if generated.status == "failed" else "approve",
        }
        copied = await self.repository.update_fields(run.letter_id, run.run_id, fields) or run
        await self.repository.complete_effect(effect_key)
        return copied


async def process_drafting_job(payload: Dict[str, Any], *, worker_name: str) -> None:
    """Worker entrypoint; only IDs cross the durable Redis boundary."""
    db = await get_database()
    engine = LangGraphDraftingEngine(db)
    run = await engine.repository.get(str(payload["letter_id"]), str(payload["run_id"]))
    if not run:
        return
    if run.execution_status == "cancel_requested":
        await engine.repository.update_fields(
            run.letter_id,
            run.run_id,
            {"status": "cancelled", "execution_status": "cancelled", "next_action": "cancelled"},
        )
        return
    running = await engine.repository.compare_and_set_execution(
        run.letter_id,
        run.run_id,
        run.state_version,
        {"status": "running", "execution_status": "running", "next_action": "poll", "lease_owner": worker_name},
    )
    if not running:
        return
    store = MongoDraftCheckpointStore()
    try:
        evidence_state = await engine.collect_evidence({"run_id": running.run_id})
        graph = build_drafting_graph(checkpointer=store.saver)
        config = {"configurable": {"thread_id": running.thread_id}}
        domain_status: Optional[str] = None
        if payload.get("resume"):
            update: Dict[str, Any] = {}
            if payload.get("resume_gate") == "confirm_strategy":
                domain_run = await engine.execute_confirmed_domain_pipeline(running)
                domain_status = domain_run.status
                update = {
                    "domain_execution_status": domain_run.execution_status,
                    "domain_next_action": domain_run.next_action,
                }
            result = await asyncio.to_thread(
                graph.invoke,
                Command(resume={"run_id": running.run_id}, update=update or None),
                config,
            )
        else:
            result = await asyncio.to_thread(
                graph.invoke,
                {
                    "run_id": running.run_id,
                    "letter_id": running.letter_id,
                    "state_schema_version": running.state_schema_version,
                    "cancellation_requested": False,
                    **evidence_state,
                },
                config,
            )
        state = await asyncio.to_thread(graph.get_state, config)
        checkpoint_id = str(state.config.get("configurable", {}).get("checkpoint_id", "")) or None
        await engine.repository.update_fields(
            running.letter_id,
            running.run_id,
            {
                "status": domain_status or result.get("execution_status", "awaiting_user_direction"),
                "execution_status": result.get("execution_status", "awaiting_user_direction"),
                "next_action": result.get("next_action", "answer_questions"),
                "last_checkpoint_id": checkpoint_id,
                "lease_owner": None,
            },
        )
    finally:
        await asyncio.to_thread(store.close)
