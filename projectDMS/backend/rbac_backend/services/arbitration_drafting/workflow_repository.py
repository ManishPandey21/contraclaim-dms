from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .repository import _collect, _jsonable


DOWNSTREAM_APPROVAL_GATES = ("matrix_review", "readiness", "plan", "legal_review", "draft", "export")
DOWNSTREAM_APPROVAL_FIELDS = tuple(f"{gate}_approval_receipt_id" for gate in DOWNSTREAM_APPROVAL_GATES)
EARLY_OR_REVIEW_NODES = {"document_selection_gate", "material_question_gate", "matrix_review_gate"}
TERMINAL_WORKFLOW_STATUSES = {"completed", "cancelled", "failed"}


def artifact_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(_jsonable(value), sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class ArbitrationWorkflowRepository:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return await self.db.arbitration_workflow_runs.find_one({"_id": run_id})

    async def get_by_idempotency_key(self, case_id: str, key: str) -> Optional[Dict[str, Any]]:
        return await self.db.arbitration_workflow_runs.find_one({"case_id": case_id, "idempotency_key": key})

    async def create_run(self, run: Dict[str, Any]) -> Dict[str, Any]:
        payload = _jsonable(run)
        idempotency_key = payload.get("idempotency_key")
        if not idempotency_key:
            await self.db.arbitration_workflow_runs.insert_one(payload)
            await self.append_event(run["_id"], "workflow_created", actor_id=run.get("created_by"), data={"engine": run.get("engine")})
            return run

        query = {"case_id": payload.get("case_id"), "idempotency_key": idempotency_key}
        inserted = False
        try:
            result = await self.db.arbitration_workflow_runs.update_one(
                query,
                {"$setOnInsert": payload},
                upsert=True,
            )
            inserted = getattr(result, "upserted_id", None) is not None
        except DuplicateKeyError:
            # A concurrent request won the unique (case_id, idempotency_key)
            # insert. Read and validate that winner below.
            inserted = False
        stored = await self.db.arbitration_workflow_runs.find_one(query)
        if not stored:
            raise HTTPException(status_code=409, detail="Unable to resolve the idempotent workflow creation")
        if stored.get("request_hash") != payload.get("request_hash"):
            raise HTTPException(status_code=409, detail="Idempotency-Key was reused with a different workflow request")
        if inserted:
            await self.append_event(
                run["_id"],
                "workflow_created",
                actor_id=run.get("created_by"),
                data={"engine": run.get("engine")},
            )
        return stored

    async def create_snapshot(self, *, run_id: str, kind: str, payload: Dict[str, Any], effect_key: str) -> Dict[str, Any]:
        snapshot = {
            "_id": str(uuid.uuid4()),
            "run_id": run_id,
            "kind": kind,
            "effect_key": effect_key,
            "payload": _jsonable(payload),
            "snapshot_hash": artifact_hash(payload),
            "created_at": datetime.now(timezone.utc),
        }
        try:
            await self.db.arbitration_workflow_snapshots.update_one(
                {"effect_key": effect_key},
                {"$setOnInsert": snapshot},
                upsert=True,
            )
        except DuplicateKeyError:
            pass
        stored = await self.db.arbitration_workflow_snapshots.find_one({"effect_key": effect_key})
        if not stored:
            raise HTTPException(status_code=409, detail="Unable to resolve the idempotent workflow snapshot")
        if (
            stored.get("run_id") != run_id
            or stored.get("kind") != kind
            or stored.get("snapshot_hash") != snapshot["snapshot_hash"]
        ):
            raise HTTPException(status_code=409, detail="Workflow snapshot effect key was reused with different input")
        return stored

    async def claim_effect(self, *, run_id: str, effect_key: str, effect_type: str, input_hash: str) -> Dict[str, Any]:
        effect = {
            "_id": str(uuid.uuid4()),
            "run_id": run_id,
            "effect_key": effect_key,
            "effect_type": effect_type,
            "input_hash": input_hash,
            "status": "claimed",
            "created_at": datetime.now(timezone.utc),
        }
        claimed_now = False
        try:
            result = await self.db.arbitration_workflow_effects.update_one(
                {"effect_key": effect_key},
                {"$setOnInsert": effect},
                upsert=True,
            )
            claimed_now = getattr(result, "upserted_id", None) is not None
        except DuplicateKeyError:
            claimed_now = False
        stored = await self.db.arbitration_workflow_effects.find_one({"effect_key": effect_key})
        if not stored:
            raise HTTPException(status_code=409, detail="Unable to resolve the workflow effect claim")
        if (
            stored.get("run_id") != run_id
            or stored.get("effect_type") != effect_type
            or stored.get("input_hash") != input_hash
        ):
            raise HTTPException(status_code=409, detail="Workflow effect key was reused with different input")
        return {**stored, "_claimed_now": claimed_now}

    async def complete_effect(self, effect_key: str, output: Dict[str, Any]) -> None:
        output_refs = _jsonable(output)
        updated = await self.db.arbitration_workflow_effects.find_one_and_update(
            {"effect_key": effect_key, "status": "claimed"},
            {"$set": {"status": "completed", "output_refs": output_refs, "completed_at": datetime.now(timezone.utc)}},
            return_document=ReturnDocument.AFTER,
        )
        if updated:
            return
        existing = await self.db.arbitration_workflow_effects.find_one({"effect_key": effect_key})
        if (
            existing
            and existing.get("status") == "completed"
            and artifact_hash(existing.get("output_refs")) == artifact_hash(output_refs)
        ):
            return
        raise HTTPException(status_code=409, detail="Workflow effect cannot be completed from its current state")

    async def fail_effect(self, effect_key: str, *, error_code: str) -> None:
        await self.db.arbitration_workflow_effects.update_one(
            {"effect_key": effect_key, "status": "claimed"},
            {
                "$set": {
                    "status": "failed",
                    "error_code": str(error_code),
                    "failed_at": datetime.now(timezone.utc),
                }
            },
        )

    async def transition(self, run_id: str, expected_version: int, update: Dict[str, Any], *, event: str) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        next_version = expected_version + 1
        updated = await self.db.arbitration_workflow_runs.find_one_and_update(
            {"_id": run_id, "state_version": expected_version, "status": {"$nin": ["completed", "cancelled", "failed"]}},
            {"$set": {**_jsonable(update), "state_version": next_version, "updated_at": now}},
            return_document=True,
        )
        if not updated:
            current = await self.get_run(run_id)
            if not current:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arbitration workflow run not found")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "Stale arbitration workflow state version", "current_state_version": current.get("state_version")},
            )
        await self.append_event(run_id, event, data={"state_version": next_version, "status": updated.get("status")})
        return updated

    async def append_event(
        self,
        run_id: str,
        event_type: str,
        *,
        actor_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        event = {
            "_id": str(uuid.uuid4()),
            "run_id": run_id,
            "event_type": event_type,
            "actor_id": actor_id,
            "data": _jsonable(data or {}),
            "created_at": datetime.now(timezone.utc),
        }
        await self.db.arbitration_workflow_events.insert_one(event)
        return event

    async def list_events(self, run_id: str, limit: int = 500) -> List[Dict[str, Any]]:
        return await _collect(self.db.arbitration_workflow_events.find({"run_id": run_id}).sort("created_at", 1).limit(limit))

    async def invalidate_case_dependencies(
        self,
        case_id: str,
        *,
        reason: str,
        actor_id: Optional[str],
    ) -> int:
        """Invalidate downstream gate receipts and rewind active runs after drift.

        Immutable snapshots, plans, and draft versions remain available for audit;
        only their authority in the active workflow is removed. The CAS increment
        also makes any already-open review/approval request stale.
        """

        now = datetime.now(timezone.utc)
        await self.db.arbitration_workflow_approvals.update_many(
            {
                "case_id": case_id,
                "gate": {"$in": list(DOWNSTREAM_APPROVAL_GATES)},
                "invalidated_at": None,
            },
            {
                "$set": {
                    "invalidated_at": now,
                    "invalidated_by": actor_id,
                    "invalidation_reason": reason,
                }
            },
        )
        runs = await _collect(
            self.db.arbitration_workflow_runs.find(
                {"case_id": case_id, "status": {"$nin": sorted(TERMINAL_WORKFLOW_STATUSES)}}
            )
        )
        invalidated = 0
        for run in runs:
            current_node = str(run.get("current_node") or "")
            update: Dict[str, Any] = {
                **{field: None for field in DOWNSTREAM_APPROVAL_FIELDS},
                "analysis_artifact_set_id": None,
                "analysis_artifact_set_hash": None,
                "matrix_revision_set_id": None,
                "matrix_revision_hash": None,
                "readiness_artifact_id": None,
                "readiness_artifact_hash": None,
                "plan_id": None,
                "plan_hash": None,
                "draft_version_id": None,
                "draft_version_hash": None,
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
                "dependency_drift_reason": reason,
                "dependency_drift_at": now,
                "updated_at": now,
            }
            if current_node not in EARLY_OR_REVIEW_NODES:
                update.update(
                    {
                        "status": "awaiting_matrix_review",
                        "current_node": "matrix_review_gate",
                        "next_action": "review_matrices",
                        "required_human_role": "legal_reviewer",
                        "progress": 40,
                    }
                )
            expected_version = int(run.get("state_version") or 0)
            changed = await self.db.arbitration_workflow_runs.find_one_and_update(
                {"_id": run["_id"], "state_version": expected_version},
                {"$set": _jsonable(update), "$inc": {"state_version": 1}},
                return_document=ReturnDocument.AFTER,
            )
            if not changed:
                continue
            invalidated += 1
            await self.append_event(
                str(run["_id"]),
                "workflow_dependencies_invalidated",
                actor_id=actor_id,
                data={
                    "reason": reason,
                    "previous_state_version": expected_version,
                    "state_version": changed.get("state_version"),
                    "routed_to": changed.get("current_node"),
                },
            )
        return invalidated

    async def record_approval(self, receipt: Dict[str, Any]) -> Dict[str, Any]:
        query = {
            "run_id": receipt.get("run_id"),
            "gate": receipt.get("gate"),
            "artifact_hash": receipt.get("artifact_hash"),
            "decision": receipt.get("decision"),
        }
        try:
            await self.db.arbitration_workflow_approvals.update_one(
                query,
                {"$setOnInsert": _jsonable(receipt)},
                upsert=True,
            )
        except DuplicateKeyError:
            pass
        stored = await self.db.arbitration_workflow_approvals.find_one(query)
        if not stored:
            raise HTTPException(status_code=409, detail="Unable to resolve the idempotent approval receipt")
        return stored

    async def create_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        query = {"run_id": plan.get("run_id"), "plan_hash": plan.get("plan_hash")}
        try:
            await self.db.arbitration_plans.update_one(
                query,
                {"$setOnInsert": _jsonable(plan)},
                upsert=True,
            )
        except DuplicateKeyError:
            pass
        stored = await self.db.arbitration_plans.find_one(query)
        if not stored:
            raise HTTPException(status_code=409, detail="Unable to resolve the idempotent pleading plan")
        return stored
