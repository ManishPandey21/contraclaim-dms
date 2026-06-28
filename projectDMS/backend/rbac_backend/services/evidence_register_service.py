"""CRUD services for evidence graph domain registers."""

from __future__ import annotations

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
    DelayEvent,
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

    async def create_delay_event(self, payload: DelayEventCreate, current_user: Any) -> Dict[str, Any]:
        doc = await self._create("delay_events", DelayEvent, payload, current_user)
        await self._emit_graph_event(
            doc,
            current_user,
            entity_type=EvidenceEntityType.DELAY_EVENT,
            event_type=ProjectEventType.DELAY,
            event_date=doc.get("start_date"),
            event_end_date=doc.get("end_date"),
            title=f"{doc.get('delay_ref')} - {doc.get('title')}",
            description=doc.get("description"),
            relation_type=EventRelationType.CAUSES_DELAY,
        )
        return doc

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
        return await self._list(
            "delay_events",
            scope_filter,
            project_id=project_id,
            status=status,
            responsibility=responsibility,
            location=location,
            skip=skip,
            limit=limit,
            sort_field="start_date",
        )

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
        return await self._get("delay_events", item_id)

    async def get_programme_milestone(self, item_id: str) -> Optional[Dict[str, Any]]:
        return await self._get("programme_milestones", item_id)

    async def update_drawing_reference(self, item_id: str, payload: DrawingReferenceUpdate, current_user: Any) -> Dict[str, Any]:
        return await self._update("drawing_references", item_id, payload.model_dump(exclude_unset=True), current_user)

    async def update_delay_event(self, item_id: str, payload: DelayEventUpdate, current_user: Any) -> Dict[str, Any]:
        return await self._update("delay_events", item_id, payload.model_dump(exclude_unset=True), current_user)

    async def update_programme_milestone(self, item_id: str, payload: ProgrammeMilestoneUpdate, current_user: Any) -> Dict[str, Any]:
        return await self._update("programme_milestones", item_id, payload.model_dump(exclude_unset=True), current_user)

    async def _create(self, collection_name: str, model: Type[Any], payload: Any, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        doc = _jsonable(model(**payload.model_dump()).model_dump(by_alias=True))
        if not doc.get("organization_id"):
            doc["organization_id"] = getattr(current_user, "organization_id", None)
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
        return await db[collection_name].find_one({"_id": item_id})

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
        return await _collect_cursor(cursor)

    async def _update(self, collection_name: str, item_id: str, payload: Dict[str, Any], current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        existing = await db[collection_name].find_one({"_id": item_id})
        if not existing:
            raise EvidenceRegisterNotFound(item_id)
        update = {key: _jsonable(value) for key, value in payload.items() if value is not None}
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = _actor_id(current_user)
        updated = await db[collection_name].find_one_and_update({"_id": item_id}, {"$set": update}, return_document=True)
        updated = updated or {**existing, **update}
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
            return
