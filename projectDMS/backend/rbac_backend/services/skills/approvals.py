"""Reviewer approvals that gate publication of a skill version.

A skill is method text that shapes contractual and legal output. Before a
version that names ``required_approvals`` can be published, each named review
must exist, given by a user who holds that reviewer role, and bound to the
exact text and evaluation result the reviewer saw.

Three responsibilities are kept apart, by user:

- the evaluator, who ran the evaluation (``eval_report.recorded_by``),
- the approvers, technical and legal,
- the publisher.

The legal signatory may hold no other of these on the same version. Holding a
platform role (Super Admin) is not holding a reviewer role.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from ...core.permissions import actor_role_names
from ...models.skill import (
    SkillApproval,
    SkillApprovalRequest,
    SkillApprovalStatus,
    SkillEvalReport,
    SkillVersion,
)
from ...utils.datetime import now_utc

SKILL_APPROVALS = "skill_approvals"


def eval_report_hash(report: SkillEvalReport) -> str:
    """Identity of an evaluation result: what was run and what it scored."""
    payload = {
        "suite": report.suite,
        "score": round(float(report.score), 6),
        "threshold": round(float(report.threshold), 6),
        "case_count": int(report.case_count),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _actor_id(actor: Any) -> str:
    if isinstance(actor, dict):
        return str(actor.get("id") or actor.get("_id") or "")
    return str(getattr(actor, "id", "") or "")


def approval_statuses(
    version: SkillVersion, report: SkillEvalReport, approvals: List[SkillApproval]
) -> List[SkillApprovalStatus]:
    """For each required review, the best matching approval and why it does or does not count."""
    report_hash = eval_report_hash(report)
    result: List[SkillApprovalStatus] = []
    for requirement in version.manifest.required_approvals:
        candidates = sorted(
            (
                item
                for item in approvals
                if item.skill_key == version.skill_key
                and item.version == version.version
                and item.kind == requirement.kind
                and item.reviewer_role == requirement.reviewer_role
            ),
            key=lambda item: item.decided_at,
            reverse=True,
        )
        status = SkillApprovalStatus(kind=requirement.kind, reviewer_role=requirement.reviewer_role)
        exact = [
            item
            for item in candidates
            if item.content_hash == version.content_hash and item.eval_report_hash == report_hash
        ]
        live = next((item for item in exact if item.revoked_at is None), None)
        if live is not None:
            status.satisfied, status.state, status.approval = True, "ok", live
        elif exact:
            status.state, status.approval = "revoked", exact[0]
        elif candidates:
            latest = candidates[0]
            status.approval = latest
            status.state = (
                "stale_content" if latest.content_hash != version.content_hash else "stale_evaluation"
            )
        result.append(status)
    return result


_STATE_TEXT = {
    "missing": "not given",
    "revoked": "revoked",
    "stale_content": "given for different skill text",
    "stale_evaluation": "given for a different evaluation result",
}


def describe_unsatisfied(statuses: List[SkillApprovalStatus]) -> List[str]:
    return [
        f"{item.kind} approval by {item.reviewer_role}: {_STATE_TEXT.get(item.state, item.state)}"
        for item in statuses
        if not item.satisfied
    ]


class SkillApprovalService:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def list(self, skill_key: str, version: str) -> List[SkillApproval]:
        docs = await self.db[SKILL_APPROVALS].find(
            {"skill_key": skill_key, "version": version}
        ).to_list(length=1000)
        return sorted((SkillApproval(**doc) for doc in docs), key=lambda item: item.decided_at)

    async def record(
        self, version: SkillVersion, request: SkillApprovalRequest, actor: Any
    ) -> SkillApproval:
        actor_id = _actor_id(actor)
        if not actor_id:
            raise HTTPException(status_code=403, detail="An approval needs an identified reviewer")
        if version.status != "draft":
            raise HTTPException(
                status_code=409,
                detail=f"{version.skill_key} {version.version} is {version.status}; only a draft is reviewed",
            )
        required = {(item.kind, item.reviewer_role) for item in version.manifest.required_approvals}
        if (request.kind, request.reviewer_role) not in required:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{version.skill_key} {version.version} does not call for a {request.kind} "
                    f"approval by {request.reviewer_role}"
                ),
            )
        if request.reviewer_role not in actor_role_names(actor):
            # Deliberately no platform-role shortcut: administrative privilege
            # is not specialist review.
            raise HTTPException(
                status_code=403,
                detail=f"Only a user holding the {request.reviewer_role} role can give this approval",
            )
        report = request.eval_report
        if not report.passed:
            raise HTTPException(
                status_code=422,
                detail="An approval can only be given against a passing evaluation",
            )
        if not report.recorded_by:
            raise HTTPException(
                status_code=422,
                detail="The evaluation report must name who ran it (recorded_by)",
            )
        if str(report.recorded_by) == actor_id:
            raise HTTPException(
                status_code=409,
                detail="The person who ran the evaluation cannot approve it",
            )
        report_hash = eval_report_hash(report)
        existing = [
            item
            for item in await self.list(version.skill_key, version.version)
            if item.revoked_at is None
            and item.content_hash == version.content_hash
            and item.eval_report_hash == report_hash
        ]
        for item in existing:
            if item.reviewer_user_id != actor_id:
                continue
            if item.kind == request.kind and item.reviewer_role == request.reviewer_role:
                return item  # already given; recording twice changes nothing
            if "legal" in {item.kind, request.kind}:
                raise HTTPException(
                    status_code=409,
                    detail="Legal approval must be independent: the legal signatory cannot also give a technical approval",
                )
        approval = SkillApproval(
            _id=str(uuid.uuid4()),
            skill_key=version.skill_key,
            version=version.version,
            content_hash=version.content_hash,
            eval_suite=report.suite,
            eval_report_hash=report_hash,
            kind=request.kind,
            reviewer_role=request.reviewer_role,
            reviewer_user_id=actor_id,
            comment=request.comment,
        )
        await self.db[SKILL_APPROVALS].insert_one(approval.model_dump(by_alias=True))
        return approval

    async def get(self, approval_id: str) -> Optional[SkillApproval]:
        doc = await self.db[SKILL_APPROVALS].find_one({"_id": approval_id})
        return SkillApproval(**doc) if doc else None

    async def revoke(self, approval_id: str, reason: str, actor: Any) -> SkillApproval:
        approval = await self.get(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="Approval not found")
        actor_id = _actor_id(actor)
        if approval.reviewer_user_id != actor_id and approval.reviewer_role not in actor_role_names(actor):
            raise HTTPException(
                status_code=403,
                detail=f"Only the reviewer or another {approval.reviewer_role} can revoke this approval",
            )
        if approval.revoked_at is not None:
            return approval
        update: Dict[str, Any] = {
            "revoked_at": now_utc(),
            "revoked_by": actor_id,
            "revoke_reason": reason,
        }
        await self.db[SKILL_APPROVALS].update_one({"_id": approval_id}, {"$set": update})
        refreshed = await self.get(approval_id)
        return refreshed or approval


__all__ = [
    "SKILL_APPROVALS",
    "SkillApprovalService",
    "approval_statuses",
    "describe_unsatisfied",
    "eval_report_hash",
]
