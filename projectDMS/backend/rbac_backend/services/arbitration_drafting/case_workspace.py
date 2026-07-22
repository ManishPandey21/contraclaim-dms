from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from ...models.arbitration_drafting import (
    ArbitrationAgentRun,
    ArbitrationAgentRunRequest,
    ArbitrationApprovalReceipt,
    ArbitrationCase,
    ArbitrationCaseCreate,
    ArbitrationCaseStatus,
    ArbitrationCaseUpdate,
    ArbitrationClaimHead,
    ArbitrationDraftStatus,
    ArbitrationMatrixReviewRequest,
    ArbitrationMatrixRow,
    ArbitrationMatrixRowCreate,
    ArbitrationMatrixRowUpdate,
    ArbitrationReadinessCheck,
    ArbitrationReadinessApprovalRequest,
    ArbitrationRejoinderPermissionRequest,
    ArbitrationReadinessResponse,
    ArbitrationSelectedReference,
    ReadinessCheckStatus,
)
from ..background_jobs import submit_background_job
from ..observability import observability_registry
from ..task_sync_service import TaskSyncService
from .agents import agent_run_metadata, run_arbitration_agent
from .exporter import ArbitrationDraftExporter
from .matrix_registry import MATRIX_COLLECTIONS
from .repository import ArbitrationDraftingRepository, _collect, _jsonable
from .approval_policy import enforce_author_approver_separation
from .paragraph_positions import PARAGRAPH_POSITION_MATRICES, is_paragraph_position_projection
from .workflow_repository import ArbitrationWorkflowRepository
from .filing_export_queue import get_filing_export_queue


BLOCKING_READINESS_STATUSES = {
    ReadinessCheckStatus.NEEDS_EVIDENCE.value,
    ReadinessCheckStatus.NEEDS_CLAUSE_SUPPORT.value,
    ReadinessCheckStatus.NEEDS_QUANTUM_SUPPORT.value,
    ReadinessCheckStatus.NEEDS_LEGAL_REVIEW.value,
    ReadinessCheckStatus.NEEDS_USER_CONFIRMATION.value,
    ReadinessCheckStatus.BLOCKED.value,
}

BUNDLE_EXPORT_FORMATS = {
    "zip": ("application/zip", "arbitration-case-bundle.zip"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "arbitration-filing-bundle.docx"),
    "pdf": ("application/pdf", "arbitration-filing-bundle.pdf"),
}
MAX_INLINE_BUNDLE_EXPORT_BYTES = 12 * 1024 * 1024

# Filing bundle volume structure from the SoC/SoD/Rejoinder guide (§19).
BUNDLE_VOLUME_PLEADINGS = "volume-1-pleadings"
BUNDLE_VOLUME_CONTRACT = "volume-2-contract-documents"
BUNDLE_VOLUME_CORRESPONDENCE = "volume-3-correspondence"
BUNDLE_VOLUME_PROGRAMME = "volume-4-programme-delay-records"
BUNDLE_VOLUME_PAYMENT = "volume-5-payment-measurement-records"
BUNDLE_VOLUME_CALCULATIONS = "volume-6-claim-calculations"
BUNDLE_VOLUME_EXPERT = "volume-7-expert-reports"
BUNDLE_VOLUME_AUTHORITIES = "volume-8-authorities"

# Pin-cites such as "C-12, p.3", "R-4 at page 12 ¶3", "CE-1, p. 7 para 2".
_PIN_CITE_RE = re.compile(
    r"\b((?:C|R|J|CE|RE|QE|DE)-\d+)\s*(?:,|\bat\b)?\s*p(?:age|g)?\.?\s*(\d+)"
    r"(?:\s*(?:¶|para(?:graph)?\.?)\s*(\d+))?",
    re.IGNORECASE,
)


def _exhibit_volume(row: Dict[str, Any]) -> str:
    """Map a document-index row to its guide §19 bundle volume."""
    text = " ".join(
        str(row.get(key) or "") for key in ["document_type", "title", "relevance_note"]
    ).lower()
    if any(term in text for term in ["authority", "case law", "judgment", "precedent"]):
        return BUNDLE_VOLUME_AUTHORITIES
    if "expert" in text:
        return BUNDLE_VOLUME_EXPERT
    if any(term in text for term in ["programme", "schedule", "delay", "eot", "extension of time", "critical path"]):
        return BUNDLE_VOLUME_PROGRAMME
    if any(term in text for term in ["payment", "ipc", "bill", "invoice", "measurement", "certificate"]):
        return BUNDLE_VOLUME_PAYMENT
    if any(
        term in text
        for term in ["contract", "agreement", "loa", "loi", "gcc", "scc", "boq", "specification", "drawing", "addend"]
    ):
        return BUNDLE_VOLUME_CONTRACT
    return BUNDLE_VOLUME_CORRESPONDENCE


def _valid_exhibit_pages(row: Dict[str, Any]) -> Optional[set[int]]:
    """Known valid page numbers for an exhibit, or None when unverifiable."""
    pages = [int(page) for page in row.get("page_numbers") or [] if str(page).isdigit()]
    if pages:
        return set(pages)
    page_count = row.get("page_count")
    if isinstance(page_count, int) and page_count > 0:
        return set(range(1, page_count + 1))
    return None


def _actor_id(user: Any) -> Optional[str]:
    if isinstance(user, dict):
        return user.get("id") or user.get("email") or user.get("_id")
    return getattr(user, "id", None) or getattr(user, "email", None)


def _actor_snapshot(user: Any) -> Dict[str, Any]:
    if isinstance(user, dict):
        return dict(user)
    return {
        "id": getattr(user, "id", None),
        "email": getattr(user, "email", None),
        "organization_id": getattr(user, "organization_id", None),
    }


def _clean_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None and key != "id"}


SERVER_CONTROLLED_MATRIX_FIELDS = {
    "approved",
    "approved_at",
    "approved_by",
    "approval_log",
    "approval_status",
    "human_approval_status",
    "last_review_action",
    "last_reviewed_at",
    "last_reviewed_by",
    "permission_approved_at",
    "permission_approved_by",
    "permission_obtained",
    "permission_source_id",
    "projection_hash",
    "projection_read_only",
    "projection_source",
    "readiness_status",
    "rejected_at",
    "rejected_by",
    "review_completed_roles",
    "review_status",
    "source_paragraph_response_id",
    "verified",
    "verification_status",
}


