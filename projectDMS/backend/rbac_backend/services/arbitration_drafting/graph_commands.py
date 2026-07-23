from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Dict, Optional, TypedDict

from fastapi import HTTPException

from ...core.config import settings
from ...models.arbitration_drafting import (
    ArbitrationGenerateRequest,
    ArbitrationReadinessApprovalRequest,
    ArbitrationWorkflowCreateRequest,
)
from .case_workspace import ArbitrationCaseWorkspaceService, _actor_id
from .matrix_registry import MATRIX_COLLECTIONS
from .repository import ArbitrationDraftingRepository
from .service import ArbitrationDraftingService, immutable_version_hash
from .workflow_domain import ANALYSIS_NODE_BRANCHES, ArbitrationWorkflowDomain
from .workflow_repository import ArbitrationWorkflowRepository, artifact_hash
from .workflow_validation import ArbitrationValidationOrchestrator, VALIDATION_BRANCHES


class GraphNodeOutput(TypedDict, total=False):
    state_version: int
    execution_status: str
    current_node: str
    next_action: str
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
    remediation_cycle: int
    filing_export_id: str
    filing_export_effect_key: str


VALIDATION_NODE_BRANCH = {
    "validate_citations": "citations",
    "validate_assertions": "assertions",
    "validate_legal_structure": "legal_structure",
    "validate_new_matter": "new_matter",
    "validate_quantum": "quantum",
    "validate_duplication": "duplication",
    "validate_exhibits": "exhibits",
    "validate_source_drift": "source_drift",
}


def classify_retry(exc: Exception) -> str:
    """Classify node failures without persisting provider or legal-content details."""

    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return "transient"
    if isinstance(exc, HTTPException) and int(exc.status_code) in {408, 425, 429, 502, 503, 504}:
        return "transient"
    return "permanent"


