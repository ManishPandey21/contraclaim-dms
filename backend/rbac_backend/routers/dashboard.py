"""
Dashboard statistics endpoint.

Aggregates document data from MongoDB, scoped by the caller's RBAC
permissions so that each user only sees the data they are authorised
to access.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..core.database import get_db
from ..core.security import (
    CurrentUser,
    build_scope_query,
    get_current_user,
    require_permission,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class StatusCount(BaseModel):
    status: str
    count: int
    color: str


class RecentActivity(BaseModel):
    id: str
    letter: str
    previousStatus: str
    newStatus: str
    timestamp: str
    user: str


class RecentDocument(BaseModel):
    id: str
    documentNumber: str = ""
    filename: str = ""
    subject: str = ""
    direction: str = ""
    letterDate: str = ""
    uploadedAt: str = ""


class ActionableLetter(BaseModel):
    id: str
    title: str
    organization: str
    project: str
    status: str
    updatedAt: str
    updatedBy: str
    assignedTo: str


class OrganizationStat(BaseModel):
    id: str
    name: str
    totalLetters: int
    replyOverdue: int
    underReview: int


class ProjectStat(BaseModel):
    id: str
    name: str
    organization: str
    organizationId: str = ""
    totalLetters: int
    inputRequired: int
    closed: int


class DashboardStatsResponse(BaseModel):
    # Overview
    totalLetters: int = 0
    totalDocuments: int = 0
    inputRequiredCount: int = 0
    incomingCount: int = 0
    replyOverdueCount: int = 0
    outgoingCount: int = 0
    underReviewCount: int = 0
    draftInProcessCount: int = 0
    letterStatuses: List[StatusCount] = Field(default_factory=list)
    documentStatuses: List[StatusCount] = Field(default_factory=list)
    recentActivity: List[RecentActivity] = Field(default_factory=list)
    recentDocuments: List[RecentDocument] = Field(default_factory=list)
    actionableLetters: List[ActionableLetter] = Field(default_factory=list)
    # Organizations tab
    organizations: List[OrganizationStat] = Field(default_factory=list)
    # Projects tab
    projects: List[ProjectStat] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Colour map for document statuses (matches the UI design)
# ---------------------------------------------------------------------------

STATUS_COLORS: Dict[str, str] = {
    "Received": "#3B82F6",
    "Input Required": "#F59E0B",
    "Under Review": "#8B5CF6",
    "Under Process": "#10B981",
    "On Hold": "#6B7280",
    "Replied": "#14B8A6",
    "Forwarded": "#EC4899",
    "Completed": "#22C55E",
    "Closed": "#64748B",
    "Reply Received": "#0EA5E9",
    "No Reply Received": "#EF4444",
    "Reply Overdue": "#DC2626",
    "Draft": "#9CA3AF",
    "Strategy": "#7C3AED",
    "Review": "#F97316",
    "Approval": "#0D9488",
}

# Canonical ordering for the bar chart
STATUS_ORDER = [
    "Draft",
    "Review",
    "Approval",
    "Strategy",
    "Received",
    "Input Required",
    "Under Review",
    "Under Process",
    "On Hold",
    "Replied",
    "Forwarded",
    "Completed",
    "Closed",
    "Reply Received",
    "No Reply Received",
    "Reply Overdue",
]


def _safe_str(value: Any) -> str:
    """Convert a value to string, handling None gracefully."""
    if value is None:
        return ""
    return str(value)


def _safe_datetime_iso(value: Any) -> str:
    """Convert a datetime-like value to ISO string."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_timestamp(value: Any) -> datetime:
    """Best-effort parser for stored date/datetime fields."""
    if isinstance(value, datetime):
        return value

    raw = _safe_str(value).strip()
    if not raw:
        return datetime.min

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        pass

    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            continue

    return datetime.min