def _matrix_review_defaults(matrix_slug: str) -> Dict[str, Any]:
    defaults: Dict[str, Any] = {
        "approval_status": "needs_review",
        "human_approval_status": "needs_review",
        "verification_status": "needs_review",
        "readiness_status": "needs_review",
        "review_status": "needs_review",
        "review_completed_roles": [],
    }
    if matrix_slug == "issue-matrix":
        defaults["status"] = "needs_review"
    return defaults


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(_jsonable(value), sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _is_positive(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "yes", "true", "approved", "ready", "verified"}


def _is_ready_row(row: Dict[str, Any], *, approval_key: str = "approval_status") -> bool:
    review_status = str(row.get("review_status") or "").lower()
    if review_status in {"assigned", "under_review", "partially_approved", "changes_requested", "rejected"}:
        return False
    required_roles = {str(role).lower() for role in row.get("review_required_roles") or [] if role}
    completed_roles = {str(role).lower() for role in row.get("review_completed_roles") or [] if role}
    if required_roles and not required_roles.issubset(completed_roles):
        return False
    status_values = {
        str(row.get(approval_key) or "").lower(),
        str(row.get("human_approval_status") or "").lower(),
        str(row.get("readiness_status") or "").lower(),
        str(row.get("verification_status") or "").lower(),
    }
    return bool(status_values & {"approved", "ready", "verified", "supported"})


def _date_sort_value(row: Dict[str, Any]) -> str:
    value = row.get("document_date") or row.get("date") or row.get("created_at") or ""
    return str(value)


class ArbitrationCaseWorkspaceService:
    def __init__(self, db: Any) -> None:
        self.db = db
        self.draft_repo = ArbitrationDraftingRepository(db)

    async def create_case(self, payload: ArbitrationCaseCreate, current_user: Any) -> Dict[str, Any]:
        case_payload = payload.model_dump()
        case_payload["organization_id"] = payload.organization_id or getattr(current_user, "organization_id", None)
        case = ArbitrationCase(
            **case_payload,
            status=ArbitrationCaseStatus.MATRIX_PREPARATION,
            created_by=_actor_id(current_user),
            updated_at=datetime.utcnow(),
        ).model_dump(by_alias=True)
        await self.db.arbitration_cases.insert_one(_jsonable(case))
        return case

    async def list_cases(
        self,
        scope: Dict[str, Any],
        filters: Dict[str, Any],
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        query = dict(scope or {})
        query["deleted_at"] = {"$exists": False}
        for key in ["project_id", "contract_id", "status"]:
            if filters.get(key):
                query[key] = filters[key]
        if filters.get("q"):
            text = str(filters["q"])
            query["$or"] = [
                {"title": {"$regex": text, "$options": "i"}},
                {"case_reference": {"$regex": text, "$options": "i"}},
            ]
        cursor = self.db.arbitration_cases.find(query).sort("updated_at", -1).skip(skip).limit(limit)
        return await _collect(cursor)

    async def get_case(self, case_id: str, *, scope: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Load a case by id.

        ``scope`` is a tenant filter (e.g. from ``build_scope_query``) applied as
        defense-in-depth at the router boundary: a cross-tenant id yields 404
        instead of leaking existence. Internal service calls omit it because the
        route has already verified access.
        """
        query: Dict[str, Any] = {"_id": case_id, "deleted_at": {"$exists": False}}
        if scope:
            query.update(scope)
        case = await self.db.arbitration_cases.find_one(query)
        if not case:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arbitration case not found")
        return case

    async def update_case(self, case_id: str, payload: ArbitrationCaseUpdate, current_user: Any) -> Dict[str, Any]:
        await self.get_case(case_id)
        update = _clean_payload(payload.model_dump(exclude_unset=True))
        update["updated_by"] = _actor_id(current_user)
        update["updated_at"] = datetime.utcnow()
        updated = await self.db.arbitration_cases.find_one_and_update(
            {"_id": case_id, "deleted_at": {"$exists": False}},
            {"$set": _jsonable(update)},
            return_document=True,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arbitration case not found")
        await self.invalidate_readiness_approvals(case_id, "case_dependency_changed", current_user)
        return updated

    async def soft_delete_case(self, case_id: str, current_user: Any) -> None:
        await self.get_case(case_id)
        await self.db.arbitration_cases.update_one(
            {"_id": case_id},
            {
                "$set": {
                    "deleted_at": datetime.utcnow(),
                    "deleted_by": _actor_id(current_user),
                    "updated_at": datetime.utcnow(),
                }
            },
        )

    def _collection(self, matrix_slug: str) -> Any:
        collection_name = MATRIX_COLLECTIONS.get(matrix_slug)
        if not collection_name:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"message": "Unknown arbitration matrix", "matrix": matrix_slug, "available": sorted(MATRIX_COLLECTIONS)},
            )
        return self.db[collection_name]

    async def list_matrix_rows(self, case_id: str, matrix_slug: str, *, draft_id: Optional[str] = None) -> List[Dict[str, Any]]:
        await self.get_case(case_id)
        query: Dict[str, Any] = {"case_id": case_id, "deleted_at": {"$exists": False}}
        if draft_id:
            query["draft_id"] = draft_id
        rows = await _collect(self._collection(matrix_slug).find(query).sort("created_at", 1))
        if matrix_slug == "document-index":
            rows.sort(key=lambda row: (str(row.get("exhibit_prefix") or ""), int(row.get("exhibit_number") or 0), _date_sort_value(row)))
        return rows

    async def create_matrix_row(
        self,
        case_id: str,
        matrix_slug: str,
        payload: ArbitrationMatrixRowCreate,
        current_user: Any,
    ) -> Dict[str, Any]:
        case = await self.get_case(case_id)
        body = _clean_payload(payload.model_dump(exclude_none=True))
        if matrix_slug in PARAGRAPH_POSITION_MATRICES and body.get("draft_id"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Draft-bound defence/rejoinder positions are server-owned projections; update the paragraph response instead",
            )
        for key in SERVER_CONTROLLED_MATRIX_FIELDS:
            body.pop(key, None)
        if str(body.get("status") or "").lower() in {"approved", "ready", "verified"}:
            body.pop("status", None)
        body.update(_matrix_review_defaults(matrix_slug))
        now = datetime.utcnow()
        row = ArbitrationMatrixRow(
            **body,
            case_id=case_id,
            organization_id=case.get("organization_id"),
            project_id=case.get("project_id"),
            contract_id=case.get("contract_id"),
            created_by=_actor_id(current_user),
            created_at=now,
            updated_by=_actor_id(current_user),
            updated_at=now,
        ).model_dump(by_alias=True)
        row = await self._assign_exhibit_if_needed(case_id, matrix_slug, row)
        await self._collection(matrix_slug).insert_one(_jsonable(row))
        await self._touch_case(case_id, current_user)
        return row

    async def update_matrix_row(
        self,
        case_id: str,
        matrix_slug: str,
        row_id: str,
        payload: ArbitrationMatrixRowUpdate,
        current_user: Any,
    ) -> Dict[str, Any]:
        await self.get_case(case_id)
        collection = self._collection(matrix_slug)
        existing = await collection.find_one(
            {"_id": row_id, "case_id": case_id, "deleted_at": {"$exists": False}}
        )
        if existing and is_paragraph_position_projection(existing):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This row is a read-only paragraph-response projection; update the authoritative paragraph response instead",
            )
        update = _clean_payload(payload.model_dump(exclude_unset=True, by_alias=True))
        update.pop("_id", None)
        for key in SERVER_CONTROLLED_MATRIX_FIELDS:
            update.pop(key, None)
        if str(update.get("status") or "").lower() in {"approved", "ready", "verified"}:
            update.pop("status", None)
        update["updated_by"] = _actor_id(current_user)
        update["updated_at"] = datetime.utcnow()
        updated = await collection.find_one_and_update(
            {"_id": row_id, "case_id": case_id, "deleted_at": {"$exists": False}},
            {"$set": _jsonable(update)},
            return_document=True,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arbitration matrix row not found")
        if matrix_slug == "document-index" and not updated.get("exhibit_id"):
            updated = await self._assign_and_update_exhibit(case_id, updated)
        await self._touch_case(case_id, current_user)
        return updated

    async def matrix_row_from_payload(
        self,
        case_id: str,
        matrix_slug: str,
        payload: ArbitrationMatrixRowUpdate,
        current_user: Any,
    ) -> Dict[str, Any]:
        row_id = payload.id
        if not row_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Matrix row _id is required for PATCH")
        return await self.update_matrix_row(case_id, matrix_slug, row_id, payload, current_user)

    async def review_matrix_row(
        self,
        case_id: str,
        matrix_slug: str,
        row_id: str,
        payload: ArbitrationMatrixReviewRequest,
        current_user: Any,
    ) -> Dict[str, Any]:
        case = await self.get_case(case_id)
        collection = self._collection(matrix_slug)
        row = await collection.find_one({"_id": row_id, "case_id": case_id, "deleted_at": {"$exists": False}})
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arbitration matrix row not found")

        action = str(getattr(payload.action, "value", payload.action))
        role = str(getattr(payload.reviewer_role, "value", payload.reviewer_role) or "legal").lower()
        actor = _actor_id(current_user)
        now = datetime.utcnow()
        required_roles = self._review_roles(payload.required_roles or row.get("review_required_roles") or self._default_review_roles(matrix_slug))
        completed_roles = self._review_roles(row.get("review_completed_roles") or [])
        assignments = list(row.get("review_assignments") or [])
        comments = list(row.get("review_comments") or [])
        approval_log = list(row.get("approval_log") or [])

        if action in {"request_changes", "reject"} and not (payload.comment or "").strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Review comment is required for this action")
        if action == "assign" and not payload.reviewer_user_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="reviewer_user_id is required when assigning review")
        if action == "approve":
            enforce_author_approver_separation(
                current_user, [row.get("created_by"), row.get("last_material_editor_id")], gate=f"{matrix_slug} matrix review"
            )

        event = {
            "_id": str(uuid.uuid4()),
            "action": action,
            "reviewer_role": role,
            "reviewer_user_id": payload.reviewer_user_id,
            "comment": (payload.comment or "").strip() or None,
            "actor_id": actor,
            "actor_email": getattr(current_user, "email", None),
            "created_at": now,
            "due_at": payload.due_at,
        }
        approval_log.append(event)
        if event["comment"]:
            comments.append(event)

        update: Dict[str, Any] = {
            "review_required_roles": required_roles,
            "review_completed_roles": completed_roles,
            "review_comments": comments,
            "approval_log": approval_log,
            "last_review_action": action,
            "last_reviewed_by": actor,
            "last_reviewed_at": now,
            "updated_by": actor,
            "updated_at": now,
        }

        if action == "assign":
            assignment = {
                "_id": str(uuid.uuid4()),
                "reviewer_role": role,
                "reviewer_user_id": payload.reviewer_user_id,
                "assigned_by": actor,
                "assigned_at": now,
                "due_at": payload.due_at,
                "status": "assigned",
            }
            assignments.append(assignment)
            update.update(
                {
                    "review_status": "under_review",
                    "review_assignments": assignments,
                    "assigned_reviewer_id": payload.reviewer_user_id,
                    "approval_status": "needs_review",
                    "human_approval_status": "needs_review",
                    "verification_status": "needs_review",
                    "readiness_status": "needs_review",
                }
            )
        elif action == "comment":
            update["review_status"] = row.get("review_status") or "under_review"
            update["review_assignments"] = assignments
        elif action == "request_changes":
            update.update(
                {
                    "review_status": "changes_requested",
                    "approval_status": "needs_review",
                    "human_approval_status": "needs_review",
                    "verification_status": "needs_review",
                    "readiness_status": "needs_review",
                    "review_assignments": self._mark_assignments(assignments, role, "changes_requested", now),
                }
            )
        elif action == "approve":
            if role not in completed_roles:
                completed_roles.append(role)
            all_roles_complete = set(required_roles).issubset(set(completed_roles))
            update["review_completed_roles"] = completed_roles
            update["review_assignments"] = self._mark_assignments(assignments, role, "completed", now)
            if all_roles_complete:
                update.update(
                    {
                        "review_status": "approved",
                        "approval_status": "approved",
                        "human_approval_status": "approved",
                        "verification_status": "verified",
                        "readiness_status": "ready",
                        "approved_by": actor,
                        "approved_at": now,
                    }
                )
            else:
                update.update(
                    {
                        "review_status": "partially_approved",
                        "approval_status": "needs_review",
                        "human_approval_status": "needs_review",
                        "verification_status": "needs_review",
                        "readiness_status": "needs_review",
                    }
                )
        elif action == "reject":
            update.update(
                {
                    "review_status": "rejected",
                    "approval_status": "rejected",
                    "human_approval_status": "rejected",
                    "verification_status": "rejected",
                    "readiness_status": "rejected",
                    "review_assignments": self._mark_assignments(assignments, role, "rejected", now),
                    "rejected_by": actor,
                    "rejected_at": now,
                }
            )
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported review action: {action}")

        updated = await collection.find_one_and_update(
            {"_id": row_id, "case_id": case_id, "deleted_at": {"$exists": False}},
            {"$set": _jsonable(update)},
            return_document=True,
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arbitration matrix row not found")

        task_sync = TaskSyncService(self.db)
        if action == "assign":
            await task_sync.on_arbitration_matrix_review_assigned(case, updated, matrix_slug, payload.reviewer_user_id, actor, due_at=payload.due_at)
        elif action in {"approve", "reject"} and str(updated.get("review_status") or "") in {"approved", "rejected"}:
            await task_sync.on_arbitration_matrix_review_completed(updated, actor)

        await self.invalidate_readiness_approvals(case_id, f"matrix_review_{action}", current_user)
        await self.readiness(case_id)
        return updated

    async def record_rejoinder_permission(
        self,
        case_id: str,
        row_id: str,
        payload: ArbitrationRejoinderPermissionRequest,
        current_user: Any,
    ) -> Dict[str, Any]:
        case = await self.get_case(case_id)
        collection = self._collection("rejoinder-matrix")
        row = await collection.find_one({"_id": row_id, "case_id": case_id, "deleted_at": {"$exists": False}})
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rejoinder matrix row not found")
        if not _is_positive(row.get("new_matter")) or not _is_positive(row.get("permission_required")):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A permission receipt is only applicable to rejoinder rows with permission-required new matter",
            )
        scope = {"_id": payload.permission_source_id}
        if case.get("organization_id"):
            scope["organization_id"] = case.get("organization_id")
        if case.get("project_id"):
            scope["project_id"] = case.get("project_id")
        permission_source = None
        for collection_name in ("documents", "letters"):
            permission_source = await self.db[collection_name].find_one(scope)
            if permission_source:
                break
        if not permission_source:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Permission source must be an authoritative document in the same organization and project",
            )
        actor = _actor_id(current_user)
        enforce_author_approver_separation(
            current_user, [row.get("created_by"), row.get("last_material_editor_id")], gate="rejoinder permission"
        )
        now = datetime.utcnow()
        receipt = {
            "_id": str(uuid.uuid4()),
            "gate": "rejoinder_permission",
            "case_id": case_id,
            "draft_id": row.get("draft_id"),
            "draft_type": "rejoinder",
            "organization_id": case.get("organization_id"),
            "project_id": case.get("project_id"),
            "matrix_row_id": row_id,
            "permission_source_id": payload.permission_source_id,
            "permission_source_hash": _canonical_hash(
                {
                    key: permission_source.get(key)
                    for key in ("_id", "sha256", "current_version_id", "updated_at")
                }
            ),
            "decision": "approved",
            "approver_id": actor,
            "approver_role": "legal",
            "comment": payload.permission_notes,
            "approved_at": now,
        }
        await self.db.arbitration_workflow_approvals.insert_one(_jsonable(receipt))
        updated = await collection.find_one_and_update(
            {"_id": row_id, "case_id": case_id},
            {
                "$set": {
                    "permission_obtained": True,
                    "permission_source_id": payload.permission_source_id,
                    "permission_approved_by": actor,
                    "permission_approved_at": now,
                    "permission_notes": payload.permission_notes,
                    "permission_approval_receipt_id": receipt["_id"],
                    "updated_by": actor,
                    "updated_at": now,
                }
            },
            return_document=True,
        )
        if updated and updated.get("source_paragraph_response_id"):
            await self.db.arbitration_paragraph_responses.update_one(
                {"_id": updated["source_paragraph_response_id"], "draft_id": updated.get("draft_id")},
                {
                    "$set": {
                        "permission_obtained": True,
                        "permission_source_id": payload.permission_source_id,
                        "permission_approved_by": actor,
                        "permission_approved_at": now,
                        "permission_notes": payload.permission_notes,
                        "permission_approval_receipt_id": receipt["_id"],
                        "updated_at": now,
                    }
                },
            )
        await self.invalidate_readiness_approvals(case_id, "rejoinder_permission_changed", current_user)
        await self.readiness(case_id)
        return updated or row

    async def dashboard(self, case_id: str) -> Dict[str, Any]:
        case = await self.get_case(case_id)
        matrix_counts: Dict[str, int] = {}
        approved_counts: Dict[str, int] = {}
        for slug in MATRIX_COLLECTIONS:
            rows = await self.list_matrix_rows(case_id, slug)
            matrix_counts[slug] = len(rows)
            approved_counts[slug] = sum(1 for row in rows if _is_ready_row(row))
        draft_cursor = self.db.arbitration_drafts.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("updated_at", -1)
        drafts = await _collect(draft_cursor)
        readiness = await self.readiness(case_id)
        latest_runs = await _collect(self.db.arbitration_agent_runs.find({"case_id": case_id}).sort("created_at", -1).limit(10))
        return {
            "case": case,
            "matrix_counts": matrix_counts,
            "approved_counts": approved_counts,
            "drafts": drafts,
            "readiness": readiness,
            "latest_agent_runs": latest_runs,
        }

    async def readiness(
        self,
        case_id: str,
        *,
        draft_id: Optional[str] = None,
        draft_type: Optional[str] = None,
        persist: bool = True,
    ) -> Dict[str, Any]:
        case = await self.get_case(case_id)
        if draft_id and not draft_type:
            draft = await self.draft_repo.get_draft(draft_id)
            draft_type = (draft or {}).get("draft_type")
        rows = {slug: await self.list_matrix_rows(case_id, slug) for slug in MATRIX_COLLECTIONS}
        checks = self._compute_readiness_checks(case, rows, draft_id=draft_id, draft_type=draft_type)
        ready_count = sum(1 for check in checks if check["status"] == ReadinessCheckStatus.READY.value)
        score = round((ready_count / max(len(checks), 1)) * 100)
        blockers = [check for check in checks if check["status"] in BLOCKING_READINESS_STATUSES]
        response = {
            "case_id": case_id,
            "draft_id": draft_id,
            "readiness_score": score,
            "status": "ready" if not blockers else "blocked",
            "blockers": blockers,
            "checks": checks,
        }
        if persist:
            await self._persist_readiness(case_id, draft_id, checks, score, blockers)
        missing_evidence_count = sum(1 for check in checks if check["status"] == ReadinessCheckStatus.NEEDS_EVIDENCE.value)
        await observability_registry.record_arbitration_readiness(
            case_id=case_id,
            status=response["status"],
            score=score,
            missing_evidence_count=missing_evidence_count,
        )
        return response

    async def approve_readiness(
        self,
        case_id: str,
        current_user: Any,
        payload: Optional[ArbitrationReadinessApprovalRequest] = None,
    ) -> Dict[str, Any]:
        request = payload or ArbitrationReadinessApprovalRequest()
        case = await self.get_case(case_id)
        draft_id = request.draft_id
        draft_type = str(request.draft_type or "") or None
        if draft_id:
            draft = await self.draft_repo.get_draft(draft_id)
            if not draft or str(draft.get("case_id") or "") != case_id:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case-linked arbitration draft not found")
            draft_type = str(draft.get("draft_type") or draft_type or "")
        draft_type = draft_type or self._default_draft_type(case)
        readiness = await self.readiness(case_id, draft_id=draft_id, draft_type=draft_type)
        if readiness["blockers"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Readiness blockers must be resolved before drafting approval",
                    "blockers": readiness["blockers"],
                },
            )
        artifact = await self._readiness_artifact_state(case, draft_type)
        actor = _actor_id(current_user)
        if not actor:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Readiness approver identity is required")
        enforce_author_approver_separation(
            current_user, [case.get("created_by"), case.get("updated_by")], gate="readiness"
        )
        receipt = ArbitrationApprovalReceipt(
            gate="readiness",
            case_id=case_id,
            draft_id=draft_id,
            draft_type=draft_type,
            organization_id=case.get("organization_id"),
            project_id=str(case.get("project_id")),
            matrix_revision_set_id=artifact["matrix_revision_set_id"],
            matrix_revision_hash=artifact["matrix_revision_hash"],
            evidence_snapshot_hash=artifact["evidence_snapshot_hash"],
            artifact_hash=artifact["artifact_hash"],
            approver_id=actor,
            approver_role=str(request.reviewer_role or "legal"),
            comment=request.comment,
        ).model_dump(by_alias=True)
        await self.db.arbitration_workflow_approvals.insert_one(_jsonable(receipt))
        updated = await self.db.arbitration_cases.find_one_and_update(
            {"_id": case_id, "deleted_at": {"$exists": False}},
            {
                "$set": {
                    "status": ArbitrationCaseStatus.READY_FOR_DRAFTING.value,
                    "readiness_approved_by": actor,
                    "readiness_approved_at": receipt["approved_at"],
                    "readiness_approval_receipt_id": receipt["_id"],
                    "readiness_matrix_revision_set_id": receipt["matrix_revision_set_id"],
                    "readiness_matrix_revision_hash": receipt["matrix_revision_hash"],
                    "readiness_evidence_snapshot_hash": receipt["evidence_snapshot_hash"],
                    "readiness_draft_type": draft_type,
                    "updated_by": actor,
                    "updated_at": datetime.utcnow(),
                }
            },
            return_document=True,
        )
        return {"case": updated, "readiness": readiness, "approval_receipt": receipt}

    async def assert_case_ready_for_draft(
        self,
        draft: Dict[str, Any],
        *,
        allow_standalone_working_draft: bool = False,
    ) -> None:
        case_id = draft.get("case_id")
        if not case_id:
            if allow_standalone_working_draft:
                return
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Standalone arbitration drafts are working drafts and cannot pass filing readiness",
            )
        readiness = await self.readiness(case_id, draft_id=str(draft.get("_id")), draft_type=str(draft.get("draft_type") or ""))
        if readiness["blockers"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Arbitration case matrices are not ready for draft generation",
                    "case_id": case_id,
                    "readiness_score": readiness["readiness_score"],
                    "blockers": readiness["blockers"],
                },
            )
        case = await self.get_case(str(case_id))
        await self.require_readiness_approval(case, draft)

    async def require_readiness_approval(
        self,
        case: Dict[str, Any],
        draft: Dict[str, Any],
    ) -> Dict[str, Any]:
        case_id = str(case.get("_id") or "")
        draft_type = str(draft.get("draft_type") or self._default_draft_type(case))
        artifact = await self._readiness_artifact_state(case, draft_type)
        cursor = self.db.arbitration_workflow_approvals.find({"case_id": case_id, "gate": "readiness"}).sort(
            "approved_at", -1
        )
        receipts = await _collect(cursor)
        for receipt in receipts:
            if receipt.get("invalidated_at"):
                continue
            if str(receipt.get("draft_type") or "") != draft_type:
                continue
            receipt_draft_id = receipt.get("draft_id")
            if receipt_draft_id and str(receipt_draft_id) != str(draft.get("_id") or ""):
                continue
            if (
                receipt.get("matrix_revision_hash") == artifact["matrix_revision_hash"]
                and receipt.get("evidence_snapshot_hash") == artifact["evidence_snapshot_hash"]
                and receipt.get("artifact_hash") == artifact["artifact_hash"]
            ):
                return receipt
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "A current revision-bound readiness approval is required",
                "case_id": case_id,
                "draft_type": draft_type,
                "matrix_revision_set_id": artifact["matrix_revision_set_id"],
                "matrix_revision_hash": artifact["matrix_revision_hash"],
                "evidence_snapshot_hash": artifact["evidence_snapshot_hash"],
            },
        )

    async def invalidate_readiness_approvals(self, case_id: str, reason: str, current_user: Any) -> None:
        now = datetime.utcnow()
        actor_id = _actor_id(current_user)
        await self.db.arbitration_workflow_approvals.update_many(
            {"case_id": case_id, "gate": "readiness", "invalidated_at": None},
            {
                "$set": {
                    "invalidated_at": now,
                    "invalidated_by": actor_id,
                    "invalidation_reason": reason,
                }
            },
        )
        await ArbitrationWorkflowRepository(self.db).invalidate_case_dependencies(
            case_id,
            reason=reason,
            actor_id=actor_id,
        )
        await self.db.arbitration_cases.update_one(
            {"_id": case_id},
            {
                "$set": {
                    "status": ArbitrationCaseStatus.MATRIX_PREPARATION.value,
                    "readiness_approved_by": None,
                    "readiness_approved_at": None,
                    "readiness_approval_receipt_id": None,
                    "readiness_matrix_revision_set_id": None,
                    "readiness_matrix_revision_hash": None,
                    "readiness_evidence_snapshot_hash": None,
                    "readiness_draft_type": None,
                    "updated_at": now,
                }
            },
        )

    async def _readiness_artifact_state(self, case: Dict[str, Any], draft_type: str) -> Dict[str, str]:
        case_id = str(case.get("_id") or "")
        rows = {slug: await self.list_matrix_rows(case_id, slug) for slug in MATRIX_COLLECTIONS}
        matrix_manifest: List[Dict[str, Any]] = []
        for slug in sorted(rows):
            for row in sorted(rows[slug], key=lambda item: str(item.get("_id") or "")):
                matrix_manifest.append(
                    {
                        "matrix": slug,
                        "row": {
                            key: value
                            for key, value in row.items()
                            if key not in {"approval_log", "review_comments", "review_assignments"}
                        },
                    }
                )
        matrix_revision_hash = _canonical_hash(matrix_manifest)
        evidence_manifest = await self._authoritative_evidence_manifest(case, rows)
        evidence_snapshot_hash = _canonical_hash(evidence_manifest)
        artifact_hash = _canonical_hash(
            {
                "case_id": case_id,
                "draft_type": draft_type,
                "matrix_revision_hash": matrix_revision_hash,
                "evidence_snapshot_hash": evidence_snapshot_hash,
            }
        )
        return {
            "matrix_revision_set_id": f"mrs_{matrix_revision_hash[:24]}",
            "matrix_revision_hash": matrix_revision_hash,
            "evidence_snapshot_hash": evidence_snapshot_hash,
            "artifact_hash": artifact_hash,
        }

    async def _authoritative_evidence_manifest(
        self,
        case: Dict[str, Any],
        rows: Dict[str, List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        manifest: List[Dict[str, Any]] = []
        scope = {"organization_id": case.get("organization_id"), "project_id": case.get("project_id")}
        for row in rows.get("document-index") or []:
            if not _is_ready_row(row) or not row.get("source_id"):
                continue
            source_id = str(row.get("source_id"))
            authoritative = None
            for collection_name in ("documents", "letters"):
                query = {"_id": source_id, **{key: value for key, value in scope.items() if value}}
                authoritative = await self.db[collection_name].find_one(query)
                if authoritative:
                    break
            manifest.append(
                {
                    "matrix_row_id": row.get("_id"),
                    "source_id": source_id,
                    "source_hash": row.get("source_hash"),
                    "authoritative": {
                        key: (authoritative or {}).get(key)
                        for key in ("_id", "sha256", "current_version_id", "updated_at", "page_count")
                    },
                }
            )
        for row in rows.get("clause-matrix") or []:
            if not _is_ready_row(row):
                continue
            manifest.append(
                {
                    "matrix_row_id": row.get("_id"),
                    "source_id": row.get("clause_source_id") or row.get("source_id"),
                    "clause_number": row.get("clause_number"),
                    "clause_text_excerpt": row.get("clause_text_excerpt"),
                    "updated_at": row.get("updated_at"),
                }
            )
        return sorted(manifest, key=lambda item: (str(item.get("matrix_row_id") or ""), str(item.get("source_id") or "")))

    @staticmethod
    def _default_draft_type(case: Dict[str, Any]) -> str:
        return (
            "statement_of_defence"
            if str(case.get("party_perspective") or "").lower() == "respondent"
            else "statement_of_claim"
        )

    async def prepare_draft_from_case(self, draft_id: str, current_user: Any) -> Dict[str, Any]:
        draft = await self.draft_repo.get_draft(draft_id)
        if not draft:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arbitration draft not found")
        case_id = draft.get("case_id")
        if not case_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Draft is not linked to an arbitration case")
        case = await self.get_case(str(case_id))
        document_rows = [
            row
            for row in await self.list_matrix_rows(str(case_id), "document-index")
            if row.get("source_id") and _is_ready_row(row)
        ]
        clause_rows = [row for row in await self.list_matrix_rows(str(case_id), "clause-matrix") if _is_ready_row(row)]
        claim_rows = [row for row in await self.list_matrix_rows(str(case_id), "claim-matrix") if _is_ready_row(row)]
        references = self._references_from_case_rows(draft_id, document_rows, clause_rows, current_user)
        claim_heads = self._claim_heads_from_case_rows(draft_id, claim_rows)
        await self.draft_repo.replace_references(draft_id, references)
        if claim_heads:
            await self.draft_repo.replace_claim_heads(draft_id, claim_heads)
        draft_update = {
            "tribunal_details": draft.get("tribunal_details") or case.get("tribunal_details"),
            "arbitration_clause": draft.get("arbitration_clause") or case.get("arbitration_clause"),
            "governing_law": draft.get("governing_law") or case.get("governing_law"),
            "updated_by": _actor_id(current_user),
            "updated_at": datetime.utcnow(),
        }
        await self.draft_repo.update_draft(draft_id, {key: value for key, value in draft_update.items() if value is not None})
        return {
            "draft_id": draft_id,
            "case_id": case_id,
            "references_added": len(references),
            "claim_heads_added": len(claim_heads),
        }

    async def run_agent(
        self,
        case_id: str,
        agent_type: str,
        payload: ArbitrationAgentRunRequest,
        current_user: Any,
    ) -> Dict[str, Any]:
        return await self._run_agent_and_persist(case_id, agent_type, payload, current_user)

    async def queue_agent_run(
        self,
        case_id: str,
        agent_type: str,
        payload: ArbitrationAgentRunRequest,
        current_user: Any,
    ) -> Dict[str, Any]:
        case = await self.get_case(case_id)
        input_hash = self._agent_input_hash(case_id, agent_type, payload)
        run_metadata = agent_run_metadata(payload.options)
        run = ArbitrationAgentRun(
            case_id=case_id,
            draft_id=payload.draft_id,
            agent_type=agent_type,
            status="queued",
            input_hash=input_hash,
            prompt_version=run_metadata["prompt_version"],
            model=run_metadata["model"],
            created_by=_actor_id(current_user),
        ).model_dump(by_alias=True)
        await self.db.arbitration_agent_runs.insert_one(_jsonable(run))
        job_id = await submit_background_job(
            f"arbitration-agent:{agent_type}",
            self.execute_queued_agent_run,
            case_id,
            str(run["_id"]),
            agent_type,
            payload.model_dump(),
            _actor_snapshot(current_user),
            priority=5 if agent_type == "orchestrator" else 3,
            max_retries=1,
        )
        updated = await self.db.arbitration_agent_runs.find_one_and_update(
            {"_id": run["_id"], "case_id": case_id},
            {"$set": {"background_job_id": job_id}},
            return_document=True,
        )
        return updated or {**run, "background_job_id": job_id, "case": case}

    async def execute_queued_agent_run(
        self,
        case_id: str,
        run_id: str,
        agent_type: str,
        payload_data: Dict[str, Any],
        actor: Dict[str, Any],
    ) -> Dict[str, Any]:
        await self.db.arbitration_agent_runs.update_one(
            {"_id": run_id, "case_id": case_id},
            {"$set": {"status": "running", "started_at": datetime.utcnow()}},
        )
        try:
            payload = ArbitrationAgentRunRequest(**(payload_data or {}))
            return await self._run_agent_and_persist(case_id, agent_type, payload, actor, existing_run_id=run_id)
        except Exception as exc:
            failed = await self.db.arbitration_agent_runs.find_one_and_update(
                {"_id": run_id, "case_id": case_id},
                {
                    "$set": {
                        "status": "failed",
                        "errors": [str(exc)],
                        "completed_at": datetime.utcnow(),
                    }
                },
                return_document=True,
            )
            await observability_registry.record_arbitration_agent_run(
                agent_type=agent_type,
                status="failed",
                missing_evidence_count=0,
            )
            if failed:
                return failed
            raise

    async def _run_agent_and_persist(
        self,
        case_id: str,
        agent_type: str,
        payload: ArbitrationAgentRunRequest,
        current_user: Any,
        *,
        existing_run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        case = await self.get_case(case_id)
        input_hash = self._agent_input_hash(case_id, agent_type, payload)
        result = await run_arbitration_agent(
            self.db,
            case,
            agent_type,
            payload=payload,
            current_user=current_user,
        )
        errors = result.get("errors") or []
        warnings = result.get("warnings") or []
        run = ArbitrationAgentRun(
            case_id=case_id,
            draft_id=payload.draft_id,
            agent_type=agent_type,
            status="failed" if errors else "completed_with_warnings" if warnings else "completed",
            input_hash=input_hash,
            source_ids=result.get("source_ids") or [],
            output_summary=result.get("output_summary"),
            created_records=result.get("created_records") or [],
            warnings=warnings,
            errors=errors,
            prompt_version=result.get("prompt_version") or "arbitration-workflow-deterministic-1",
            model=result.get("model") or "deterministic-matrix-agent",
            created_by=_actor_id(current_user),
            completed_at=datetime.utcnow(),
        ).model_dump(by_alias=True)
        if existing_run_id:
            run["_id"] = existing_run_id
            await self.db.arbitration_agent_runs.update_one(
                {"_id": existing_run_id, "case_id": case_id},
                {"$set": _jsonable(run)},
            )
            stored = await self.db.arbitration_agent_runs.find_one({"_id": existing_run_id, "case_id": case_id})
            if stored:
                run = stored
        else:
            await self.db.arbitration_agent_runs.insert_one(_jsonable(run))
        if run.get("created_records"):
            await self.invalidate_readiness_approvals(case_id, f"agent_matrix_change:{agent_type}", current_user)
        readiness = await self.readiness(case_id, draft_id=payload.draft_id)
        missing_evidence_count = sum(1 for check in readiness.get("checks", []) if check.get("status") == ReadinessCheckStatus.NEEDS_EVIDENCE.value)
        await observability_registry.record_arbitration_agent_run(
            agent_type=agent_type,
            status=str(run.get("status") or "unknown"),
            missing_evidence_count=missing_evidence_count,
        )
        return run

    @staticmethod
    def _agent_input_hash(case_id: str, agent_type: str, payload: ArbitrationAgentRunRequest) -> str:
        source_state = {
            "case_id": case_id,
            "draft_id": payload.draft_id,
            "agent_type": agent_type,
            "options": payload.options,
        }
        return hashlib.sha256(json.dumps(source_state, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    async def list_agent_runs(self, case_id: str) -> List[Dict[str, Any]]:
        await self.get_case(case_id)
        return await _collect(self.db.arbitration_agent_runs.find({"case_id": case_id}).sort("created_at", -1))

    async def get_agent_run(self, case_id: str, run_id: str) -> Dict[str, Any]:
        await self.get_case(case_id)
        run = await self.db.arbitration_agent_runs.find_one({"_id": run_id, "case_id": case_id})
        if not run:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arbitration agent run not found")
        return run

    async def exhibit_list(self, case_id: str) -> List[Dict[str, Any]]:
        rows = [row for row in await self.list_matrix_rows(case_id, "document-index") if row.get("exhibit_id")]
        return sorted(rows, key=lambda row: (str(row.get("exhibit_prefix") or ""), int(row.get("exhibit_number") or 0)))

    async def _find_exhibit_source_record(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        source_id = str(row.get("source_id") or "")
        if not source_id:
            return None
        for collection_name in ("documents", "letters"):
            collection = getattr(self.db, collection_name, None)
            if collection is None:
                continue
            try:
                record = await collection.find_one({"_id": source_id})
            except Exception:
                record = None
            if record:
                return dict(record)
        return None

    @staticmethod
    def _existing_local_path(*candidates: Any) -> Optional[Path]:
        for candidate in candidates:
            if not candidate:
                continue
            try:
                path = Path(str(candidate))
                if path.is_file():
                    return path
            except OSError:
                continue
        return None

    async def _exhibit_file_status(self, row: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Cheap resolvability check for the citation audit (no byte fetch)."""
        record = await self._find_exhibit_source_record(row)
        if self._existing_local_path((record or {}).get("filepath_local"), row.get("source_file_link")):
            return True, None
        if (record or {}).get("filepath_s3"):
            return True, None
        if record is None:
            return False, "source record not found and source_file_link is not an existing file"
        return False, "source record has no local file or storage key"

    async def _load_exhibit_file(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve exhibit bytes: local path first, then S3 (mirrors document download)."""
        record = await self._find_exhibit_source_record(row)
        local = self._existing_local_path((record or {}).get("filepath_local"), row.get("source_file_link"))
        if local:
            try:
                return {"content": local.read_bytes(), "filename": (record or {}).get("filename") or local.name, "error": None}
            except OSError as exc:
                return {"content": None, "filename": (record or {}).get("filename") or local.name, "error": f"Local read failed: {exc}"}
        s3_key = (record or {}).get("filepath_s3")
        if s3_key:
            try:
                from ..s3_service import S3Service

                content = await S3Service().download_bytes(str(s3_key))
                return {
                    "content": content,
                    "filename": (record or {}).get("filename") or Path(str(s3_key)).name,
                    "error": None,
                }
            except Exception as exc:
                return {"content": None, "filename": (record or {}).get("filename"), "error": f"S3 download failed: {exc}"}
        return {
            "content": None,
            "filename": (record or {}).get("filename"),
            "error": "No resolvable file: source record has no local path or storage key.",
        }

    async def _collect_exhibit_files(self, exhibits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for row in exhibits:
            exhibit_id = str(row.get("exhibit_id") or "")
            if not exhibit_id:
                continue
            loaded = await self._load_exhibit_file(row)
            volume = _exhibit_volume(row)
            filename = str(loaded.get("filename") or f"{exhibit_id}.bin")
            suffix = Path(filename).suffix or ".bin"
            title = re.sub(r"[^A-Za-z0-9 _.-]+", "", str(row.get("title") or row.get("document_type") or "exhibit")).strip()
            archive_path = f"{volume}/{exhibit_id} - {(title or 'exhibit')[:80]}{suffix}"
            content = loaded.get("content")
            entries.append(
                {
                    "exhibit_id": exhibit_id,
                    "source_id": row.get("source_id"),
                    "volume": volume,
                    "path": archive_path if content is not None else None,
                    "filename": filename,
                    "size": len(content) if content is not None else 0,
                    "error": loaded.get("error"),
                    "content": content,
                }
            )
        return entries

    async def citation_audit(
        self,
        case_id: str,
        *,
        draft_ids: Optional[set[str]] = None,
        approved_only: bool = False,
    ) -> Dict[str, Any]:
        exhibits = await self.exhibit_list(case_id)
        exhibit_ids = {str(row.get("exhibit_id")) for row in exhibits if row.get("exhibit_id")}
        document_rows = await self.list_matrix_rows(case_id, "document-index")
        if approved_only:
            document_rows = [row for row in document_rows if _is_ready_row(row)]
            exhibits = [row for row in exhibits if _is_ready_row(row)]
            exhibit_ids = {str(row.get("exhibit_id")) for row in exhibits if row.get("exhibit_id")}
        issues: List[Dict[str, Any]] = []
        for row in document_rows:
            row_label = row.get("title") or row.get("document_type") or row.get("_id")
            if not row.get("source_id") and not row.get("source_file_link"):
                issues.append(
                    {
                        "severity": "blocking",
                        "issue_type": "missing_source_link",
                        "matrix": "document-index",
                        "matrix_row_id": row.get("_id"),
                        "message": f"Document index row has no source id or file link: {row_label}",
                    }
                )
            if not row.get("exhibit_id"):
                issues.append(
                    {
                        "severity": "blocking",
                        "issue_type": "missing_exhibit_id",
                        "matrix": "document-index",
                        "matrix_row_id": row.get("_id"),
                        "message": f"Document index row has no exhibit id: {row_label}",
                    }
                )
            if not _is_ready_row(row):
                issues.append(
                    {
                        "severity": "warning",
                        "issue_type": "document_not_approved",
                        "matrix": "document-index",
                        "matrix_row_id": row.get("_id"),
                        "message": f"Document index row is not verified/approved for filing: {row_label}",
                    }
                )
            if row.get("exhibit_id"):
                resolvable, reason = await self._exhibit_file_status(row)
                if not resolvable:
                    issues.append(
                        {
                            "severity": "blocking",
                            "issue_type": "exhibit_file_missing",
                            "matrix": "document-index",
                            "matrix_row_id": row.get("_id"),
                            "exhibit_id": row.get("exhibit_id"),
                            "message": f"Exhibit {row.get('exhibit_id')} file cannot be resolved for the bundle ({reason}): {row_label}",
                        }
                    )
        exhibit_pages = {str(row.get("exhibit_id")): _valid_exhibit_pages(row) for row in exhibits}
        draft_cursor = self.db.arbitration_drafts.find({"case_id": case_id, "deleted_at": {"$exists": False}})
        drafts = await _collect(draft_cursor)
        if draft_ids is not None:
            drafts = [draft for draft in drafts if str(draft.get("_id")) in draft_ids]
        cited: set[str] = set()
        draft_reports: List[Dict[str, Any]] = []
        for draft in drafts:
            latest = await self.draft_repo.latest_version(str(draft.get("_id")))
            text = str((latest or {}).get("full_markdown") or "")
            citations = sorted(set(re.findall(r"\b(?:C|R|J|CE|RE|QE|DE)-\d+\b", text)))
            source_key_citations = sorted(set(re.findall(r"\[(S\d+):[^\]]+\]", text)))
            pin_cites: List[Dict[str, Any]] = []
            for match in _PIN_CITE_RE.finditer(text):
                label, page_text, paragraph_text = match.group(1), match.group(2), match.group(3)
                pin_cite = {
                    "exhibit_id": label,
                    "page": int(page_text),
                    "paragraph": int(paragraph_text) if paragraph_text else None,
                    "text": match.group(0),
                }
                pin_cites.append(pin_cite)
                if label not in exhibit_ids:
                    continue  # reported below as missing_exhibit_reference
                valid_pages = exhibit_pages.get(label)
                if valid_pages is not None and pin_cite["page"] not in valid_pages:
                    issues.append(
                        {
                            "severity": "blocking",
                            "issue_type": "invalid_pin_cite",
                            "draft_id": draft.get("_id"),
                            "exhibit_id": label,
                            "message": (
                                f"Draft pin-cite '{pin_cite['text']}' references page {pin_cite['page']} of {label}, "
                                f"but that page is not in the exhibit's recorded pages."
                            ),
                        }
                    )
            source_ledger = (latest or {}).get("source_ledger") or []
            source_keys = {str(row.get("source_key")) for row in source_ledger if row.get("source_key")}
            missing_source_keys = [key for key in source_key_citations if key not in source_keys]
            cited.update(citations)
            for citation in citations:
                if citation not in exhibit_ids:
                    issues.append(
                        {
                            "severity": "blocking",
                            "issue_type": "missing_exhibit_reference",
                            "draft_id": draft.get("_id"),
                            "message": f"Draft cites exhibit {citation}, but it is absent from the case exhibit list.",
                        }
                    )
            for source_key in missing_source_keys:
                issues.append(
                    {
                        "severity": "blocking",
                        "issue_type": "missing_source_key",
                        "draft_id": draft.get("_id"),
                        "message": f"Draft cites {source_key}, but the latest source ledger does not contain that key.",
                    }
                )
            draft_reports.append(
                {
                    "draft_id": draft.get("_id"),
                    "title": draft.get("title"),
                    "version": (latest or {}).get("version"),
                    "citations": citations,
                    "source_key_citations": source_key_citations,
                    "pin_cites": pin_cites,
                    "missing_exhibits": [citation for citation in citations if citation not in exhibit_ids],
                    "missing_source_keys": missing_source_keys,
                }
            )
        missing = sorted(citation for citation in cited if citation not in exhibit_ids)
        unused = sorted(exhibit_id for exhibit_id in exhibit_ids if exhibit_id not in cited)
        blocking_issues = [issue for issue in issues if issue.get("severity") == "blocking"]
        return {
            "case_id": case_id,
            "ok": not blocking_issues,
            "exhibit_count": len(exhibit_ids),
            "cited_exhibits": sorted(cited),
            "missing_exhibits": missing,
            "unused_exhibits": unused,
            "issues": issues,
            "blocking_issue_count": len(blocking_issues),
            "warning_issue_count": len(issues) - len(blocking_issues),
            "drafts": draft_reports,
        }

    async def filing_bundle_manifest(self, case_id: str) -> Dict[str, Any]:
        payload = await self.filing_bundle_payload(case_id)
        return {
            "bundle_version": payload["bundle_version"],
            "generated_at": payload["generated_at"],
            "case": payload["case"],
            "exhibit_list": payload["exhibit_list"],
            "citation_audit": payload["citation_audit"],
            "matrix_counts": payload["matrix_counts"],
            "approved_counts": payload["approved_counts"],
            "readiness": payload["readiness"],
            "drafts": payload["drafts"],
        }

    async def filing_bundle_payload(self, case_id: str) -> Dict[str, Any]:
        dashboard = await self.dashboard(case_id)
        matrices = {slug: await self.list_matrix_rows(case_id, slug) for slug in MATRIX_COLLECTIONS}
        drafts = await self._filing_bundle_drafts(case_id)
        if not drafts:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No approved immutable draft versions are available for filing")
        draft_ids = {str(draft.get("_id")) for draft in drafts}
        citation_audit = await self.citation_audit(case_id, draft_ids=draft_ids, approved_only=True)
        if not citation_audit.get("ok"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "Citation or exhibit audit blocks filing bundle export", "issues": citation_audit.get("issues") or []},
            )
        return {
            "bundle_version": "arbitration-filing-bundle.v1",
            "generated_at": datetime.utcnow(),
            "case": dashboard["case"],
            "exhibit_list": await self.exhibit_list(case_id),
            "citation_audit": citation_audit,
            "matrix_counts": dashboard["matrix_counts"],
            "approved_counts": dashboard["approved_counts"],
            "readiness": dashboard["readiness"],
            "drafts": drafts,
            "matrices": matrices,
        }

    async def export_filing_bundle_zip(self, case_id: str, current_user: Any = None) -> bytes:
        payload = await self.filing_bundle_payload(case_id)
        if current_user is not None:
            await self._authorize_filing_bundle(payload, "zip", current_user)
        markdown = self._filing_bundle_markdown(payload)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(_jsonable(payload), default=str, indent=2))
            archive.writestr("filing-bundle-summary.md", markdown)
            archive.writestr("exhibit-list.json", json.dumps(_jsonable(payload.get("exhibit_list") or []), default=str, indent=2))
            archive.writestr("citation-audit.json", json.dumps(_jsonable(payload.get("citation_audit") or {}), default=str, indent=2))
            archive.writestr("readiness.json", json.dumps(_jsonable(payload.get("readiness") or {}), default=str, indent=2))
            for slug, rows in (payload.get("matrices") or {}).items():
                archive.writestr(f"matrices/{slug}.json", json.dumps(_jsonable(rows), default=str, indent=2))
            for draft in payload.get("drafts") or []:
                draft_id = str(draft.get("_id") or draft.get("draft_id") or "draft")
                safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", draft_id).strip("-") or "draft"
                archive.writestr(f"drafts/{safe_id}/draft.json", json.dumps(_jsonable(draft), default=str, indent=2))
                latest = draft.get("latest_version") or {}
                if latest.get("full_markdown"):
                    archive.writestr(f"drafts/{safe_id}/latest-version.md", str(latest.get("full_markdown") or ""))
                    # Guide §19 volume structure: pleadings are Volume 1.
                    archive.writestr(f"{BUNDLE_VOLUME_PLEADINGS}/{safe_id}.md", str(latest.get("full_markdown") or ""))
                if latest:
                    archive.writestr(f"drafts/{safe_id}/latest-version.json", json.dumps(_jsonable(latest), default=str, indent=2))
            # Guide §19 volumes: exhibit binaries placed by document type.
            exhibit_files = await self._collect_exhibit_files(payload.get("exhibit_list") or [])
            for entry in exhibit_files:
                if entry.get("content") is not None and entry.get("path"):
                    archive.writestr(entry["path"], entry["content"])
            archive.writestr(
                "exhibits/exhibit-files.json",
                json.dumps(
                    _jsonable([{key: value for key, value in entry.items() if key != "content"} for entry in exhibit_files]),
                    default=str,
                    indent=2,
                ),
            )
            matrices = payload.get("matrices") or {}
            archive.writestr(
                f"{BUNDLE_VOLUME_CALCULATIONS}/quantum-annexures.json",
                json.dumps(_jsonable(matrices.get("quantum-annexures") or []), default=str, indent=2),
            )
            archive.writestr(
                f"{BUNDLE_VOLUME_EXPERT}/expert-alignment.json",
                json.dumps(_jsonable(matrices.get("expert-alignment") or []), default=str, indent=2),
            )
        return buffer.getvalue()

    async def export_filing_bundle_docx(self, case_id: str, current_user: Any = None) -> bytes:
        payload = await self.filing_bundle_payload(case_id)
        if current_user is not None:
            await self._authorize_filing_bundle(payload, "docx", current_user)
        return ArbitrationDraftExporter.build_docx({"full_markdown": self._filing_bundle_markdown(payload)})

    async def export_filing_bundle_pdf(self, case_id: str, current_user: Any = None) -> bytes:
        payload = await self.filing_bundle_payload(case_id)
        if current_user is not None:
            await self._authorize_filing_bundle(payload, "pdf", current_user)
        return ArbitrationDraftExporter.build_pdf({"full_markdown": self._filing_bundle_markdown(payload)})

    async def queue_filing_bundle_export(self, case_id: str, export_format: str, current_user: Any) -> Dict[str, Any]:
        await self.get_case(case_id)
        normalized = self._normalize_bundle_format(export_format)
        # Fail synchronously before a durable export request is admitted.
        payload = await self.filing_bundle_payload(case_id)
        authorization = await self._authorize_filing_bundle(payload, normalized, current_user)
        bundle_hash = authorization["bundle_hash"]
        effect_key = f"arbitration_filing_bundle:{bundle_hash}:{normalized}"
        existing = await self.db.arbitration_bundle_exports.find_one(
            {"effect_key": effect_key}
        )
        if existing:
            if existing.get("status") != "completed":
                try:
                    await get_filing_export_queue().enqueue(
                        job_id=str(existing["_id"]),
                        effect_key=effect_key,
                        payload={
                            "case_id": case_id,
                            "export_id": str(existing["_id"]),
                            "format": normalized,
                            "effect_key": effect_key,
                        },
                    )
                except Exception as exc:
                    await self.db.arbitration_bundle_exports.update_one(
                        {"_id": existing["_id"], "effect_key": effect_key, "status": {"$ne": "completed"}},
                        {
                            "$set": {
                                "status": "queue_unavailable",
                                "error": str(exc)[:2000],
                                "updated_at": datetime.utcnow(),
                            }
                        },
                    )
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Durable filing export queue is unavailable; no export job was accepted",
                    ) from exc
                existing = await self.db.arbitration_bundle_exports.find_one_and_update(
                    {"_id": existing["_id"], "effect_key": effect_key, "status": {"$ne": "completed"}},
                    {"$set": {"status": "queued", "error": None, "updated_at": datetime.utcnow()}},
                    return_document=True,
                ) or existing
            return self._public_bundle_export(existing)
        content_type, filename = BUNDLE_EXPORT_FORMATS[normalized]
        export_id = str(uuid.uuid5(uuid.NAMESPACE_URL, effect_key))
        export = {
            "_id": export_id,
            "case_id": case_id,
            "format": normalized,
            "status": "queued",
            "content_type": content_type,
            "filename": filename,
            "content_length": 0,
            "bundle_hash": bundle_hash,
            "effect_key": effect_key,
            "export_authorization_id": authorization["_id"],
            "attempts": 0,
            "created_by": _actor_id(current_user),
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(days=7),
        }
        try:
            await self.db.arbitration_bundle_exports.insert_one(_jsonable(export))
        except DuplicateKeyError:
            existing = await self.db.arbitration_bundle_exports.find_one({"effect_key": effect_key})
            if not existing:
                raise
            export = existing
        try:
            job_id = await get_filing_export_queue().enqueue(
                job_id=str(export["_id"]),
                effect_key=effect_key,
                payload={
                    "case_id": case_id,
                    "export_id": str(export["_id"]),
                    "format": normalized,
                    "effect_key": effect_key,
                },
            )
        except Exception as exc:
            await self.db.arbitration_bundle_exports.update_one(
                {"_id": export["_id"], "effect_key": effect_key, "status": {"$ne": "completed"}},
                {"$set": {"status": "queue_unavailable", "error": str(exc)[:2000], "updated_at": datetime.utcnow()}},
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Durable filing export queue is unavailable; no export job was accepted",
            ) from exc
        updated = await self.db.arbitration_bundle_exports.find_one_and_update(
            {"_id": export["_id"], "case_id": case_id},
            {"$set": {"background_job_id": job_id, "status": "queued", "error": None, "updated_at": datetime.utcnow()}},
            return_document=True,
        )
        return self._public_bundle_export(updated or {**export, "background_job_id": job_id})

    async def execute_filing_bundle_export_job(
        self,
        case_id: str,
        export_id: str,
        export_format: str,
        *,
        effect_key: Optional[str] = None,
        lease_token: Optional[str] = None,
        lease_seconds: int = 300,
        worker_name: str = "inline",
    ) -> Dict[str, Any]:
        normalized = self._normalize_bundle_format(export_format)
        existing = await self.db.arbitration_bundle_exports.find_one({"_id": export_id, "case_id": case_id})
        if not existing:
            raise RuntimeError("Filing bundle export record does not exist")
        expected_effect = str(effect_key or existing.get("effect_key") or "")
        if not expected_effect or str(existing.get("effect_key") or "") != expected_effect:
            raise RuntimeError("Filing bundle export effect-key mismatch")
        if existing.get("status") == "completed" and existing.get("content"):
            return self._public_bundle_export(existing)
        token = str(lease_token or uuid.uuid4())
        now = datetime.utcnow()
        lease_expires = now + timedelta(seconds=max(60, int(lease_seconds)))
        claimed = await self.db.arbitration_bundle_exports.find_one_and_update(
            {
                "_id": export_id,
                "case_id": case_id,
                "effect_key": expected_effect,
                "$or": [
                    {"status": {"$in": ["queued", "retrying", "failed", "queue_unavailable"]}},
                    {"execution_lease_expires_at": {"$lte": now}},
                    {"execution_lease_token": token},
                ],
            },
            {
                "$set": {
                    "status": "running",
                    "started_at": now,
                    "updated_at": now,
                    "execution_lease_token": token,
                    "execution_lease_expires_at": lease_expires,
                    "worker_name": worker_name,
                    "error": None,
                },
                "$inc": {"attempts": 1},
            },
            return_document=True,
        )
        if not claimed:
            current = await self.db.arbitration_bundle_exports.find_one({"_id": export_id, "effect_key": expected_effect})
            if current and current.get("status") == "completed" and current.get("content"):
                return self._public_bundle_export(current)
            raise TimeoutError("Filing bundle export effect is owned by another live lease")
        content = await self._build_filing_bundle_export_content(case_id, normalized)
        if len(content) > MAX_INLINE_BUNDLE_EXPORT_BYTES:
            raise RuntimeError(
                f"Filing bundle export is {len(content)} bytes; inline export limit is {MAX_INLINE_BUNDLE_EXPORT_BYTES} bytes"
            )
        content_type, filename = BUNDLE_EXPORT_FORMATS[normalized]
        completed_at = datetime.utcnow()
        updated = await self.db.arbitration_bundle_exports.find_one_and_update(
            {
                "_id": export_id,
                "case_id": case_id,
                "effect_key": expected_effect,
                "execution_lease_token": token,
                "status": "running",
            },
            {
                "$set": {
                    "status": "completed",
                    "content": content,
                    "content_sha256": hashlib.sha256(content).hexdigest(),
                    "content_type": content_type,
                    "filename": filename,
                    "content_length": len(content),
                    "completed_at": completed_at,
                    "updated_at": completed_at,
                    "execution_lease_token": None,
                    "execution_lease_expires_at": None,
                }
            },
            return_document=True,
        )
        if not updated:
            current = await self.db.arbitration_bundle_exports.find_one({"_id": export_id, "effect_key": expected_effect})
            if current and current.get("status") == "completed" and current.get("content"):
                return self._public_bundle_export(current)
            raise TimeoutError("Filing bundle export lost its Mongo effect lease before commit")
        await observability_registry.record_arbitration_bundle_export(format=normalized, status="completed")
        return self._public_bundle_export(updated)

    async def heartbeat_filing_bundle_export_job(
        self,
        case_id: str,
        export_id: str,
        *,
        effect_key: str,
        lease_token: str,
        lease_seconds: int,
        worker_name: str,
    ) -> None:
        now = datetime.utcnow()
        result = await self.db.arbitration_bundle_exports.update_one(
            {
                "_id": export_id,
                "case_id": case_id,
                "effect_key": effect_key,
                "execution_lease_token": lease_token,
                "status": "running",
            },
            {
                "$set": {
                    "execution_lease_expires_at": now + timedelta(seconds=max(60, int(lease_seconds))),
                    "heartbeat_at": now,
                    "updated_at": now,
                    "worker_name": worker_name,
                }
            },
        )
        if not getattr(result, "matched_count", 0):
            raise TimeoutError("Filing bundle export Mongo lease was lost")

    async def record_filing_bundle_export_failure(
        self,
        case_id: str,
        export_id: str,
        *,
        effect_key: str,
        lease_token: str,
        terminal: bool,
        error: str,
    ) -> None:
        now = datetime.utcnow()
        await self.db.arbitration_bundle_exports.update_one(
            {
                "_id": export_id,
                "case_id": case_id,
                "effect_key": effect_key,
                "execution_lease_token": lease_token,
                "status": "running",
            },
            {
                "$set": {
                    "status": "failed" if terminal else "retrying",
                    "error": str(error)[:2000],
                    "updated_at": now,
                    "completed_at": now if terminal else None,
                    "execution_lease_token": None,
                    "execution_lease_expires_at": None,
                }
            },
        )
        await observability_registry.record_arbitration_bundle_export(
            format=str((await self.db.arbitration_bundle_exports.find_one({"_id": export_id}) or {}).get("format") or "unknown"),
            status="failed" if terminal else "retrying",
        )

    async def _authorize_filing_bundle(self, payload: Dict[str, Any], export_format: str, current_user: Any) -> Dict[str, Any]:
        drafts = payload.get("drafts") or []
        enforce_author_approver_separation(
            current_user,
            [
                value
                for draft in drafts
                for value in (draft.get("created_by"), draft.get("updated_by"), (draft.get("approved_version") or {}).get("created_by"))
            ],
            gate="filing bundle export authorization",
        )
        bundle_manifest = {
            "case_id": (payload.get("case") or {}).get("_id"),
            "draft_versions": sorted(
                {
                    str(draft.get("_id")): str(draft.get("approved_version_hash"))
                    for draft in drafts
                }.items()
            ),
            "readiness": payload.get("readiness"),
            "citation_audit": payload.get("citation_audit"),
        }
        bundle_hash = _canonical_hash(bundle_manifest)
        query = {"case_id": bundle_manifest["case_id"], "bundle_hash": bundle_hash, "format": export_format}
        existing = await self.db.arbitration_export_authorizations.find_one(query)
        if existing:
            return existing
        authorization = {
            "_id": str(uuid.uuid4()), "gate": "filing_bundle_export", **query,
            "authorized_by": _actor_id(current_user), "authorized_at": datetime.utcnow(),
            "draft_version_hashes": dict(bundle_manifest["draft_versions"]),
        }
        await self.db.arbitration_export_authorizations.insert_one(_jsonable(authorization))
        return authorization

    async def get_filing_bundle_export(self, case_id: str, export_id: str) -> Dict[str, Any]:
        await self.get_case(case_id)
        export = await self.db.arbitration_bundle_exports.find_one({"_id": export_id, "case_id": case_id})
        if not export:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing bundle export job not found")
        return self._public_bundle_export(export)

    async def get_filing_bundle_export_content(self, case_id: str, export_id: str) -> Dict[str, Any]:
        export = await self.db.arbitration_bundle_exports.find_one({"_id": export_id, "case_id": case_id})
        if not export:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing bundle export job not found")
        if export.get("status") != "completed" or not export.get("content"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "Filing bundle export is not ready", "status": export.get("status")},
            )
        return {
            "content": bytes(export.get("content")),
            "content_type": export.get("content_type") or BUNDLE_EXPORT_FORMATS.get(str(export.get("format")), BUNDLE_EXPORT_FORMATS["zip"])[0],
            "filename": export.get("filename") or BUNDLE_EXPORT_FORMATS.get(str(export.get("format")), BUNDLE_EXPORT_FORMATS["zip"])[1],
        }

    async def _build_filing_bundle_export_content(self, case_id: str, export_format: str) -> bytes:
        if export_format == "zip":
            return await self.export_filing_bundle_zip(case_id)
        if export_format == "docx":
            return await self.export_filing_bundle_docx(case_id)
        if export_format == "pdf":
            return await self.export_filing_bundle_pdf(case_id)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported bundle export format: {export_format}")

    @staticmethod
    def _normalize_bundle_format(export_format: str) -> str:
        normalized = str(export_format or "").strip().lower()
        if normalized not in BUNDLE_EXPORT_FORMATS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"message": "Unsupported filing bundle export format", "available": sorted(BUNDLE_EXPORT_FORMATS)},
            )
        return normalized

    @staticmethod
    def _public_bundle_export(export: Dict[str, Any]) -> Dict[str, Any]:
        public = dict(export or {})
        public.pop("content", None)
        public.pop("execution_lease_token", None)
        return public

    async def _filing_bundle_drafts(self, case_id: str) -> List[Dict[str, Any]]:
        cursor = self.db.arbitration_drafts.find(
            {
                "case_id": case_id,
                "deleted_at": {"$exists": False},
                "status": {"$in": [ArbitrationDraftStatus.APPROVED.value, ArbitrationDraftStatus.EXPORTED.value]},
                "is_locked": True,
            }
        ).sort("updated_at", -1)
        drafts = await _collect(cursor)
        out: List[Dict[str, Any]] = []
        for draft in drafts:
            version_no = draft.get("approved_version")
            approved = await self.draft_repo.get_version(str(draft.get("_id")), int(version_no or 0)) if version_no else None
            if not approved or str(approved.get("_id")) != str(draft.get("approved_version_id")):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Filing bundle draft is missing its approved immutable version")
            from .service import immutable_version_hash

            version_hash = approved.get("version_hash") or immutable_version_hash(approved)
            if version_hash != draft.get("approved_version_hash"):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Filing bundle draft version hash has drifted")
            if approved.get("validation_status") != "passed" or approved.get("missing_evidence"):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Filing bundle contains a validation-defective approved version")
            case = await self.get_case(case_id)
            await self.require_readiness_approval(case, draft)
            out.append({**draft, "latest_version": approved, "approved_version": approved})
        return out

    def _filing_bundle_markdown(self, payload: Dict[str, Any]) -> str:
        case = payload.get("case") or {}
        readiness = payload.get("readiness") or {}
        audit = payload.get("citation_audit") or {}
        lines = [
            f"# Filing Bundle - {case.get('title') or case.get('_id') or 'Arbitration Case'}",
            "",
            f"Generated: {payload.get('generated_at')}",
            f"Bundle version: {payload.get('bundle_version')}",
            "",
            "## Case",
            "",
            f"- Case ID: {case.get('_id')}",
            f"- Reference: {case.get('case_reference') or 'null'}",
            f"- Project ID: {case.get('project_id')}",
            f"- Contract ID: {case.get('contract_id') or 'null'}",
            f"- Party perspective: {case.get('party_perspective') or 'null'}",
            f"- Status: {case.get('status') or 'null'}",
            "",
            "## Readiness",
            "",
            f"- Status: {readiness.get('status')}",
            f"- Score: {readiness.get('readiness_score')}%",
            f"- Blockers: {len(readiness.get('blockers') or [])}",
            "",
            "## Citation Audit",
            "",
            f"- OK: {audit.get('ok')}",
            f"- Exhibit count: {audit.get('exhibit_count')}",
            f"- Blocking issues: {audit.get('blocking_issue_count') or 0}",
            f"- Warning issues: {audit.get('warning_issue_count') or 0}",
            "",
            "## Exhibit List",
            "",
        ]
        exhibits = payload.get("exhibit_list") or []
        if exhibits:
            for row in exhibits:
                lines.append(f"- {row.get('exhibit_id')}: {row.get('title') or row.get('document_type') or row.get('source_id')} ({row.get('source_id') or 'no source id'})")
        else:
            lines.append("- No exhibits assigned.")
        lines.extend(["", "## Matrix Counts", ""])
        for slug, count in sorted((payload.get("matrix_counts") or {}).items()):
            approved = (payload.get("approved_counts") or {}).get(slug, 0)
            lines.append(f"- {slug}: {approved}/{count} approved")
        lines.extend(["", "## Matrices", ""])
        for slug, rows in (payload.get("matrices") or {}).items():
            lines.extend([f"### {slug}", ""])
            if not rows:
                lines.append("- No rows.")
                lines.append("")
                continue
            for row in rows[:50]:
                label = (
                    row.get("title")
                    or row.get("claim_head")
                    or row.get("issue")
                    or row.get("topic")
                    or row.get("event")
                    or row.get("calculation_id")
                    or row.get("notice_ref")
                    or row.get("_id")
                )
                status_text = row.get("review_status") or row.get("approval_status") or row.get("readiness_status") or "unmarked"
                lines.append(f"- {label} [{status_text}]")
            lines.append("")
        lines.extend(["## Drafts", ""])
        drafts = payload.get("drafts") or []
        if drafts:
            for draft in drafts:
                latest = draft.get("latest_version") or {}
                lines.append(
                    f"- {draft.get('title') or draft.get('_id')} ({draft.get('draft_type') or 'draft'}), "
                    f"version {latest.get('version') or 'none'}, status {draft.get('status') or 'unknown'}"
                )
        else:
            lines.append("- No drafts linked to this case.")
        lines.extend(["", "## Audit Issues", ""])
        issues = audit.get("issues") or []
        if issues:
            for issue in issues:
                lines.append(f"- {issue.get('severity') or 'issue'}: {issue.get('message')}")
        else:
            lines.append("- No citation audit issues.")
        return "\n".join(lines).strip() + "\n"

    async def _assign_exhibit_if_needed(self, case_id: str, matrix_slug: str, row: Dict[str, Any]) -> Dict[str, Any]:
        if matrix_slug != "document-index":
            return row
        prefix = str(row.get("exhibit_prefix") or "C").strip().upper()
        if row.get("exhibit_id") and row.get("exhibit_number"):
            return row
        if not row.get("exhibit_number"):
            existing = await _collect(self.db.arbitration_document_index.find({"case_id": case_id, "exhibit_prefix": prefix}))
            used = [int(item.get("exhibit_number") or 0) for item in existing if str(item.get("exhibit_number") or "").isdigit()]
            row["exhibit_number"] = (max(used) if used else 0) + 1
        row["exhibit_prefix"] = prefix
        row["exhibit_id"] = f"{prefix}-{row['exhibit_number']}"
        return row

    async def _assign_and_update_exhibit(self, case_id: str, row: Dict[str, Any]) -> Dict[str, Any]:
        updated = await self._assign_exhibit_if_needed(case_id, "document-index", row)
        await self.db.arbitration_document_index.update_one(
            {"_id": updated["_id"], "case_id": case_id},
            {"$set": {"exhibit_prefix": updated.get("exhibit_prefix"), "exhibit_number": updated.get("exhibit_number"), "exhibit_id": updated.get("exhibit_id")}},
        )
        return updated

    @staticmethod
    def _review_roles(values: Any) -> List[str]:
        if values in (None, ""):
            return []
        if not isinstance(values, list):
            values = [values]
        out: List[str] = []
        for value in values:
            role = str(getattr(value, "value", value) or "").strip().lower()
            if role and role not in out:
                out.append(role)
        return out

    @staticmethod
    def _default_review_roles(matrix_slug: str) -> List[str]:
        defaults = {
            "document-index": ["contracts"],
            "chronology-matrix": ["delay"],
            "clause-matrix": ["legal", "contracts"],
            "issue-matrix": ["legal"],
            "claim-matrix": ["legal", "quantum"],
            "defence-matrix": ["legal"],
            "counterclaim-matrix": ["legal", "quantum"],
            "rejoinder-matrix": ["legal"],
            "quantum-annexures": ["quantum"],
            "notice-compliance": ["legal", "contracts"],
            "jurisdiction-matrix": ["legal"],
            "expert-alignment": ["reviewer"],
        }
        return list(defaults.get(matrix_slug, ["reviewer"]))

    @staticmethod
    def _mark_assignments(assignments: List[Dict[str, Any]], role: str, status_value: str, at: datetime) -> List[Dict[str, Any]]:
        updated: List[Dict[str, Any]] = []
        for assignment in assignments:
            item = dict(assignment)
            if str(item.get("reviewer_role") or "").lower() == role and item.get("status") in {None, "assigned", "in_review"}:
                item["status"] = status_value
                item["completed_at"] = at
            updated.append(item)
        return updated

    @staticmethod
    def _review_blocks_readiness(row: Dict[str, Any]) -> bool:
        review_status = str(row.get("review_status") or "").lower()
        if review_status in {"assigned", "under_review", "partially_approved", "changes_requested", "rejected"}:
            return True
        required = {str(role).lower() for role in row.get("review_required_roles") or [] if role}
        completed = {str(role).lower() for role in row.get("review_completed_roles") or [] if role}
        return bool(required and not required.issubset(completed))

    def _compute_readiness_checks(
        self,
        case: Dict[str, Any],
        rows: Dict[str, List[Dict[str, Any]]],
        *,
        draft_id: Optional[str] = None,
        draft_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        checks: List[Dict[str, Any]] = []

        def add(check_key: str, check_group: str, check_status: ReadinessCheckStatus, message: str, row_id: Optional[str] = None) -> None:
            checks.append(
                ArbitrationReadinessCheck(
                    case_id=str(case.get("_id")),
                    draft_id=draft_id,
                    check_key=check_key,
                    check_group=check_group,
                    status=check_status,
                    message=message,
                    linked_matrix_row_id=row_id,
                    updated_at=datetime.utcnow(),
                ).model_dump(by_alias=True)
            )

        document_rows = rows.get("document-index") or []
        usable_documents = [
            row
            for row in document_rows
            if row.get("source_id") and row.get("exhibit_id") and _is_ready_row(row)
        ]
        add(
            "document_index_verified",
            "documents",
            ReadinessCheckStatus.READY if usable_documents else ReadinessCheckStatus.NEEDS_EVIDENCE,
            "At least one verified and approved exhibit-backed document is available." if usable_documents else "Add at least one verified document index row with source link, exhibit id, and approval.",
        )
        missing_exhibits = [row for row in document_rows if not row.get("exhibit_id")]
        if missing_exhibits:
            add(
                "document_index_exhibit_numbers",
                "documents",
                ReadinessCheckStatus.NEEDS_USER_CONFIRMATION,
                f"{len(missing_exhibits)} document index row(s) need exhibit numbering before filing.",
                str(missing_exhibits[0].get("_id")) if missing_exhibits else None,
            )

        clause_rows = rows.get("clause-matrix") or []
        usable_clauses = [
            row for row in clause_rows if (row.get("clause_number") or row.get("clause_text_excerpt")) and _is_ready_row(row)
        ]
        add(
            "clause_support",
            "clauses",
            ReadinessCheckStatus.READY if usable_clauses else ReadinessCheckStatus.NEEDS_CLAUSE_SUPPORT,
            "Approved clause matrix support is available." if usable_clauses else "Approve at least one clause matrix row with clause number or clause excerpt.",
        )

        issue_rows = rows.get("issue-matrix") or []
        usable_issues = [row for row in issue_rows if row.get("issue") and _is_ready_row(row, approval_key="status")]
        add(
            "issue_framing",
            "issues",
            ReadinessCheckStatus.READY if usable_issues else ReadinessCheckStatus.BLOCKED,
            "Issue matrix contains approved tribunal-facing issues." if usable_issues else "Create and approve issue matrix rows before drafting.",
        )

        pending_review_rows = [
            row
            for matrix_rows in rows.values()
            for row in matrix_rows
            if self._review_blocks_readiness(row)
        ]
        if pending_review_rows:
            first = pending_review_rows[0]
            add(
                "matrix_human_review",
                "human_review",
                ReadinessCheckStatus.NEEDS_LEGAL_REVIEW,
                f"{len(pending_review_rows)} matrix row(s) are pending, rejected, or incomplete in human review.",
                str(first.get("_id")) if first else None,
            )

        claim_rows = rows.get("claim-matrix") or []
        defence_rows = rows.get("defence-matrix") or []
        counterclaim_rows = rows.get("counterclaim-matrix") or []
        rejoinder_rows = rows.get("rejoinder-matrix") or []
        normalized_type = str(draft_type or "").lower()
        if not normalized_type:
            perspective = str(case.get("party_perspective") or "").lower()
            normalized_type = "statement_of_defence" if perspective == "respondent" else "statement_of_claim"
        if normalized_type == "statement_of_claim":
            self._claim_readiness(add, claim_rows)
        elif normalized_type == "statement_of_defence":
            self._defence_readiness(add, defence_rows)
        elif normalized_type == "counterclaim":
            self._counterclaim_readiness(add, counterclaim_rows)
        elif normalized_type == "rejoinder":
            self._rejoinder_readiness(add, rejoinder_rows)

        quantum_rows = rows.get("quantum-annexures") or []
        amount_claim_rows = [row for row in claim_rows + counterclaim_rows if row.get("amount_or_days") or row.get("amount")]
        if amount_claim_rows:
            ready_quantum = [row for row in quantum_rows if row.get("amount") and _is_ready_row(row)]
            add(
                "quantum_support",
                "quantum",
                ReadinessCheckStatus.READY if ready_quantum else ReadinessCheckStatus.NEEDS_QUANTUM_SUPPORT,
                "Approved quantum annexure is available for monetary/time claims." if ready_quantum else "Claims with amount/days require an approved quantum annexure.",
                str(amount_claim_rows[0].get("_id")) if amount_claim_rows else None,
            )

        notice_rows = rows.get("notice-compliance") or []
        risky_notice_rows = [row for row in notice_rows if str(row.get("compliance_status") or "").lower() in {"non_compliant", "late", "missing"}]
        if risky_notice_rows:
            add(
                "notice_compliance_risk",
                "procedure",
                ReadinessCheckStatus.NEEDS_LEGAL_REVIEW,
                "Notice compliance table contains missing, late, or non-compliant entries requiring legal review.",
                str(risky_notice_rows[0].get("_id")),
            )

        self._jurisdiction_readiness(add, rows.get("jurisdiction-matrix") or [])
        self._expert_readiness(
            add,
            claim_rows + counterclaim_rows,
            rows.get("expert-alignment") or [],
        )

        return checks

    def _expert_readiness(
        self,
        add: Any,
        claim_rows: List[Dict[str, Any]],
        expert_rows: List[Dict[str, Any]],
    ) -> None:
        """Guide §12: claims relying on delay or quantum need an aligned expert record."""

        def _claim_no(row: Dict[str, Any]) -> str:
            return str(row.get("claim_no") or row.get("counterclaim_no") or row.get("source_claim_id") or row.get("_id"))

        def _is_delay(row: Dict[str, Any]) -> bool:
            text = " ".join(str(row.get(key) or "") for key in ["claim_head", "facts", "causation", "relief", "breach"]).lower()
            return any(term in text for term in ["delay", "eot", "extension of time", "prolongation", "critical path"])

        def _has_amount(row: Dict[str, Any]) -> bool:
            return row.get("amount") is not None or bool(row.get("amount_or_days"))

        relevant = [row for row in claim_rows if _is_delay(row) or _has_amount(row)]
        if not relevant:
            return

        aligned: Dict[tuple[str, str], Dict[str, Any]] = {}
        for row in expert_rows:
            if not _is_ready_row(row):
                continue
            key = (str(row.get("expert_type") or ""), str(row.get("claim_no") or ""))
            aligned[key] = row

        unaligned: List[Dict[str, Any]] = []
        contradicted: List[Dict[str, Any]] = []
        for claim in relevant:
            claim_no = _claim_no(claim)
            if _is_delay(claim):
                delay_row = aligned.get(("delay", claim_no))
                if not delay_row or not _is_positive(delay_row.get("concurrency_addressed")):
                    unaligned.append(claim)
                    continue
            if _has_amount(claim):
                quantum_row = aligned.get(("quantum", claim_no))
                if not quantum_row or not _is_positive(quantum_row.get("calculation_match")):
                    unaligned.append(claim)
                    continue
                if quantum_row.get("contradictions"):
                    contradicted.append(claim)
        if unaligned:
            add(
                "expert_alignment",
                "experts",
                ReadinessCheckStatus.NEEDS_LEGAL_REVIEW,
                f"{len(unaligned)} claim(s) relying on delay or quantum have no approved, aligned expert record "
                "(concurrency addressed / calculation match required).",
                str(unaligned[0].get("_id")),
            )
        elif contradicted:
            add(
                "expert_alignment",
                "experts",
                ReadinessCheckStatus.NEEDS_LEGAL_REVIEW,
                f"{len(contradicted)} claim(s) have expert alignment contradictions to resolve.",
                str(contradicted[0].get("_id")),
            )
        else:
            add(
                "expert_alignment",
                "experts",
                ReadinessCheckStatus.READY,
                "Delay and quantum claims are aligned with approved expert records.",
            )

    def _jurisdiction_readiness(self, add: Any, jurisdiction_rows: List[Dict[str, Any]]) -> None:
        limitation_rows = [row for row in jurisdiction_rows if str(row.get("check_type") or "") == "limitation"]
        if not limitation_rows:
            add(
                "limitation_analysis",
                "jurisdiction",
                ReadinessCheckStatus.NEEDS_LEGAL_REVIEW,
                "Limitation analysis has not been prepared. Run the jurisdiction agent or add limitation rows to the jurisdiction matrix.",
            )
        else:
            time_barred = [row for row in limitation_rows if str(row.get("limitation_status") or "").lower() == "time_barred"]
            unresolved = [
                row
                for row in limitation_rows
                if str(row.get("limitation_status") or "").lower() != "within_limitation" or not _is_ready_row(row)
            ]
            if time_barred:
                add(
                    "limitation_analysis",
                    "jurisdiction",
                    ReadinessCheckStatus.BLOCKED,
                    f"{len(time_barred)} limitation row(s) appear time-barred; the claim cannot proceed to drafting without legal resolution.",
                    str(time_barred[0].get("_id")),
                )
            elif unresolved:
                add(
                    "limitation_analysis",
                    "jurisdiction",
                    ReadinessCheckStatus.NEEDS_LEGAL_REVIEW,
                    f"{len(unresolved)} limitation row(s) need legal confirmation (at-risk, missing dates, or pending approval).",
                    str(unresolved[0].get("_id")),
                )
            else:
                add(
                    "limitation_analysis",
                    "jurisdiction",
                    ReadinessCheckStatus.READY,
                    "Limitation analysis confirms claims are within limitation.",
                )

        prearb_rows = [row for row in jurisdiction_rows if str(row.get("check_type") or "") == "pre_arbitration_step"]
        incomplete_statuses = {"pending", "not_started", "incomplete", "in_progress"}
        resolved_statuses = {"complete", "completed", "not_applicable", "waived", "not_required"}
        if not prearb_rows:
            add(
                "pre_arbitration_compliance",
                "jurisdiction",
                ReadinessCheckStatus.NEEDS_LEGAL_REVIEW,
                "Pre-arbitration compliance steps have not been recorded. Run the jurisdiction agent or add pre-arbitration rows to the jurisdiction matrix.",
            )
        else:
            incomplete_required = [
                row
                for row in prearb_rows
                if _is_positive(row.get("required")) and str(row.get("compliance_status") or "").lower() in incomplete_statuses
            ]
            unresolved = [
                row
                for row in prearb_rows
                if str(row.get("compliance_status") or "").lower() not in resolved_statuses or not _is_ready_row(row)
            ]
            if incomplete_required:
                add(
                    "pre_arbitration_compliance",
                    "jurisdiction",
                    ReadinessCheckStatus.BLOCKED,
                    f"Arbitration appears premature: {len(incomplete_required)} required pre-arbitration step(s) are incomplete.",
                    str(incomplete_required[0].get("_id")),
                )
            elif unresolved:
                add(
                    "pre_arbitration_compliance",
                    "jurisdiction",
                    ReadinessCheckStatus.NEEDS_LEGAL_REVIEW,
                    f"{len(unresolved)} pre-arbitration step(s) need confirmation or approval.",
                    str(unresolved[0].get("_id")),
                )
            else:
                add(
                    "pre_arbitration_compliance",
                    "jurisdiction",
                    ReadinessCheckStatus.READY,
                    "Pre-arbitration procedural steps are complete or confirmed not applicable.",
                )

        scope_rows = [row for row in jurisdiction_rows if str(row.get("check_type") or "") == "arbitration_clause_scope"]
        out_of_scope = [row for row in scope_rows if str(row.get("scope_status") or "").lower() == "out_of_scope"]
        if out_of_scope:
            add(
                "arbitration_clause_scope",
                "jurisdiction",
                ReadinessCheckStatus.BLOCKED,
                f"{len(out_of_scope)} claim(s) are marked outside the arbitration clause; the tribunal may lack jurisdiction.",
                str(out_of_scope[0].get("_id")),
            )

        # Guide §2 pleading timetable: overdue, unfiled stages need legal attention.
        overdue_timetable = [
            row
            for row in jurisdiction_rows
            if str(row.get("check_type") or "") == "pleading_timetable"
            and str(row.get("timetable_status") or "").lower() == "overdue"
        ]
        if overdue_timetable:
            add(
                "pleading_timetable",
                "jurisdiction",
                ReadinessCheckStatus.NEEDS_LEGAL_REVIEW,
                f"{len(overdue_timetable)} pleading-timetable stage(s) are past their deadline and not marked filed.",
                str(overdue_timetable[0].get("_id")),
            )

    def _claim_readiness(self, add: Any, claim_rows: List[Dict[str, Any]]) -> None:
        ready_claims = [row for row in claim_rows if (row.get("claim_head") or row.get("facts")) and _is_ready_row(row)]
        add(
            "claim_matrix",
            "claims",
            ReadinessCheckStatus.READY if ready_claims else ReadinessCheckStatus.BLOCKED,
            "Claim matrix has approved entitlement, facts, causation, and relief rows." if ready_claims else "Statement of Claim requires approved claim matrix rows.",
        )

    def _defence_readiness(self, add: Any, defence_rows: List[Dict[str, Any]]) -> None:
        ready_defences = [row for row in defence_rows if (row.get("admission_denial") or row.get("defence")) and _is_ready_row(row)]
        add(
            "defence_matrix",
            "defence",
            ReadinessCheckStatus.READY if ready_defences else ReadinessCheckStatus.BLOCKED,
            "Defence matrix has approved para-wise admissions/denials and positive case rows." if ready_defences else "Statement of Defence requires approved defence matrix rows.",
        )

    def _counterclaim_readiness(self, add: Any, counterclaim_rows: List[Dict[str, Any]]) -> None:
        ready_counterclaims = [row for row in counterclaim_rows if (row.get("breach") or row.get("facts")) and _is_ready_row(row)]
        add(
            "counterclaim_matrix",
            "counterclaim",
            ReadinessCheckStatus.READY if ready_counterclaims else ReadinessCheckStatus.BLOCKED,
            "Counterclaim matrix has approved jurisdiction, limitation, breach, causation, and relief rows." if ready_counterclaims else "Counterclaim requires approved counterclaim matrix rows.",
        )

    def _rejoinder_readiness(self, add: Any, rejoinder_rows: List[Dict[str, Any]]) -> None:
        ready_replies = [row for row in rejoinder_rows if (row.get("claimant_reply") or row.get("reply_to_counterclaim")) and _is_ready_row(row)]
        add(
            "rejoinder_matrix",
            "rejoinder",
            ReadinessCheckStatus.READY if ready_replies else ReadinessCheckStatus.BLOCKED,
            "Rejoinder matrix has approved paragraph-wise replies." if ready_replies else "Rejoinder requires approved reply matrix rows.",
        )
        new_matter = [
            row
            for row in rejoinder_rows
            if _is_positive(row.get("new_matter"))
            and _is_positive(row.get("permission_required"))
            and not _is_positive(row.get("permission_obtained"))
        ]
        if new_matter:
            add(
                "rejoinder_new_matter",
                "rejoinder",
                ReadinessCheckStatus.NEEDS_LEGAL_REVIEW,
                "Rejoinder row introduces new matter but required permission has not been obtained.",
                str(new_matter[0].get("_id")),
            )

    async def _persist_readiness(
        self,
        case_id: str,
        draft_id: Optional[str],
        checks: List[Dict[str, Any]],
        score: int,
        blockers: List[Dict[str, Any]],
    ) -> None:
        query: Dict[str, Any] = {"case_id": case_id}
        if draft_id:
            query["draft_id"] = draft_id
        else:
            query["draft_id"] = None
        await self.db.arbitration_readiness_checks.delete_many(query)
        if checks:
            for check in checks:
                check["draft_id"] = draft_id
            await self.db.arbitration_readiness_checks.insert_many(_jsonable(checks))
        await self.db.arbitration_cases.update_one(
            {"_id": case_id},
            {
                "$set": {
                    "readiness_score": score,
                    "readiness_blockers": _jsonable(blockers),
                    "updated_at": datetime.utcnow(),
                }
            },
        )

    def _references_from_case_rows(
        self,
        draft_id: str,
        document_rows: List[Dict[str, Any]],
        clause_rows: List[Dict[str, Any]],
        current_user: Any,
    ) -> List[Dict[str, Any]]:
        references: List[Dict[str, Any]] = []
        for row in document_rows:
            references.append(
                ArbitrationSelectedReference(
                    draft_id=draft_id,
                    source_type=row.get("source_type") or "document",
                    source_id=str(row.get("source_id")),
                    label=row.get("title") or row.get("document_type") or row.get("exhibit_id") or "Case document",
                    citation=row.get("exhibit_id") or row.get("letter_no") or row.get("title"),
                    snippet=row.get("relevance_note") or row.get("summary") or row.get("document_type"),
                    page_numbers=row.get("page_numbers") or [],
                    letter_no=row.get("letter_no"),
                    event_date=row.get("document_date") if isinstance(row.get("document_date"), datetime) else None,
                    allowed_use=row.get("allowed_use") or "fact",
                    selected_by=_actor_id(current_user),
                    metadata={
                        "verification_status": row.get("verification_status"),
                        "exhibit_id": row.get("exhibit_id"),
                        "matrix_row_id": row.get("_id"),
                    },
                ).model_dump(by_alias=True)
            )
        for row in clause_rows:
            if not row.get("clause_number") and not row.get("clause_text_excerpt"):
                continue
            references.append(
                ArbitrationSelectedReference(
                    draft_id=draft_id,
                    source_type="clause",
                    source_id=str(row.get("clause_source_id") or row.get("_id")),
                    label=row.get("topic") or row.get("clause_number") or "Clause matrix row",
                    citation=row.get("clause_number") or row.get("topic"),
                    snippet=row.get("clause_text_excerpt") or row.get("obligation_or_right"),
                    clause_number=row.get("clause_number"),
                    allowed_use="clause",
                    selected_by=_actor_id(current_user),
                    metadata={"matrix_row_id": row.get("_id"), "risk": row.get("risk")},
                ).model_dump(by_alias=True)
            )
        return references

    def _claim_heads_from_case_rows(self, draft_id: str, claim_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        heads: List[Dict[str, Any]] = []
        for row in claim_rows:
            description = row.get("claim_head") or row.get("facts") or row.get("relief")
            if not description:
                continue
            amount = row.get("amount") if row.get("amount") is not None else None
            heads.append(
                ArbitrationClaimHead(
                    draft_id=draft_id,
                    description=str(description),
                    amount=amount,
                    currency=row.get("currency"),
                    calculation_basis=row.get("causation") or row.get("calculation_id"),
                    supporting_source_ids=[str(item) for item in row.get("evidence_ids") or []],
                    status="supported" if _is_ready_row(row) else "evidence_required",
                ).model_dump(by_alias=True)
            )
        return heads

    async def _touch_case(self, case_id: str, current_user: Any) -> None:
        await self.invalidate_readiness_approvals(case_id, "matrix_dependency_changed", current_user)
        await self.db.arbitration_cases.update_one(
            {"_id": case_id},
            {"$set": {"updated_by": _actor_id(current_user), "updated_at": datetime.utcnow()}},
        )


__all__ = ["ArbitrationCaseWorkspaceService", "MATRIX_COLLECTIONS"]
