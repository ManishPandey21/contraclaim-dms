from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from .repository import _collect, _jsonable


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
        await self.db.arbitration_workflow_runs.insert_one(_jsonable(run))
        await self.append_event(run["_id"], "workflow_created", actor_id=run.get("created_by"), data={"engine": run.get("engine")})
        return run

    async def create_snapshot(self, *, run_id: str, kind: str, payload: Dict[str, Any], effect_key: str) -> Dict[str, Any]:
        existing = await self.db.arbitration_workflow_snapshots.find_one({"effect_key": effect_key})
        if existing:
            return existing
        snapshot = {
            "_id": str(uuid.uuid4()),
            "run_id": run_id,
            "kind": kind,
            "effect_key": effect_key,
            "payload": _jsonable(payload),
            "snapshot_hash": artifact_hash(payload),
            "created_at": datetime.now(timezone.utc),
        }
        await self.db.arbitration_workflow_snapshots.insert_one(snapshot)
        return snapshot

    async def claim_effect(self, *, run_id: str, effect_key: str, effect_type: str, input_hash: str) -> Dict[str, Any]:
        existing = await self.db.arbitration_workflow_effects.find_one({"effect_key": effect_key})
        if existing:
            if existing.get("input_hash") != input_hash:
                raise HTTPException(status_code=409, detail="Workflow effect key was reused with different input")
            return existing
        effect = {
            "_id": str(uuid.uuid4()),
            "run_id": run_id,
            "effect_key": effect_key,
            "effect_type": effect_type,
            "input_hash": input_hash,
            "status": "claimed",
            "created_at": datetime.now(timezone.utc),
        }
        await self.db.arbitration_workflow_effects.insert_one(effect)
        return effect

    async def complete_effect(self, effect_key: str, output: Dict[str, Any]) -> None:
        await self.db.arbitration_workflow_effects.update_one(
            {"effect_key": effect_key},
            {"$set": {"status": "completed", "output_refs": _jsonable(output), "completed_at": datetime.now(timezone.utc)}},
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

    async def record_approval(self, receipt: Dict[str, Any]) -> Dict[str, Any]:
        existing = await self.db.arbitration_workflow_approvals.find_one(
            {"run_id": receipt.get("run_id"), "gate": receipt.get("gate"), "artifact_hash": receipt.get("artifact_hash"), "decision": receipt.get("decision")}
        )
        if existing:
            return existing
        await self.db.arbitration_workflow_approvals.insert_one(_jsonable(receipt))
        return receipt

    async def create_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        existing = await self.db.arbitration_plans.find_one({"run_id": plan.get("run_id"), "plan_hash": plan.get("plan_hash")})
        if existing:
            return existing
        await self.db.arbitration_plans.insert_one(_jsonable(plan))
        return plan
