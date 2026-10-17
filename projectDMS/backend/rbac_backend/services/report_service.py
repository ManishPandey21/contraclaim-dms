import asyncio
import csv
import io
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase

from bson import ObjectId

from ..core.database import get_database
from ..core.security import CurrentUser, build_scope_query
from ..models.report import (
    ReportCategory,
    ReportDefinition,
    ReportPreview,
    ReportRequest,
)

logger = logging.getLogger(__name__)

#: Graph candidates fetched per linked-chain page. A floor, not the answer: the
#: page is `max(request.limit, this)`, so a large report never pages one row at
#: a time and a small one still reads a useful batch. Correctness does not
#: depend on the value — only how many round trips a contaminated
#: neighbourhood costs.
_LINKED_CHAIN_GRAPH_PAGE_SIZE = 100

#: Hard stop on how many RAW graph candidates one linked-chain report will
#: examine. Purely a termination guarantee against a pathological
#: neighbourhood; reaching it is reported in `metrics` rather than silently
#: returned as "no linked evidence" (see `_generate_linked_chain_report`).
_LINKED_CHAIN_MAX_GRAPH_CANDIDATES = 5000


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
        scope_clause = self._scope_clause(
            current_user,
            "organization_id",
            "project_id",
            requested_organization_id=request.organization_id,
            requested_project_id=request.project_id,
        )
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

        scope_clause = self._scope_clause(
            current_user,
            "organization_id",
            "project_id",
            requested_organization_id=request.organization_id,
            requested_project_id=request.project_id,
        )
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
        scope_clause = self._scope_clause(
            current_user,
            "organization_id",
            "project_id",
            requested_organization_id=request.organization_id,
            requested_project_id=request.project_id,
        )
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

        # NO tenant filter on node properties. This traversal used to carry
        # `node.organization_id IS NULL OR node.organization_id = $orgId`, which
        # was this report's ONLY tenant boundary - it is the one report of the
        # six that never calls `_scope_clause`. G32 then stripped the shared
        # `(:Letter {normCode})` node down to identity, so `organization_id` is
        # NULL on every node the current writer produces and the clause became a
        # tautology that admits every tenant's letters. A filter that reads a
        # property nothing writes is not a weak guard, it is no guard.
        #
        # The boundary now lives where authority lives: each traversed normCode
        # is resolved to Mongo documents inside the caller's scope, and codes
        # with no in-scope consumable supporter are dropped (see below). The
        # node supplies identity only.
        #
        # CANDIDATE CAPACITY IS BOUNDED, AND THE BOUND APPLIES TO AUTHORISED
        # EVIDENCE, NOT TO RAW GRAPH CANDIDATES.
        #
        # This traversal used to end `RETURN DISTINCT node.normCode LIMIT $limit`
        # and resolve authority afterwards. So an inadmissible node - blocked,
        # another tenant's, or resolving to no Mongo document at all - spent a
        # report slot before `build_scope_query`, `is_consumable` or
        # `graph_codes_denied` were ever consulted, and a legitimate linked
        # letter was pushed out of the answer entirely. Reproduced against a
        # real FalkorDB: eleven candidates, ten inadmissible, `limit=3` returned
        # NO rows and `metrics["total_linked"] == 0` while one genuine linked
        # letter existed. Withholding a contaminated node's CONTENT is not zero
        # material influence if its mere presence deletes a valid row.
        #
        # The reasoning was already written 40 lines below for the Python cut
        # ("Slicing before containment lets a denied or out-of-scope code consume
        # a report slot and silently push a legitimate linked letter out of the
        # CSV"). It simply was never applied to the Cypher `LIMIT` one statement
        # upstream of it.
        #
        # Fix: page the graph in stable batches and filter each batch through the
        # canonical predicates, stopping when `request.limit` AUTHORISED rows
        # exist or the candidate space is exhausted. Rejected alternatives:
        #   * a fixed overfetch multiplier (`limit * k`) is not a boundary - the
        #     shared `(:Letter {normCode})` node is GLOBAL, so the inadmissible
        #     pool is instance-wide and unbounded; any k is exceeded by k+1
        #     contaminants;
        #   * removing the bound outright makes traversal cost unbounded on that
        #     same global graph, which is why the bound exists;
        #   * fencing the query on a canonical eligible normCode set would make a
        #     globally-shared identity the security boundary and would redesign
        #     what the traversal means (G32 ownership), which this seam may not do.
        #
        # A STABLE ORDER is what makes paging safe. `normCode` is the only
        # identity property the shared node is allowed to carry, it is immutable
        # and it is not content, so ordering on it adds no reader-side dependency
        # on a property the writer no longer maintains (G32 reader half). Paging
        # SKIP/LIMIT over an unspecified traversal order would drop and repeat
        # candidates between pages. Engine order must not be load-bearing here
        # for the same reason `graph_codes_denied` stopped trusting Mongo natural
        # order: correctness that depends on it is correctness by luck.
        if direction == "down":
            pattern = "(root)-[:CITES|REPLIES_TO*1..5]->(node:Letter)"
        else:
            pattern = "(node:Letter)-[:CITES|REPLIES_TO*1..5]->(root)"

        traversal = f"""
        MATCH (root:Letter {{normCode:$norm}})
        OPTIONAL MATCH {pattern}
        WITH DISTINCT node.normCode AS normCode
        WHERE normCode IS NOT NULL
        RETURN normCode
        ORDER BY normCode ASC
        SKIP $skip
        LIMIT $page
        """

        prepared_rows: List[Dict[str, Any]] = []
        seen_codes: set = set()

        if request.include_self:
            # The root is a candidate like any other: it goes through the same
            # authority resolution rather than being trusted because the caller
            # named it.
            seen_codes.add(norm)
            for row in await self._admissible_linked_rows(
                db,
                [
                    {
                        "normCode": norm,
                        "code": request.letter_no,
                    }
                ],
                request,
                current_user,
                direction,
            ):
                prepared_rows.append(row)

        page_size = max(int(request.limit), _LINKED_CHAIN_GRAPH_PAGE_SIZE)
        skip = 0
        examined = 0
        exhausted = False
        truncated = False

        while len(prepared_rows) < request.limit and not exhausted:
            try:
                result = await asyncio.to_thread(
                    svc._execute,
                    traversal,
                    {"norm": norm, "skip": skip, "page": page_size},
                )
                batch = svc._parse_rows(result)
            except Exception as exc:
                raise ReportServiceError(f"Graph traversal failed: {exc}")

            if len(batch) < page_size:
                # A short page means the candidate space ran out. This is the
                # normal termination: no admissible row is required to exist.
                exhausted = True
            skip += page_size
            examined += len(batch)

            candidates: List[Dict[str, Any]] = []
            for row in batch:
                code = str(row.get("code") or row.get("normCode") or "")
                # Duplicate suppression must survive paging: the same code seen
                # on two pages (concurrent graph writes shift the ordering) must
                # neither be emitted twice nor spend the window twice.
                if not code or code in seen_codes:
                    continue
                seen_codes.add(code)
                candidates.append(row)

            for row in await self._admissible_linked_rows(
                db, candidates, request, current_user, direction
            ):
                if len(prepared_rows) >= request.limit:
                    break
                prepared_rows.append(row)

            if not exhausted and examined >= _LINKED_CHAIN_MAX_GRAPH_CANDIDATES:
                # A safety cap, so a pathological neighbourhood cannot make one
                # report scan forever. Reaching it is NOT allowed to look like
                # "no linked evidence": the caller is told the scan was cut
                # short, in the metrics, rather than being handed a confident
                # under-count. Fail visible, never silently short.
                truncated = True
                break

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
        metrics: Dict[str, Any] = {"total_linked": len(prepared_rows)}
        if truncated:
            # Only present when the cap actually fired, so an ordinary report
            # keeps its existing metric shape and a degraded one is impossible
            # to mistake for a complete answer.
            metrics["linked_chain_truncated"] = True
            metrics["linked_chain_candidates_examined"] = examined
        return ReportPreview(
            report_id=definition.id,
            report_name=definition.name,
            generated_at=datetime.utcnow(),
            columns=columns,
            rows=prepared_rows,
            metrics=metrics,
            total_rows=len(prepared_rows),
        )

    async def _admissible_linked_rows(
        self,
        db: Any,
        candidate_rows: List[Dict[str, Any]],
        request: ReportRequest,
        current_user: CurrentUser,
        direction: str,
    ) -> List[Dict[str, Any]]:
        """Canonical authority for ONE batch of linked-chain graph candidates.

        Extracted so the report's bound can be applied to what comes OUT of this
        (authorised rows) instead of to what goes in (raw graph candidates). The
        predicates are unchanged and there is no second policy here:
        `build_scope_query` for row visibility, `is_consumable` for publication
        authority, `graph_codes_denied` for graph provenance. A batch is
        evaluated whole, so the answer for a given candidate never depends on
        which page it arrived on.
        """
        from ..services.publication_policy import (
            authoritative_summary,
            graph_codes_denied,
            is_consumable,
        )
        from ..services.falkor_graph_service import normalize_letter_code

        if not candidate_rows:
            return []

        # Enrich with letter metadata from Mongo
        codes = [
            str(code)
            for code in (r.get("code") or r.get("normCode") for r in candidate_rows)
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
            # Tenant scope. This enrichment matched on LETTER CODE ALONE, and a
            # letter code is global - the same code exists in other tenants. So a
            # legitimate org-A user received org-B's subject/summary in the
            # preview and the CSV download. The graph traversal was org-filtered;
            # the Mongo lookup that supplies the actual text was not.
            #
            # The boundary is derived from the CALLER'S IDENTITY, never from the
            # request alone. `request.organization_id`/`project_id` are optional
            # fields: an omitted `organizationId` still passes the router gate
            # (it authorizes against `request.organization_id or
            # current_user.organization_id`) while leaving the service with no
            # filter at all, so the enrichment ran across every tenant and the
            # `if not meta: continue` guard below stopped dropping anything. The
            # shipped Reports page sends exactly that request whenever no
            # organisation or project is selected. A request-derived filter also
            # cannot see `current_user.projects`, so a project-tier user read
            # every project in their organisation.
            #
            # `build_scope_query` is the canonical row-visibility helper: it
            # treats the request fields as a NARROWING selection inside the
            # caller's entitlement (a foreign id denies all), ANDs organisation
            # with project assignments, and falls back to deny-all rather than to
            # "unfiltered" for an unrecognised caller.
            scope_query = build_scope_query(
                current_user,
                organization_id=request.organization_id,
                project_id=request.project_id,
            )
            enrichment_query: Dict[str, Any] = (
                {"$and": [{"$or": match_filters}, scope_query]}
                if scope_query
                else {"$or": match_filters}
            )
            docs = await db.documents.find(enrichment_query).to_list(length=None)
            letters = await db.letters.find(enrichment_query).to_list(length=None)
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
                # Authority-controlled: `summary` is extraction-derived and
                # `subject` is filing metadata, but neither may come from a
                # document that is not currently publishable. This report reached
                # an exported CSV with a blocked, quarantined, cross-tenant
                # document's text attached.
                consumable = is_consumable(doc)
                row_summary = {
                    "summary": (authoritative_summary(doc) if consumable else ""),
                    "subject": (doc.get("subject") or "") if consumable else "",
                    # Inside the consumable gate too: a blocked document's date
                    # and tenancy are still its data.
                    "date": (doc.get("date") or doc.get("created_at") or doc.get("createdAt") or "") if consumable else "",
                    "organization_id": (doc.get("organization_id") or doc.get("organizationId")) if consumable else None,
                    "project_id": (doc.get("project_id") or doc.get("projectId")) if consumable else None,
                }
                if key not in summaries:
                    summaries[key] = row_summary
                normalized_key = normalize_letter_code(key)
                if normalized_key and normalized_key not in summaries:
                    summaries[normalized_key] = row_summary

        admissible: List[Dict[str, Any]] = []
        # A denied code must not COUNT either. Suppressing its text while still
        # incrementing `total_linked` is material influence: the metric asserts
        # how many linked letters support this chain, and a blocked or orphan
        # code is not support. Same denial set the serving paths use - one
        # authority semantic, not separate "display" and "metric" ones.
        row_codes = [str(r.get("normCode") or r.get("code") or "") for r in candidate_rows]
        try:
            denied_codes = await graph_codes_denied(db, [c for c in row_codes if c])
        except Exception:
            denied_codes = {c for c in row_codes if c}  # fail closed

        for row in candidate_rows:
            code = str(row.get("code") or row.get("normCode") or "")
            row_norm = str(row.get("normCode") or "") or normalize_letter_code(code)
            if row_norm and row_norm in denied_codes:
                continue
            meta = summaries.get(code, {})
            # `summaries` is built from Mongo scoped to the CALLER, so an empty
            # meta means this code has no in-scope supporting document. That is
            # the tenant boundary for this report: the graph node is shared
            # across tenants on normCode alone, so emitting an unenriched row
            # would publish a FOREIGN tenant's letter number (and count it in
            # metrics["total_linked"]).
            if not meta:
                continue
            admissible.append(
                {
                    "letter_no": code,
                    # No fallback to `row` (the shared graph node): it is
                    # identity-only and any subject still on it is legacy
                    # contamination owned by some other document/tenant.
                    "subject": meta.get("subject") or "",
                    "summary": meta.get("summary", ""),
                    # Same rule as `subject` above: no fallback to `row`. The
                    # shared node is identity-only, so `direction`/
                    # `organization_id`/`project_id` still sitting on it are
                    # legacy values owned by whichever document wrote last.
                    "direction": meta.get("direction") or direction,
                    "date": meta.get("date") or "",
                    "organization_id": meta.get("organization_id") or "",
                    "project_id": meta.get("project_id") or "",
                }
            )
        return admissible

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

        scope_clause = self._scope_clause(
            current_user,
            organization_field,
            project_field,
            requested_organization_id=request.organization_id,
            requested_project_id=request.project_id,
        )
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
        current_user: CurrentUser,
        organization_field: str,
        project_field: str,
        *,
        requested_organization_id: Optional[str] = None,
        requested_project_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Canonical row visibility for every report that filters by tenancy.

        This used to build its own filter, and it UNIONED the two scope
        dimensions:

            {"$or": [{"organization_id": "org-A"}, {"project_id": "proj-A"}]}

        Either branch alone admits a row, so a project-tier caller assigned to
        Project A matched every record whose organisation is A - including the
        sibling Project B they are not assigned to. Organisation entitlement and
        project assignment must INTERSECT.

        Three further widenings came from the same hand-rolled clause: it ORed
        every entry of `current_user.organizations` (entitlement history, not
        the active navbar selection); it treated an unrecognised role as
        organisation-wide instead of denying it; and it could not see that a
        caller-supplied `projectId` lay outside the caller's assignments.

        `build_scope_query` is the single canonical row-visibility helper
        (`core/security.py`) and it already expresses all four semantics,
        including superadmin's unrestricted read and superuser's
        multi-organisation entitlement. The report layer CONSUMES that
        authority; it does not invent a second implementation, and it never
        authorises by role name.

        `requested_organization_id` / `requested_project_id` are the caller's
        request fields. They are passed as a NARROWING selection inside the
        caller's entitlement - a foreign id denies all - never as an authority
        substitute.
        """
        scope = build_scope_query(
            current_user,
            organization_id=requested_organization_id,
            project_id=requested_project_id,
            org_field=organization_field,
            project_field=project_field,
        )
        return scope or None

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