def _normalise_direction(value: Any) -> str:
    direction = _safe_str(value).strip().lower()
    if direction == "incoming":
        return "Incoming"
    if direction == "outgoing":
        return "Outgoing"
    return _safe_str(value).strip()


def _expand_id_values(values: List[str]) -> List[Any]:
    expanded: List[Any] = []
    for value in values:
        raw = _safe_str(value).strip()
        if not raw:
            continue
        if raw not in expanded:
            expanded.append(raw)
        try:
            oid = ObjectId(raw)
        except Exception:
            continue
        if oid not in expanded:
            expanded.append(oid)
    return expanded


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/stats",
    response_model=DashboardStatsResponse,
    summary="Aggregated dashboard statistics",
    dependencies=[Depends(require_permission("dms.dashboard.view"))],
)
async def get_dashboard_stats(
    search: Optional[str] = Query(
        None,
        description="Search documents by number, filename, or subject",
    ),
    status_filter: Optional[str] = Query(
        None,
        alias="status",
        description="Filter by document status",
    ),
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Return aggregated dashboard statistics scoped to the current user's
    RBAC permissions.

    - **superadmin**: sees all data across all organisations / projects
    - **orgadmin / orguser**: sees data within their organisation
    - **projectadmin / projectuser**: sees data within their assigned projects
    """

    try:
        # -----------------------------------------------------------------
        # 1. Build the RBAC-scoped base query for the documents collection
        # -----------------------------------------------------------------
        base_query = build_scope_query(current_user)

        # Optional search filter
        if search:
            # SECURITY (H6): escape user input before using it in a Mongo $regex.
            # Unescaped input let a user inject regex metacharacters (ReDoS / broad
            # matches). re.escape keeps this a literal substring search.
            safe_search = re.escape(search)
            base_query["$or"] = [
                {"filename": {"$regex": safe_search, "$options": "i"}},
                {"subject": {"$regex": safe_search, "$options": "i"}},
                {"letterNo": {"$regex": safe_search, "$options": "i"}},
            ]

        # Optional status filter
        if status_filter and status_filter != "all":
            base_query["status"] = status_filter

        # -----------------------------------------------------------------
        # 2. Fetch all matching documents (projection to limit payload)
        # -----------------------------------------------------------------
        projection = {
            "_id": 1,
            "filename": 1,
            "subject": 1,
            "status": 1,
            "uploadType": 1,
            "organization_id": 1,
            "project_id": 1,
            "createdBy": 1,
            "updatedAt": 1,
            "createdAt": 1,
            "letterNo": 1,
            "date": 1,
        }

        documents_cursor = db.documents.find(base_query, projection)
        documents: List[Dict[str, Any]] = [
            document async for document in documents_cursor
        ]

        # -----------------------------------------------------------------
        # 3. Compute overview statistics
        # -----------------------------------------------------------------
        total_documents = len(documents)

        # Direction counts for document-based summary cards
        incoming_count = 0
        outgoing_count = 0
        letter_count = 0
        for document in documents:
            upload_type = _safe_str(document.get("uploadType")).strip().lower()
            direction = _normalise_direction(
                document.get("uploadType") or document.get("direction")
            )
            if direction == "Incoming":
                incoming_count += 1
            elif direction == "Outgoing":
                outgoing_count += 1
            # H6: "letters" are correspondence documents (incoming/outgoing),
            # distinct from contract uploads (uploadType == "contract"). Previously
            # total_letters was hard-set equal to total_documents, which counted
            # contracts as letters and produced misleading business metrics.
            if upload_type != "contract":
                letter_count += 1

        total_letters = letter_count

        # Count by status
        status_counts: Dict[str, int] = {}
        for document in documents:
            s = _safe_str(document.get("status")).strip() or "Unknown"
            status_counts[s] = status_counts.get(s, 0) + 1

        input_required_count = status_counts.get("Input Required", 0)
        reply_overdue_count = status_counts.get("Reply Overdue", 0)
        under_review_count = status_counts.get("Under Review", 0)
        draft_in_process_count = status_counts.get("Draft", 0)

        # Build ordered status list for bar chart
        document_statuses: List[StatusCount] = []
        # First add statuses in canonical order
        for s in STATUS_ORDER:
            if s in status_counts:
                document_statuses.append(
                    StatusCount(
                        status=s,
                        count=status_counts[s],
                        color=STATUS_COLORS.get(s, "#6B7280"),
                    )
                )
        # Then add any remaining statuses not in the canonical order
        for s, count in status_counts.items():
            if s not in STATUS_ORDER:
                document_statuses.append(
                    StatusCount(
                        status=s,
                        count=count,
                        color=STATUS_COLORS.get(s, "#6B7280"),
                    )
                )

        # -----------------------------------------------------------------
        # 4. Recent uploads for the dashboard overview
        # -----------------------------------------------------------------
        recent_documents_raw = sorted(
            documents,
            key=lambda document: _parse_timestamp(
                document.get("createdAt")
                or document.get("uploadedAt")
                or document.get("updatedAt")
                or document.get("date")
            ),
            reverse=True,
        )

        recent_documents = [
            RecentDocument(
                id=_safe_str(document.get("_id")),
                documentNumber=_safe_str(document.get("letterNo")),
                filename=_safe_str(document.get("filename")),
                subject=_safe_str(document.get("subject"))
                or _safe_str(document.get("filename"))
                or "Untitled",
                direction=_normalise_direction(
                    document.get("uploadType") or document.get("direction")
                ),
                letterDate=_safe_datetime_iso(document.get("date")),
                uploadedAt=_safe_datetime_iso(
                    document.get("createdAt")
                    or document.get("uploadedAt")
                    or document.get("updatedAt")
                    or document.get("date")
                ),
            )
            for document in recent_documents_raw[:6]
        ]

        recent_activity = [
            RecentActivity(
                id=document.id,
                letter=document.documentNumber
                or document.subject
                or document.filename
                or "Untitled",
                previousStatus="",
                newStatus="Uploaded",
                timestamp=document.uploadedAt,
                user=_safe_str(
                    next(
                        (
                            raw.get("createdBy")
                            for raw in recent_documents_raw
                            if _safe_str(raw.get("_id")) == document.id
                        ),
                        "",
                    )
                )
                or "System",
            )
            for document in recent_documents
        ]

        # -----------------------------------------------------------------
        # 5. Actionable documents (for the existing lower dashboard section)
        # -----------------------------------------------------------------
        actionable_statuses = {"Input Required", "Reply Overdue", "Under Review"}
        actionable_letters_raw = [
            document
            for document in documents
            if document.get("status") in actionable_statuses
        ]
        # Sort by updatedAt descending
        actionable_letters_raw.sort(
            key=lambda x: _parse_timestamp(
                x.get("updatedAt") or x.get("createdAt") or x.get("date")
            ),
            reverse=True,
        )

        # Resolve org and project names
        org_ids = list(
            {
                _safe_str(document.get("organization_id"))
                for document in documents
                if document.get("organization_id")
            }
        )
        proj_ids = list(
            {
                _safe_str(document.get("project_id"))
                for document in documents
                if document.get("project_id")
            }
        )

        # Fetch org names
        org_name_map: Dict[str, str] = {}
        if org_ids:
            org_lookup_values = _expand_id_values(org_ids)
            org_cursor = db.organizations.find(
                {"_id": {"$in": org_lookup_values}},
                {"_id": 1, "name": 1},
            )
            async for org in org_cursor:
                org_name_map[_safe_str(org["_id"])] = org.get("name", "Unknown")

        # Fetch project names and their org ids
        proj_name_map: Dict[str, str] = {}
        proj_org_map: Dict[str, str] = {}
        if proj_ids:
            project_lookup_values = _expand_id_values(proj_ids)
            proj_cursor = db.projects.find(
                {"_id": {"$in": project_lookup_values}},
                {"_id": 1, "name": 1, "organization_id": 1},
            )
            async for proj in proj_cursor:
                pid = _safe_str(proj["_id"])
                proj_name_map[pid] = proj.get("name", "Unknown")
                proj_org_map[pid] = _safe_str(proj.get("organization_id", ""))

        actionable_letters = [
            ActionableLetter(
                id=_safe_str(document.get("_id")),
                title=document.get("subject") or document.get("filename") or "Untitled",
                organization=org_name_map.get(
                    _safe_str(document.get("organization_id")),
                    "Unknown",
                ),
                project=proj_name_map.get(
                    _safe_str(document.get("project_id")),
                    "Unknown",
                ),
                status=document.get("status", "Unknown"),
                updatedAt=_safe_datetime_iso(
                    document.get("updatedAt")
                    or document.get("createdAt")
                    or document.get("date")
                ),
                updatedBy=_safe_str(document.get("createdBy")),
                assignedTo="",
            )
            for document in actionable_letters_raw[:50]
        ]

        # -----------------------------------------------------------------
        # 6. Organization-level stats
        # -----------------------------------------------------------------
        org_stats_map: Dict[str, Dict[str, Any]] = {}
        for document in documents:
            oid = _safe_str(document.get("organization_id"))
            if not oid:
                continue
            if oid not in org_stats_map:
                org_stats_map[oid] = {
                    "id": oid,
                    "name": org_name_map.get(oid, "Unknown"),
                    "totalLetters": 0,
                    "replyOverdue": 0,
                    "underReview": 0,
                }
            org_stats_map[oid]["totalLetters"] += 1
            s = document.get("status", "")
            if s == "Reply Overdue":
                org_stats_map[oid]["replyOverdue"] += 1
            elif s == "Under Review":
                org_stats_map[oid]["underReview"] += 1

        organizations = [
            OrganizationStat(**v)
            for v in sorted(org_stats_map.values(), key=lambda x: x["totalLetters"], reverse=True)
        ]

        # -----------------------------------------------------------------
        # 7. Project-level stats
        # -----------------------------------------------------------------
        proj_stats_map: Dict[str, Dict[str, Any]] = {}
        for document in documents:
            pid = _safe_str(document.get("project_id"))
            if not pid:
                continue
            if pid not in proj_stats_map:
                oid = _safe_str(document.get("organization_id"))
                proj_stats_map[pid] = {
                    "id": pid,
                    "name": proj_name_map.get(pid, "Unknown"),
                    "organization": org_name_map.get(oid, org_name_map.get(proj_org_map.get(pid, ""), "Unknown")),
                    "organizationId": oid or proj_org_map.get(pid, ""),
                    "totalLetters": 0,
                    "inputRequired": 0,
                    "closed": 0,
                }
            proj_stats_map[pid]["totalLetters"] += 1
            s = document.get("status", "")
            if s == "Input Required":
                proj_stats_map[pid]["inputRequired"] += 1
            elif s == "Closed":
                proj_stats_map[pid]["closed"] += 1

        projects = [
            ProjectStat(**v)
            for v in sorted(proj_stats_map.values(), key=lambda x: x["totalLetters"], reverse=True)
        ]

        # -----------------------------------------------------------------
        # 8. Build response
        # -----------------------------------------------------------------
        return DashboardStatsResponse(
            totalLetters=total_letters,
            totalDocuments=total_documents,
            inputRequiredCount=input_required_count,
            incomingCount=incoming_count,
            replyOverdueCount=reply_overdue_count,
            outgoingCount=outgoing_count,
            underReviewCount=under_review_count,
            draftInProcessCount=draft_in_process_count,
            letterStatuses=document_statuses,
            documentStatuses=document_statuses,
            recentActivity=recent_activity,
            recentDocuments=recent_documents,
            actionableLetters=actionable_letters,
            organizations=organizations,
            projects=projects,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to compute dashboard stats: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to load dashboard statistics")
