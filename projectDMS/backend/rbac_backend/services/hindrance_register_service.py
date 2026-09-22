"""Hindrance & Constraint Register over the `delay_events` collection.

One service owns the collection. `/api/hindrances` (the register) and
`/api/delay-events` (the compatibility API) both land here, so reference
allocation, archive state, PATCH semantics, audit and timeline projection have
a single definition.

Canonical state is the Mongo row. The ProjectEvent on the Contract Timeline is a
derived projection: when it cannot be written the row still commits, and the
failure is recorded on the row (`timeline_sync_status`), in the audit trail and
in the log, never swallowed. `POST /hindrances/{id}/timeline-sync` repairs it
idempotently.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..core.permissions import Permissions
from ..models.evidence_graph import (
    EventLinkCreate,
    EventRelationType,
    EvidenceEntityType,
    ProjectEventCreate,
    ProjectEventStatus,
    ProjectEventType,
)
from ..models.evidence_registers import (
    DelayEvent,
    HindranceEventType,
    HindranceLinkCreate,
    TimelineSyncStatus,
)
from ..utils.error_handler import BaseDomainError
from .evidence_graph_service import EvidenceGraphService
from .evidence_register_service import EvidenceRegisterNotFound, EvidenceRegisterService, _actor_id, _jsonable
from .scope_service import ScopeService

logger = logging.getLogger(__name__)

COLLECTION = "delay_events"
COUNTERS = "delay_event_reference_counters"
LINKS = "delay_event_links"
LINK_RESOURCE = "delay_event_link"

REFERENCE_PREFIXES: Dict[str, str] = {
    HindranceEventType.HINDRANCE.value: "HIN",
    HindranceEventType.CONSTRAINT.value: "CNS",
    HindranceEventType.DELAY_EVENT.value: "DLY",
}
_REFERENCE_ATTEMPTS = 5

SORTABLE_FIELDS = (
    "start_date",
    "end_date",
    "hindrance_ref",
    "title",
    "status",
    "event_type",
    "category",
    "responsibility",
    "created_at",
    "updated_at",
)
_SEARCH_FIELDS = (
    "hindrance_ref",
    "delay_ref",
    "title",
    "description",
    "location",
    "cause",
    "responsible_party",
    "affected_party",
)
_TIMELINE_FIELDS = frozenset(
    {
        "event_type",
        "title",
        "description",
        "category",
        "start_date",
        "end_date",
        "responsibility",
        "location",
        "status",
        "claim_status",
        "critical_path_impact",
    }
)
_ARCHIVED_READ_ONLY = "Archived register entries are read-only; restore it first"
_HISTORY_NOISE = frozenset({"_id", "updated_at", "updated_by", "timeline_synced_at"})


class HindranceError(BaseDomainError):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail, status_code, error="HindranceError")
        self.detail = detail
        self.status_code = status_code

    def as_http(self) -> HTTPException:
        return HTTPException(status_code=self.status_code, detail=self.detail)


def _value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def event_type_of(doc: Dict[str, Any]) -> str:
    """Rows written before the register have no type; they are delay events."""
    return str(_value(doc.get("event_type")) or HindranceEventType.DELAY_EVENT.value)


def reference_of(doc: Dict[str, Any]) -> str:
    return str(doc.get("hindrance_ref") or doc.get("delay_ref") or doc.get("_id") or "")


def project_event_type_for(event_type: str) -> ProjectEventType:
    return {
        HindranceEventType.DELAY_EVENT.value: ProjectEventType.DELAY,
        HindranceEventType.HINDRANCE.value: ProjectEventType.HINDRANCE,
        HindranceEventType.CONSTRAINT.value: ProjectEventType.CONSTRAINT,
    }[event_type]


def relation_for(event_type: str) -> EventRelationType:
    """Only a delay event asserts that it causes delay."""
    if event_type == HindranceEventType.DELAY_EVENT.value:
        return EventRelationType.CAUSES_DELAY
    return EventRelationType.AFFECTS


def project_event_status_for(doc: Dict[str, Any]) -> ProjectEventStatus:
    if doc.get("archived_at"):
        return ProjectEventStatus.CLOSED
    status = str(_value(doc.get("status")) or "open")
    if status == "under_review":
        return ProjectEventStatus.UNDER_REVIEW
    if status in {"resolved", "closed", "rejected"}:
        return ProjectEventStatus.CLOSED
    return ProjectEventStatus.OPEN


def timeline_fields(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic projection of one register row onto its ProjectEvent."""
    event_type = event_type_of(doc)
    responsibility = _value(doc.get("responsibility"))
    return {
        "event_type": project_event_type_for(event_type),
        "event_date": doc.get("start_date") or doc.get("created_at") or datetime.utcnow(),
        "event_end_date": doc.get("end_date"),
        "title": f"{reference_of(doc)} - {doc.get('title') or ''}"[:300],
        "description": doc.get("description"),
        "impact_area": doc.get("location"),
        "status": project_event_status_for(doc),
        "metadata": {
            "register_source": EvidenceEntityType.DELAY_EVENT.value,
            "register_event_type": event_type,
            "reference": reference_of(doc),
            "location": doc.get("location"),
            "status": _value(doc.get("status")),
            "claim_status": _value(doc.get("claim_status")),
            "category": _value(doc.get("category")),
            "responsibility": responsibility,
            # The timeline's responsibility filter reads this key.
            "delay_responsibility": responsibility,
            "critical_path_impact": bool(doc.get("critical_path_impact")),
            "archived": bool(doc.get("archived_at")),
        },
    }


