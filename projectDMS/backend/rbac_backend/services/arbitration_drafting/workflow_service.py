from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

from ...models.arbitration_drafting import (
    ArbitrationWorkflowApprovalRequest,
    ArbitrationWorkflowCreateRequest,
)
from .case_workspace import ArbitrationCaseWorkspaceService, _actor_id
from .engines import ArbitrationEngineSelector, ArbitrationV2WorkflowEngine, canonical_workflow_request_hash
from .workflow_repository import ArbitrationWorkflowRepository
from .workflow_domain import ArbitrationWorkflowDomain
from .approval_policy import enforce_author_approver_separation, enforce_gate_role
from ...models.arbitration_drafting import ArbitrationReadinessApprovalRequest, ArbitrationGenerateRequest
from .service import ArbitrationDraftingService, immutable_version_hash
from .workflow_validation import ArbitrationValidationOrchestrator
from ..observability import observability_registry


GATE_ARTIFACT_FIELDS = {
    "document_selection": "document_manifest_hash",
    "matrix_review": "matrix_revision_hash",
    "readiness": "readiness_artifact_hash",
    "plan": "plan_hash",
    "legal_review": "draft_version_hash",
    "draft": "draft_version_hash",
    "export": "draft_version_hash",
}

GATE_TRANSITIONS = {
    "document_selection": ("running", "analyze_documents", "poll", 20),
    "matrix_review": ("awaiting_readiness_approval", "readiness_approval_gate", "approve_readiness", 45),
    "readiness": ("awaiting_plan_approval", "plan_approval_gate", "approve_plan", 60),
    "plan": ("running", "generate_draft", "poll", 70),
    "legal_review": ("awaiting_draft_approval", "draft_approval_gate", "approve_draft", 88),
    "draft": ("awaiting_export_authorization", "export_authorization_gate", "authorize_export", 95),
    "export": ("completed", "complete", "completed", 100),
}

RESUMABLE_GATES = {
    "document_selection_gate": "document_selection",
    "material_question_gate": "material_question",
    "matrix_review_gate": "matrix_review",
}

TERMINAL_WORKFLOW_STATUSES = {"completed", "cancelled", "failed"}


