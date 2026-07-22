from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from fastapi import HTTPException, status


GATE_ARTIFACT_FIELDS = {
    "readiness": "readiness_artifact_hash",
    "plan": "plan_hash",
    "legal_review": "draft_version_hash",
    "draft": "draft_version_hash",
    "export": "draft_version_hash",
}


async def require_governed_workflow_chain(
    db: Any,
    *,
    draft_id: str,
    draft_version_hash: str,
    required_gates: Iterable[str],
    run: Optional[Dict[str, Any]] = None,
    allowed_nodes: Optional[set[str]] = None,
    allowed_statuses: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """Resolve a current workflow and its exact, non-invalidated approval chain.

    Filing and final approval must never infer governance from the mutable draft
    row alone.  The authoritative chain is the workflow run plus exact-hash
    receipts for every required human gate.
    """

    resolved = run
    if resolved is None:
        cursor = db.arbitration_workflow_runs.find(
            {
                "draft_id": str(draft_id),
                "draft_version_hash": str(draft_version_hash),
                "status": {"$nin": ["cancelled", "failed"]},
            }
        ).sort("updated_at", -1)
        rows = await cursor.to_list(length=2)
        resolved = rows[0] if rows else None
    if not resolved or str(resolved.get("draft_id") or "") != str(draft_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A governed arbitration workflow is required for this operation",
        )
    if str(resolved.get("draft_version_hash") or "") != str(draft_version_hash):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The workflow is not bound to the current immutable draft version",
        )
    if allowed_nodes is not None and str(resolved.get("current_node") or "") not in allowed_nodes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The workflow has not reached the required approval node",
        )
    if allowed_statuses is not None and str(resolved.get("status") or "") not in allowed_statuses:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The workflow has not reached the required approval status",
        )

    receipts: Dict[str, str] = {}
    for gate in required_gates:
        artifact_field = GATE_ARTIFACT_FIELDS.get(str(gate))
        artifact_hash = str(resolved.get(artifact_field) or "") if artifact_field else ""
        if not artifact_hash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Workflow artifact for the {gate} gate is unavailable",
            )
        receipt = await db.arbitration_workflow_approvals.find_one(
            {
                "run_id": str(resolved.get("_id")),
                "gate": str(gate),
                "artifact_hash": artifact_hash,
                "decision": "approved",
                "receipt_status": "committed",
                "invalidated_at": {"$exists": False},
            }
        )
        if not receipt:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Exact-hash {gate} approval receipt is required",
            )
        receipts[str(gate)] = str(receipt.get("_id"))

    return {**resolved, "_resolved_approval_receipts": receipts}


async def require_approved_plan_for_generation(db: Any, plan: Dict[str, Any]) -> Dict[str, Any]:
    run_id = str(plan.get("run_id") or "")
    plan_hash = str(plan.get("plan_hash") or "")
    receipt_id = str(plan.get("approval_receipt_id") or "")
    if not run_id or not plan_hash or str(plan.get("status") or "") != "approved" or not receipt_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Draft generation requires an approved, exact-hash pleading plan",
        )
    receipt = await db.arbitration_workflow_approvals.find_one(
        {
            "_id": receipt_id,
            "run_id": run_id,
            "gate": "plan",
            "artifact_hash": plan_hash,
            "decision": "approved",
            "receipt_status": {"$in": ["pending", "committed"]},
            "invalidated_at": {"$exists": False},
        }
    )
    if not receipt:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The pleading-plan approval receipt is missing, stale, or invalidated",
        )
    return receipt
