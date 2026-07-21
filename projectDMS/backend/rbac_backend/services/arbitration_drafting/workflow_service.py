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


class ArbitrationWorkflowService:
    def __init__(self, db: Any) -> None:
        self.db = db
        self.repository = ArbitrationWorkflowRepository(db)
        self.cases = ArbitrationCaseWorkspaceService(db)
        self.domain = ArbitrationWorkflowDomain(db)
        self.drafting = ArbitrationDraftingService(db)
        self.validation = ArbitrationValidationOrchestrator()

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
        await self.repository.record_approval(receipt)
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
            await self.repository.claim_effect(
                run_id=run_id, effect_key=f"{run_id}:generate:{run['plan_hash']}", effect_type="draft_generation", input_hash=run["plan_hash"]
            )
            detail = await self.drafting.generate(str(run["draft_id"]), ArbitrationGenerateRequest(), current_user)
            version = await self.drafting.repo.latest_version(str(run["draft_id"]))
            if not version:
                raise HTTPException(status_code=409, detail="Draft generation did not create an immutable version")
            version_hash = version.get("version_hash") or immutable_version_hash(version)
            validation = self.validation.bounded_remediation(await self.validation.evaluate(version))
            validation_snapshot = await self.repository.create_snapshot(
                run_id=run_id, kind="validation_report", payload=validation,
                effect_key=f"{run_id}:validation:{version_hash}",
            )
            await self.repository.complete_effect(f"{run_id}:generate:{run['plan_hash']}", {"draft_version_id": version.get("_id"), "draft_version_hash": version_hash})
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
        if updated.get("engine") == "langgraph_v1":
            flag = {
                "document_selection": "documents_selected", "matrix_review": "matrices_approved",
                "readiness": "readiness_approved", "plan": "plan_approved", "legal_review": "legal_review_approved",
                "draft": "draft_approved", "export": "export_authorized",
            }[gate]
            from .langgraph_engine import LangGraphArbitrationEngine

            await LangGraphArbitrationEngine(self.db).checkpoint_transition(updated, {flag: True})
        await observability_registry.record_arbitration_workflow(
            engine=str(updated.get("engine")), status=str(updated.get("status")), node=str(updated.get("current_node")), event=f"{gate}_approved"
        )
        return self.public_state(updated)

    async def resume(self, case_id: str, run_id: str, payload: Any, current_user: Any) -> Dict[str, Any]:
        run = await self.repository.get_run(run_id)
        if not run or str(run.get("case_id")) != case_id:
            raise HTTPException(status_code=404, detail="Arbitration workflow run not found")
        if payload.gate not in str(run.get("current_node") or ""):
            raise HTTPException(status_code=409, detail="Resume gate does not match the paused workflow node")
        update: Dict[str, Any] = {"status": "running", "next_action": "poll", "required_human_role": None}
        if str(run.get("current_node")) == "material_question_gate":
            required = {str(item.get("question_id")) for item in run.get("targeted_questions") or [] if item.get("required")}
            supplied = {str(key) for key, value in (payload.answers or {}).items() if str(value).strip()}
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
        elif str(run.get("current_node")) == "matrix_review_gate" and payload.gate == "matrix_review":
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
        if updated.get("engine") == "langgraph_v1" and str(run.get("current_node")) in {"document_selection_gate", "material_question_gate"}:
            from .langgraph_engine import LangGraphArbitrationEngine

            await LangGraphArbitrationEngine(self.db).checkpoint_transition(
                updated, {
                    "documents_selected": bool(updated.get("documents_selected")),
                    "user_direction_complete": str(updated.get("current_node")) != "material_question_gate",
                }
            )
        await observability_registry.record_arbitration_workflow(
            engine=str(updated.get("engine")), status=str(updated.get("status")), node=str(updated.get("current_node")), event="resumed"
        )
        return self.public_state(updated)

    async def cancel(self, case_id: str, run_id: str, payload: Any, current_user: Any) -> Dict[str, Any]:
        await self.get(case_id, run_id)
        updated = await self.repository.transition(
            run_id, payload.state_version,
            {"status": "cancelled", "current_node": "cancelled", "next_action": "cancelled", "fallback_available": False, "cancellation_reason": payload.reason},
            event="workflow_cancelled",
        )
        if updated.get("engine") == "langgraph_v1":
            from .langgraph_engine import LangGraphArbitrationEngine

            await LangGraphArbitrationEngine(self.db).checkpoint_transition(updated, {"cancellation_requested": True})
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
        if run.get("authoritative_effects"):
            raise HTTPException(status_code=409, detail="Fallback is prohibited after an authoritative effect")
        updated = await self.repository.transition(
            run_id, payload.state_version,
            {"engine": "arbitration_v2", "status": "fallback_v2", "current_node": "v2_fallback", "next_action": "resume_v2", "fallback_reason": payload.reason, "fallback_available": False},
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
                "created_at", "updated_at", "document_manifest_hash", "opponent_pleading_snapshot_hash",
                "evidence_snapshot_hash", "matrix_revision_set_id", "matrix_revision_hash",
                "readiness_artifact_hash", "plan_id", "plan_hash", "draft_version_id", "draft_version_hash",
                "validation_status", "validation_blockers",
                "targeted_questions",
            )
        } | {
            "run_id": run.get("_id"),
            "approval_receipt_ids": {
                gate: run.get(f"{gate}_approval_receipt_id")
                for gate in GATE_ARTIFACT_FIELDS if run.get(f"{gate}_approval_receipt_id")
            },
        }
