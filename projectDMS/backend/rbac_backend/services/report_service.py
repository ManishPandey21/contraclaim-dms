import asyncio
import csv
import io
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase

from bson import ObjectId

from ..core.database import get_database
from ..core.security import CurrentUser
from ..models.report import (
    ReportCategory,
    ReportDefinition,
    ReportPreview,
    ReportRequest,
)

logger = logging.getLogger(__name__)


class ReportServiceError(Exception):
    """Raised when a report cannot be generated."""


class ReportService:
    """
    Generates analytical snapshots for letters, documents, and tasks. Each report exposes
    a preview (JSON rows) and an optional CSV download.
    """

    _LETTER_COLUMNS = [
        "id",
        "letter_no",
        "organization_name",
        "project_name",
        "date",
        "from",
        "to",
        "direction",
        "subject",
        "summary",
        "status",
        "created_at",
        "updated_at",
        "owner",
        "owner_name",
        "references",
    ]
    _DOCUMENT_COLUMNS = [
        "id",
        "letter_no",
        "name",
        "status",
        "created_at",
        "updated_at",
        "owner",
        "owner_name",
        "type",
        "organization_id",
        "organization_name",
        "project_id",
        "project_name",
    ]
    _TASK_COLUMNS = [
        "id",
        "title",
        "status",
        "priority",
        "assigned_to",
        "assigned_to_name",
        "organization_id",
        "organization_name",
        "project_id",
        "project_name",
        "created_at",
        "updated_at",
    ]
    _LINKED_CHAIN_COLUMNS = [
        "letter_no",
        "subject",
        "summary",
        "direction",
        "date",
        "organization_name",
        "project_name",
    ]
    _TAG_REPORT_COLUMNS = [
        "letter_no",
        "organization_name",
        "project_name",
        "date",
        "from",
        "to",
        "direction",
        "subject",
        "summary",
        "status",
        "tags",
        "sub_tags",
    ]

    _REPORT_DEFINITIONS: Dict[str, ReportDefinition] = {
        "letter-status": ReportDefinition(
            id="letter-status",
            name="Letter Status Summary",
            description="Distribution of letters by status with recent activity.",
            category=ReportCategory.LETTERS,
            default_columns=_LETTER_COLUMNS,
            metrics=["total_letters", "status_breakdown"],
        ),
        "document-activity": ReportDefinition(
            id="document-activity",
            name="Document Activity",
            description="Document uploads, status and ownership trends.",
            category=ReportCategory.DOCUMENTS,
            default_columns=_DOCUMENT_COLUMNS,
            metrics=["total_documents", "status_breakdown", "upload_breakdown"],
        ),
        "task-completion": ReportDefinition(
            id="task-completion",
            name="Task Completion",
            description="Task completion and assignment performance.",
            category=ReportCategory.TASKS,
            default_columns=_TASK_COLUMNS,
            metrics=["total_tasks", "status_breakdown", "completion_rate"],
        ),
        "linked-letter-chain": ReportDefinition(
            id="linked-letter-chain",
            name="Linked Letters Chain",
            description="Letter No, Subject, Summary for linked letters above/below a given letter.",
            category=ReportCategory.LETTERS,
            default_columns=_LINKED_CHAIN_COLUMNS,
            metrics=["total_linked"],
            download_formats=["csv"],
        ),
        "letter-received": ReportDefinition(
            id="letter-received",
            name="Letter Received Report",
            description="Letters received over a period with org/project and reference info.",
            category=ReportCategory.LETTERS,
            default_columns=[
                "organization_name",
                "project_name",
                "letter_no",
                "date",
                "from",
                "to",
                "direction",
                "subject",
                "summary",
                "status",
                "references",
            ],
            metrics=["total_letters"],
            download_formats=["csv"],
        ),
        "letter-by-tags": ReportDefinition(
            id="letter-by-tags",
            name="Letters by Tag/Subtag",
            description="Letters filtered by tag/subtag and date range.",
            category=ReportCategory.LETTERS,
            default_columns=_TAG_REPORT_COLUMNS,
            metrics=["total_letters"],
            download_formats=["csv"],
        ),
    }

    def __init__(self, db: Optional[AsyncIOMotorDatabase] = None):
        self.db = db

    async def _get_db(self) -> AsyncIOMotorDatabase:
        if self.db is None:
            self.db = await get_database()
        return self.db

    async def list_available_reports(self) -> List[ReportDefinition]:
        return list(self._REPORT_DEFINITIONS.values())

    def _get_definition(self, report_id: str) -> ReportDefinition:
        definition = self._REPORT_DEFINITIONS.get(report_id)
        if not definition:
            raise ReportServiceError(f"Unknown report '{report_id}'")
        return definition

    async def generate_preview(
        self, request: ReportRequest, current_user: CurrentUser
    ) -> ReportPreview:
        definition = self._get_definition(request.report_id)
        if definition.id == "letter-status":
            return await self._generate_letter_status_report(request, definition, current_user)
        if definition.id == "document-activity":
            return await self._generate_document_activity_report(
                request, definition, current_user
            )
        if definition.id == "task-completion":
            return await self._generate_task_completion_report(request, definition, current_user)
        if definition.id == "linked-letter-chain":
            return await self._generate_linked_chain_report(request, definition, current_user)
        if definition.id == "letter-received":
            return await self._generate_letter_received_report(request, definition, current_user)
        if definition.id == "letter-by-tags":
            return await self._generate_letter_by_tags_report(request, definition, current_user)
        raise ReportServiceError(f"No generator wired for report '{definition.id}'")

    async def generate_download(
        self, request: ReportRequest, current_user: CurrentUser
    ) -> Tuple[bytes, str]:
        preview = await self.generate_preview(request, current_user)
        filename = f"{preview.report_id}-{preview.generated_at.date():%Y%m%d}.csv"
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=preview.columns)
        writer.writeheader()
        for row in preview.rows:
            writer.writerow({column: row.get(column, "") for column in preview.columns})
        return buffer.getvalue().encode("utf-8"), filename

    async def _generate_letter_status_report(
        self,
        request: ReportRequest,
        definition: ReportDefinition,
        current_user: CurrentUser,
    ) -> ReportPreview:
        db = await self._get_db()
        start_date, end_date = self._sanitize_date_range(request.start_date, request.end_date)
        match_clauses: List[Dict[str, Any]] = []
        if start_date or end_date:
            date_range: Dict[str, Any] = {}
            if start_date:
                date_range["$gte"] = start_date
            if end_date:
                date_range["$lte"] = end_date
            match_clauses.append(
                {
                    "$or": [
                        {"date": date_range},
                        {"created_at": date_range},
                        {"createdAt": date_range},
                        {"updated_at": date_range},
                        {"updatedAt": date_range},
                    ]
                }
            )
        if request.organization_id:
            org_clause = self._build_id_match(
                ["organization_id", "organizationId"], request.organization_id
            )
            if org_clause:
                match_clauses.append(org_clause)
        if request.project_id:
            proj_clause = self._build_id_match(
                ["project_id", "projectId"], request.project_id
            )
            if proj_clause:
                match_clauses.append(proj_clause)
        scope_clause = self._scope_clause(current_user, "organization_id", "project_id")
        match = self._combine_clauses([self._combine_clauses(match_clauses), scope_clause])
        status_values = [s for s in (request.statuses or []) if s]
        if status_values:
            status_clause = (
                {"status": {"$in": status_values}}
                if len(status_values) > 1
                else {"status": status_values[0]}
            )
            match = self._combine_clauses([match, status_clause])
        pipeline = [
            {"$match": match or {}},
            {
                "$project": {
                    "_id": 1,
                    "letter_no": {"$ifNull": ["$letter_no", "$letterNo"]},
                    "subject": 1,
                    "summary": {"$ifNull": ["$summary", "$content_summary", ""]},
                    "status": 1,
                    "created_at": {"$ifNull": ["$created_at", "$createdAt"]},
                    "updated_at": {"$ifNull": ["$updated_at", "$updatedAt"]},
                    "date": {"$ifNull": ["$date", "$created_at", "$createdAt"]},
                    "from": {"$ifNull": ["$from", "$from_", ""]},
                    "to": {"$ifNull": ["$to", "$recipient", ""]},
                    "direction": {"$ifNull": ["$direction", "$uploadType", "$upload_type", ""]},
                    "organization_id": 1,
                    "project_id": 1,
                    "owner": {"$ifNull": ["$created_by", "$createdBy", "$owner", ""]},
                    "references": {"$ifNull": ["$references", "$reference", []]},
                }
            },
            {"$sort": {"updated_at": -1}},
            {"$limit": request.limit},
        ]
        rows = await db.documents.aggregate(pipeline).to_list(length=request.limit)
        prepared_rows = rows
        await self._attach_metadata(
            prepared_rows,
            owner_field="owner",
            owner_target_field="owner_name",
            organization_field="organization_id",
            organization_target_field="organization_name",
            project_field="project_id",
            project_target_field="project_name",
        )
        prepared_rows = self._sanitize_rows(prepared_rows)
        status_breakdown = await db.documents.aggregate(
            [
                {"$match": match or {}},
                {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]
        ).to_list(length=None)
        total_rows = await db.documents.count_documents(match or {})
        metrics = {
            "total_letters": total_rows,
            "status_breakdown": [
                {"status": item["_id"], "count": item["count"]} for item in status_breakdown
            ],
        }
        return ReportPreview(
            report_id=definition.id,
            report_name=definition.name,
            generated_at=datetime.utcnow(),
            columns=definition.default_columns,
            rows=prepared_rows,
            metrics=metrics,
            total_rows=total_rows,
        )

    async def _generate_letter_received_report(
        self,
        request: ReportRequest,
        definition: ReportDefinition,
        current_user: CurrentUser,
    ) -> ReportPreview:
        db = await self._get_db()
        start_date, end_date = self._sanitize_date_range(request.start_date, request.end_date)
        match_clauses: List[Dict[str, Any]] = []
        if start_date or end_date:
            date_range: Dict[str, Any] = {}
            if start_date:
                date_range["$gte"] = start_date
            if end_date:
                date_range["$lte"] = end_date
            match_clauses.append(
                {
                    "$or": [
                        {"date": date_range},
                        {"created_at": date_range},
                        {"createdAt": date_range},
                    ]
                }
            )
        if request.organization_id:
            org_clause = self._build_id_match(
                ["organization_id", "organizationId"], request.organization_id
            )
            if org_clause:
                match_clauses.append(org_clause)
        if request.project_id:
            proj_clause = self._build_id_match(
                ["project_id", "projectId"], request.project_id
            )
            if proj_clause:
                match_clauses.append(proj_clause)

        scope_clause = self._scope_clause(current_user, "organization_id", "project_id")
        match = self._combine_clauses([self._combine_clauses(match_clauses), scope_clause])
        if request.direction and request.direction.lower() in ("incoming", "outgoing"):
            direction_value = request.direction.lower()
            direction_clause = {
                "$or": [
                    {"direction": direction_value},
                    {"uploadType": direction_value},
                    {"upload_type": direction_value},
                ]
            }
            match = self._combine_clauses([match, direction_clause])

        pipeline = [
            {"$match": match or {}},
            {
                "$project": {
                    "_id": 1,
                    "letter_no": {"$ifNull": ["$letter_no", "$letterNo"]},
                    "subject": 1,
                    "summary": {"$ifNull": ["$summary", "$content_summary", ""]},
                    "status": 1,
                    "created_at": {"$ifNull": ["$created_at", "$createdAt"]},
                    "updated_at": {"$ifNull": ["$updated_at", "$updatedAt"]},
                    "date": {"$ifNull": ["$date", "$created_at", "$createdAt"]},
                    "from": {"$ifNull": ["$from", "$from_", ""]},
                    "to": {"$ifNull": ["$to", "$recipient", ""]},
                    "direction": {"$ifNull": ["$direction", "$uploadType", "$upload_type", ""]},
                    "organization_id": 1,
                    "project_id": 1,
                    "references": {"$ifNull": ["$references", []]},
                }
            },
            {"$sort": {"date": -1}},
            {"$limit": request.limit},
        ]

        rows = await db.documents.aggregate(pipeline).to_list(length=request.limit)
        prepared_rows = rows
        await self._attach_metadata(
            prepared_rows,
            owner_field="organization_id",  # dummy, not used
            owner_target_field="owner_name",
            organization_field="organization_id",
            organization_target_field="organization_name",
            project_field="project_id",
            project_target_field="project_name",
        )
        prepared_rows = self._sanitize_rows(prepared_rows)
        total_rows = await db.documents.count_documents(match or {})
        metrics = {"total_letters": total_rows}

        columns = definition.default_columns
        return ReportPreview(
            report_id=definition.id,
            report_name=definition.name,
            generated_at=datetime.utcnow(),
            columns=columns,
            rows=prepared_rows,
            metrics=metrics,
            total_rows=total_rows,
        )

    async def _generate_letter_by_tags_report(
        self,
        request: ReportRequest,
        definition: ReportDefinition,
        current_user: CurrentUser,
    ) -> ReportPreview:
        db = await self._get_db()
        start_date, end_date = self._sanitize_date_range(request.start_date, request.end_date)
        match_clauses: List[Dict[str, Any]] = []
        if start_date or end_date:
            date_range: Dict[str, Any] = {}
            if start_date:
                date_range["$gte"] = start_date
            if end_date:
                date_range["$lte"] = end_date
            match_clauses.append(
                {
                    "$or": [
                        {"date": date_range},
                        {"created_at": date_range},
                        {"createdAt": date_range},
                        {"updated_at": date_range},
                        {"updatedAt": date_range},
                    ]
                }
            )
        if request.organization_id:
            org_clause = self._build_id_match(
                ["organization_id", "organizationId"], request.organization_id
            )
            if org_clause:
                match_clauses.append(org_clause)
        if request.project_id:
            proj_clause = self._build_id_match(
                ["project_id", "projectId"], request.project_id
            )
            if proj_clause:
                match_clauses.append(proj_clause)
        scope_clause = self._scope_clause(current_user, "organization_id", "project_id")
        base_match = self._combine_clauses([self._combine_clauses(match_clauses), scope_clause])

        tag_values = [t for t in (request.tags or []) if t]
        subtag_values = [t for t in (request.sub_tags or []) if t]

        clauses: List[Optional[Dict[str, Any]]] = [base_match]
        if tag_values:
            clauses.append(
                {
                    "$or": [
                        {"tags": {"$in": tag_values}},
                        {"tag": {"$in": tag_values}},
                    ]
                }
            )
        if subtag_values:
            clauses.append(
                {
                    "$or": [
                        {"subTags": {"$in": subtag_values}},
                        {"sub_tags": {"$in": subtag_values}},
                        {"subTag": {"$in": subtag_values}},
                        {"sub_tag": {"$in": subtag_values}},
                    ]
                }
            )

        match = self._combine_clauses(clauses)

        pipeline = [
            {"$match": match or {}},
            {
                "$project": {
                    "_id": 1,
                    "letter_no": {"$ifNull": ["$letter_no", "$letterNo"]},
                    "subject": 1,
                    "summary": {"$ifNull": ["$summary", "$content_summary", ""]},
                    "status": 1,
                    "date": {"$ifNull": ["$date", "$created_at", "$createdAt"]},
                    "from": {"$ifNull": ["$from", "$from_", ""]},
                    "to": {"$ifNull": ["$to", "$recipient", ""]},
                    "direction": {"$ifNull": ["$direction", "$uploadType", "$upload_type", ""]},
                    "organization_id": 1,
                    "project_id": 1,
                    "tags": {"$ifNull": ["$tags", []]},
                    "sub_tags": {"$ifNull": ["$subTags", "$sub_tags", []]},
                }
            },
            {"$sort": {"date": -1, "updated_at": -1}},
            {"$limit": request.limit},
        ]

        rows = await db.documents.aggregate(pipeline).to_list(length=request.limit)
        prepared_rows = rows
        await self._attach_metadata(
            prepared_rows,
            owner_field="owner",  # placeholder, rows won't contain owner
            owner_target_field="owner_name",
            organization_field="organization_id",
            organization_target_field="organization_name",
            project_field="project_id",
            project_target_field="project_name",
        )
        prepared_rows = self._sanitize_rows(prepared_rows)
        total_rows = await db.documents.count_documents(match or {})
        metrics = {"total_letters": total_rows}

        columns = definition.default_columns
        return ReportPreview(
            report_id=definition.id,
            report_name=definition.name,
            generated_at=datetime.utcnow(),
            columns=columns,
            rows=prepared_rows,
            metrics=metrics,
            total_rows=total_rows,
        )

    async def _generate_linked_chain_report(
        self,
        request: ReportRequest,
        definition: ReportDefinition,
        current_user: CurrentUser,
    ) -> ReportPreview:
        if not request.letter_no:
            raise ReportServiceError("letter_no is required for linked-letter-chain report")

        direction = (request.chain_direction or "up").lower()
        if direction not in ("up", "down"):
            raise ReportServiceError("chain_direction must be 'up' or 'down'")

        db = await self._get_db()
        from ..services.falkor_graph_service import FalkorGraphService, normalize_letter_code

        norm = normalize_letter_code(request.letter_no)
        if not norm:
            raise ReportServiceError("Invalid letter number provided")

        svc = FalkorGraphService()
        if not svc.enabled:
            raise ReportServiceError("FalkorDB is disabled")

        # Build direction-specific traversal
        # Allow nodes missing org/project metadata to pass so we can enrich them from Mongo later.
        where_clauses = []
        params = {"norm": norm, "limit": request.limit}
        if request.organization_id:
            where_clauses.append(
                "(node IS NULL OR node.organization_id IS NULL OR node.organization_id = $orgId)"
            )
            params["orgId"] = request.organization_id
        if request.project_id:
            where_clauses.append(
                "(node IS NULL OR node.project_id IS NULL OR node.project_id = $projId OR node.project = $projId)"
            )
            params["projId"] = request.project_id
        where_filter = ""
        if where_clauses:
            where_filter = "WHERE " + " AND ".join(where_clauses)

        if direction == "down":
            traversal = f"""
            MATCH (root:Letter {{normCode:$norm}})
            OPTIONAL MATCH path = (root)-[:CITES|REPLIES_TO*1..5]->(node:Letter)
            {where_filter}
            RETURN DISTINCT node.normCode AS normCode, node.code AS code, node.subject AS subject,
                            toString(node.date) AS date, node.organization_id AS organization_id,
                            node.project_id AS project_id, node.direction AS direction
            LIMIT $limit
            """
        else:
            traversal = f"""
            MATCH (root:Letter {{normCode:$norm}})
            OPTIONAL MATCH path = (node:Letter)-[:CITES|REPLIES_TO*1..5]->(root)
            {where_filter}
            RETURN DISTINCT node.normCode AS normCode, node.code AS code, node.subject AS subject,
                            toString(node.date) AS date, node.organization_id AS organization_id,
                            node.project_id AS project_id, node.direction AS direction
            LIMIT $limit
            """

        rows: List[Dict[str, Any]] = []
        try:
            result = await asyncio.to_thread(svc._execute, traversal, params)
            parsed = svc._parse_rows(result)
            if request.include_self:
                # add root node too (basic data)
                rows.append(
                    {
                        "normCode": norm,
                        "code": request.letter_no,
                        "subject": "",
                        "date": "",
                        "organization_id": request.organization_id,
                        "project_id": request.project_id,
                        "direction": "self",
                    }
                )
            rows.extend(parsed)
        except Exception as exc:
            raise ReportServiceError(f"Graph traversal failed: {exc}")

        # Deduplicate and trim to limit
        deduped: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            code = str(row.get("code") or row.get("normCode") or "")
            if not code:
                continue
            deduped[code] = row
        limited_rows = list(deduped.values())[: request.limit]

        # Enrich with letter metadata from Mongo
        codes = [
            str(code)
            for code in (r.get("code") or r.get("normCode") for r in limited_rows)
            if code
        ]
        summaries: Dict[str, Dict[str, Any]] = {}
        if codes:
            normalized_codes = [
                normalize_letter_code(code) for code in codes if code
            ]
            match_filters: List[Dict[str, Any]] = [
                {"letter_no": {"$in": codes}},
                {"letterNo": {"$in": codes}},
                {"code": {"$in": codes}},
            ]
            if normalized_codes:
                match_filters.append({"letterNoNormalized": {"$in": normalized_codes}})
                match_filters.append({"normCode": {"$in": normalized_codes}})
            docs = await db.documents.find({"$or": match_filters}).to_list(length=None)
            letters = await db.letters.find({"$or": match_filters}).to_list(length=None)
            for doc in docs + letters:
                key = str(
                    doc.get("letter_no")
                    or doc.get("letterNo")
                    or doc.get("code")
                    or doc.get("normCode")
                    or ""
                )
                if not key:
                    continue
                row_summary = {
                    "summary": doc.get("summary") or doc.get("content_summary") or "",
                    "subject": doc.get("subject") or "",
                    "date": doc.get("date") or doc.get("created_at") or doc.get("createdAt") or "",
                    "organization_id": doc.get("organization_id") or doc.get("organizationId"),
                    "project_id": doc.get("project_id") or doc.get("projectId"),
                }
                if key not in summaries:
                    summaries[key] = row_summary
                normalized_key = normalize_letter_code(key)
                if normalized_key and normalized_key not in summaries:
                    summaries[normalized_key] = row_summary

        prepared_rows: List[Dict[str, Any]] = []
        for row in limited_rows:
            code = str(row.get("code") or row.get("normCode") or "")
            meta = summaries.get(code, {})
            prepared_rows.append(
                {
                    "letter_no": code,
                    "subject": meta.get("subject") or row.get("subject") or "",
                    "summary": meta.get("summary", ""),
                    "direction": row.get("direction") or direction,
                    "date": meta.get("date") or row.get("date") or "",
                    "organization_id": meta.get("organization_id") or row.get("organization_id") or "",
                    "project_id": meta.get("project_id") or row.get("project_id") or "",
                }
            )

        # Attach org/project names
        await self._attach_metadata(
            prepared_rows,
            owner_field="organization_id",  # not used, but keep required args
            owner_target_field="owner_name",
            organization_field="organization_id",
            organization_target_field="organization_name",
            project_field="project_id",
            project_target_field="project_name",
        )
        prepared_rows = self._sanitize_rows(prepared_rows)

        columns = [
            "letter_no",
            "subject",
            "summary",
            "direction",
            "date",
            "organization_name",
            "project_name",
        ]
        return ReportPreview(
            report_id=definition.id,
            report_name=definition.name,
            generated_at=datetime.utcnow(),
            columns=columns,
            rows=prepared_rows,
            metrics={"total_linked": len(prepared_rows)},
            total_rows=len(prepared_rows),
        )

    async def _generate_document_activity_report(
        self,
        request: ReportRequest,
        definition: ReportDefinition,
        current_user: CurrentUser,
    ) -> ReportPreview:
        db = await self._get_db()
        match = self._build_match(
            request,
            date_field="createdAt",
            organization_field="organization_id",
            project_field="project_id",
            current_user=current_user,
        )
        status_breakdown = await db.documents.aggregate(
            [
                {"$match": match or {}},
                {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]
        ).to_list(length=None)
        upload_breakdown = await db.documents.aggregate(
            [
                {"$match": match or {}},
                {"$group": {"_id": "$uploadType", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]
        ).to_list(length=None)
        total_rows = sum(item["count"] for item in status_breakdown)
        rows = (
            await db.documents.find(match or {})
            .sort("updatedAt", -1)
            .limit(request.limit)
            .to_list(length=request.limit)
        )
        prepared_rows = [
            self._map_row(doc, self._DOCUMENT_COLUMNS, self._document_field_map())
            for doc in rows
        ]
        await self._attach_metadata(
            prepared_rows,
            owner_field="owner",
            owner_target_field="owner_name",
            organization_field="organization_id",
            organization_target_field="organization_name",
            project_field="project_id",
            project_target_field="project_name",
        )
        prepared_rows = self._sanitize_rows(prepared_rows)
        metrics = {
            "total_documents": total_rows,
            "status_breakdown": [
                {"status": item["_id"], "count": item["count"]} for item in status_breakdown
            ],
            "upload_breakdown": [
                {"upload_type": item["_id"], "count": item["count"]} for item in upload_breakdown
            ],
        }
        return ReportPreview(
            report_id=definition.id,
            report_name=definition.name,
            generated_at=datetime.utcnow(),
            columns=definition.default_columns,
            rows=prepared_rows,
            metrics=metrics,
            total_rows=total_rows,
        )

    async def _generate_task_completion_report(
        self,
        request: ReportRequest,
        definition: ReportDefinition,
        current_user: CurrentUser,
    ) -> ReportPreview:
        db = await self._get_db()
        match = self._build_match(
            request,
            date_field="created_at",
            organization_field="organization_id",
            project_field="project_id",
            current_user=current_user,
        )
        status_breakdown = await db.tasks.aggregate(
            [
                {"$match": match or {}},
                {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]
        ).to_list(length=None)
        total_rows = sum(item["count"] for item in status_breakdown)
        rows = (
            await db.tasks.find(match or {})
            .sort("updated_at", -1)
            .limit(request.limit)
            .to_list(length=request.limit)
        )
        prepared_rows = [
            self._map_row(doc, self._TASK_COLUMNS, self._task_field_map())
            for doc in rows
        ]
        await self._attach_metadata(
            prepared_rows,
            owner_field="assigned_to",
            owner_target_field="assigned_to_name",
            organization_field="organization_id",
            organization_target_field="organization_name",
            project_field="project_id",
            project_target_field="project_name",
        )
        prepared_rows = self._sanitize_rows(prepared_rows)
        completed = next((item["count"] for item in status_breakdown if (item["_id"] or "").lower() in ("done", "completed", "complete")), 0)
        completion_rate = (completed / total_rows * 100) if total_rows else 0
        metrics = {
            "total_tasks": total_rows,
            "status_breakdown": [
                {"status": item["_id"], "count": item["count"]} for item in status_breakdown
            ],
            "completion_rate": round(completion_rate, 2),
        }
        return ReportPreview(
            report_id=definition.id,
            report_name=definition.name,
            generated_at=datetime.utcnow(),
            columns=definition.default_columns,
            rows=prepared_rows,
            metrics=metrics,
            total_rows=total_rows,
        )

    def _build_match(
        self,
        request: ReportRequest,
        *,
        date_field: str,
        organization_field: str,
        project_field: str,
        current_user: CurrentUser,
    ) -> Optional[Dict[str, Any]]:
        clauses: List[Dict[str, Any]] = []
        start_date, end_date = self._sanitize_date_range(request.start_date, request.end_date)
        if start_date or end_date:
            date_clause: Dict[str, Any] = {}
            if start_date:
                date_clause["$gte"] = start_date
            if end_date:
                date_clause["$lte"] = end_date
            clauses.append({date_field: date_clause})
        if request.organization_id:
            org_clause = self._build_id_match([organization_field], request.organization_id)
            if org_clause:
                clauses.append(org_clause)
        if request.project_id:
            proj_clause = self._build_id_match([project_field], request.project_id)
            if proj_clause:
                clauses.append(proj_clause)

        scope_clause = self._scope_clause(current_user, organization_field, project_field)
        combined = self._combine_clauses([self._combine_clauses(clauses), scope_clause])
        return combined

    @staticmethod
    def _combine_clauses(clauses: List[Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
        filtered = [clause for clause in clauses if clause]
        if not filtered:
            return None
        if len(filtered) == 1:
            return filtered[0]
        return {"$and": filtered}

    @staticmethod
    def _build_id_match(fields: List[str], value: Optional[str]) -> Optional[Dict[str, Any]]:
        if not value:
            return None
        raw = str(value)
        clauses: List[Dict[str, Any]] = []
        for field in fields:
            clauses.append({field: raw})
            try:
                if ObjectId.is_valid(raw):
                    clauses.append({field: ObjectId(raw)})
            except Exception:
                continue
        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$or": clauses}

    @staticmethod
    def _sanitize_date_range(
        start_date: Optional[datetime], end_date: Optional[datetime]
    ) -> Tuple[Optional[datetime], Optional[datetime]]:
        if start_date and end_date and end_date < start_date:
            return end_date, start_date
        return start_date, end_date

    @staticmethod
    def _scope_clause(
        current_user: CurrentUser, organization_field: str, project_field: str
    ) -> Optional[Dict[str, Any]]:
        roles = [role.lower() for role in (current_user.roles or [])]
        if "superadmin" in roles:
            return None
        org_ids = set()
        if getattr(current_user, "organization_id", None):
            org_ids.add(str(current_user.organization_id))
        for org in getattr(current_user, "organizations", []) or []:
            if org:
                org_ids.add(str(org))
        project_ids = {str(pid) for pid in (getattr(current_user, "projects", []) or []) if pid}
        scope_clauses: List[Dict[str, Any]] = []
        if org_ids:
            org_list = sorted(org_ids)
            clause: Dict[str, Any] = (
                {organization_field: org_list[0]}
                if len(org_list) == 1
                else {organization_field: {"$in": org_list}}
            )
            scope_clauses.append(clause)
        if project_ids:
            project_list = sorted(project_ids)
            clause = (
                {project_field: project_list[0]}
                if len(project_list) == 1
                else {project_field: {"$in": project_list}}
            )
            scope_clauses.append(clause)
        if not scope_clauses:
            return None
        if len(scope_clauses) == 1:
            return scope_clauses[0]
        return {"$or": scope_clauses}

    @staticmethod
    def _letter_field_map() -> Dict[str, Tuple[str, ...]]:
        return {
            "id": ("_id", "id"),
            "letter_no": ("letter_no", "letterNo"),
            "date": ("date", "created_at", "createdAt"),
            "from": ("from", "from_", "sender"),
            "to": ("to", "recipient"),
            "direction": ("direction", "uploadType", "upload_type"),
            "subject": ("subject",),
            "summary": ("summary", "content_summary"),
            "status": ("status",),
            "created_at": ("created_at", "createdAt"),
            "updated_at": ("updated_at", "updatedAt"),
            "owner": ("created_by", "createdBy", "owner"),
            "owner_name": ("owner_name",),
            "organization_id": ("organization_id",),
            "project_id": ("project_id",),
            "organization_name": ("organization_name",),
            "project_name": ("project_name",),
            "references": ("references",),
        }

    @staticmethod
    def _document_field_map() -> Dict[str, Tuple[str, ...]]:
        return {
            "id": ("_id", "id"),
            "letter_no": ("letterNo", "letter_no"),
            "name": ("subject", "filename"),
            "status": ("status",),
            "created_at": ("createdAt", "created_at", "date"),
            "updated_at": ("updatedAt", "updated_at"),
            "owner": ("createdBy", "uploadedBy", "created_by"),
            "type": ("uploadType", "filetype"),
            "organization_id": ("organization_id",),
            "project_id": ("project_id",),
            "owner_name": ("owner_name",),
            "organization_name": ("organization_name",),
            "project_name": ("project_name",),
        }

    @staticmethod
    def _task_field_map() -> Dict[str, Tuple[str, ...]]:
        return {
            "id": ("_id", "id"),
            "title": ("title",),
            "status": ("status",),
            "priority": ("priority",),
            "assigned_to": ("assigned_to",),
            "organization_id": ("organization_id",),
            "project_id": ("project_id",),
            "created_at": ("created_at",),
            "updated_at": ("updated_at",),
            "assigned_to_name": ("assigned_to_name",),
            "organization_name": ("organization_name",),
            "project_name": ("project_name",),
        }

    async def _attach_metadata(
        self,
        rows: List[Dict[str, Any]],
        *,
        owner_field: str,
        owner_target_field: str,
        organization_field: str,
        organization_target_field: str,
        project_field: str,
        project_target_field: str,
    ) -> None:
        if not rows:
            return

        db = await self._get_db()

        owner_ids = self._collect_identifier_map(rows, owner_field)
        organization_ids = self._collect_identifier_map(rows, organization_field)
        project_ids = self._collect_identifier_map(rows, project_field)

        owner_lookup = await self._fetch_name_map(
            db.users, owner_ids, fallback_fields=("username", "email", "name")
        )
        org_lookup = await self._fetch_name_map(
            db.organizations, organization_ids, fallback_fields=("name",)
        )
        project_lookup = await self._fetch_name_map(
            db.projects,
            project_ids,
            fallback_fields=("name", "project_name", "projectName"),
        )

        for row in rows:
            owner_key = self._normalize_lookup_key(row.get(owner_field))
            if owner_key:
                row[owner_target_field] = owner_lookup.get(owner_key, "")
            org_key = self._normalize_lookup_key(row.get(organization_field))
            if org_key:
                row[organization_target_field] = org_lookup.get(org_key, "")
            project_key = self._normalize_lookup_key(row.get(project_field))
            if project_key:
                row[project_target_field] = project_lookup.get(project_key, "")

    @staticmethod
    def _collect_identifier_map(
        rows: List[Dict[str, Any]], field_name: str
    ) -> Dict[str, Optional[ObjectId]]:
        identifiers: Dict[str, Optional[ObjectId]] = {}
        for row in rows:
            value = row.get(field_name)
            if not value:
                continue
            key = ReportService._normalize_lookup_key(value)
            if not key or key in identifiers:
                continue
            identifiers[key] = ReportService._to_object_id(value)
        return identifiers

    @staticmethod
    def _normalize_lookup_key(value: Any) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, ObjectId):
            return str(value)
        text = str(value).strip()
        return text or None

    @staticmethod
    def _to_object_id(value: Any) -> Optional[ObjectId]:
        if isinstance(value, ObjectId):
            return value
        try:
            return ObjectId(str(value))
        except Exception:
            return None

    @staticmethod
    async def _fetch_name_map(
        collection,
        identifiers: Dict[str, Optional[ObjectId]],
        *,
        fallback_fields: Tuple[str, ...],
    ) -> Dict[str, str]:
        if not identifiers:
            return {}
        # Query using both ObjectId and raw string ids (some data stores ids as strings)
        ids_to_query: List[Any] = []
        for key, oid in identifiers.items():
            if isinstance(oid, ObjectId):
                ids_to_query.append(oid)
            elif key:
                ids_to_query.append(key)
        if not ids_to_query:
            return {}
        docs = await collection.find({"_id": {"$in": ids_to_query}}).to_list(length=None)
        mapping: Dict[str, str] = {}
        for doc in docs:
            key = str(doc.get("_id"))
            name = ""
            for field in fallback_fields:
                if doc.get(field):
                    name = str(doc[field])
                    break
            mapping[key] = name
        return mapping

    def _map_row(
        self,
        document: Dict[str, Any],
        columns: List[str],
        field_map: Dict[str, Tuple[str, ...]],
    ) -> Dict[str, Any]:
        row: Dict[str, Any] = {}
        for column in columns:
            keys = field_map.get(column, (column,))
            row[column] = self._extract_field(document, keys)
        return row

    @staticmethod
    def _json_safe(value: Any) -> Any:
        if isinstance(value, ObjectId):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, list):
            return [ReportService._json_safe(item) for item in value]
        if isinstance(value, dict):
            return {key: ReportService._json_safe(val) for key, val in value.items()}
        return value

    def _sanitize_rows(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [self._json_safe(row) for row in rows]

    @staticmethod
    def _extract_field(document: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
        for key in keys:
            if key not in document:
                continue
            value = document[key]
            if value in (None, ""):
                continue
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, ObjectId) or key.endswith("_id") or key in (
                "_id",
                "id",
            ):
                return str(value)
            return value
        return ""
