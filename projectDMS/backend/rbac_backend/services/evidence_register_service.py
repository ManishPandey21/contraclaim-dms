"""CRUD services for evidence graph domain registers."""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Type

from ..core.database import get_database
from ..models.evidence_graph import (
    EventLinkCreate,
    EventRelationType,
    EvidenceEntityType,
    ProjectEventCreate,
    ProjectEventStatus,
    ProjectEventType,
)
from ..models.evidence_registers import (
    DelayEventCreate,
    DelayEventUpdate,
    DrawingReference,
    DrawingReferenceCreate,
    DrawingReferenceUpdate,
    ProgrammeMilestone,
    ProgrammeMilestoneCreate,
    ProgrammeMilestoneUpdate,
)
from .audit_event_service import AuditEventService
from .evidence_graph_service import EvidenceGraphService
from .publication_policy import is_consumable, resolve_canonical_document

logger = logging.getLogger(__name__)


def _actor_id(user: Any) -> Optional[str]:
    return getattr(user, "id", None) or getattr(user, "email", None)


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


async def _collect_cursor(cursor: Any) -> List[Dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return [dict(item) for item in await cursor.to_list(length=None)]
    return [dict(item) async for item in cursor]


class EvidenceRegisterNotFound(Exception):
    pass


class EvidenceRegisterService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    async def _get_db(self) -> Any:
        return self.db if self.db is not None else await get_database()

    async def create_drawing_reference(self, payload: DrawingReferenceCreate, current_user: Any) -> Dict[str, Any]:
        doc = await self._create("drawing_references", DrawingReference, payload, current_user)
        await self._emit_graph_event(
            doc,
            current_user,
            entity_type=EvidenceEntityType.DRAWING,
            event_type=ProjectEventType.DRAWING,
            event_date=doc.get("issue_date") or doc.get("received_date") or doc.get("created_at"),
            title=f"{doc.get('drawing_number')} Rev {doc.get('revision') or '-'} - {doc.get('title')}",
            description=doc.get("metadata", {}).get("description"),
            relation_type=EventRelationType.REFERS_TO,
        )
        return doc

    # `delay_events` belong to the Hindrance & Constraint Register; these
    # methods keep the historical entry points and delegate to its service.

    def _hindrances(self) -> Any:
        from .hindrance_register_service import HindranceRegisterService

        return HindranceRegisterService(self.db)

    async def create_delay_event(self, payload: DelayEventCreate, current_user: Any) -> Dict[str, Any]:
        return await self._hindrances().create(payload, current_user)

    async def create_programme_milestone(self, payload: ProgrammeMilestoneCreate, current_user: Any) -> Dict[str, Any]:
        doc = await self._create("programme_milestones", ProgrammeMilestone, payload, current_user)
        await self._emit_graph_event(
            doc,
            current_user,
            entity_type=EvidenceEntityType.PROGRAMME_MILESTONE,
            event_type=ProjectEventType.MILESTONE,
            event_date=doc.get("planned_date"),
            event_end_date=doc.get("actual_date") or doc.get("forecast_date"),
            title=f"{doc.get('milestone_ref')} - {doc.get('title')}",
            description=doc.get("description"),
            relation_type=EventRelationType.AFFECTS,
        )
        return doc

    async def list_drawing_references(
        self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None, status: Optional[str] = None,
        location: Optional[str] = None, skip: int = 0, limit: int = 200
    ) -> List[Dict[str, Any]]:
        return await self._list("drawing_references", scope_filter, project_id=project_id, status=status, location=location, skip=skip, limit=limit)

    async def list_delay_events(
        self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None, status: Optional[str] = None,
        responsibility: Optional[str] = None, location: Optional[str] = None, skip: int = 0, limit: int = 200
    ) -> List[Dict[str, Any]]:
        rows, _ = await self._hindrances().list_entries(
            scope_filter,
            project_id=project_id,
            status=status,
            responsibility=responsibility,
            location=location,
            skip=skip,
            limit=limit,
        )
        return rows

    async def list_programme_milestones(
        self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None, status: Optional[str] = None,
        milestone_type: Optional[str] = None, location: Optional[str] = None, skip: int = 0, limit: int = 200
    ) -> List[Dict[str, Any]]:
        return await self._list(
            "programme_milestones",
            scope_filter,
            project_id=project_id,
            status=status,
            milestone_type=milestone_type,
            location=location,
            skip=skip,
            limit=limit,
            sort_field="planned_date",
        )

    async def get_drawing_reference(self, item_id: str) -> Optional[Dict[str, Any]]:
        return await self._get("drawing_references", item_id)

    async def get_delay_event(self, item_id: str) -> Optional[Dict[str, Any]]:
        return await self._hindrances().get(item_id)

    async def get_programme_milestone(self, item_id: str) -> Optional[Dict[str, Any]]:
        return await self._get("programme_milestones", item_id)

    async def update_drawing_reference(self, item_id: str, payload: DrawingReferenceUpdate, current_user: Any) -> Dict[str, Any]:
        return await self._update("drawing_references", item_id, payload.model_dump(exclude_unset=True), current_user)

    async def update_delay_event(self, item_id: str, payload: DelayEventUpdate, current_user: Any) -> Dict[str, Any]:
        return await self._hindrances().update(item_id, payload.model_dump(exclude_unset=True), current_user)

    async def update_programme_milestone(self, item_id: str, payload: ProgrammeMilestoneUpdate, current_user: Any) -> Dict[str, Any]:
        return await self._update("programme_milestones", item_id, payload.model_dump(exclude_unset=True), current_user)

    async def _create(self, collection_name: str, model: Type[Any], payload: Any, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        doc = _jsonable(model(**payload.model_dump()).model_dump(by_alias=True))
        if not doc.get("organization_id"):
            doc["organization_id"] = getattr(current_user, "organization_id", None)
        if "linked_document_ids" in doc:
            doc["linked_document_ids"] = await self._authorized_document_links(
                db,
                doc.get("linked_document_ids"),
                organization_id=doc.get("organization_id"),
                project_id=doc.get("project_id"),
            )
        doc["created_at"] = datetime.utcnow()
        doc["created_by"] = _actor_id(current_user)
        result = await db[collection_name].insert_one(doc)
        created = await db[collection_name].find_one({"_id": result.inserted_id}) or doc
        await self.audit.emit(
            action=f"{collection_name}.created",
            actor_id=_actor_id(current_user),
            resource_type=collection_name,
            resource_id=str(created.get("_id")),
            organization_id=created.get("organization_id"),
            project_id=created.get("project_id"),
            after=created,
        )
        return created

    async def _get(self, collection_name: str, item_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        return await self._current_authority_projection(
            db, await db[collection_name].find_one({"_id": item_id})
        )

    async def _list(
        self,
        collection_name: str,
        scope_filter: Dict[str, Any],
        *,
        project_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 200,
        sort_field: str = "created_at",
        **filters: Optional[str],
    ) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        if project_id:
            query["project_id"] = project_id
        for key, value in filters.items():
            if value:
                query[key] = value
        cursor = db[collection_name].find(query).sort(sort_field, -1).skip(skip).limit(limit)
        return await self._current_authority_projections(db, await _collect_cursor(cursor))

    async def _update(self, collection_name: str, item_id: str, payload: Dict[str, Any], current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        existing = await db[collection_name].find_one({"_id": item_id})
        if not existing:
            raise EvidenceRegisterNotFound(item_id)
        # `payload` is `model_dump(exclude_unset=True)`: an omitted field is
        # absent, so a None here is an explicit clear and is written as such.
        # The Update models refuse null for fields that cannot be cleared.
        update = {key: _jsonable(value) for key, value in payload.items()}
        if "linked_document_ids" in update:
            update["linked_document_ids"] = await self._authorized_document_links(
                db,
                update.get("linked_document_ids"),
                organization_id=existing.get("organization_id"),
                project_id=existing.get("project_id"),
            )
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = _actor_id(current_user)
        updated = await db[collection_name].find_one_and_update({"_id": item_id}, {"$set": update}, return_document=True)
        updated = updated or {**existing, **update}
        # `after` is a claim about what the item NOW IS, so it carries the served
        # view: an update that never mentioned `linked_document_ids` must not
        # re-publish a supporter that lost authority in the meantime. `before` is
        # the historical prior state and is recorded exactly as it stood.
        updated = await self._current_authority_projection(db, updated)
        await self.audit.emit(
            action=f"{collection_name}.updated",
            actor_id=_actor_id(current_user),
            resource_type=collection_name,
            resource_id=item_id,
            organization_id=updated.get("organization_id"),
            project_id=updated.get("project_id"),
            before=existing,
            after=updated,
        )
        return updated

    async def _current_authority_projection(
        self, db: Any, item: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """The item as it may be SERVED, not as it is stored.

        `_authorized_document_links` resolved these ids when they were written,
        and that answer expires: the supporting Document can lose publication
        authority — human review, quarantine, deletion — or stop resolving at
        all, long after a human linked it. A stored association is history; only
        a currently authorized one is support, so the question is asked again
        here rather than trusted from the row.

        Storage is untouched. Destroying the stored ids would destroy the
        register's provenance (what was linked, and when) and would make the
        retraction irreversible; filtering at serve time keeps both, and a
        document that regains authority is served again with no re-linking.

        The primitive is the POSITIVE one the write path already uses, on
        purpose. `publication_policy.blocked_document_ids` is subtractive and
        fails OPEN by design — it returns only ids positively resolved AND
        positively unusable — so an orphaned id survives subtraction. Here that
        would serve a supporter whose document no longer exists.
        """
        if not item or "linked_document_ids" not in item:
            return item
        served = dict(item)
        served["linked_document_ids"] = await self._authorized_document_links(
            db,
            item.get("linked_document_ids"),
            organization_id=item.get("organization_id"),
            project_id=item.get("project_id"),
        )
        return served

    async def _current_authority_projections(
        self, db: Any, items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """`_current_authority_projection` across a listing.

        Resolution costs one canonical document lookup per id, so identical
        supporter sets within one page are resolved once. Register listings
        commonly repeat them; distinct sets still cost what the write path costs.
        """
        cache: Dict[Any, List[str]] = {}
        served: List[Dict[str, Any]] = []
        for item in items:
            if not item or "linked_document_ids" not in item:
                served.append(item)
                continue
            key = (
                tuple(str(value) for value in (item.get("linked_document_ids") or [])),
                item.get("organization_id"),
                item.get("project_id"),
            )
            if key not in cache:
                cache[key] = await self._authorized_document_links(
                    db,
                    item.get("linked_document_ids"),
                    organization_id=item.get("organization_id"),
                    project_id=item.get("project_id"),
                )
            row = dict(item)
            row["linked_document_ids"] = list(cache[key])
            served.append(row)
        return served

    async def _authorized_document_links(
        self,
        db: Any,
        document_ids: Any,
        *,
        organization_id: Optional[str],
        project_id: Optional[str],
    ) -> List[str]:
        """Keep independently authorized, in-scope document supporters only."""
        allowed: List[str] = []
        seen = set()
        for value in document_ids or []:
            document_id = str(value or "").strip()
            if not document_id or document_id in seen:
                continue
            seen.add(document_id)
            try:
                document = await resolve_canonical_document(db, document_id)
            except Exception:
                continue
            if not document or not is_consumable(document):
                continue
            if organization_id and str(document.get("organization_id") or "") != str(
                organization_id
            ):
                continue
            if project_id and str(document.get("project_id") or "") != str(project_id):
                continue
            allowed.append(document_id)
        return allowed

    async def _emit_graph_event(
        self,
        doc: Dict[str, Any],
        current_user: Any,
        *,
        entity_type: EvidenceEntityType,
        event_type: ProjectEventType,
        event_date: Optional[datetime],
        title: str,
        description: Optional[str],
        relation_type: EventRelationType,
        event_end_date: Optional[datetime] = None,
    ) -> None:
        try:
            graph = EvidenceGraphService(await self._get_db())
            event = await graph.create_project_event(
                ProjectEventCreate(
                    organization_id=doc.get("organization_id"),
                    project_id=doc.get("project_id"),
                    event_type=event_type,
                    event_date=event_date or datetime.utcnow(),
                    event_end_date=event_end_date,
                    title=title[:300],
                    description=description,
                    package=doc.get("package"),
                    impact_area=doc.get("location"),
                    source_entity_type=entity_type,
                    source_entity_id=str(doc.get("_id")),
                    status=ProjectEventStatus.OPEN,
                    confidence=1.0,
                    metadata={
                        "register_source": entity_type.value,
                        "reference": doc.get("drawing_number") or doc.get("delay_ref") or doc.get("milestone_ref"),
                        "location": doc.get("location"),
                        "status": _enum_value(doc.get("status")),
                        "responsibility": _enum_value(doc.get("responsibility")),
                        "milestone_type": _enum_value(doc.get("milestone_type")),
                    },
                ),
                current_user,
            )
            await graph.suggest_link(
                EventLinkCreate(
                    organization_id=doc.get("organization_id"),
                    project_id=doc.get("project_id"),
                    source_type=EvidenceEntityType.PROJECT_EVENT,
                    source_id=str(event.get("_id")),
                    target_type=entity_type,
                    target_id=str(doc.get("_id")),
                    relation_type=relation_type,
                    confidence=1.0,
                    evidence_text=title,
                ),
                current_user=current_user,
            )
        except Exception:
            # Mongo is canonical; the graph row is derived. Record the miss
            # visibly rather than returning as if the projection had succeeded.
            logger.warning(
                "Evidence graph projection failed for %s %s",
                entity_type.value,
                doc.get("_id"),
                exc_info=True,
            )