def _naive_datetimes(values: Dict[str, Any]) -> Dict[str, Any]:
    """Stored dates are naive UTC; an offset-aware input is converted, not
    compared raw (which would raise TypeError and surface as a 500)."""
    for key, value in list(values.items()):
        if isinstance(value, datetime) and value.tzinfo is not None:
            values[key] = value.astimezone(timezone.utc).replace(tzinfo=None)
    return values


def _merge(query: Dict[str, Any], clauses: List[Dict[str, Any]], key: str, condition: Any) -> None:
    """Add a condition without overwriting one the scope filter already set."""
    if key in query:
        clauses.append({key: condition})
    else:
        query[key] = condition


def _changed_fields(before: Any, after: Any) -> List[str]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    keys = (set(before) | set(after)) - _HISTORY_NOISE
    return sorted(key for key in keys if before.get(key) != after.get(key))


@dataclass(frozen=True)
class _LinkTarget:
    collection: str
    view_permission: str
    relationship_role: str
    label_fields: Tuple[str, ...]
    date_fields: Tuple[str, ...]
    kind_field: Optional[str]
    route: Callable[[Dict[str, Any]], Optional[str]]


LINK_TARGETS: Dict[str, _LinkTarget] = {
    "programme_milestone": _LinkTarget(
        collection="programme_milestones",
        view_permission=Permissions.EVIDENCE_GRAPH_VIEW,
        relationship_role="affects_activity",
        label_fields=("milestone_ref", "title"),
        date_fields=("forecast_date", "planned_date"),
        kind_field="milestone_type",
        route=lambda row: None,
    ),
    "key_date": _LinkTarget(
        collection="key_date_milestones",
        view_permission=Permissions.KEYDATE_VIEW,
        relationship_role="impacts_key_date",
        label_fields=("milestone_ref", "title"),
        date_fields=("current_approved_key_date", "calculated_key_date", "due_date"),
        kind_field=None,
        route=lambda row: f"/key-dates/{row.get('_id')}",
    ),
    "eot_submission": _LinkTarget(
        collection="key_date_eot_submissions",
        view_permission=Permissions.KEYDATE_VIEW,
        relationship_role="supports_eot_submission",
        label_fields=("revision_label",),
        date_fields=("contractor_submission_date",),
        kind_field=None,
        route=lambda row: f"/key-dates?project_id={row.get('project_id')}&submission_id={row.get('_id')}",
    ),
}


