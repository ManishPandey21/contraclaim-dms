from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException

from ....core.config import settings
from ....models.arbitration_drafting import ArbitrationWorkflowCreateRequest
from ..case_workspace import ArbitrationCaseWorkspaceService, _actor_id
from ..matrix_registry import MATRIX_COLLECTIONS
from ..repository import ArbitrationDraftingRepository
from ..workflow_repository import ArbitrationWorkflowRepository, artifact_hash
from ..workflow_domain import ArbitrationWorkflowDomain


class ArbitrationV2WorkflowEngine:
    name = "arbitration_v2"
    version = "2"

    def __init__(self, db: Any) -> None:
        self.db = db
        self.repository = ArbitrationWorkflowRepository(db)
        self.drafts = ArbitrationDraftingRepository(db)
        self.cases = ArbitrationCaseWorkspaceService(db)
        self.domain = ArbitrationWorkflowDomain(db)

    async def create_workflow(
        self,
        case_id: str,
        payload: ArbitrationWorkflowCreateRequest,
        current_user: Any,
        *,
        idempotency_key: Optional[str],
        request_hash: str,
        rollout_mode: str = "off",
    ) -> dict:
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
        run_id = str(uuid.uuid4())
        input_payload = {
            "case_id": case_id,
            "draft_id": payload.draft_id,
            "pleading_type": payload.pleading_type,
            "opponent_draft_id": payload.opponent_draft_id,
            "opponent_version_id": payload.opponent_version_id,
            "opponent_pleadings": [item.model_dump() for item in payload.opponent_pleadings],
        }
        input_snapshot = await self.repository.create_snapshot(
            run_id=run_id, kind="input", payload=input_payload, effect_key=f"{run_id}:snapshot:input"
        )
        manifest = await self.repository.create_snapshot(
            run_id=run_id, kind="document_manifest", payload={"documents": documents}, effect_key=f"{run_id}:snapshot:documents"
        )
        opponent_snapshot = await self.domain.capture_opponent_snapshot(
            run_id, case_id, str(payload.pleading_type), payload.opponent_draft_id, payload.opponent_version_id,
            payload.opponent_pleadings,
        )
        analysis = await self.domain.analyze(
            {"_id": run_id, "case_id": case_id, "draft_id": payload.draft_id, "pleading_type": payload.pleading_type},
            parallel=True,
        )
        rows = {slug: await self.cases.list_matrix_rows(case_id, slug, draft_id=payload.draft_id) for slug in MATRIX_COLLECTIONS}
        evidence_manifest = await self.cases._authoritative_evidence_manifest(case, rows)
        evidence_snapshot = await self.repository.create_snapshot(
            run_id=run_id,
            kind="evidence_manifest",
            payload={"evidence": evidence_manifest},
            effect_key=f"{run_id}:snapshot:evidence:{artifact_hash(evidence_manifest)}",
        )
        readiness_artifact = await self.cases._readiness_artifact_state(case, str(payload.pleading_type))
        questions = self.domain.material_questions(analysis["blockers"], str(payload.pleading_type))
        question_snapshot = await self.repository.create_snapshot(
            run_id=run_id, kind="material_questions", payload={"questions": questions}, effect_key=f"{run_id}:snapshot:questions"
        )
        now = datetime.now(timezone.utc)
        has_documents = bool(documents)
        run = {
            "_id": run_id,
            "thread_id": f"arbitration:{run_id}",
            "case_id": case_id,
            "draft_id": payload.draft_id,
            "organization_id": case.get("organization_id"),
            "project_id": case.get("project_id"),
            "pleading_type": payload.pleading_type,
            "engine": self.name,
            "engine_version": self.version,
            "rollout_mode": rollout_mode,
            "graph_version": str(settings.ARBITRATION_ENGINE_GRAPH_VERSION),
            "state_schema_version": int(settings.ARBITRATION_ENGINE_STATE_SCHEMA_VERSION),
            "status": ("awaiting_user_direction" if questions else "awaiting_matrix_review") if has_documents else "awaiting_document_selection",
            "current_node": ("material_question_gate" if questions else "matrix_review_gate") if has_documents else "document_selection_gate",
            "next_action": ("answer_questions" if questions else "review_matrices") if has_documents else "select_documents",
            "required_human_role": ("counsel" if questions else "legal_reviewer") if has_documents else "drafter",
            "progress": 25 if has_documents else 10,
            "state_version": 1,
            "input_snapshot_id": input_snapshot["_id"],
            "input_snapshot_hash": input_snapshot["snapshot_hash"],
            "document_manifest_id": manifest["_id"],
            "document_manifest_hash": manifest["snapshot_hash"],
            "opponent_pleading_snapshot_id": (opponent_snapshot or {}).get("_id"),
            "opponent_pleading_snapshot_hash": (opponent_snapshot or {}).get("snapshot_hash"),
            "evidence_snapshot_id": evidence_snapshot["_id"],
            "evidence_snapshot_hash": readiness_artifact["evidence_snapshot_hash"],
            "matrix_revision_set_id": analysis["revision_set_id"],
            "matrix_revision_hash": analysis["revision_hash"],
            "readiness_artifact_id": readiness_artifact["matrix_revision_set_id"],
            "readiness_artifact_hash": readiness_artifact["artifact_hash"],
            "documents_selected": has_documents,
            "request_hash": request_hash,
            "idempotency_key": idempotency_key,
            "authoritative_effects": [],
            "blockers": analysis["blockers"],
            "targeted_questions": questions,
            "question_snapshot_id": question_snapshot["_id"],
            "question_snapshot_hash": question_snapshot["snapshot_hash"],
            "material_questions_required": bool(questions),
            "last_material_editor_id": _actor_id(current_user),
            "fallback_available": True,
            "created_by": _actor_id(current_user),
            "created_at": now,
            "updated_at": now,
        }
        return await self.repository.create_run(run)

    async def get_state(self, run_id: str) -> dict:
        run = await self.repository.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Arbitration workflow run not found")
        return run

    async def resume_workflow(self, run_id: str, payload: Any, current_user: Any) -> dict:
        return await self.repository.transition(run_id, payload.state_version, {"status": "running", "next_action": "poll"}, event="workflow_resumed")

    async def cancel_workflow(self, run_id: str, payload: Any, current_user: Any) -> dict:
        return await self.repository.transition(run_id, payload.state_version, {"status": "cancelled", "current_node": "cancelled", "next_action": "cancelled", "fallback_available": False, "cancellation_reason": payload.reason}, event="workflow_cancelled")

    async def fallback_to_v2(self, run_id: str, payload: Any, current_user: Any) -> dict:
        run = await self.get_state(run_id)
        if run.get("authoritative_effects"):
            raise HTTPException(status_code=409, detail="Fallback is prohibited after an authoritative draft, approval, or export effect")
        return await self.repository.transition(run_id, payload.state_version, {"engine": "arbitration_v2", "status": "fallback_v2", "current_node": "v2_fallback", "next_action": "resume_v2", "fallback_reason": payload.reason, "fallback_available": False}, event="workflow_fallback_v2")