class ArbitrationWorkflowService:
    def __init__(self, db: Any) -> None:
        self.db = db
        self.repository = ArbitrationWorkflowRepository(db)
        self.cases = ArbitrationCaseWorkspaceService(db)
        self.domain = ArbitrationWorkflowDomain(db)
        self.drafting = ArbitrationDraftingService(db)
        self.validation = ArbitrationValidationOrchestrator()

    @staticmethod
    def _assert_state_version(run: Dict[str, Any], expected_version: int) -> None:
        current_version = int(run.get("state_version") or 0)
        if current_version != int(expected_version):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Stale arbitration workflow state version",
                    "current_state_version": current_version,
                },
            )

    async def _sync_langgraph_checkpoint(
        self,
        run: Dict[str, Any],
        update: Dict[str, Any],
        *,
        traverse: bool = True,
    ) -> None:
        if run.get("engine") != "langgraph_v1":
            return
        from .langgraph_engine import LangGraphArbitrationEngine

        try:
            engine = LangGraphArbitrationEngine(self.db)
            if traverse:
                await engine.checkpoint_transition(run, update)
            else:
                await engine.checkpoint_state_update(run, update)
        except Exception as exc:
            run.update(
                {
                    "checkpoint_sync_status": "pending",
                    "checkpoint_sync_state_version": run.get("state_version"),
                    "checkpoint_sync_error_code": type(exc).__name__,
                }
            )
            await self.db.arbitration_workflow_runs.update_one(
                {"_id": run["_id"], "state_version": run.get("state_version")},
                {
                    "$set": {
                        "checkpoint_sync_status": "pending",
                        "checkpoint_sync_state_version": run.get("state_version"),
                        "checkpoint_sync_error_code": type(exc).__name__,
                    }
                },
            )
            await self.repository.append_event(
                run["_id"],
                "checkpoint_sync_pending",
                data={"state_version": run.get("state_version"), "error_code": type(exc).__name__},
            )
            return
        run.update(
            {
                "checkpoint_sync_status": "synced",
                "checkpoint_sync_state_version": run.get("state_version"),
                "checkpoint_sync_error_code": None,
            }
        )
        await self.db.arbitration_workflow_runs.update_one(
            {"_id": run["_id"], "state_version": run.get("state_version")},
            {
                "$set": {
                    "checkpoint_sync_status": "synced",
                    "checkpoint_sync_state_version": run.get("state_version"),
                    "checkpoint_sync_error_code": None,
                }
            },
        )

    async def create(
        self,
        case_id: str,
        payload: ArbitrationWorkflowCreateRequest,
        current_user: Any,
        *,
        idempotency_key: Optional[str],
    ) -> Dict[str, Any]:
        case = await self.cases.get_case(case_id)
        request_hash = canonical_workflow_request_hash(
            case_id=case_id,
            payload=payload,
            tenant_id=str(case.get("organization_id") or ""),
            project_id=str(case.get("project_id") or ""),
        )
        decision = ArbitrationEngineSelector().select(
            tenant_id=str(case.get("organization_id") or ""),
            project_id=str(case.get("project_id") or ""),
            request_hash=request_hash,
        )
        if decision.engine == "langgraph_v1":
            from .langgraph_engine import LangGraphArbitrationEngine

            engine = LangGraphArbitrationEngine(self.db)
        else:
            engine = ArbitrationV2WorkflowEngine(self.db)
        run = await engine.create_workflow(
            case_id,
            payload,
            current_user,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            rollout_mode=decision.rollout_mode,
        )
        if payload.requested_engine and payload.requested_engine != decision.engine:
            await self.repository.append_event(
                run["_id"], "client_engine_request_ignored", actor_id=_actor_id(current_user),
                data={"requested": payload.requested_engine, "selected": decision.engine},
            )
        if decision.shadow:
            serial = await self.domain.analyze(run, parallel=False)
            await self.repository.append_event(
                run["_id"], "shadow_comparison",
                data={
                    "authoritative_engine": "arbitration_v2", "candidate_engine": "langgraph_v1",
                    "parallel_revision_hash": run.get("matrix_revision_hash"),
                    "serial_revision_hash": serial.get("revision_hash"),
                    "parity": run.get("matrix_revision_hash") == serial.get("revision_hash"),
                    "authoritative_writes": False,
                },
            )
        await observability_registry.record_arbitration_workflow(
            engine=str(run.get("engine")), status=str(run.get("status")), node=str(run.get("current_node")), event="created"
        )
        return self.public_state(run)

    async def get(self, case_id: str, run_id: str) -> Dict[str, Any]:
        run = await self.repository.get_run(run_id)
        if not run or str(run.get("case_id")) != case_id:
            raise HTTPException(status_code=404, detail="Arbitration workflow run not found")
        return self.public_state(run)

    async def events(self, case_id: str, run_id: str) -> list[Dict[str, Any]]:
        await self.get(case_id, run_id)
        return await self.repository.list_events(run_id)

    async def approve_gate(
        self,
        case_id: str,
        run_id: str,
        gate: str,
        payload: ArbitrationWorkflowApprovalRequest,
        current_user: Any,
    ) -> Dict[str, Any]:
        run = await self.repository.get_run(run_id)
        if not run or str(run.get("case_id")) != case_id:
            raise HTTPException(status_code=404, detail="Arbitration workflow run not found")
        self._assert_state_version(run, payload.state_version)
        artifact_field = GATE_ARTIFACT_FIELDS.get(gate)
        transition = GATE_TRANSITIONS.get(gate)
        if not artifact_field or not transition:
            raise HTTPException(status_code=400, detail="Unknown arbitration workflow approval gate")
        if gate == "matrix_review":
            current_analysis = await self.domain.analyze(run, parallel=True)
            if current_analysis["revision_hash"] != run.get("matrix_revision_hash"):
                raise HTTPException(status_code=409, detail="Matrix revision set drifted; refresh the workflow before approval")
            if current_analysis["blockers"]:
                raise HTTPException(status_code=409, detail={"message": "Required pleading matrices are incomplete", "blockers": current_analysis["blockers"]})
        elif gate == "readiness":
            case = await self.cases.get_case(case_id)
            current_readiness = await self.cases._readiness_artifact_state(case, str(run.get("pleading_type")))
            if current_readiness["artifact_hash"] != run.get("readiness_artifact_hash"):
                raise HTTPException(status_code=409, detail="Readiness evidence or matrix state drifted; refresh the workflow")
        elif gate in {"legal_review", "draft", "export"}:
            if not run.get("draft_id"):
                raise HTTPException(status_code=409, detail="Workflow has no case-linked draft")
            current_version = await self.drafting.repo.latest_version(str(run["draft_id"]))
            current_hash = (current_version or {}).get("version_hash") or immutable_version_hash(current_version or {})
            if current_hash != run.get("draft_version_hash"):
                raise HTTPException(status_code=409, detail="Draft version drifted; review the current immutable version")
        expected_hash = str(run.get(artifact_field) or "")
        if not expected_hash or expected_hash != payload.artifact_hash:
            raise HTTPException(status_code=409, detail="Approval artifact hash is stale or does not match the workflow state")
        expected_node = {
            "document_selection": "document_selection_gate", "matrix_review": "matrix_review_gate",
            "readiness": "readiness_approval_gate", "plan": "plan_approval_gate",
            "legal_review": "legal_review_gate", "draft": "draft_approval_gate", "export": "export_authorization_gate",
        }[gate]
        if str(run.get("current_node")) != expected_node:
            raise HTTPException(status_code=409, detail="Approval gate does not match the current workflow node")
        role = enforce_gate_role(gate, payload.reviewer_role, current_user)
        actor = _actor_id(current_user)
        enforce_author_approver_separation(
            current_user,
            [run.get("created_by"), run.get("last_material_editor_id"), (run.get("artifact_authors") or {}).get(artifact_field)],
            gate=gate,
        )
        receipt = {
            "_id": str(uuid.uuid4()),
            "run_id": run_id,
            "case_id": case_id,
            "draft_id": run.get("draft_id"),
            "gate": gate,
            "artifact_hash": payload.artifact_hash,
            "decision": payload.decision,
            "approver_id": actor,
            "approver_role": role,
            "comment": payload.comment,
            "approved_at": datetime.now(timezone.utc),
        }
        receipt = await self.repository.record_approval(receipt)
        if payload.decision != "approved":
            updated = await self.repository.transition(
                run_id,
                payload.state_version,
                {"status": "awaiting_matrix_review", "current_node": "matrix_review_gate", "next_action": "review_matrices", "required_human_role": "legal"},
                event=f"{gate}_{payload.decision}",
            )
            return self.public_state(updated)
        status_value, node, next_action, progress = transition
        transition_update: Dict[str, Any] = {}
        if gate == "readiness":
            readiness = await self.cases.approve_readiness(
                case_id,
                current_user,
                ArbitrationReadinessApprovalRequest(
                    draft_id=run.get("draft_id"), draft_type=run.get("pleading_type"), reviewer_role="legal", comment=payload.comment
                ),
            )
            plan = await self.domain.build_plan(run, current_user)
            transition_update.update(
                {
                    "readiness_approval_receipt_id": (readiness.get("approval_receipt") or {}).get("_id"),
                    "plan_id": plan["_id"], "plan_hash": plan["plan_hash"],
                    "artifact_authors": {**(run.get("artifact_authors") or {}), "plan_hash": plan.get("created_by")},
                }
            )
        elif gate == "plan":
            if not run.get("draft_id"):
                raise HTTPException(status_code=409, detail="A case-linked draft is required before the approved plan can be generated")
            effect_key = f"{run_id}:generate:{run['plan_hash']}"
            effect = await self.repository.claim_effect(
                run_id=run_id,
                effect_key=effect_key,
                effect_type="draft_generation",
                input_hash=run["plan_hash"],
            )
            if effect.get("status") == "completed":
                output_refs = effect.get("output_refs") or {}
                version = await self.db.arbitration_draft_versions.find_one(
                    {"_id": output_refs.get("draft_version_id"), "draft_id": str(run["draft_id"])}
                )
                if not version:
                    raise HTTPException(status_code=409, detail="Completed draft effect references a missing immutable version")
                version_hash = version.get("version_hash") or immutable_version_hash(version)
                if version_hash != output_refs.get("draft_version_hash"):
                    raise HTTPException(status_code=409, detail="Completed draft effect output hash does not match its immutable version")
            else:
                if not effect.get("_claimed_now"):
                    raise HTTPException(status_code=409, detail="Draft generation effect is already in progress")
                try:
                    await self.drafting.generate(str(run["draft_id"]), ArbitrationGenerateRequest(), current_user)
                    version = await self.drafting.repo.latest_version(str(run["draft_id"]))
                    if not version:
                        raise HTTPException(status_code=409, detail="Draft generation did not create an immutable version")
                    version_hash = version.get("version_hash") or immutable_version_hash(version)
                    await self.repository.complete_effect(
                        effect_key,
                        {"draft_version_id": version.get("_id"), "draft_version_hash": version_hash},
                    )
                except Exception as exc:
                    await self.repository.fail_effect(effect_key, error_code=type(exc).__name__)
                    raise
            validation = self.validation.bounded_remediation(await self.validation.evaluate(version))
            validation_snapshot = await self.repository.create_snapshot(
                run_id=run_id, kind="validation_report", payload=validation,
                effect_key=f"{run_id}:validation:{version_hash}",
            )
            status_value, node, next_action, progress = "awaiting_legal_review", "legal_review_gate", "legal_review", 85
            transition_update.update(
                {
                    "draft_version_id": version.get("_id"), "draft_version_hash": version_hash,
                    "authoritative_effects": [*(run.get("authoritative_effects") or []), "draft_version"],
                    "artifact_authors": {**(run.get("artifact_authors") or {}), "draft_version_hash": version.get("created_by")},
                    "validation_status": validation.get("status"),
                    "validation_blockers": validation.get("blockers") or [],
                    "validation_report_id": validation_snapshot["_id"],
                    "validation_report_hash": validation["report_hash"],
                    "remediation_cycle": validation.get("remediation_cycle", 0),
                }
            )
        elif gate == "draft":
            await self.drafting.approve(str(run.get("draft_id")), current_user)
            transition_update["authoritative_effects"] = [*(run.get("authoritative_effects") or []), "draft_approval"]
        elif gate == "export":
            transition_update["authoritative_effects"] = [*(run.get("authoritative_effects") or []), "export_authorization"]
        updated = await self.repository.transition(
            run_id,
            payload.state_version,
            {
                "status": status_value,
                "current_node": node,
                "next_action": next_action,
                "progress": progress,
                "required_human_role": None if status_value in {"running", "completed"} else "senior_legal",
                f"{gate}_approval_receipt_id": receipt["_id"],
                **transition_update,
            },
            event=f"{gate}_approved",
        )
        flag = {
            "document_selection": "documents_selected", "matrix_review": "matrices_approved",
            "readiness": "readiness_approved", "plan": "plan_approved", "legal_review": "legal_review_approved",
            "draft": "draft_approved", "export": "export_authorized",
        }[gate]
        await self._sync_langgraph_checkpoint(updated, {flag: True})
        await observability_registry.record_arbitration_workflow(
            engine=str(updated.get("engine")), status=str(updated.get("status")), node=str(updated.get("current_node")), event=f"{gate}_approved"
        )
        return self.public_state(updated)

    async def resume(self, case_id: str, run_id: str, payload: Any, current_user: Any) -> Dict[str, Any]:
        run = await self.repository.get_run(run_id)
        if not run or str(run.get("case_id")) != case_id:
            raise HTTPException(status_code=404, detail="Arbitration workflow run not found")
        self._assert_state_version(run, payload.state_version)
        if str(run.get("status")) in TERMINAL_WORKFLOW_STATUSES:
            raise HTTPException(status_code=409, detail="Terminal arbitration workflows cannot be resumed")
        current_node = str(run.get("current_node") or "")
        expected_gate = RESUMABLE_GATES.get(current_node)
        if not expected_gate:
            raise HTTPException(status_code=409, detail="Workflow is not waiting at a resumable human gate")
        if payload.gate != expected_gate:
            raise HTTPException(status_code=409, detail="Resume gate does not match the paused workflow node")
        if payload.selected_document_ids and current_node != "document_selection_gate":
            raise HTTPException(status_code=422, detail="Documents can only be selected at the document selection gate")
        if (payload.answers or payload.directions) and current_node != "material_question_gate":
            raise HTTPException(status_code=422, detail="User directions can only be supplied at the material question gate")
        if current_node == "document_selection_gate" and not payload.selected_document_ids:
            raise HTTPException(status_code=422, detail="At least one scoped document is required to continue")
        if current_node == "matrix_review_gate" and payload.decision != "refresh":
            raise HTTPException(status_code=422, detail="Matrix review resume only supports decision=refresh")
        update: Dict[str, Any] = {"status": "running", "next_action": "poll", "required_human_role": None}
        if current_node == "material_question_gate":
            required = {str(item.get("question_id")) for item in run.get("targeted_questions") or [] if item.get("required")}
            submitted = {str(key) for key in (payload.answers or {})}
            supplied = {str(key) for key, value in (payload.answers or {}).items() if str(value).strip()}
            known = {str(item.get("question_id")) for item in run.get("targeted_questions") or []}
            unknown = submitted - known
            if unknown:
                raise HTTPException(status_code=422, detail={"unknown_question_ids": sorted(unknown)})
            if required - supplied:
                raise HTTPException(status_code=422, detail={"missing_required_question_ids": sorted(required - supplied)})
            answer_snapshot = await self.repository.create_snapshot(
                run_id=run_id,
                kind="user_direction",
                payload={"answers": payload.answers, "directions": payload.directions, "question_snapshot_hash": run.get("question_snapshot_hash")},
                effect_key=f"{run_id}:snapshot:user-direction:{payload.state_version}",
            )
            update.update(
                {
                    "user_direction_snapshot_id": answer_snapshot["_id"],
                    "user_direction_snapshot_hash": answer_snapshot["snapshot_hash"],
                    "status": "awaiting_matrix_review", "current_node": "matrix_review_gate", "next_action": "review_matrices",
                    "required_human_role": "legal_reviewer", "progress": 40,
                    "last_material_editor_id": _actor_id(current_user),
                }
            )
        elif current_node == "matrix_review_gate":
            analysis = await self.domain.analyze(run, parallel=True)
            case = await self.cases.get_case(case_id)
            readiness = await self.cases._readiness_artifact_state(case, str(run.get("pleading_type")))
            update.update(
                {
                    "status": "awaiting_matrix_review", "current_node": "matrix_review_gate", "next_action": "review_matrices",
                    "required_human_role": "legal_reviewer", "progress": 40,
                    "matrix_revision_set_id": analysis["revision_set_id"], "matrix_revision_hash": analysis["revision_hash"],
                    "readiness_artifact_id": readiness["matrix_revision_set_id"], "readiness_artifact_hash": readiness["artifact_hash"],
                    "blockers": analysis["blockers"], "last_material_editor_id": _actor_id(current_user),
                }
            )
        if payload.selected_document_ids:
            case = await self.cases.get_case(case_id)
            manifest = []
            for document_id in sorted(set(payload.selected_document_ids)):
                record = await self.db.documents.find_one(
                    {"_id": document_id, "organization_id": case.get("organization_id"), "project_id": case.get("project_id")}
                )
                if not record:
                    raise HTTPException(status_code=400, detail=f"Selected document is outside case scope: {document_id}")
                manifest.append({"document_id": document_id, "version_id": record.get("current_version_id"), "sha256": record.get("sha256")})
            snapshot = await self.repository.create_snapshot(
                run_id=run_id,
                kind="document_manifest",
                payload={"documents": manifest},
                effect_key=f"{run_id}:snapshot:documents:{payload.state_version}",
            )
            analysis = await self.domain.analyze(run, parallel=True)
            case = await self.cases.get_case(case_id)
            readiness = await self.cases._readiness_artifact_state(case, str(run.get("pleading_type")))
            questions = self.domain.material_questions(analysis["blockers"], str(run.get("pleading_type")))
            question_snapshot = await self.repository.create_snapshot(
                run_id=run_id, kind="material_questions", payload={"questions": questions},
                effect_key=f"{run_id}:snapshot:questions:{analysis['revision_hash']}",
            )
            update.update({
                "documents_selected": True, "document_manifest_id": snapshot["_id"], "document_manifest_hash": snapshot["snapshot_hash"],
                "matrix_revision_set_id": analysis["revision_set_id"], "matrix_revision_hash": analysis["revision_hash"],
                "readiness_artifact_id": readiness["matrix_revision_set_id"], "readiness_artifact_hash": readiness["artifact_hash"],
                "status": "awaiting_user_direction" if questions else "awaiting_matrix_review",
                "current_node": "material_question_gate" if questions else "matrix_review_gate",
                "next_action": "answer_questions" if questions else "review_matrices",
                "required_human_role": "legal_reviewer", "progress": 40, "blockers": analysis["blockers"],
                "last_material_editor_id": _actor_id(current_user),
                "targeted_questions": questions, "material_questions_required": bool(questions),
                "question_snapshot_id": question_snapshot["_id"], "question_snapshot_hash": question_snapshot["snapshot_hash"],
            })
        updated = await self.repository.transition(run_id, payload.state_version, update, event="workflow_resumed")
        if current_node in {"document_selection_gate", "material_question_gate"}:
            await self._sync_langgraph_checkpoint(
                updated,
                {
                    "documents_selected": bool(updated.get("documents_selected")),
                    "user_direction_complete": str(updated.get("current_node")) != "material_question_gate",
                },
            )
        await observability_registry.record_arbitration_workflow(
            engine=str(updated.get("engine")), status=str(updated.get("status")), node=str(updated.get("current_node")), event="resumed"
        )
        return self.public_state(updated)

    async def cancel(self, case_id: str, run_id: str, payload: Any, current_user: Any) -> Dict[str, Any]:
        run = await self.repository.get_run(run_id)
        if not run or str(run.get("case_id")) != case_id:
            raise HTTPException(status_code=404, detail="Arbitration workflow run not found")
        self._assert_state_version(run, payload.state_version)
        if str(run.get("status")) in TERMINAL_WORKFLOW_STATUSES:
            raise HTTPException(status_code=409, detail="Terminal arbitration workflows cannot be cancelled")
        updated = await self.repository.transition(
            run_id, payload.state_version,
            {"status": "cancelled", "current_node": "cancelled", "next_action": "cancelled", "fallback_available": False, "cancellation_reason": payload.reason},
            event="workflow_cancelled",
        )
        await self._sync_langgraph_checkpoint(
            updated,
            {
                "cancellation_requested": True,
                "execution_status": "cancelled",
                "current_node": "cancelled",
                "next_action": "cancelled",
            },
            traverse=False,
        )
        await observability_registry.record_arbitration_workflow(
            engine=str(updated.get("engine")), status=str(updated.get("status")), node=str(updated.get("current_node")), event="cancelled"
        )
        return self.public_state(updated)

    async def checkpoints(self, run_id: str, *, limit: int = 50) -> list[Dict[str, Any]]:
        from .langgraph_engine import LangGraphArbitrationEngine

        return await LangGraphArbitrationEngine(self.db).list_checkpoints(run_id, limit=limit)

    async def fallback(self, case_id: str, run_id: str, payload: Any, current_user: Any) -> Dict[str, Any]:
        run = await self.repository.get_run(run_id)
        if not run or str(run.get("case_id")) != case_id:
            raise HTTPException(status_code=404, detail="Arbitration workflow run not found")
        self._assert_state_version(run, payload.state_version)
        if run.get("engine") != "langgraph_v1":
            raise HTTPException(status_code=409, detail="Only LangGraph arbitration workflows can use the v2 fallback")
        if run.get("authoritative_effects"):
            raise HTTPException(status_code=409, detail="Fallback is prohibited after an authoritative effect")
        input_snapshot = await self.db.arbitration_workflow_snapshots.find_one(
            {"_id": run.get("input_snapshot_id"), "run_id": run_id, "kind": "input"}
        )
        if not input_snapshot or input_snapshot.get("snapshot_hash") != run.get("input_snapshot_hash"):
            raise HTTPException(status_code=409, detail="Immutable workflow input snapshot is missing or has drifted")
        updated = await self.repository.transition(
            run_id, payload.state_version,
            {
                "engine": "arbitration_v2",
                "engine_version": ArbitrationV2WorkflowEngine.version,
                "fallback_from_engine": "langgraph_v1",
                "fallback_input_snapshot_id": input_snapshot["_id"],
                "fallback_input_snapshot_hash": input_snapshot["snapshot_hash"],
                "fallback_reason": payload.reason,
                "fallback_available": False,
            },
            event="workflow_fallback_v2",
        )
        await observability_registry.record_arbitration_workflow(
            engine=str(updated.get("engine")), status=str(updated.get("status")), node=str(updated.get("current_node")), event="fallback_v2"
        )
        return self.public_state(updated)

    @staticmethod
    def public_state(run: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: run.get(key)
            for key in (
                "_id", "case_id", "draft_id", "pleading_type", "engine", "rollout_mode", "status",
                "current_node", "next_action", "state_version", "graph_version", "state_schema_version",
                "progress", "blockers", "required_human_role", "fallback_available", "last_checkpoint_at",
                "checkpoint_sync_status", "checkpoint_sync_state_version",
                "created_at", "updated_at", "document_manifest_hash", "opponent_pleading_snapshot_hash",
                "evidence_snapshot_hash", "matrix_revision_set_id", "matrix_revision_hash",
                "readiness_artifact_hash", "plan_id", "plan_hash", "draft_version_id", "draft_version_hash",
                "validation_status", "validation_blockers",
                "targeted_questions",
                "fallback_reason", "fallback_from_engine", "fallback_input_snapshot_id", "fallback_input_snapshot_hash",
            )
        } | {
            "run_id": run.get("_id"),
            "approval_receipt_ids": {
                gate: run.get(f"{gate}_approval_receipt_id")
                for gate in GATE_ARTIFACT_FIELDS if run.get(f"{gate}_approval_receipt_id")
            },
        }
