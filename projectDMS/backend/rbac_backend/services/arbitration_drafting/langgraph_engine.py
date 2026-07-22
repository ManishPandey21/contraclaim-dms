"""Official in-process LangGraph orchestration for arbitration pleadings.

Only opaque identifiers, hashes, booleans, counters and statuses are admitted
to checkpoint state. Evidence, opponent pleadings, prompts, plans and draft text
remain in scoped immutable MongoDB artifacts referenced by ID.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TypedDict
from urllib.parse import urlparse

from fastapi import HTTPException
from pymongo import MongoClient

from ...core.config import settings
from .engines.v2 import ArbitrationV2WorkflowEngine

try:  # Core graph stays independently testable when the optional Mongo plugin is absent.
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    LANGGRAPH_RUNTIME_AVAILABLE = True
except Exception:  # pragma: no cover - exercised in dependency-parity smoke tests
    BaseCheckpointSaver = Any  # type: ignore[assignment,misc]
    StateGraph = None  # type: ignore[assignment]
    Command = None  # type: ignore[assignment]
    START = "__start__"
    END = "__end__"
    LANGGRAPH_RUNTIME_AVAILABLE = False

try:  # Deployment capability is separate from the in-memory graph capability.
    from langgraph.checkpoint.mongodb import MongoDBSaver
except Exception:  # pragma: no cover - covered by runtime dependency-parity checks
    MongoDBSaver = None  # type: ignore[assignment]


class ArbitrationGraphState(TypedDict, total=False):
    run_id: str
    thread_id: str
    case_id: str
    draft_id: str
    organization_id: str
    project_id: str
    pleading_type: str
    graph_version: str
    state_schema_version: int
    execution_status: str
    current_node: str
    next_action: str
    state_version: int
    input_snapshot_id: str
    input_snapshot_hash: str
    document_manifest_id: str
    document_manifest_hash: str
    evidence_snapshot_id: str
    evidence_snapshot_hash: str
    opponent_pleading_snapshot_id: str
    opponent_pleading_snapshot_hash: str
    analysis_artifact_set_id: str
    analysis_artifact_set_hash: str
    matrix_revision_set_id: str
    matrix_revision_hash: str
    readiness_artifact_id: str
    readiness_artifact_hash: str
    plan_id: str
    plan_hash: str
    draft_version_id: str
    draft_version_hash: str
    validation_artifact_set_id: str
    validation_artifact_set_hash: str
    validation_report_id: str
    validation_report_hash: str
    validation_status: str
    validation_route: str
    remediation_artifact_id: str
    remediation_artifact_hash: str
    documents_selected: bool
    material_questions_required: bool
    user_direction_complete: bool
    documents_analyzed: bool
    chronology_analyzed: bool
    clause_analysis_complete: bool
    pleading_position_complete: bool
    quantum_analysis_complete: bool
    matrices_approved: bool
    readiness_approved: bool
    plan_approved: bool
    draft_generated: bool
    citations_validated: bool
    assertions_validated: bool
    legal_structure_validated: bool
    new_matter_validated: bool
    quantum_validated: bool
    duplication_validated: bool
    exhibits_validated: bool
    source_drift_validated: bool
    validation_complete: bool
    legal_review_approved: bool
    draft_approved: bool
    export_authorized: bool
    cancellation_requested: bool
    remediation_cycle: int
    retry_budget_remaining: int


ALLOWED_CHECKPOINT_KEYS = frozenset(ArbitrationGraphState.__annotations__)
RAW_CHECKPOINT_KEY_FRAGMENTS = {"text", "content", "prompt", "evidence", "markdown", "opponent_pleading"}
BOOLEAN_CHECKPOINT_KEYS = {
    "documents_selected",
    "material_questions_required",
    "user_direction_complete",
    "documents_analyzed",
    "chronology_analyzed",
    "clause_analysis_complete",
    "pleading_position_complete",
    "quantum_analysis_complete",
    "matrices_approved",
    "readiness_approved",
    "plan_approved",
    "draft_generated",
    "citations_validated",
    "assertions_validated",
    "legal_structure_validated",
    "new_matter_validated",
    "quantum_validated",
    "duplication_validated",
    "exhibits_validated",
    "source_drift_validated",
    "validation_complete",
    "legal_review_approved",
    "draft_approved",
    "export_authorized",
    "cancellation_requested",
}
INTEGER_CHECKPOINT_KEYS = {
    "state_schema_version",
    "state_version",
    "remediation_cycle",
    "retry_budget_remaining",
}


def validate_checkpoint_state(state: Dict[str, Any]) -> None:
    unknown = set(state).difference(ALLOWED_CHECKPOINT_KEYS)
    raw_keys = {
        key
        for key in state
        if any(fragment in key.lower() for fragment in RAW_CHECKPOINT_KEY_FRAGMENTS)
        and not key.endswith(("_id", "_hash"))
        and key not in {"evidence_snapshot_id", "evidence_snapshot_hash", "opponent_pleading_snapshot_id", "opponent_pleading_snapshot_hash"}
    }
    if unknown or raw_keys:
        raise ValueError(f"Unsafe arbitration checkpoint fields: {sorted(unknown | raw_keys)}")
    invalid_types = []
    oversized_strings = []
    for key, value in state.items():
        if value is None:
            continue
        if key in BOOLEAN_CHECKPOINT_KEYS:
            if not isinstance(value, bool):
                invalid_types.append(key)
        elif key in INTEGER_CHECKPOINT_KEYS:
            if not isinstance(value, int) or isinstance(value, bool):
                invalid_types.append(key)
        elif not isinstance(value, str):
            invalid_types.append(key)
        elif len(value) > 1024:
            oversized_strings.append(key)
    if invalid_types:
        raise ValueError(f"Invalid arbitration checkpoint value types: {sorted(invalid_types)}")
    if oversized_strings:
        raise ValueError(f"Oversized arbitration checkpoint string fields: {sorted(oversized_strings)}")
    encoded = json.dumps(state, sort_keys=True, default=str).encode("utf-8")
    if len(encoded) > int(settings.ARBITRATION_ENGINE_MAX_CHECKPOINT_BYTES):
        raise ValueError("Arbitration checkpoint exceeds configured size limit")


def _gate(kind: str, required_flag: str, next_status: str, next_action: str):
    def node(state: ArbitrationGraphState) -> Dict[str, Any]:
        if state.get("cancellation_requested"):
            return {"execution_status": "cancelled", "current_node": "cancelled", "next_action": "cancelled"}
        if state.get(required_flag):
            return {}
        interrupt({"kind": kind, "run_id": state["run_id"], "state_version": state.get("state_version")})
        return {"execution_status": next_status, "current_node": kind, "next_action": next_action}

    return node


def build_arbitration_graph(*, checkpointer: Optional[BaseCheckpointSaver] = None):
    if not LANGGRAPH_RUNTIME_AVAILABLE or StateGraph is None:
        raise RuntimeError("Official LangGraph MongoDB checkpoint dependencies are unavailable")

    def marker(node: str, **values: Any):
        def run(state: ArbitrationGraphState) -> Dict[str, Any]:
            update = {"current_node": node, **values}
            validate_checkpoint_state({**state, **update})
            return update

        return run

    def artifact_marker(node: str, *required_fields: str, **values: Any):
        def run(state: ArbitrationGraphState) -> Dict[str, Any]:
            missing = [field for field in required_fields if not state.get(field)]
            if missing:
                raise ValueError(f"{node} requires durable artifact references: {sorted(missing)}")
            update = {"current_node": node, **values}
            validate_checkpoint_state({**state, **update})
            return update

        return run

    def validation_route(state: ArbitrationGraphState) -> str:
        return "remediate" if state.get("validation_route") == "remediate" else "review"

    def completion_marker(flag: str):
        def run(state: ArbitrationGraphState) -> Dict[str, bool]:
            return {flag: True}

        return run

    graph = StateGraph(ArbitrationGraphState)
    graph.add_node("validate_intake", marker("validate_intake", execution_status="running", next_action="poll"))
    graph.add_node("capture_input_snapshot", marker("capture_input_snapshot"))
    graph.add_node("document_selection_gate", _gate("document_selection_gate", "documents_selected", "awaiting_document_selection", "select_documents"))
    graph.add_node("analyze_documents", lambda state: {"documents_analyzed": True})
    graph.add_node("analyze_chronology", lambda state: {"chronology_analyzed": True})
    graph.add_node("analyze_clause_jurisdiction_notice", lambda state: {"clause_analysis_complete": True})
    graph.add_node("analyze_pleading_position", lambda state: {"pleading_position_complete": True})
    graph.add_node("analyze_quantum_expert", lambda state: {"quantum_analysis_complete": True})
    graph.add_node("merge_evidence_and_matrices", marker("merge_evidence_and_matrices"))
    graph.add_node("material_question_gate", _gate("material_question_gate", "user_direction_complete", "awaiting_user_direction", "answer_questions"))
    graph.add_node("matrix_review_gate", _gate("matrix_review_gate", "matrices_approved", "awaiting_matrix_review", "review_matrices"))
    graph.add_node("readiness_approval_gate", _gate("readiness_approval_gate", "readiness_approved", "awaiting_readiness_approval", "approve_readiness"))
    graph.add_node("build_pleading_plan", artifact_marker("build_pleading_plan", "plan_id", "plan_hash"))
    graph.add_node("plan_approval_gate", _gate("plan_approval_gate", "plan_approved", "awaiting_plan_approval", "approve_plan"))
    graph.add_node(
        "generate_draft",
        artifact_marker("generate_draft", "draft_version_id", "draft_version_hash", draft_generated=True),
    )
    validation_nodes = {
        "validate_citations": "citations_validated",
        "validate_assertions": "assertions_validated",
        "validate_legal_structure": "legal_structure_validated",
        "validate_new_matter": "new_matter_validated",
        "validate_quantum": "quantum_validated",
        "validate_duplication": "duplication_validated",
        "validate_exhibits": "exhibits_validated",
        "validate_source_drift": "source_drift_validated",
    }
    for node, flag in validation_nodes.items():
        graph.add_node(node, completion_marker(flag))
    graph.add_node(
        "merge_validation_artifacts",
        artifact_marker(
            "merge_validation_artifacts",
            "validation_artifact_set_id",
            "validation_artifact_set_hash",
            "validation_report_id",
            "validation_report_hash",
            validation_complete=True,
        ),
    )
    graph.add_node(
        "remediate_draft",
        artifact_marker(
            "remediate_draft",
            "remediation_artifact_id",
            "remediation_artifact_hash",
            validation_route="legal_review",
        ),
    )
    graph.add_node("legal_review_gate", _gate("legal_review_gate", "legal_review_approved", "awaiting_legal_review", "legal_review"))
    graph.add_node("draft_approval_gate", _gate("draft_approval_gate", "draft_approved", "awaiting_draft_approval", "approve_draft"))
    graph.add_node("export_authorization_gate", _gate("export_authorization_gate", "export_authorized", "awaiting_export_authorization", "authorize_export"))
    graph.add_node("complete", marker("complete", execution_status="completed", next_action="completed"))
    graph.add_edge(START, "validate_intake")
    graph.add_edge("validate_intake", "capture_input_snapshot")
    graph.add_edge("capture_input_snapshot", "document_selection_gate")
    # Official fan-out: branches are read-only and write disjoint completion markers.
    for branch in ("analyze_documents", "analyze_chronology", "analyze_clause_jurisdiction_notice", "analyze_pleading_position", "analyze_quantum_expert"):
        graph.add_edge("document_selection_gate", branch)
    graph.add_edge(
        ["analyze_documents", "analyze_chronology", "analyze_clause_jurisdiction_notice", "analyze_pleading_position", "analyze_quantum_expert"],
        "merge_evidence_and_matrices",
    )
    ordered = [
        "material_question_gate", "matrix_review_gate", "readiness_approval_gate", "build_pleading_plan",
        "plan_approval_gate", "generate_draft",
    ]
    graph.add_edge("merge_evidence_and_matrices", ordered[0])
    for left, right in zip(ordered, ordered[1:]):
        graph.add_edge(left, right)
    for node in validation_nodes:
        graph.add_edge("generate_draft", node)
    graph.add_edge(list(validation_nodes), "merge_validation_artifacts")
    graph.add_conditional_edges(
        "merge_validation_artifacts",
        validation_route,
        {"remediate": "remediate_draft", "review": "legal_review_gate"},
    )
    for node in validation_nodes:
        graph.add_edge("remediate_draft", node)
    graph.add_edge("legal_review_gate", "draft_approval_gate")
    graph.add_edge("draft_approval_gate", "export_authorization_gate")
    graph.add_edge("export_authorization_gate", "complete")
    graph.add_edge("complete", END)
    return graph.compile(checkpointer=checkpointer)


class MongoArbitrationCheckpointStore:
    def __init__(self) -> None:
        if MongoDBSaver is None:
            raise RuntimeError("langgraph-checkpoint-mongodb is unavailable")
        database_url = str(settings.DATABASE_URL or "").strip()
        parsed = urlparse(database_url)
        database_name = (parsed.path or "").strip("/") or "contraclaim"
        self.client = MongoClient(database_url, serverSelectionTimeoutMS=5000)
        self.saver = MongoDBSaver(
            self.client,
            db_name=database_name,
            checkpoint_collection_name="arbitration_langgraph_checkpoints",
            writes_collection_name="arbitration_langgraph_checkpoint_writes",
            ttl=int(settings.ARBITRATION_ENGINE_CHECKPOINT_RETENTION_DAYS) * 86400,
        )

    def close(self) -> None:
        self.client.close()


class LangGraphArbitrationEngine(ArbitrationV2WorkflowEngine):
    name = "langgraph_v1"
    version = "2"

    def __init__(self, db: Any, *, checkpointer: Optional[BaseCheckpointSaver] = None) -> None:
        super().__init__(db)
        self.checkpointer = checkpointer

    async def shadow_route_projection(self, run: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the official graph without Mongo or domain side effects.

        The isolated in-memory thread proves routing/gate parity while all legal
        artifacts remain the immutable records already referenced by ``run``.
        """
        if not LANGGRAPH_RUNTIME_AVAILABLE:
            raise RuntimeError("Official arbitration LangGraph runtime is unavailable for shadow execution")
        from langgraph.checkpoint.memory import InMemorySaver

        graph = build_arbitration_graph(checkpointer=InMemorySaver())
        state: ArbitrationGraphState = {
            key: run[key]
            for key in ALLOWED_CHECKPOINT_KEYS
            if key in run and run[key] is not None
        }
        state.update(self._cumulative_gate_state(run))
        state.update(
            {
                "run_id": str(run["_id"]),
                "thread_id": f"shadow:{run['_id']}:{int(run.get('state_version') or 0)}",
                "execution_status": "running",
                "state_version": int(run.get("state_version") or 0),
                "documents_selected": bool(run.get("documents_selected")),
                "material_questions_required": bool(run.get("material_questions_required")),
                "retry_budget_remaining": int(settings.ARBITRATION_ENGINE_RETRY_BUDGET),
            }
        )
        validate_checkpoint_state(state)
        config = {"configurable": {"thread_id": state["thread_id"]}}
        # The installed Mongo saver is synchronous, so shadow execution uses
        # the same executor boundary as production checkpoint operations.
        await asyncio.to_thread(graph.invoke, state, config)
        snapshot = await asyncio.to_thread(graph.get_state, config)
        interrupts = getattr(snapshot, "interrupts", ()) or ()
        next_nodes = tuple(str(value) for value in (getattr(snapshot, "next", ()) or ()))
        return {
            "projected_node": next_nodes[0] if len(next_nodes) == 1 else str((snapshot.values or {}).get("current_node") or "unknown"),
            "next_node_count": len(next_nodes),
            "interrupt_count": len(interrupts),
        }

    async def create_workflow(self, *args: Any, **kwargs: Any) -> dict:
        if not LANGGRAPH_RUNTIME_AVAILABLE or (self.checkpointer is None and MongoDBSaver is None):
            raise HTTPException(status_code=503, detail="Official arbitration LangGraph checkpoint runtime is unavailable")
        existing_run = None
        idempotency_key = kwargs.get("idempotency_key")
        if idempotency_key and args:
            existing_run = await self.repository.get_by_idempotency_key(str(args[0]), str(idempotency_key))
        run = await super().create_workflow(*args, **kwargs)
        if existing_run and run.get("engine") != self.name:
            # Idempotency binds the original engine decision as well as inputs;
            # a later rollout-policy change must not convert an existing run.
            return run
        if run.get("engine") != self.name:
            run = await self.repository.transition(
                run["_id"], run["state_version"], {"engine": self.name, "engine_version": self.version}, event="langgraph_selected"
            )
        elif run.get("last_checkpoint_at"):
            # A retried 202 create with the same idempotency key must not append
            # another graph execution or advance the workflow state version.
            return run
        state: ArbitrationGraphState = {
            key: run[key]
            for key in ALLOWED_CHECKPOINT_KEYS
            if key in run
        }
        state["run_id"] = str(run["_id"])
        state["execution_status"] = "running"
        state["documents_selected"] = bool(run.get("documents_selected"))
        state["material_questions_required"] = bool(run.get("material_questions_required"))
        state["user_direction_complete"] = not bool(run.get("material_questions_required"))
        state["retry_budget_remaining"] = int(settings.ARBITRATION_ENGINE_RETRY_BUDGET)
        await self._checkpoint(run, initial_state=state)
        return await self.get_state(run["_id"])

    async def checkpoint_transition(self, run: Dict[str, Any], update: Dict[str, Any]) -> None:
        if not LANGGRAPH_RUNTIME_AVAILABLE or (self.checkpointer is None and MongoDBSaver is None):
            raise HTTPException(status_code=503, detail="Official arbitration LangGraph checkpoint runtime is unavailable")
        await self._checkpoint(run, resume_update={**self._cumulative_gate_state(run), **update})

    async def checkpoint_state_update(self, run: Dict[str, Any], update: Dict[str, Any]) -> None:
        """Persist terminal/operational state without traversing the graph."""
        if not LANGGRAPH_RUNTIME_AVAILABLE or (self.checkpointer is None and MongoDBSaver is None):
            raise HTTPException(status_code=503, detail="Official arbitration LangGraph checkpoint runtime is unavailable")
        await self._checkpoint(run, resume_update=update, traverse=False)

    @staticmethod
    def _cumulative_gate_state(run: Dict[str, Any]) -> Dict[str, Any]:
        """Replays every completed gate so a lagging checkpoint can catch up."""
        return {
            "documents_selected": bool(run.get("documents_selected")),
            "user_direction_complete": (
                not bool(run.get("material_questions_required"))
                or bool(run.get("user_direction_snapshot_id"))
            ),
            "matrices_approved": bool(run.get("matrix_review_approval_receipt_id")),
            "readiness_approved": bool(run.get("readiness_approval_receipt_id")),
            "plan_approved": bool(run.get("plan_approval_receipt_id")),
            "draft_generated": bool(run.get("draft_version_id") and run.get("draft_version_hash")),
            "validation_complete": bool(
                run.get("validation_artifact_set_id") and run.get("validation_artifact_set_hash")
            ),
            "legal_review_approved": bool(run.get("legal_review_approval_receipt_id")),
            "draft_approved": bool(run.get("draft_approval_receipt_id")),
            "export_authorized": bool(run.get("export_approval_receipt_id")),
        } | {
            key: run[key]
            for key in (
                "plan_id",
                "plan_hash",
                "draft_version_id",
                "draft_version_hash",
                "validation_artifact_set_id",
                "validation_artifact_set_hash",
                "validation_report_id",
                "validation_report_hash",
                "validation_status",
                "validation_route",
                "remediation_artifact_id",
                "remediation_artifact_hash",
                "remediation_cycle",
            )
            if run.get(key) is not None
        }

    async def _checkpoint(
        self,
        run: Dict[str, Any],
        *,
        initial_state: Optional[Dict[str, Any]] = None,
        resume_update: Optional[Dict[str, Any]] = None,
        traverse: bool = True,
    ) -> None:
        store = None
        checkpointer = self.checkpointer
        if checkpointer is None:
            store = MongoArbitrationCheckpointStore()
            checkpointer = store.saver
        try:
            graph = build_arbitration_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": run["thread_id"]}}
            if initial_state is not None:
                validate_checkpoint_state(initial_state)
                existing = await asyncio.to_thread(graph.get_state, config)
                existing_values = dict(getattr(existing, "values", {}) or {})
                if existing_values:
                    # Recovery path for a process that checkpointed successfully
                    # but exited before it recorded last_checkpoint_at on the run.
                    validate_checkpoint_state(existing_values)
                else:
                    await asyncio.to_thread(graph.invoke, initial_state, config)
            else:
                unsafe_keys = set(resume_update or {}).difference(ALLOWED_CHECKPOINT_KEYS)
                if unsafe_keys:
                    raise ValueError(f"Unsafe arbitration checkpoint fields: {sorted(unsafe_keys)}")
                safe_update = dict(resume_update or {})
                safe_update["state_version"] = int(run.get("state_version") or 1)
                validate_checkpoint_state(safe_update)
                if traverse:
                    await asyncio.to_thread(
                        graph.invoke,
                        Command(resume={"run_id": run["_id"]}, update=safe_update),
                        config,
                    )
                else:
                    await asyncio.to_thread(graph.update_state, config, safe_update)
            checkpoint = await asyncio.to_thread(graph.get_state, config)
            values = dict(getattr(checkpoint, "values", {}) or {})
            validate_checkpoint_state(values)
            checkpoint_id = str((getattr(checkpoint, "config", {}) or {}).get("configurable", {}).get("checkpoint_id", "")) or None
            await self.db.arbitration_workflow_runs.update_one(
                {"_id": run["_id"]},
                {"$set": {"last_checkpoint_id": checkpoint_id, "last_checkpoint_at": datetime.now(timezone.utc)}},
            )
        finally:
            if store:
                await asyncio.to_thread(store.close)

    async def list_checkpoints(self, run_id: str, *, limit: int = 50) -> list[Dict[str, Any]]:
        run = await self.get_state(run_id)
        if run.get("engine") != self.name:
            return []
        store = MongoArbitrationCheckpointStore()
        try:
            config = {"configurable": {"thread_id": run["thread_id"]}}
            results = []
            async for item in store.saver.alist(config, limit=max(1, min(int(limit), 100))):
                values = (item.checkpoint or {}).get("channel_values", {})
                results.append(
                    {
                        "checkpoint_id": str(item.config.get("configurable", {}).get("checkpoint_id", "")),
                        "run_id": run_id,
                        "node": ",".join((item.metadata or {}).get("writes", {}).keys()) or "unknown",
                        "created_at": (item.metadata or {}).get("created_at"),
                        "redacted_state": redact_checkpoint(values),
                    }
                )
            return results
        finally:
            await asyncio.to_thread(store.close)


def redact_checkpoint(values: Dict[str, Any]) -> Dict[str, Any]:
    validate_checkpoint_state(values)
    return {
        key: value if key in {"run_id", "case_id", "draft_id", "execution_status", "current_node", "next_action", "state_version"}
        else {"redacted": True, "sha256": hashlib.sha256(json.dumps(value, default=str).encode()).hexdigest()}
        for key, value in values.items()
    }
