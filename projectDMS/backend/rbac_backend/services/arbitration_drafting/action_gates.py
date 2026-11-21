"""What stands between a pleading draft and Approve or filing Export (plan 4B-39, B4).

The screen offered Approve and Export whenever a draft had text, and the server
then refused. Each gate here is a condition the server checks itself, read from
the same records, so a button the screen disables is a call the server refuses
(``tests/integration/test_arbitration_ui_api_parity.py`` proves it for every code).

The reverse is not claimed: the case readiness gate, the governed workflow chain
(readiness, plan and legal review approvals) and the filing citation audit are
checked only when the action is taken. ``checked_on_action`` names them, so the
screen can say so instead of implying the action will succeed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import HTTPException

APPROVE_CODES = (
    "no_approve_permission",
    "scratch_draft",
    "no_version",
    "approval_blockers",
    "validation_not_passed",
    "missing_evidence",
    "self_approval",
)
EXPORT_CODES = (
    "no_export_permission",
    "scratch_draft",
    "not_approved",
    "approved_version_missing",
    "approved_version_invalid",
    "self_approval",
)
APPROVED_STATUSES = {"approved", "exported"}

#: Where on the drafting page the person can see and resolve each one.
ANCHORS = {
    "scratch_draft": "draft-case",
    "no_version": "draft-versions",
    "approval_blockers": "draft-citations",
    "validation_not_passed": "draft-validation",
    "missing_evidence": "draft-citations",
    "not_approved": "draft-approval",
    "approved_version_missing": "draft-versions",
    "approved_version_invalid": "draft-validation",
}


def _reason(code: str, message: str) -> Dict[str, Any]:
    return {"code": code, "message": message, "anchor": ANCHORS.get(code)}


def _ids(*values: Any) -> set:
    return {str(value) for value in values if value}


def approve_gate(
    draft: Dict[str, Any], latest: Optional[Dict[str, Any]], actor_id: Optional[str], *, may_approve: bool
) -> Dict[str, Any]:
    reasons: List[Dict[str, Any]] = []
    if not may_approve:
        reasons.append(_reason("no_approve_permission", "You do not have permission to approve pleadings."))
    if not draft.get("case_id"):
        reasons.append(_reason("scratch_draft", "A scratch draft has no case and cannot be approved. Attach it to a case."))
    if not latest or int(draft.get("current_version") or 0) < 1:
        reasons.append(_reason("no_version", "Generate or save a version before approval."))
    else:
        structured = latest.get("structured_output") or {}
        if structured.get("approval_blockers"):
            reasons.append(
                _reason("approval_blockers", f"{len(structured['approval_blockers'])} drafting blocker(s) must be resolved in a new version.")
            )
        if latest.get("validation_status") != "passed":
            reasons.append(_reason("validation_not_passed", "This version has not passed validation."))
        if latest.get("missing_evidence"):
            reasons.append(_reason("missing_evidence", "Evidence the version relies on is missing."))
    if actor_id and str(actor_id) in _ids(draft.get("created_by"), draft.get("updated_by"), (latest or {}).get("created_by")):
        reasons.append(_reason("self_approval", "You wrote or last changed this draft, so someone else must approve it."))
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "checked_on_action": ["Case readiness approval", "Plan approval", "Legal review approval"],
    }


def export_gate(
    draft: Dict[str, Any], approved_version: Optional[Dict[str, Any]], actor_id: Optional[str], *, may_export: bool
) -> Dict[str, Any]:
    reasons: List[Dict[str, Any]] = []
    if not may_export:
        reasons.append(_reason("no_export_permission", "You do not have permission to export pleadings for filing."))
    if not draft.get("case_id"):
        reasons.append(
            _reason("scratch_draft", "A scratch draft can only be downloaded as a watermarked preview, not for filing.")
        )
    if str(draft.get("status") or "") not in APPROVED_STATUSES or not draft.get("is_locked"):
        reasons.append(_reason("not_approved", "The draft must be approved and locked before it is exported for filing."))
    elif not approved_version or str(approved_version.get("_id")) != str(draft.get("approved_version_id")):
        reasons.append(_reason("approved_version_missing", "The approved version cannot be found."))
    elif approved_version.get("validation_status") != "passed" or approved_version.get("missing_evidence"):
        reasons.append(_reason("approved_version_invalid", "The approved version has unresolved validation issues."))
    if actor_id and str(actor_id) in _ids(
        draft.get("created_by"), draft.get("updated_by"), (approved_version or {}).get("created_by")
    ):
        reasons.append(_reason("self_approval", "You wrote or last changed this draft, so someone else must export it."))
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "checked_on_action": ["Workflow export approval", "Case readiness approval", "Citation and exhibit audit"],
    }


def refuse_unless(gate: Dict[str, Any]) -> None:
    """The action's own first check: refuse with the reasons the screen shows (409)."""
    if gate["allowed"]:
        return
    reasons = gate["reasons"]
    raise HTTPException(
        status_code=409,
        detail={
            "code": "draft_action_refused",
            "user_message": reasons[0]["message"],
            "detail": {"reasons": reasons},
        },
    )


__all__ = ["APPROVE_CODES", "EXPORT_CODES", "approve_gate", "export_gate", "refuse_unless"]