class ArbitrationGraphCommandExecutor:
    """Node-scoped authoritative commands for the official arbitration graph.

    The executor is deliberately not a workflow adapter.  Every public method
    represents one graph node, reserves one deterministic effect key, returns
    only checkpoint-safe IDs/hashes/statuses, and performs run updates through
    compare-and-set transitions.
    """

    def __init__(self, db: Any, *, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self.db = db
        self.loop = loop
        self.repository = ArbitrationWorkflowRepository(db)
        self.cases = ArbitrationCaseWorkspaceService(db)
        self.domain = ArbitrationWorkflowDomain(db)
        self.drafts = ArbitrationDraftingRepository(db)
        self.drafting = ArbitrationDraftingService(db)
        self.validation = ArbitrationValidationOrchestrator()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> "ArbitrationGraphCommandExecutor":
        self.loop = loop
        return self

    def run_sync(self, node: str, state: Dict[str, Any]) -> Dict[str, Any]:
        if self.loop is None:
            raise RuntimeError("Arbitration graph command executor is not bound to an event loop")
        future = asyncio.run_coroutine_threadsafe(self.execute(node, dict(state)), self.loop)
        return future.result()

    @staticmethod
    def _actor(run: Dict[str, Any]) -> Any:
        actor_id = str(run.get("created_by") or "workflow-system")
        return SimpleNamespace(id=actor_id, email=actor_id)

    async def _run_effect(
        self,
        node: str,
        state: Dict[str, Any],
        operation: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        run = await self.repository.get_run(str(state["run_id"]))
        if not run:
            raise HTTPException(status_code=404, detail="Arbitration workflow run not found")
        common = {
            "node": node,
            "run_id": run["_id"],
            "input_snapshot_hash": run.get("input_snapshot_hash"),
        }
        if node in ANALYSIS_NODE_BRANCHES:
            dependencies = {
                "document_manifest_hash": run.get("document_manifest_hash"),
                "evidence_snapshot_hash": run.get("evidence_snapshot_hash"),
                "opponent_pleading_snapshot_hash": run.get("opponent_pleading_snapshot_hash"),
            }
        elif node == "merge_evidence_and_matrices":
            dependencies = {
                f"analysis_{suffix}_{field}": state.get(f"analysis_{suffix}_{field}")
                for graph_node in ANALYSIS_NODE_BRANCHES
                for suffix in [graph_node.removeprefix("analyze_")]
                for field in ("id", "hash")
            }
        elif node == "build_pleading_plan":
            dependencies = {
                "matrix_revision_hash": run.get("matrix_revision_hash"),
                "readiness_artifact_hash": run.get("readiness_artifact_hash"),
                "readiness_approval_receipt_id": run.get("readiness_approval_receipt_id"),
            }
        elif node == "generate_draft":
            dependencies = {
                "plan_id": run.get("plan_id"),
                "plan_hash": run.get("plan_hash"),
                "plan_approval_receipt_id": run.get("plan_approval_receipt_id"),
            }
        elif node in VALIDATION_NODE_BRANCH or node == "merge_validation_artifacts":
            dependencies = {
                "draft_version_id": run.get("draft_version_id"),
                "draft_version_hash": run.get("draft_version_hash"),
                "remediation_cycle": int(run.get("remediation_cycle") or 0),
                **(
                    {
                        f"validation_{branch}_{field}": state.get(f"validation_{branch}_{field}")
                        for branch in VALIDATION_BRANCHES
                        for field in ("id", "hash")
                    }
                    if node == "merge_validation_artifacts"
                    else {}
                ),
            }
        elif node == "remediate_draft":
            dependencies = {
                "draft_version_hash": run.get("draft_version_hash"),
                "validation_report_hash": run.get("validation_report_hash"),
                "remediation_cycle": int(run.get("remediation_cycle") or 0),
            }
        elif node == "commit_draft_approval":
            dependencies = {
                "draft_version_hash": run.get("draft_version_hash"),
                "draft_approval_receipt_id": run.get("draft_approval_receipt_id"),
            }
        elif node == "create_filing_export":
            dependencies = {
                "draft_version_hash": run.get("draft_version_hash"),
                "export_approval_receipt_id": run.get("export_approval_receipt_id"),
            }
        else:
            dependencies = {}
        input_hash = artifact_hash({**common, **dependencies})
        effect_key = f"{run['_id']}:graph-node:{node}:{input_hash}"
        effect = await self.repository.claim_effect(
            run_id=str(run["_id"]),
            effect_key=effect_key,
            effect_type=f"graph_node:{node}",
            input_hash=input_hash,
        )
        if effect.get("status") == "completed":
            return dict(effect.get("output_refs") or {})
        if not effect.get("_claimed_now"):
            if effect.get("_attempt_limit_reached"):
                raise HTTPException(
                    status_code=409,
                    detail=f"Graph node effect exhausted its recovery attempt budget: {node}",
                )
            raise HTTPException(status_code=409, detail=f"Graph node effect is already active: {node}")
        lease_token = str(effect.get("lease_token") or "")
        if not lease_token:
            raise HTTPException(status_code=409, detail=f"Graph node effect lease token is missing: {node}")

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(30)
                if not await self.repository.renew_effect(effect_key, lease_token):
                    raise RuntimeError(f"Graph node effect lease was lost: {node}")

        heartbeat_task = asyncio.create_task(heartbeat())
        operation_task = asyncio.create_task(operation(run))
        try:
            if heartbeat_task:
                done, _pending = await asyncio.wait(
                    {operation_task, heartbeat_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if heartbeat_task in done:
                    operation_task.cancel()
                    try:
                        await operation_task
                    except asyncio.CancelledError:
                        pass
                    await heartbeat_task
            output = await operation_task
            await self.repository.complete_effect(
                effect_key,
                output,
                lease_token=lease_token,
            )
            return output
        except Exception as exc:
            retry_class = classify_retry(exc)
            await self.repository.fail_effect(
                effect_key,
                error_code=type(exc).__name__,
                retryable=retry_class == "transient",
                retry_after_seconds=1 if retry_class == "transient" else 0,
                lease_token=lease_token,
            )
            raise
        finally:
            if heartbeat_task and not heartbeat_task.done():
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def bootstrap_workflow(
        self,
        case_id: str,
        payload: ArbitrationWorkflowCreateRequest,
        current_user: Any,
        *,
        idempotency_key: Optional[str],
        request_hash: str,
        rollout_mode: str,
        rollout_decision: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        case = await self.cases.get_case(case_id)
        if idempotency_key:
            existing = await self.repository.get_by_idempotency_key(case_id, idempotency_key)
            if existing:
                if existing.get("request_hash") != request_hash:
                    raise HTTPException(status_code=409, detail="Idempotency-Key was reused with a different workflow request")
                return existing
        draft = None
        if payload.draft_id:
            draft = await self.drafts.get_draft(payload.draft_id)
            if not draft or str(draft.get("case_id") or "") != case_id:
                raise HTTPException(status_code=404, detail="Case-linked arbitration draft not found")
            if str(draft.get("draft_type") or "") != str(payload.pleading_type):
                raise HTTPException(status_code=422, detail="Workflow pleading type does not match the linked draft")
        documents = []
        for document_id in sorted(set(payload.selected_document_ids)):
            record = await self.db.documents.find_one(
                {
                    "_id": document_id,
                    "organization_id": case.get("organization_id"),
                    "project_id": case.get("project_id"),
                }
            )
            if not record:
                raise HTTPException(status_code=400, detail=f"Selected document is outside case scope: {document_id}")
            documents.append(
                {
                    "document_id": document_id,
                    "version_id": record.get("current_version_id"),
                    "sha256": record.get("sha256"),
                    "updated_at": record.get("updated_at"),
                }
            )
        run_id = (
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"arbitration-workflow:{case_id}:{idempotency_key}"))
            if idempotency_key
            else str(uuid.uuid4())
        )
        input_snapshot = await self.repository.create_snapshot(
            run_id=run_id,
            kind="input",
            payload={
                "case_id": case_id,
                "draft_id": payload.draft_id,
                "pleading_type": payload.pleading_type,
                "opponent_draft_id": payload.opponent_draft_id,
                "opponent_version_id": payload.opponent_version_id,
                "opponent_pleadings": [item.model_dump() for item in payload.opponent_pleadings],
            },
            effect_key=f"{run_id}:snapshot:input",
        )
        manifest = await self.repository.create_snapshot(
            run_id=run_id,
            kind="document_manifest",
            payload={"documents": documents},
            effect_key=f"{run_id}:snapshot:documents",
        )
        opponent_snapshot = await self.domain.capture_opponent_snapshot(
            run_id,
            case_id,
            str(payload.pleading_type),
            payload.opponent_draft_id,
            payload.opponent_version_id,
            payload.opponent_pleadings,
        )
        rows = {
            slug: await self.cases.list_matrix_rows(case_id, slug, draft_id=payload.draft_id)
            for slug in MATRIX_COLLECTIONS
        }
        evidence_manifest = await self.cases._authoritative_evidence_manifest(case, rows)
        evidence_snapshot = await self.repository.create_snapshot(
            run_id=run_id,
            kind="evidence_manifest",
            payload={"evidence": evidence_manifest},
            effect_key=f"{run_id}:snapshot:evidence:{artifact_hash(evidence_manifest)}",
        )
        now = datetime.now(timezone.utc)
        run = {
            "_id": run_id,
            "thread_id": f"arbitration:{run_id}",
            "case_id": case_id,
            "draft_id": payload.draft_id,
            "organization_id": case.get("organization_id"),
            "project_id": case.get("project_id"),
            "pleading_type": payload.pleading_type,
            "engine": "langgraph_v1",
            "engine_version": "3",
            "rollout_mode": rollout_mode,
            "rollout_policy_version": (rollout_decision or {}).get("policy_version", "phase6-v1"),
            "rollout_decision_reason": (rollout_decision or {}).get("reason", "policy"),
            "rollout_decision_hash": (rollout_decision or {}).get("decision_hash"),
            "acceptance_receipt_sha256": (rollout_decision or {}).get("acceptance_receipt_sha256"),
            "v2_compatibility_mode": (rollout_decision or {}).get("v2_compatibility_mode", "active"),
            "graph_version": str(settings.ARBITRATION_ENGINE_GRAPH_VERSION),
            "state_schema_version": int(settings.ARBITRATION_ENGINE_STATE_SCHEMA_VERSION),
            "status": "running",
            "current_node": "validate_intake",
            "next_action": "poll",
            "required_human_role": None,
            "progress": 1,
            "state_version": 1,
            "input_snapshot_id": input_snapshot["_id"],
            "input_snapshot_hash": input_snapshot["snapshot_hash"],
            "document_manifest_id": manifest["_id"],
            "document_manifest_hash": manifest["snapshot_hash"],
            "opponent_pleading_snapshot_id": (opponent_snapshot or {}).get("_id"),
            "opponent_pleading_snapshot_hash": (opponent_snapshot or {}).get("snapshot_hash"),
            "evidence_snapshot_id": evidence_snapshot["_id"],
            "evidence_snapshot_hash": evidence_snapshot["snapshot_hash"],
            "documents_selected": bool(documents),
            "request_hash": request_hash,
            "idempotency_key": idempotency_key,
            "authoritative_effects": [],
            "blockers": [],
            "targeted_questions": [],
            "material_questions_required": False,
            "last_material_editor_id": _actor_id(current_user),
            "fallback_available": True,
            "created_by": _actor_id(current_user),
            "created_at": now,
            "updated_at": now,
        }
        return await self.repository.create_run(run)

    async def refresh_evidence_binding(self, run: Dict[str, Any], *, actor_id: str) -> Dict[str, Any]:
        """Bind a matrix refresh to a new immutable evidence/revision snapshot.

        This is a graph-input operation used only before re-running the analysis
        fan-out. Including row revision and review state prevents an unchanged
        source manifest from accidentally reusing an analysis effect after a
        matrix review mutation.
        """

        case = await self.cases.get_case(str(run["case_id"]))
        rows = {
            slug: await self.cases.list_matrix_rows(
                str(run["case_id"]), slug, draft_id=run.get("draft_id")
            )
            for slug in MATRIX_COLLECTIONS
        }
        evidence_manifest = await self.cases._authoritative_evidence_manifest(case, rows)
        matrix_binding = {
            slug: [
                {
                    "row_id": row.get("_id"),
                    "row_revision_id": row.get("row_revision_id"),
                    "review_status": row.get("review_status"),
                    "evidence_status": row.get("evidence_status"),
                    "source_revision_ids": sorted(
                        str(value)
                        for value in (row.get("source_revision_ids") or [])
                        if value
                    ),
                }
                for row in sorted(rows.get(slug) or [], key=lambda item: str(item.get("_id") or ""))
            ]
            for slug in sorted(rows)
        }
        binding_hash = artifact_hash(matrix_binding)
        snapshot = await self.repository.create_snapshot(
            run_id=str(run["_id"]),
            kind="evidence_manifest",
            payload={"evidence": evidence_manifest, "matrix_binding": matrix_binding},
            effect_key=f"{run['_id']}:snapshot:evidence-refresh:{binding_hash}",
        )
        return await self.repository.transition(
            str(run["_id"]),
            int(run.get("state_version") or 0),
            {
                "status": "running",
                "current_node": "analyze_documents",
                "next_action": "poll",
                "required_human_role": None,
                "evidence_snapshot_id": snapshot["_id"],
                "evidence_snapshot_hash": snapshot["snapshot_hash"],
                "analysis_artifact_set_id": None,
                "analysis_artifact_set_hash": None,
                "matrix_revision_set_id": None,
                "matrix_revision_hash": None,
                "readiness_artifact_id": None,
                "readiness_artifact_hash": None,
                "blockers": [],
                "targeted_questions": [],
                "question_snapshot_id": None,
                "question_snapshot_hash": None,
                "material_questions_required": False,
                "last_material_editor_id": actor_id,
            },
            event="graph_evidence_binding_refreshed",
        )

    async def prepare_validation_refresh(
        self,
        run: Dict[str, Any],
        candidate: Dict[str, Any],
        *,
        actor_id: str,
    ) -> Dict[str, Any]:
        candidate_hash = str(candidate.get("version_hash") or immutable_version_hash(candidate))
        if candidate_hash == str(run.get("draft_version_hash") or ""):
            raise HTTPException(
                status_code=409,
                detail="Legal-review refresh requires a new immutable draft candidate",
            )
        return await self.repository.transition(
            str(run["_id"]),
            int(run.get("state_version") or 0),
            {
                "status": "running",
                "current_node": "validate_citations",
                "next_action": "poll",
                "required_human_role": None,
                "draft_version_id": candidate["_id"],
                "draft_version_hash": candidate_hash,
                "validation_artifact_set_id": None,
                "validation_artifact_set_hash": None,
                "validation_report_id": None,
                "validation_report_hash": None,
                "validation_status": None,
                "validation_blockers": [],
                "validation_warnings": [],
                "validation_route": None,
                "remediation_artifact_id": None,
                "remediation_artifact_hash": None,
                "remediation_cycle": 0,
                "last_material_editor_id": candidate.get("created_by") or actor_id,
            },
            event="graph_validation_input_refreshed",
        )

    async def execute(self, node: str, state: Dict[str, Any]) -> Dict[str, Any]:
        if node in {"validate_intake", "capture_input_snapshot"}:
            return await self._run_effect(node, state, self._verify_intake)
        if node in ANALYSIS_NODE_BRANCHES:
            return await self._run_effect(node, state, lambda run: self._analyze_node(run, node))
        if node == "merge_evidence_and_matrices":
            return await self._run_effect(node, state, lambda run: self._merge_analysis(run, state))
        if node == "build_pleading_plan":
            return await self._run_effect(node, state, self._build_plan)
        if node == "generate_draft":
            return await self._run_effect(node, state, self._generate_draft)
        if node in VALIDATION_NODE_BRANCH:
            return await self._run_effect(node, state, lambda run: self._validate_branch(run, node))
        if node == "merge_validation_artifacts":
            return await self._run_effect(node, state, lambda run: self._merge_validation(run, state))
        if node == "remediate_draft":
            return await self._run_effect(node, state, self._remediate)
        if node == "commit_draft_approval":
            return await self._run_effect(node, state, self._commit_draft_approval)
        if node == "create_filing_export":
            return await self._run_effect(node, state, self._create_filing_export)
        if node in {"complete", "cancelled"}:
            return {"current_node": node, "execution_status": node, "next_action": node}
        raise ValueError(f"Unsupported arbitration graph command node: {node}")

    async def _verify_intake(self, run: Dict[str, Any]) -> Dict[str, Any]:
        for kind, id_field, hash_field in (
            ("input", "input_snapshot_id", "input_snapshot_hash"),
            ("document_manifest", "document_manifest_id", "document_manifest_hash"),
            ("evidence_manifest", "evidence_snapshot_id", "evidence_snapshot_hash"),
        ):
            snapshot = await self.db.arbitration_workflow_snapshots.find_one(
                {"_id": run.get(id_field), "run_id": str(run["_id"]), "kind": kind}
            )
            if not snapshot or snapshot.get("snapshot_hash") != run.get(hash_field):
                raise HTTPException(status_code=409, detail=f"Immutable workflow {kind} snapshot is missing or drifted")
        return {
            "current_node": "capture_input_snapshot",
            "execution_status": "running",
            "next_action": "poll",
        }

    async def _analyze_node(self, run: Dict[str, Any], node: str) -> Dict[str, Any]:
        result = await self.domain.analyze_node(run, node)
        suffix = node.removeprefix("analyze_")
        return {
            f"analysis_{suffix}_id": result["artifact_id"],
            f"analysis_{suffix}_hash": result["artifact_hash"],
        }

    async def _merge_analysis(self, run: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        if run.get("analysis_artifact_set_id") and run.get("matrix_revision_hash"):
            return {
                "state_version": int(run.get("state_version") or 0),
                "analysis_artifact_set_id": run["analysis_artifact_set_id"],
                "analysis_artifact_set_hash": run["analysis_artifact_set_hash"],
                "matrix_revision_set_id": run["matrix_revision_set_id"],
                "matrix_revision_hash": run["matrix_revision_hash"],
                "readiness_artifact_id": run["readiness_artifact_id"],
                "readiness_artifact_hash": run["readiness_artifact_hash"],
                "material_questions_required": bool(run.get("material_questions_required")),
                "user_direction_complete": not bool(run.get("material_questions_required"))
                or bool(run.get("user_direction_snapshot_id")),
            }
        refs = {}
        for node in ANALYSIS_NODE_BRANCHES:
            suffix = node.removeprefix("analyze_")
            refs[node] = {
                "artifact_id": state.get(f"analysis_{suffix}_id"),
                "artifact_hash": state.get(f"analysis_{suffix}_hash"),
            }
        analysis = await self.domain.merge_analysis_nodes(run, refs)
        case = await self.cases.get_case(str(run["case_id"]))
        readiness = await self.cases._readiness_artifact_state(case, str(run.get("pleading_type")))
        questions = self.domain.material_questions(analysis["blockers"], str(run.get("pleading_type")))
        question_snapshot = await self.repository.create_snapshot(
            run_id=str(run["_id"]),
            kind="material_questions",
            payload={"questions": questions},
            effect_key=f"{run['_id']}:snapshot:questions:{analysis['revision_hash']}",
        )
        target_node = "material_question_gate" if questions else "matrix_review_gate"
        updated = await self.repository.transition(
            str(run["_id"]),
            int(run.get("state_version") or 0),
            {
                "status": "awaiting_user_direction" if questions else "awaiting_matrix_review",
                "current_node": target_node,
                "next_action": "answer_questions" if questions else "review_matrices",
                "required_human_role": "counsel" if questions else "legal_reviewer",
                "progress": 40,
                "analysis_artifact_set_id": analysis["analysis_artifact_set_id"],
                "analysis_artifact_set_hash": analysis["analysis_artifact_set_hash"],
                "matrix_revision_set_id": analysis["revision_set_id"],
                "matrix_revision_hash": analysis["revision_hash"],
                "readiness_artifact_id": readiness["matrix_revision_set_id"],
                "readiness_artifact_hash": readiness["artifact_hash"],
                "blockers": analysis["blockers"],
                "targeted_questions": questions,
                "question_snapshot_id": question_snapshot["_id"],
                "question_snapshot_hash": question_snapshot["snapshot_hash"],
                "material_questions_required": bool(questions),
            },
            event="graph_analysis_merged",
        )
        return {
            "state_version": int(updated["state_version"]),
            "analysis_artifact_set_id": analysis["analysis_artifact_set_id"],
            "analysis_artifact_set_hash": analysis["analysis_artifact_set_hash"],
            "matrix_revision_set_id": analysis["revision_set_id"],
            "matrix_revision_hash": analysis["revision_hash"],
            "readiness_artifact_id": readiness["matrix_revision_set_id"],
            "readiness_artifact_hash": readiness["artifact_hash"],
            "material_questions_required": bool(questions),
            "user_direction_complete": not bool(questions),
        }

    async def _build_plan(self, run: Dict[str, Any]) -> Dict[str, Any]:
        if run.get("plan_id") and run.get("plan_hash"):
            return {
                "state_version": int(run.get("state_version") or 0),
                "plan_id": str(run["plan_id"]),
                "plan_hash": str(run["plan_hash"]),
            }
        approval = await self.db.arbitration_workflow_approvals.find_one(
            {
                "_id": run.get("readiness_approval_receipt_id"),
                "run_id": str(run["_id"]),
                "gate": "readiness",
                "decision": "approved",
                "receipt_status": "committed",
                "artifact_hash": run.get("readiness_artifact_hash"),
            }
        )
        if not approval:
            raise HTTPException(status_code=409, detail="Committed exact-hash readiness approval is required")
        actor = SimpleNamespace(id=str(approval.get("approver_id") or run.get("created_by")))
        readiness = await self.cases.approve_readiness(
            str(run["case_id"]),
            actor,
            ArbitrationReadinessApprovalRequest(
                draft_id=run.get("draft_id"),
                draft_type=run.get("pleading_type"),
                reviewer_role="senior_legal_approver",
                comment="Approved through the authoritative LangGraph readiness gate.",
            ),
        )
        plan = await self.domain.build_plan(run, self._actor(run))
        updated = await self.repository.transition(
            str(run["_id"]),
            int(run.get("state_version") or 0),
            {
                "status": "awaiting_plan_approval",
                "current_node": "plan_approval_gate",
                "next_action": "approve_plan",
                "required_human_role": "senior_legal",
                "progress": 60,
                "plan_id": plan["_id"],
                "plan_hash": plan["plan_hash"],
                "case_readiness_approval_receipt_id": (readiness.get("approval_receipt") or {}).get("_id"),
                "artifact_authors": {
                    **(run.get("artifact_authors") or {}),
                    "plan_hash": plan.get("created_by"),
                },
            },
            event="graph_plan_built",
        )
        return {
            "state_version": int(updated["state_version"]),
            "plan_id": plan["_id"],
            "plan_hash": plan["plan_hash"],
        }

    async def _generate_draft(self, run: Dict[str, Any]) -> Dict[str, Any]:
        if run.get("draft_version_id") and run.get("draft_version_hash"):
            return {
                "state_version": int(run.get("state_version") or 0),
                "draft_version_id": str(run["draft_version_id"]),
                "draft_version_hash": str(run["draft_version_hash"]),
                "draft_generated": True,
            }
        approval = await self.db.arbitration_workflow_approvals.find_one(
            {
                "_id": run.get("plan_approval_receipt_id"),
                "run_id": str(run["_id"]),
                "gate": "plan",
                "decision": "approved",
                "receipt_status": "committed",
                "artifact_hash": run.get("plan_hash"),
            }
        )
        if not approval:
            raise HTTPException(status_code=409, detail="Committed exact-hash plan approval is required")
        plan = await self.db.arbitration_plans.find_one(
            {"_id": run.get("plan_id"), "run_id": str(run["_id"]), "plan_hash": run.get("plan_hash")}
        )
        if not plan or not run.get("draft_id"):
            raise HTTPException(status_code=409, detail="Approved immutable pleading plan and linked draft are required")
        await self.db.arbitration_plans.update_one(
            {"_id": plan["_id"], "run_id": str(run["_id"]), "plan_hash": run.get("plan_hash")},
            {
                "$set": {
                    "status": "approved",
                    "approval_receipt_id": approval["_id"],
                    "approved_at": approval.get("approved_at") or datetime.now(timezone.utc),
                }
            },
        )
        plan = {
            **plan,
            "status": "approved",
            "approval_receipt_id": approval["_id"],
            "approved_at": approval.get("approved_at") or datetime.now(timezone.utc),
        }
        await self.drafting.generate(
            str(run["draft_id"]),
            ArbitrationGenerateRequest(),
            self._actor(run),
            pleading_plan=plan,
        )
        version = await self.drafting.repo.latest_version(str(run["draft_id"]))
        if not version:
            raise HTTPException(status_code=409, detail="Draft generation did not create an immutable candidate")
        version_hash = version.get("version_hash") or immutable_version_hash(version)
        updated = await self.repository.transition(
            str(run["_id"]),
            int(run.get("state_version") or 0),
            {
                "status": "running",
                "current_node": "generate_draft",
                "next_action": "poll",
                "progress": 75,
                "draft_version_id": version["_id"],
                "draft_version_hash": version_hash,
                "authoritative_effects": [*(run.get("authoritative_effects") or []), "draft_version"],
                "artifact_authors": {
                    **(run.get("artifact_authors") or {}),
                    "draft_version_hash": version.get("created_by"),
                },
            },
            event="graph_draft_generated",
        )
        return {
            "state_version": int(updated["state_version"]),
            "draft_version_id": str(version["_id"]),
            "draft_version_hash": str(version_hash),
            "draft_generated": True,
        }

    @staticmethod
    def _validation_dependencies(run: Dict[str, Any]) -> Dict[str, str]:
        return {
            key: str(run.get(key))
            for key in (
                "input_snapshot_hash",
                "evidence_snapshot_hash",
                "opponent_pleading_snapshot_hash",
                "matrix_revision_hash",
                "readiness_artifact_hash",
                "plan_hash",
            )
            if run.get(key)
        }

    async def _validate_branch(self, run: Dict[str, Any], node: str) -> Dict[str, Any]:
        branch_name = VALIDATION_NODE_BRANCH[node]
        version = await self.db.arbitration_draft_versions.find_one(
            {"_id": run.get("draft_version_id"), "draft_id": str(run.get("draft_id"))}
        )
        if not version:
            raise HTTPException(status_code=409, detail="Immutable draft candidate is missing")
        report = await self.validation.evaluate(
            version,
            dependency_hashes=self._validation_dependencies(run),
            remediation_cycle=int(run.get("remediation_cycle") or 0),
            branches=(branch_name,),
        )
        artifact = next(item for item in report["artifacts"] if item["branch"] == branch_name)
        snapshot = await self.repository.create_snapshot(
            run_id=str(run["_id"]),
            kind=f"validation_{branch_name}",
            payload=artifact,
            effect_key=f"{run['_id']}:validation:{report['validation_input_hash']}:{branch_name}:{artifact['artifact_hash']}",
        )
        return {
            f"validation_{branch_name}_id": snapshot["_id"],
            f"validation_{branch_name}_hash": snapshot["snapshot_hash"],
            f"{branch_name}_validated": True,
        }

    async def _merge_validation(self, run: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        if run.get("validation_artifact_set_id") and run.get("validation_report_hash"):
            return {
                "state_version": int(run.get("state_version") or 0),
                "validation_artifact_set_id": str(run["validation_artifact_set_id"]),
                "validation_artifact_set_hash": str(run["validation_artifact_set_hash"]),
                "validation_report_id": str(run["validation_report_id"]),
                "validation_report_hash": str(run["validation_report_hash"]),
                "validation_status": str(run.get("validation_status") or "blocked"),
                "validation_route": str(run.get("validation_route") or "human_revision"),
                "validation_complete": True,
                "remediation_cycle": int(run.get("remediation_cycle") or 0),
            }
        artifacts = []
        for branch in VALIDATION_BRANCHES:
            snapshot = await self.db.arbitration_workflow_snapshots.find_one(
                {
                    "_id": state.get(f"validation_{branch}_id"),
                    "run_id": str(run["_id"]),
                    "kind": f"validation_{branch}",
                }
            )
            if not snapshot or snapshot.get("snapshot_hash") != state.get(f"validation_{branch}_hash"):
                raise HTTPException(status_code=409, detail=f"Validation artifact is missing or drifted: {branch}")
            artifacts.append(dict(snapshot.get("payload") or {}))
        validation_input_hashes = {item.get("validation_input_hash") for item in artifacts}
        if len(validation_input_hashes) != 1:
            raise HTTPException(status_code=409, detail="Validation branches do not share one immutable input hash")
        issues = {
            (str(issue.get("branch")), str(issue.get("code")), str(issue.get("message"))): issue
            for artifact in artifacts
            for issue in artifact.get("issues") or []
        }
        ordered_issues = [issues[key] for key in sorted(issues)]
        blockers = [item for item in ordered_issues if item.get("severity") == "blocker"]
        warnings = [item for item in ordered_issues if item.get("severity") == "warning"]
        report = self.validation.bounded_remediation(
            {
                "schema_version": "phase4-v1",
                "validation_input_hash": next(iter(validation_input_hashes)),
                "version_hash": run.get("draft_version_hash"),
                "dependency_hashes": self._validation_dependencies(run),
                "artifacts": artifacts,
                "blockers": blockers,
                "warnings": warnings,
                "status": "blocked" if blockers else "passed",
                "remediation_candidates": [item for item in ordered_issues if item.get("remediable")],
                "remediation_cycle": int(run.get("remediation_cycle") or 0),
                "max_remediation_cycles": int(settings.ARBITRATION_ENGINE_MAX_REMEDIATION_CYCLES),
                "human_review_required": bool(blockers or warnings),
                "remediation_policy": "existing-citation-deduplication-only",
            }
        )
        report_snapshot = await self.repository.create_snapshot(
            run_id=str(run["_id"]),
            kind="validation_report",
            payload=report,
            effect_key=f"{run['_id']}:validation-report:{report['validation_input_hash']}:{report['report_hash']}",
        )
        artifact_refs = [
            {
                "branch": branch,
                "artifact_id": state.get(f"validation_{branch}_id"),
                "artifact_hash": state.get(f"validation_{branch}_hash"),
            }
            for branch in VALIDATION_BRANCHES
        ]
        artifact_set_hash = artifact_hash({"validation_input_hash": report["validation_input_hash"], "artifacts": artifact_refs})
        artifact_set = await self.repository.create_snapshot(
            run_id=str(run["_id"]),
            kind="validation_artifact_set",
            payload={
                "validation_input_hash": report["validation_input_hash"],
                "artifacts": artifact_refs,
                "artifact_set_hash": artifact_set_hash,
            },
            effect_key=f"{run['_id']}:validation-set:{report['validation_input_hash']}:{artifact_set_hash}",
        )
        route = str(report.get("route") or "human_revision")
        updated = await self.repository.transition(
            str(run["_id"]),
            int(run.get("state_version") or 0),
            {
                "status": "running" if route == "remediate" else "awaiting_legal_review",
                "current_node": "remediate_draft" if route == "remediate" else "legal_review_gate",
                "next_action": "poll" if route == "remediate" else ("legal_review" if report.get("status") == "passed" else "revise_draft"),
                "required_human_role": None if route == "remediate" else ("legal_reviewer" if report.get("status") == "passed" else "drafter"),
                "progress": 82 if route == "remediate" else 85,
                "validation_status": report.get("status"),
                "validation_blockers": blockers,
                "validation_warnings": warnings,
                "validation_artifact_set_id": artifact_set["_id"],
                "validation_artifact_set_hash": artifact_set_hash,
                "validation_report_id": report_snapshot["_id"],
                "validation_report_hash": report["report_hash"],
                "validation_route": route,
            },
            event="graph_validation_merged",
        )
        return {
            "state_version": int(updated["state_version"]),
            "validation_artifact_set_id": artifact_set["_id"],
            "validation_artifact_set_hash": artifact_set_hash,
            "validation_report_id": report_snapshot["_id"],
            "validation_report_hash": report["report_hash"],
            "validation_status": str(report.get("status")),
            "validation_route": route,
            "validation_complete": True,
            "remediation_cycle": int(run.get("remediation_cycle") or 0),
        }

    async def _remediate(self, run: Dict[str, Any]) -> Dict[str, Any]:
        version = await self.db.arbitration_draft_versions.find_one(
            {"_id": run.get("draft_version_id"), "draft_id": str(run.get("draft_id"))}
        )
        report_snapshot = await self.db.arbitration_workflow_snapshots.find_one(
            {"_id": run.get("validation_report_id"), "run_id": str(run["_id"]), "kind": "validation_report"}
        )
        if not version or not report_snapshot:
            raise HTTPException(status_code=409, detail="Remediation inputs are missing")
        report = dict(report_snapshot.get("payload") or {})
        remediation = self.validation.remediate(version, report)
        if not remediation:
            return {"validation_route": "human_revision"}
        cycle = int(report.get("remediation_cycle") or 0) + 1
        version_number = await self.drafting.repo.next_version(str(run["draft_id"]))
        remediated = {
            **version,
            "_id": str(uuid.uuid4()),
            "version": version_number,
            "sections": remediation["sections"],
            "full_markdown": remediation["full_markdown"],
            "structured_output": {
                **(version.get("structured_output") or {}),
                "workflow_remediation": {
                    "policy": report.get("remediation_policy"),
                    "cycle": cycle,
                    "applied_fixes": remediation["applied_fixes"],
                    "parent_version_hash": report.get("version_hash"),
                },
            },
            "parent_version_id": version.get("_id"),
            "parent_version": version.get("version"),
            "generation_run_id": None,
            "created_by": run.get("created_by"),
            "created_at": datetime.now(timezone.utc),
        }
        remediated["version_hash"] = immutable_version_hash(remediated)
        await self.drafting.repo.create_version(remediated)
        await self.drafting.repo.update_draft(
            str(run["draft_id"]),
            {
                "current_version": version_number,
                "updated_at": datetime.now(timezone.utc),
                "updated_by": run.get("created_by"),
            },
        )
        artifact = await self.repository.create_snapshot(
            run_id=str(run["_id"]),
            kind="draft_remediation",
            payload={
                "policy": report.get("remediation_policy"),
                "cycle": cycle,
                "parent_version_id": version.get("_id"),
                "parent_version_hash": report.get("version_hash"),
                "draft_version_id": remediated["_id"],
                "draft_version_hash": remediated["version_hash"],
                "applied_fixes": remediation["applied_fixes"],
                "before_content_hash": remediation["before_content_hash"],
                "after_content_hash": remediation["after_content_hash"],
            },
            effect_key=f"{run['_id']}:remediation-artifact:{report.get('report_hash')}:{cycle}",
        )
        updated = await self.repository.transition(
            str(run["_id"]),
            int(run.get("state_version") or 0),
            {
                "status": "running",
                "current_node": "remediate_draft",
                "next_action": "poll",
                "draft_version_id": remediated["_id"],
                "draft_version_hash": remediated["version_hash"],
                "remediation_artifact_id": artifact["_id"],
                "remediation_artifact_hash": artifact["snapshot_hash"],
                "remediation_cycle": cycle,
                "validation_artifact_set_id": None,
                "validation_artifact_set_hash": None,
                "validation_report_id": None,
                "validation_report_hash": None,
                "validation_status": None,
                "validation_blockers": [],
                "validation_warnings": [],
                "validation_route": None,
            },
            event="graph_draft_remediated",
        )
        return {
            "state_version": int(updated["state_version"]),
            "draft_version_id": remediated["_id"],
            "draft_version_hash": remediated["version_hash"],
            "remediation_artifact_id": artifact["_id"],
            "remediation_artifact_hash": artifact["snapshot_hash"],
            "remediation_cycle": cycle,
            "validation_route": "pending",
        }

    async def _create_filing_export(self, run: Dict[str, Any]) -> Dict[str, Any]:
        if run.get("filing_export_id") and run.get("filing_export_effect_key"):
            return {
                "state_version": int(run.get("state_version") or 0),
                "filing_export_id": str(run["filing_export_id"]),
                "filing_export_effect_key": str(run["filing_export_effect_key"]),
                "execution_status": "completed",
                "current_node": "complete",
                "next_action": "completed",
            }
        export = await self.cases.queue_filing_bundle_export(
            str(run["case_id"]),
            "zip",
            self._actor(run),
        )
        updated = await self.repository.transition(
            str(run["_id"]),
            int(run.get("state_version") or 0),
            {
                "status": "completed",
                "current_node": "complete",
                "next_action": "completed",
                "required_human_role": None,
                "progress": 100,
                "filing_export_id": export.get("_id"),
                "filing_export_effect_key": export.get("effect_key"),
                "authoritative_effects": [*(run.get("authoritative_effects") or []), "filing_export"],
                "fallback_available": False,
            },
            event="graph_filing_export_queued",
        )
        return {
            "state_version": int(updated["state_version"]),
            "filing_export_id": str(export.get("_id") or ""),
            "filing_export_effect_key": str(export.get("effect_key") or ""),
            "execution_status": "completed",
            "current_node": "complete",
            "next_action": "completed",
        }

    async def _commit_draft_approval(self, run: Dict[str, Any]) -> Dict[str, Any]:
        if run.get("draft_approval_committed_at"):
            return {
                "state_version": int(run.get("state_version") or 0),
                "current_node": "commit_draft_approval",
                "execution_status": "running",
                "next_action": "poll",
            }
        approval = await self.db.arbitration_workflow_approvals.find_one(
            {
                "_id": run.get("draft_approval_receipt_id"),
                "run_id": str(run["_id"]),
                "gate": "draft",
                "decision": "approved",
                "receipt_status": "committed",
                "artifact_hash": run.get("draft_version_hash"),
            }
        )
        if not approval:
            raise HTTPException(status_code=409, detail="Committed exact-hash draft approval is required")
        actor = SimpleNamespace(id=str(approval.get("approver_id") or run.get("created_by")))
        await self.drafting.approve(str(run.get("draft_id")), actor, workflow_run=run)
        updated = await self.repository.transition(
            str(run["_id"]),
            int(run.get("state_version") or 0),
            {
                "status": "awaiting_export_authorization",
                "current_node": "export_authorization_gate",
                "next_action": "authorize_export",
                "required_human_role": "senior_legal",
                "progress": 95,
                "draft_approval_committed_at": datetime.now(timezone.utc),
            },
            event="graph_draft_approval_committed",
        )
        return {
            "state_version": int(updated["state_version"]),
            "current_node": "commit_draft_approval",
            "execution_status": "running",
            "next_action": "poll",
        }
