"""Backfill existing DMS records into the Mongo evidence graph."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.database import get_database
from ..models.evidence_graph import (
    EventLinkCreate,
    EventLinkStatus,
    EventRelationType,
    EvidenceEntityType,
    ProjectEventCreate,
    ProjectEventStatus,
    ProjectEventType,
)
from .evidence_graph_service import EvidenceGraphService
from .publication_policy import resolve_document_authority


async def _collect_cursor(cursor: Any) -> List[Dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return [dict(item) for item in await cursor.to_list(length=None)]
    return [dict(item) async for item in cursor]


def _first(doc: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        value = doc.get(key)
        if value:
            return value
    return None


class EvidenceGraphBackfillService:
    SOURCE_SPECS = [
        {
            "collection": "documents",
            "entity_type": EvidenceEntityType.DOCUMENT,
            "event_type": ProjectEventType.LETTER,
            "date_fields": ["date", "created_at", "upload_date"],
            "title_fields": ["subject", "letterNo", "filename", "name"],
        },
        {
            "collection": "claims",
            "entity_type": EvidenceEntityType.CLAIM,
            "event_type": ProjectEventType.CLAIM,
            "date_fields": ["event_date", "claim_date", "created_at"],
            "title_fields": ["claim_ref", "title", "subject"],
        },
        {
            "collection": "key_date_milestones",
            "entity_type": EvidenceEntityType.KEY_DATE,
            "event_type": ProjectEventType.KEY_DATE,
            "date_fields": ["current_approved_key_date", "original_planned_key_date", "created_at"],
            "title_fields": ["milestone_ref", "title"],
        },
        {
            "collection": "variations",
            "entity_type": EvidenceEntityType.VARIATION,
            "event_type": ProjectEventType.VARIATION,
            "date_fields": ["variation_date", "event_date", "created_at"],
            "title_fields": ["variation_ref", "title", "subject"],
        },
        {
            "collection": "bank_guarantees",
            "entity_type": EvidenceEntityType.BANK_GUARANTEE,
            "event_type": ProjectEventType.BANK_GUARANTEE,
            "date_fields": ["issue_date", "valid_from", "created_at"],
            "title_fields": ["bg_number", "reference", "title"],
        },
        {
            "collection": "ipc_bills",
            "entity_type": EvidenceEntityType.PAYMENT_EVENT,
            "event_type": ProjectEventType.PAYMENT,
            "date_fields": ["ipc_date", "created_at"],
            "title_fields": ["ipc_number", "ipc_period", "title"],
        },
        {
            "collection": "drawing_references",
            "entity_type": EvidenceEntityType.DRAWING,
            "event_type": ProjectEventType.DRAWING,
            "date_fields": ["issue_date", "received_date", "created_at"],
            "title_fields": ["drawing_number", "title"],
        },
        {
            "collection": "delay_events",
            "entity_type": EvidenceEntityType.DELAY_EVENT,
            "event_type": ProjectEventType.DELAY,
            "date_fields": ["start_date", "created_at"],
            "title_fields": ["delay_ref", "title"],
        },
        {
            "collection": "programme_milestones",
            "entity_type": EvidenceEntityType.PROGRAMME_MILESTONE,
            "event_type": ProjectEventType.MILESTONE,
            "date_fields": ["planned_date", "forecast_date", "created_at"],
            "title_fields": ["milestone_ref", "title"],
        },
    ]

    def __init__(self, db: Any = None) -> None:
        self.db = db

    async def _get_db(self) -> Any:
        return self.db if self.db is not None else await get_database()

    async def run(
        self,
        scope_filter: Dict[str, Any],
        *,
        current_user: Any,
        dry_run: bool = True,
        project_id: Optional[str] = None,
        limit_per_collection: int = 500,
    ) -> Dict[str, Any]:
        db = await self._get_db()
        graph = EvidenceGraphService(db)
        report: Dict[str, Any] = {
            "dry_run": dry_run,
            "sources": {},
            "created_project_events": 0,
            "created_event_links": 0,
            "conflicts": [],
        }
        for spec in self.SOURCE_SPECS:
            collection_name = spec["collection"]
            query = dict(scope_filter or {})
            if project_id:
                query["project_id"] = project_id
            cursor = db[collection_name].find(query).limit(limit_per_collection)
            rows = await _collect_cursor(cursor)
            missing = []
            authority_blocked = 0
            for row in rows:
                entity_id = str(row.get("_id"))
                existing = await db.project_events.find_one(
                    {"source_entity_type": spec["entity_type"].value, "source_entity_id": entity_id}
                )
                if existing:
                    continue
                if collection_name == "documents":
                    authority = await resolve_document_authority(db, entity_id)
                    if not authority.consumable:
                        authority_blocked += 1
                        continue
                missing.append(row)
                if dry_run:
                    continue
                event_type = spec["event_type"]
                if collection_name == "delay_events":
                    # One mapping for the register and its backfill: a hindrance
                    # or constraint is not presumed to be a delay.
                    from .hindrance_register_service import event_type_of, project_event_type_for

                    event_type = project_event_type_for(event_type_of(row))
                event = await graph.create_project_event(
                    ProjectEventCreate(
                        organization_id=row.get("organization_id"),
                        project_id=row.get("project_id"),
                        event_type=event_type,
                        event_date=self._event_date(row, spec["date_fields"]),
                        title=self._title(row, spec["title_fields"], fallback=collection_name),
                        description=row.get("description") or row.get("summary"),
                        party=row.get("party") or row.get("from") or row.get("contractor_name"),
                        package=row.get("package"),
                        impact_area=row.get("location"),
                        source_entity_type=spec["entity_type"],
                        source_entity_id=entity_id,
                        status=ProjectEventStatus.OPEN,
                        confidence=1.0,
                        metadata={"backfilled": True, "source_collection": collection_name},
                    ),
                    current_user,
                )
                report["created_project_events"] += 1
                existing_link = await db.event_links.find_one(
                    {
                        "source_type": EvidenceEntityType.PROJECT_EVENT.value,
                        "source_id": str(event.get("_id")),
                        "target_type": spec["entity_type"].value,
                        "target_id": entity_id,
                    }
                )
                if not existing_link:
                    await graph.suggest_link(
                        EventLinkCreate(
                            organization_id=row.get("organization_id"),
                            project_id=row.get("project_id"),
                            source_type=EvidenceEntityType.PROJECT_EVENT,
                            source_id=str(event.get("_id")),
                            target_type=spec["entity_type"],
                            target_id=entity_id,
                            relation_type=EventRelationType.RELATES_TO,
                            status=EventLinkStatus.USER_VERIFIED,
                            confidence=1.0,
                            evidence_text="Backfilled from existing authoritative register",
                            metadata={"backfilled": True, "source_collection": collection_name},
                        ),
                        current_user=current_user,
                    )
                    report["created_event_links"] += 1
            report["sources"][collection_name] = {
                "scanned": len(rows),
                "missing_project_events": len(missing),
                "authority_blocked": authority_blocked,
                "limited": len(rows) >= limit_per_collection,
            }
        return report

    def _event_date(self, row: Dict[str, Any], fields: List[str]) -> datetime:
        value = _first(row, fields)
        if isinstance(value, datetime):
            return value
        return datetime.utcnow()

    def _title(self, row: Dict[str, Any], fields: List[str], *, fallback: str) -> str:
        first = _first(row, fields)
        if isinstance(first, str) and first.strip():
            second = row.get("title")
            if second and second != first:
                return f"{first} - {second}"[:300]
            return first[:300]
        return f"{fallback} {row.get('_id')}"[:300]