def _first(row: Dict[str, Any], fields: Tuple[str, ...]) -> Any:
    for field in fields:
        value = row.get(field)
        if value not in (None, ""):
            return value
    return None


class HindranceRegisterService(EvidenceRegisterService):
    """The single owner of `delay_events` rows and their relationships."""

    # ------------------------------------------------------------------
    # Scope
    # ------------------------------------------------------------------

    async def resolve_project_organization(
        self, project_id: str, expected_organization_id: Optional[str]
    ) -> str:
        """The organisation is the project's owner, never the caller's guess."""
        db = await self._get_db()
        project = await db.projects.find_one({"_id": ScopeService.object_id_query(str(project_id))})
        if not project:
            raise HindranceError("Project not found", 404)
        owner = str(project.get("organization_id") or project.get("organizationId") or "")
        if not owner:
            raise HindranceError("Project has no owning organisation", 409)
        if expected_organization_id and str(expected_organization_id) != owner:
            raise HindranceError("Project does not belong to the selected organisation", 422)
        return owner

    # ------------------------------------------------------------------
    # References
    # ------------------------------------------------------------------

    async def _next_reference(self, db: Any, organization_id: str, project_id: str, prefix: str) -> str:
        counter = await db[COUNTERS].find_one_and_update(
            {"_id": f"{organization_id}:{project_id}:{prefix}"},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        sequence = int((counter or {}).get("seq") or 0)
        if sequence < 1:
            raise HindranceError("Reference counter is unavailable", 503)
        return f"{prefix}-{sequence:04d}"

    async def _realign_counter(self, db: Any, organization_id: str, project_id: str, prefix: str) -> None:
        """A counter behind existing rows (imports, restores) catches up once."""
        pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
        highest = 0
        cursor = db[COLLECTION].find(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "hindrance_ref": {"$regex": rf"^{re.escape(prefix)}-[0-9]+$"},
            }
        )
        async for row in cursor:
            match = pattern.match(str(row.get("hindrance_ref") or ""))
            if match:
                highest = max(highest, int(match.group(1)))
        if highest:
            await db[COUNTERS].update_one(
                {"_id": f"{organization_id}:{project_id}:{prefix}", "seq": {"$lt": highest}},
                {"$set": {"seq": highest}},
            )

    # ------------------------------------------------------------------
    # Create / read / list
    # ------------------------------------------------------------------

    async def create(self, payload: Any, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        doc = _naive_datetimes(_jsonable(DelayEvent(**payload.model_dump(mode="json")).model_dump(by_alias=True)))
        if not doc.get("organization_id"):
            doc["organization_id"] = getattr(current_user, "organization_id", None)
        organization_id = str(doc.get("organization_id") or "")
        project_id = str(doc.get("project_id") or "")
        if not organization_id or not project_id:
            raise HindranceError("A register entry requires an organisation and a project", 422)
        event_type = event_type_of(doc)
        doc["event_type"] = event_type
        doc["linked_document_ids"] = await self._authorized_document_links(
            db,
            doc.get("linked_document_ids"),
            organization_id=organization_id,
            project_id=project_id,
        )
        now = datetime.utcnow()
        doc.update(
            {
                "archived_at": None,
                "archived_by": None,
                "archive_reason": None,
                "timeline_sync_status": TimelineSyncStatus.PENDING.value,
                "timeline_sync_error": None,
                "timeline_event_id": None,
                "timeline_synced_at": None,
                "created_at": now,
                "created_by": _actor_id(current_user),
                "updated_at": None,
                "updated_by": None,
            }
        )
        prefix = REFERENCE_PREFIXES[event_type]
        inserted_id = None
        for attempt in range(_REFERENCE_ATTEMPTS):
            doc["hindrance_ref"] = await self._next_reference(db, organization_id, project_id, prefix)
            try:
                result = await db[COLLECTION].insert_one(doc)
            except DuplicateKeyError:
                await self._realign_counter(db, organization_id, project_id, prefix)
                continue
            inserted_id = result.inserted_id
            break
        if inserted_id is None:
            raise HindranceError("Could not allocate a unique register reference; retry", 409)

        created = await db[COLLECTION].find_one({"_id": inserted_id}) or doc
        await self.audit.emit(
            action=f"{COLLECTION}.created",
            actor_id=_actor_id(current_user),
            resource_type=COLLECTION,
            resource_id=str(created.get("_id")),
            organization_id=created.get("organization_id"),
            project_id=created.get("project_id"),
            after=created,
        )
        return await self.sync_timeline(created, current_user)

    async def _served(self, db: Any, doc: Dict[str, Any]) -> Dict[str, Any]:
        """The served projection of a row that is known to exist."""
        return await self._current_authority_projection(db, doc) or doc

    async def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        return await self._current_authority_projection(db, await db[COLLECTION].find_one({"_id": item_id}))

    def _query(
        self,
        scope_filter: Dict[str, Any],
        *,
        project_id: Optional[str] = None,
        event_type: Optional[str] = None,
        status: Optional[str] = None,
        claim_status: Optional[str] = None,
        category: Optional[str] = None,
        responsibility: Optional[str] = None,
        location: Optional[str] = None,
        critical_path_impact: Optional[bool] = None,
        timeline_sync_status: Optional[str] = None,
        start_from: Optional[datetime] = None,
        start_to: Optional[datetime] = None,
        q: Optional[str] = None,
        include_archived: bool = False,
    ) -> Dict[str, Any]:
        query: Dict[str, Any] = dict(scope_filter or {})
        clauses: List[Dict[str, Any]] = []
        if project_id:
            _merge(query, clauses, "project_id", project_id)
        if event_type == HindranceEventType.DELAY_EVENT.value:
            clauses.append(
                {"$or": [{"event_type": event_type}, {"event_type": {"$exists": False}}, {"event_type": None}]}
            )
        elif event_type:
            _merge(query, clauses, "event_type", event_type)
        for key, value in (
            ("status", status),
            ("claim_status", claim_status),
            ("category", category),
            ("responsibility", responsibility),
            ("location", location),
            ("timeline_sync_status", timeline_sync_status),
        ):
            if value:
                _merge(query, clauses, key, value)
        if critical_path_impact is not None:
            _merge(query, clauses, "critical_path_impact", bool(critical_path_impact))
        if start_from or start_to:
            window: Dict[str, Any] = {}
            if start_from:
                window["$gte"] = start_from
            if start_to:
                window["$lte"] = start_to
            _merge(query, clauses, "start_date", window)
        text = (q or "").strip()
        if text:
            pattern = re.escape(text[:200])
            clauses.append({"$or": [{field: {"$regex": pattern, "$options": "i"}} for field in _SEARCH_FIELDS]})
        if not include_archived:
            # Matches rows that predate the field as well as unarchived ones.
            _merge(query, clauses, "archived_at", None)
        return {"$and": [query, *clauses]} if clauses else query

    async def list_entries(
        self,
        scope_filter: Dict[str, Any],
        *,
        sort: str = "start_date",
        order: str = "desc",
        skip: int = 0,
        limit: int = 200,
        with_total: bool = False,
        **filters: Any,
    ) -> Tuple[List[Dict[str, Any]], int]:
        if sort not in SORTABLE_FIELDS:
            raise HindranceError("Unsupported sort field", 422)
        db = await self._get_db()
        query = self._query(scope_filter, **filters)
        direction = 1 if order == "asc" else -1
        cursor = db[COLLECTION].find(query).sort(sort, direction).skip(skip).limit(limit)
        rows = [dict(item) async for item in cursor]
        served = await self._current_authority_projections(db, rows)
        total = await db[COLLECTION].count_documents(query) if with_total else len(served)
        return served, total

    # ------------------------------------------------------------------
    # Update / archive
    # ------------------------------------------------------------------

    async def update(self, item_id: str, changes: Dict[str, Any], current_user: Any) -> Dict[str, Any]:
        """Apply a PATCH. `changes` holds only fields the client sent; a None in
        it is an explicit clear, not an omission."""
        db = await self._get_db()
        existing = await db[COLLECTION].find_one({"_id": item_id})
        if not existing:
            raise EvidenceRegisterNotFound(item_id)
        if existing.get("archived_at"):
            raise HindranceError(_ARCHIVED_READ_ONLY, 409)
        changes = _naive_datetimes(dict(changes))
        start = changes.get("start_date", existing.get("start_date"))
        end = changes.get("end_date", existing.get("end_date"))
        if start is not None and end is not None and end < start:
            raise HindranceError("end_date cannot be before start_date", 422)
        update = {key: _jsonable(value) for key, value in changes.items()}
        if "linked_document_ids" in update:
            update["linked_document_ids"] = await self._authorized_document_links(
                db,
                update.get("linked_document_ids"),
                organization_id=existing.get("organization_id"),
                project_id=existing.get("project_id"),
            )
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = _actor_id(current_user)
        # Fenced on the scope and archive state read above: an entry archived or
        # re-scoped between the read and this write refuses instead of changing.
        updated = await db[COLLECTION].find_one_and_update(
            {
                "_id": item_id,
                "organization_id": existing.get("organization_id"),
                "project_id": existing.get("project_id"),
                "archived_at": None,
            },
            {"$set": update},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise HindranceError(_ARCHIVED_READ_ONLY, 409)
        updated = await self._served(db, updated or {**existing, **update})
        await self.audit.emit(
            action=f"{COLLECTION}.updated",
            actor_id=_actor_id(current_user),
            resource_type=COLLECTION,
            resource_id=item_id,
            organization_id=updated.get("organization_id"),
            project_id=updated.get("project_id"),
            before=existing,
            after=updated,
        )
        if _TIMELINE_FIELDS & set(changes):
            return await self.sync_timeline(updated, current_user)
        return updated

    async def _set_archive_state(
        self, item_id: str, current_user: Any, *, archive: bool, reason: str
    ) -> Dict[str, Any]:
        db = await self._get_db()
        existing = await db[COLLECTION].find_one({"_id": item_id})
        if not existing:
            raise HindranceError("Register entry not found", 404)
        if bool(existing.get("archived_at")) == archive:
            raise HindranceError(
                "Register entry is already archived" if archive else "Register entry is not archived", 409
            )
        now = datetime.utcnow()
        actor = _actor_id(current_user)
        state = (
            {"archived_at": now, "archived_by": actor, "archive_reason": reason}
            if archive
            else {"archived_at": None, "archived_by": None, "archive_reason": None}
        )
        state.update({"updated_at": now, "updated_by": actor})
        guard: Dict[str, Any] = (
            {"_id": item_id, "archived_at": None} if archive else {"_id": item_id, "archived_at": {"$ne": None}}
        )
        updated = await db[COLLECTION].find_one_and_update(
            guard, {"$set": state}, return_document=ReturnDocument.AFTER
        )
        if not updated:
            raise HindranceError("Register entry changed concurrently; reload and retry", 409)
        await self.audit.emit(
            action=f"{COLLECTION}.{'archived' if archive else 'restored'}",
            actor_id=actor,
            resource_type=COLLECTION,
            resource_id=item_id,
            organization_id=updated.get("organization_id"),
            project_id=updated.get("project_id"),
            before=existing,
            after=updated,
            reason=reason,
        )
        return await self.sync_timeline(await self._served(db, updated), current_user)

    async def archive(self, item_id: str, current_user: Any, *, reason: str) -> Dict[str, Any]:
        return await self._set_archive_state(item_id, current_user, archive=True, reason=reason)

    async def restore(self, item_id: str, current_user: Any, *, reason: str) -> Dict[str, Any]:
        return await self._set_archive_state(item_id, current_user, archive=False, reason=reason)

    # ------------------------------------------------------------------
    # Timeline projection (derived, fail-visible, idempotent)
    # ------------------------------------------------------------------

    async def sync_timeline(self, doc: Dict[str, Any], current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        item_id = str(doc.get("_id"))
        now = datetime.utcnow()
        try:
            state = await self._project_timeline(db, doc, current_user, now)
        except Exception as exc:  # noqa: BLE001 - recorded below, never swallowed
            logger.warning("Timeline projection failed for delay_event %s", item_id, exc_info=True)
            error = f"{type(exc).__name__}: timeline projection failed"
            state = {
                "timeline_sync_status": TimelineSyncStatus.FAILED.value,
                "timeline_sync_error": error,
                "timeline_event_id": doc.get("timeline_event_id"),
            }
            await self.audit.emit(
                action=f"{COLLECTION}.timeline_sync_failed",
                actor_id=_actor_id(current_user),
                resource_type=COLLECTION,
                resource_id=item_id,
                organization_id=doc.get("organization_id"),
                project_id=doc.get("project_id"),
                result="failure",
                reason=error,
            )
        stamped = await db[COLLECTION].find_one_and_update(
            {"_id": doc.get("_id")}, {"$set": state}, return_document=ReturnDocument.AFTER
        )
        return await self._served(db, stamped or {**doc, **state})

    async def _project_timeline(
        self, db: Any, doc: Dict[str, Any], current_user: Any, now: datetime
    ) -> Dict[str, Any]:
        item_id = str(doc.get("_id"))
        fields = timeline_fields(doc)
        event_type = event_type_of(doc)
        graph = EvidenceGraphService(db)
        # The row remembers its projection; the source key is the fallback that
        # also finds an event created by the backfill or by a run whose stamp
        # never landed, so a retry can never mint a second event.
        existing = None
        if doc.get("timeline_event_id"):
            existing = await db.project_events.find_one({"_id": doc.get("timeline_event_id")})
        if not existing:
            existing = await db.project_events.find_one(
                {"source_entity_type": EvidenceEntityType.DELAY_EVENT.value, "source_entity_id": item_id}
            )
        if existing:
            event_id = str(existing.get("_id"))
            await db.project_events.find_one_and_update(
                {"_id": existing.get("_id")},
                {"$set": {**_jsonable(fields), "updated_at": now, "updated_by": _actor_id(current_user)}},
            )
        else:
            event = await graph.create_project_event(
                ProjectEventCreate(
                    organization_id=doc.get("organization_id"),
                    project_id=doc.get("project_id"),
                    source_entity_type=EvidenceEntityType.DELAY_EVENT,
                    source_entity_id=item_id,
                    confidence=1.0,
                    **fields,
                ),
                current_user,
            )
            event_id = str(event.get("_id"))
        # Both ids are UUIDs, so the pair identifies the register's own link.
        link = await db.event_links.find_one({"source_id": event_id, "target_id": item_id})
        if not link:
            await graph.suggest_link(
                EventLinkCreate(
                    organization_id=doc.get("organization_id"),
                    project_id=doc.get("project_id"),
                    source_type=EvidenceEntityType.PROJECT_EVENT,
                    source_id=event_id,
                    target_type=EvidenceEntityType.DELAY_EVENT,
                    target_id=item_id,
                    relation_type=relation_for(event_type),
                    confidence=1.0,
                    evidence_text=fields["title"],
                ),
                current_user=current_user,
            )
        return {
            "timeline_sync_status": TimelineSyncStatus.SYNCED.value,
            "timeline_sync_error": None,
            "timeline_event_id": event_id,
            "timeline_synced_at": now,
        }

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def history(self, item_id: str, *, limit: int = 500) -> List[Dict[str, Any]]:
        db = await self._get_db()
        cursor = (
            db.audit_events.find(
                {
                    "$or": [
                        {"resource_type": COLLECTION, "resource_id": item_id},
                        {"resource_type": LINK_RESOURCE, "metadata.delay_event_id": item_id},
                    ]
                }
            )
            .sort("created_at", 1)
            .limit(limit)
        )
        entries: List[Dict[str, Any]] = []
        async for row in cursor:
            entries.append(
                {
                    "id": str(row.get("_id")) if row.get("_id") is not None else None,
                    "action": str(row.get("action") or ""),
                    "actor_id": row.get("actor_id"),
                    "resource_type": row.get("resource_type"),
                    "resource_id": row.get("resource_id"),
                    "result": row.get("result"),
                    "reason": row.get("reason"),
                    "changed_fields": _changed_fields(row.get("before"), row.get("after")),
                    "created_at": row.get("created_at"),
                }
            )
        return entries

    # ------------------------------------------------------------------
    # Activity / key-date / EOT relationships
    # ------------------------------------------------------------------

    # The caller authorizes the register entry itself (HINDRANCE_VIEW or
    # HINDRANCE_EDIT on `item`) before calling these; they authorize the far
    # side of the relationship, which only they load.

    async def load_link_target(self, target_type: str, target_id: str) -> Dict[str, Any]:
        if target_type not in LINK_TARGETS:
            raise HindranceError("Unsupported relationship target", 422)
        target = await self._load_target(await self._get_db(), target_type, target_id)
        if not target:
            raise HindranceError("Link target not found", 404)
        return target

    async def _load_target(self, db: Any, target_type: str, target_id: str) -> Optional[Dict[str, Any]]:
        spec = LINK_TARGETS[target_type]
        return await db[spec.collection].find_one({"_id": ScopeService.object_id_query(str(target_id))})

    @staticmethod
    def _summary(target_type: str, row: Dict[str, Any]) -> Dict[str, Any]:
        spec = LINK_TARGETS[target_type]
        return {
            "label": str(_first(row, spec.label_fields) or row.get("_id")),
            "title": row.get("title"),
            "status": _value(row.get("status")),
            "date": _first(row, spec.date_fields),
            "kind": _value(row.get(spec.kind_field)) if spec.kind_field else None,
            "route": spec.route(row),
        }

    async def _link_view(self, db: Any, stored: Dict[str, Any], actor: Any, policy: Any) -> Dict[str, Any]:
        target_type = str(stored.get("target_type"))
        view = {**stored, "target": None, "target_available": True, "target_restricted": False}
        target = await self._load_target(db, target_type, str(stored.get("target_id")))
        if not target or str(target.get("project_id") or "") != str(stored.get("project_id")):
            view["target_available"] = False
            return view
        try:
            await policy.authorize_document(
                actor, LINK_TARGETS[target_type].view_permission, target, resource_type=target_type
            )
        except HTTPException:
            view["target_restricted"] = True
            return view
        view["target"] = self._summary(target_type, target)
        return view

    async def link(
        self, actor: Any, item: Dict[str, Any], payload: HindranceLinkCreate, policy: Any
    ) -> Tuple[Dict[str, Any], bool]:
        db = await self._get_db()
        item_id = str(item.get("_id"))
        if item.get("archived_at"):
            raise HindranceError(_ARCHIVED_READ_ONLY, 409)
        target_type = _value(payload.target_type)
        spec = LINK_TARGETS[target_type]
        target = await self._load_target(db, target_type, payload.target_id)
        if not target:
            raise HindranceError("Link target not found", 404)
        if str(target.get("organization_id") or "") != str(item.get("organization_id")) or str(
            target.get("project_id") or ""
        ) != str(item.get("project_id")):
            raise HindranceError("Link target scope does not match the register entry", 403)
        await policy.authorize_document(actor, spec.view_permission, target, resource_type=target_type)
        identity = {
            "organization_id": str(item.get("organization_id")),
            "project_id": str(item.get("project_id")),
            "delay_event_id": item_id,
            "target_type": target_type,
            "target_id": str(target.get("_id")),
            "removed_at": None,
        }
        existing = await db[LINKS].find_one(identity)
        if existing:
            return await self._link_view(db, existing, actor, policy), False
        stored = {
            "_id": str(uuid.uuid4()),
            **identity,
            "relationship_role": spec.relationship_role,
            "description": payload.description,
            "created_at": datetime.utcnow(),
            "created_by": _actor_id(actor),
            "removed_by": None,
            "removal_reason": None,
            "_revision": 1,
        }
        try:
            await db[LINKS].insert_one(stored)
        except DuplicateKeyError:
            existing = await db[LINKS].find_one(identity)
            if not existing:
                raise HindranceError("Relationship changed during link; retry", 409)
            return await self._link_view(db, existing, actor, policy), False
        await self.audit.emit(
            action=f"{LINKS}.linked",
            actor_id=_actor_id(actor),
            resource_type=LINK_RESOURCE,
            resource_id=stored["_id"],
            organization_id=identity["organization_id"],
            project_id=identity["project_id"],
            after=stored,
            metadata={"delay_event_id": item_id, "target_type": target_type, "target_id": identity["target_id"]},
        )
        return await self._link_view(db, stored, actor, policy), True

    async def list_links(self, actor: Any, item: Dict[str, Any], policy: Any) -> List[Dict[str, Any]]:
        db = await self._get_db()
        item_id = str(item.get("_id"))
        cursor = db[LINKS].find(
            {
                "organization_id": str(item.get("organization_id")),
                "project_id": str(item.get("project_id")),
                "delay_event_id": item_id,
                "removed_at": None,
            }
        ).sort("created_at", 1)
        return [await self._link_view(db, row, actor, policy) async for row in cursor]

    async def remove_link(
        self, actor: Any, item: Dict[str, Any], link_id: str, reason: str, policy: Any
    ) -> Dict[str, Any]:
        db = await self._get_db()
        item_id = str(item.get("_id"))
        if item.get("archived_at"):
            raise HindranceError(_ARCHIVED_READ_ONLY, 409)
        stored = await db[LINKS].find_one({"_id": link_id, "delay_event_id": item_id})
        if not stored:
            raise HindranceError("Relationship not found", 404)
        if stored.get("removed_at") is not None:
            raise HindranceError("Relationship is already removed", 409)
        revision = int(stored.get("_revision") or 1)
        now = datetime.utcnow()
        change = {"removed_at": now, "removed_by": _actor_id(actor), "removal_reason": reason}
        result = await db[LINKS].update_one(
            {"_id": link_id, "removed_at": None, "_revision": revision},
            {"$set": change, "$inc": {"_revision": 1}},
        )
        if not getattr(result, "matched_count", 0):
            raise HindranceError("Relationship was modified; reload and retry", 409)
        removed = {**stored, **change, "_revision": revision + 1}
        await self.audit.emit(
            action=f"{LINKS}.unlinked",
            actor_id=_actor_id(actor),
            resource_type=LINK_RESOURCE,
            resource_id=link_id,
            organization_id=stored.get("organization_id"),
            project_id=stored.get("project_id"),
            before=stored,
            after=removed,
            reason=reason,
            metadata={"delay_event_id": item_id, "target_type": stored.get("target_type"), "target_id": stored.get("target_id")},
        )
        return await self._link_view(db, removed, actor, policy)

    async def affecting(
        self, actor: Any, target_type: str, target: Dict[str, Any], policy: Any
    ) -> List[Dict[str, Any]]:
        """Reverse lookup: the active register entries linked to one target.

        The caller has authorized the target (its view permission) and register
        access in the target's project.
        """
        db = await self._get_db()
        cursor = db[LINKS].find(
            {
                "organization_id": str(target.get("organization_id") or ""),
                "project_id": str(target.get("project_id") or ""),
                "target_type": target_type,
                "target_id": str(target.get("_id")),
                "removed_at": None,
            }
        ).sort("created_at", 1)
        items: List[Dict[str, Any]] = []
        async for row in cursor:
            hindrance = await self.get(str(row.get("delay_event_id")))
            if not hindrance or hindrance.get("archived_at"):
                continue
            items.append({"link": await self._link_view(db, row, actor, policy), "hindrance": hindrance})
        return items
