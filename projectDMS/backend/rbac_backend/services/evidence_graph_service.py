"""Mongo-backed evidence graph service.

MongoDB is the durable source of truth. FalkorDB can mirror verified graph data,
but link decisions and audit history live here as append-only revisions.
"""

from __future__ import annotations

import logging
import hashlib
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..core.database import get_database
from ..models.document_metadata import METADATA_EXTRACTION_PROMPT_VERSION
from ..models.evidence_graph import (
    AIExtraction,
    AIExtractionCreate,
    AIExtractionStatus,
    EventLink,
    EventLinkCreate,
    EventLinkStatus,
    EventRelationType,
    EvidenceEntityType,
    ProjectEvent,
    ProjectEventCreate,
    ProjectEventStatus,
    ProjectEventType,
    SourceSpan,
    TimelineEvent,
    TimelineResponse,
    TimelineSummary,
)
from .audit_event_service import AuditEventService
from .source_text import select_body_text


logger = logging.getLogger(__name__)


class EvidenceGraphError(Exception):
    pass


class EvidenceGraphNotFound(EvidenceGraphError):
    pass


def _actor_id(user: Any) -> Optional[str]:
    return getattr(user, "id", None) or getattr(user, "email", None)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _document_body(document_data: Dict[str, Any], metadata: Any) -> str:
    """The letter text evidence is derived from: source text first.

    ``metadata.full_content`` is the LLM's retyped Item 25; source spans, the
    content hash and the claim/delay signals must come from the letter's own
    text whenever the document carries it, and use Item 25 only as the
    fallback when it does not.
    """
    return select_body_text(document_data, include_summary=False) or str(
        getattr(metadata, "full_content", None) or ""
    )


def _cursor_to_list(cursor: Any) -> Any:
    if hasattr(cursor, "to_list"):
        return cursor.to_list(length=None)
    return None


async def _collect_cursor(cursor: Any) -> List[Dict[str, Any]]:
    to_list = _cursor_to_list(cursor)
    if to_list is not None:
        return [dict(item) for item in await to_list]
    return [dict(item) async for item in cursor]


class EvidenceGraphService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        return await get_database()

    async def create_project_event(self, payload: ProjectEventCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        doc = ProjectEvent(**payload.model_dump()).model_dump(by_alias=True)
        if not doc.get("organization_id"):
            doc["organization_id"] = getattr(current_user, "organization_id", None)
        doc["created_at"] = datetime.utcnow()
        doc["created_by"] = _actor_id(current_user)
        result = await db.project_events.insert_one(doc)
        created = await db.project_events.find_one({"_id": result.inserted_id}) or doc
        await self.audit.emit(
            action="project_event.created",
            actor_id=_actor_id(current_user),
            resource_type="project_event",
            resource_id=str(created.get("_id")),
            organization_id=created.get("organization_id"),
            project_id=created.get("project_id"),
            after=created,
        )
        return created

    async def get_project_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        record = await db.project_events.find_one({"_id": event_id})
        if not record:
            return None
        from .publication_policy import safe_event_records

        return (await safe_event_records(db, [record], ("description", "title"), dict_fields=("metadata",)))[0]

    async def list_project_events(
        self,
        scope_filter: Dict[str, Any],
        *,
        event_type: Optional[str] = None,
        status: Optional[str] = None,
        package: Optional[str] = None,
        party: Optional[str] = None,
        claim_type: Optional[str] = None,
        location: Optional[str] = None,
        delay_responsibility: Optional[str] = None,
        payment_status: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        if event_type:
            query["event_type"] = event_type
        if status:
            query["status"] = status
        if package:
            query["package"] = package
        if party:
            query["party"] = party
        for key, value in {
            "claim_type": claim_type,
            "location": location,
            "delay_responsibility": delay_responsibility,
            "payment_status": payment_status,
        }.items():
            if value:
                query[f"metadata.{key}"] = value
        if date_from or date_to:
            range_query: Dict[str, Any] = {}
            if date_from:
                range_query["$gte"] = date_from
            if date_to:
                range_query["$lte"] = date_to
            query["event_date"] = range_query
        cursor = db.project_events.find(query).sort("event_date", -1).skip(skip).limit(limit)
        from .publication_policy import safe_event_records

        return await safe_event_records(db, await _collect_cursor(cursor), ("description", "title"), dict_fields=("metadata",))

    async def create_ai_extraction(self, payload: AIExtractionCreate, current_user: Any = None) -> Dict[str, Any]:
        db = await self._get_db()
        existing = await db.ai_extractions.find_one(
            {
                "source_document_id": payload.source_document_id,
                "content_hash": payload.content_hash,
                "schema_version": payload.schema_version,
            }
        )
        if existing:
            existing = dict(existing)
            existing["_existing"] = True
            return existing
        doc = AIExtraction(**payload.model_dump()).model_dump(by_alias=True)
        doc["created_by"] = _actor_id(current_user)
        result = await db.ai_extractions.insert_one(doc)
        return await db.ai_extractions.find_one({"_id": result.inserted_id}) or doc

    async def suggest_link(self, payload: EventLinkCreate, current_user: Any = None) -> Dict[str, Any]:
        self._validate_relation(payload)
        status = payload.status or EventLinkStatus.AI_SUGGESTED
        return await self._append_link_revision(payload, status=status, current_user=current_user)

    async def verify_link(
        self,
        link_group_id: str,
        current_user: Any,
        *,
        approved: bool = False,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        status = EventLinkStatus.APPROVED if approved else EventLinkStatus.USER_VERIFIED
        return await self._decide_link(link_group_id, status, current_user, note=note)

    async def reject_link(self, link_group_id: str, current_user: Any, *, note: Optional[str] = None) -> Dict[str, Any]:
        return await self._decide_link(link_group_id, EventLinkStatus.REJECTED, current_user, note=note)

    async def approve_link(self, link_group_id: str, current_user: Any, *, note: Optional[str] = None) -> Dict[str, Any]:
        return await self._decide_link(link_group_id, EventLinkStatus.APPROVED, current_user, note=note)

    def _validate_relation(self, payload: EventLinkCreate) -> None:
        source_type = _enum_value(payload.source_type)
        target_type = _enum_value(payload.target_type)
        relation = _enum_value(payload.relation_type)
        if source_type == target_type and payload.source_id == payload.target_id:
            raise EvidenceGraphError("Evidence graph links cannot point to themselves")
        allowed_targets = {
            EventRelationType.GOVERNED_BY.value: {EvidenceEntityType.CLAUSE.value},
            EventRelationType.CAUSES_DELAY.value: {
                EvidenceEntityType.DELAY_EVENT.value,
                EvidenceEntityType.PROGRAMME_MILESTONE.value,
                EvidenceEntityType.KEY_DATE.value,
                EvidenceEntityType.UNRESOLVED_REFERENCE.value,
            },
            EventRelationType.CAUSES_VARIATION.value: {
                EvidenceEntityType.VARIATION.value,
                EvidenceEntityType.UNRESOLVED_REFERENCE.value,
            },
            EventRelationType.SUPPORTS_MEASUREMENT.value: {
                EvidenceEntityType.PAYMENT_EVENT.value,
                EvidenceEntityType.DRAWING.value,
                EvidenceEntityType.UNRESOLVED_REFERENCE.value,
            },
        }.get(relation)
        if allowed_targets and target_type not in allowed_targets:
            raise EvidenceGraphError(f"{relation} cannot target {target_type}")

    async def _decide_link(
        self,
        link_group_id: str,
        status: EventLinkStatus,
        current_user: Any,
        *,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        latest = await self.get_latest_link(link_group_id)
        if not latest:
            raise EvidenceGraphNotFound(f"Link group not found: {link_group_id}")
        payload_data = {
            key: value
            for key, value in latest.items()
            if key
            in {
                "organization_id",
                "project_id",
                "source_type",
                "source_id",
                "target_type",
                "target_id",
                "relation_type",
                "confidence",
                "evidence_text",
                "source_spans",
                "ai_extraction_id",
                "metadata",
            }
        }
        metadata = dict(payload_data.get("metadata") or {})
        if note:
            metadata["decision_note"] = note
        payload_data["metadata"] = metadata
        payload = EventLinkCreate(link_group_id=link_group_id, **payload_data)
        self._validate_relation(payload)
        return await self._append_link_revision(payload, status=status, current_user=current_user)

    async def _append_link_revision(
        self,
        payload: EventLinkCreate,
        *,
        status: EventLinkStatus,
        current_user: Any = None,
    ) -> Dict[str, Any]:
        db = await self._get_db()
        group_id = payload.link_group_id or ""
        latest_revision = 0
        if group_id:
            latest = await self.get_latest_link(group_id)
            latest_revision = int((latest or {}).get("revision") or 0)
        payload_data = payload.model_dump(exclude={"link_group_id", "status"})
        if not payload_data.get("organization_id"):
            payload_data["organization_id"] = getattr(current_user, "organization_id", None)
        doc = EventLink(
            **payload_data,
            link_group_id=group_id or str(uuid.uuid4()),
            revision=latest_revision + 1,
            status=status,
            created_by=_actor_id(current_user),
        ).model_dump(by_alias=True)
        result = await db.event_links.insert_one(doc)
        created = await db.event_links.find_one({"_id": result.inserted_id}) or doc
        await self.audit.emit(
            action=f"event_link.{status.value}",
            actor_id=_actor_id(current_user),
            resource_type="event_link",
            resource_id=str(created.get("_id")),
            organization_id=created.get("organization_id"),
            project_id=created.get("project_id"),
            after=created,
        )
        return created

    async def get_latest_link(self, link_group_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        cursor = db.event_links.find({"link_group_id": link_group_id}).sort("revision", -1).limit(1)
        rows = await _collect_cursor(cursor)
        return rows[0] if rows else None

    async def get_link_history(self, scope_filter: Dict[str, Any], link_group_id: str) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query = dict(scope_filter or {})
        query["link_group_id"] = link_group_id
        cursor = db.event_links.find(query).sort("revision", 1)
        # Same gate as `list_links`: a link whose document target is blocked must
        # not serve that document's evidence_text/source_spans, even on the
        # history/audit surface.
        from .publication_policy import safe_event_records

        return await safe_event_records(
            db, await _collect_cursor(cursor), ("evidence_text",), span_fields=("source_spans",)
        )

    async def list_links(
        self,
        scope_filter: Dict[str, Any],
        *,
        status: Optional[str] = None,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        latest_only: bool = True,
        skip: int = 0,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        if status:
            query["status"] = status
        if source_type:
            query["source_type"] = source_type
        if source_id:
            query["source_id"] = source_id
        if target_type:
            query["target_type"] = target_type
        if target_id:
            query["target_id"] = target_id
        cursor = db.event_links.find(query).sort("created_at", -1).skip(skip).limit(limit)
        links = await _collect_cursor(cursor)
        from .publication_policy import safe_event_records

        links = await safe_event_records(db, links, ("evidence_text",), span_fields=("source_spans",))
        if not latest_only:
            return links
        return self._latest_by_group(links)

    def _latest_by_group(self, links: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        latest: Dict[str, Dict[str, Any]] = {}
        for link in links:
            group_id = str(link.get("link_group_id") or link.get("_id"))
            current = latest.get(group_id)
            if current is None or int(link.get("revision") or 0) > int(current.get("revision") or 0):
                latest[group_id] = link
        return sorted(latest.values(), key=lambda item: item.get("created_at") or datetime.min, reverse=True)

    async def downstream_links(
        self,
        scope_filter: Dict[str, Any],
        *,
        source_type: Optional[str] = None,
        source_id: Optional[str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        include_ai_suggested: bool = False,
        skip: int = 0,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Latest links safe for downstream use.

        Downstream modules consume verified/approved evidence by default. Review
        screens may opt into AI suggestions, but rejected links are never returned.
        """
        links = await self.list_links(
            scope_filter,
            source_type=source_type,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            latest_only=True,
            limit=10000,
        )
        allowed = {EventLinkStatus.USER_VERIFIED.value, EventLinkStatus.APPROVED.value}
        if include_ai_suggested:
            allowed.add(EventLinkStatus.AI_SUGGESTED.value)
        filtered = [link for link in links if _enum_value(link.get("status")) in allowed]
        return filtered[skip: skip + limit]

    async def timeline(
        self,
        scope_filter: Dict[str, Any],
        *,
        event_type: Optional[str] = None,
        link_status: Optional[str] = None,
        claim_type: Optional[str] = None,
        clause: Optional[str] = None,
        drawing: Optional[str] = None,
        key_date: Optional[str] = None,
        delay_responsibility: Optional[str] = None,
        payment_status: Optional[str] = None,
        location: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        package: Optional[str] = None,
        party: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> TimelineResponse:
        events = await self.list_project_events(
            scope_filter,
            event_type=event_type,
            package=package,
            party=party,
            claim_type=claim_type,
            location=location,
            delay_responsibility=delay_responsibility,
            payment_status=payment_status,
            date_from=date_from,
            date_to=date_to,
            skip=skip,
            limit=limit + 1,
        )
        has_more = len(events) > limit
        events = events[:limit]
        events = self._filter_events_by_metadata_refs(
            events,
            [
                (EvidenceEntityType.CLAUSE.value, clause),
                (EvidenceEntityType.DRAWING.value, drawing),
                (EvidenceEntityType.KEY_DATE.value, key_date),
            ],
        )
        event_ids = {str(event.get("_id")) for event in events}
        links = await self.list_links(scope_filter, status=link_status, latest_only=True, limit=5000)
        relevant_links = [
            link
            for link in links
            if (_enum_value(link.get("source_type")) == EvidenceEntityType.PROJECT_EVENT.value and str(link.get("source_id")) in event_ids)
            or (_enum_value(link.get("target_type")) == EvidenceEntityType.PROJECT_EVENT.value and str(link.get("target_id")) in event_ids)
        ]
        relevant_links = self._filter_links_by_targets(
            relevant_links,
            clause=clause,
            drawing=drawing,
            key_date=key_date,
        )
        if clause or drawing or key_date:
            linked_event_ids = self._event_ids_for_links(relevant_links)
            events = [event for event in events if str(event.get("_id")) in linked_event_ids]
            event_ids = {str(event.get("_id")) for event in events}
            relevant_links = [
                link
                for link in relevant_links
                if (_enum_value(link.get("source_type")) == EvidenceEntityType.PROJECT_EVENT.value and str(link.get("source_id")) in event_ids)
                or (_enum_value(link.get("target_type")) == EvidenceEntityType.PROJECT_EVENT.value and str(link.get("target_id")) in event_ids)
            ]

        links_by_event: Dict[str, List[Dict[str, Any]]] = {event_id: [] for event_id in event_ids}
        for link in relevant_links:
            if _enum_value(link.get("source_type")) == EvidenceEntityType.PROJECT_EVENT.value:
                links_by_event.setdefault(str(link.get("source_id")), []).append(link)
            if _enum_value(link.get("target_type")) == EvidenceEntityType.PROJECT_EVENT.value:
                links_by_event.setdefault(str(link.get("target_id")), []).append(link)

        status_counts: Dict[str, int] = {}
        for link in relevant_links:
            key = str(_enum_value(link.get("status") or ""))
            status_counts[key] = status_counts.get(key, 0) + 1

        timeline_events = [
            TimelineEvent(**event, links=[EventLink(**link) for link in links_by_event.get(str(event.get("_id")), [])])
            for event in events
        ]
        summary = TimelineSummary(
            total_events=len(timeline_events),
            graph_links=len(relevant_links),
            ai_suggested=status_counts.get(EventLinkStatus.AI_SUGGESTED.value, 0),
            user_verified=status_counts.get(EventLinkStatus.USER_VERIFIED.value, 0),
            approved=status_counts.get(EventLinkStatus.APPROVED.value, 0),
            rejected=status_counts.get(EventLinkStatus.REJECTED.value, 0),
            awaiting_review=status_counts.get(EventLinkStatus.AI_SUGGESTED.value, 0),
        )
        return TimelineResponse(events=timeline_events, summary=summary, skip=skip, limit=limit, has_more=has_more)

    def _filter_events_by_metadata_refs(
        self,
        events: List[Dict[str, Any]],
        filters: List[Tuple[str, Optional[str]]],
    ) -> List[Dict[str, Any]]:
        active = [(kind, value.lower()) for kind, value in filters if value]
        if not active:
            return events
        metadata_keys = {
            EvidenceEntityType.CLAUSE.value: ("clauses",),
            EvidenceEntityType.DRAWING.value: ("drawing_refs", "drawings"),
            EvidenceEntityType.KEY_DATE.value: ("key_date_refs", "milestone_refs"),
        }
        filtered: List[Dict[str, Any]] = []
        for event in events:
            metadata = event.get("metadata") or {}
            keep = True
            for kind, expected in active:
                values: List[str] = []
                for key in metadata_keys.get(kind, ()):
                    raw = metadata.get(key) or []
                    if isinstance(raw, str):
                        values.append(raw)
                    else:
                        values.extend([str(item) for item in raw])
                if not any(expected in value.lower() for value in values):
                    keep = False
                    break
            if keep:
                filtered.append(event)
        return filtered

    def _filter_links_by_targets(
        self,
        links: List[Dict[str, Any]],
        *,
        clause: Optional[str] = None,
        drawing: Optional[str] = None,
        key_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        active = [
            (EvidenceEntityType.CLAUSE.value, clause.lower() if clause else None),
            (EvidenceEntityType.DRAWING.value, drawing.lower() if drawing else None),
            (EvidenceEntityType.KEY_DATE.value, key_date.lower() if key_date else None),
        ]
        active = [(kind, value) for kind, value in active if value]
        if not active:
            return links
        filtered = []
        for link in links:
            target_type = str(_enum_value(link.get("target_type")) or "")
            target_id = str(link.get("target_id") or "").lower()
            if any(target_type == kind and expected in target_id for kind, expected in active):
                filtered.append(link)
        return filtered

    def _event_ids_for_links(self, links: List[Dict[str, Any]]) -> set[str]:
        event_ids: set[str] = set()
        for link in links:
            if _enum_value(link.get("source_type")) == EvidenceEntityType.PROJECT_EVENT.value:
                event_ids.add(str(link.get("source_id")))
            if _enum_value(link.get("target_type")) == EvidenceEntityType.PROJECT_EVENT.value:
                event_ids.add(str(link.get("target_id")))
        return event_ids

    async def ingest_document_metadata(
        self,
        *,
        document_id: str,
        document_data: Dict[str, Any],
        metadata: Any,
        metadata_source: Optional[str],
        upload_type: Optional[str],
        current_user: Any = None,
    ) -> Optional[Dict[str, Any]]:
        org_id = str(document_data.get("organization_id") or "")
        project_id = str(document_data.get("project_id") or "")
        # Defence in depth. The production call site is already inside the
        # publishable-gated block, but this function would otherwise happily
        # build persistent AI-extraction and project-event records from any
        # dict handed to it. Derived artifacts retain source_document_id, so a
        # consumer can re-establish authority - but they should not be created
        # from blocked content in the first place.
        from .publication_policy import is_consumable

        if not is_consumable(document_data):
            logger.info(
                "[evidence_graph] Skipping ingestion for %s: source is not consumable",
                document_id,
            )
            return None

        full_text = _document_body(document_data, metadata)
        content_seed = "|".join(
            [
                document_id,
                full_text[:20000],
                str(getattr(metadata, "summary", None) or ""),
                str(getattr(metadata, "letter_no", None) or document_data.get("letterNo") or ""),
            ]
        )
        content_hash = hashlib.sha256(content_seed.encode("utf-8", errors="ignore")).hexdigest()
        parsed = self._parsed_metadata(document_data, metadata, upload_type)
        extraction = await self.create_ai_extraction(
            AIExtractionCreate(
                organization_id=org_id or None,
                project_id=project_id or None,
                source_document_id=document_id,
                content_hash=content_hash,
                schema_version="evidence_graph.v2",
                model=metadata_source,
                prompt_version=METADATA_EXTRACTION_PROMPT_VERSION,
                raw_output=parsed,
                parsed_output=parsed,
                confidence=0.75 if metadata_source else None,
                status=AIExtractionStatus.APPLIED,
            ),
            current_user=current_user,
        )
        event = await self._get_or_create_document_event(
            document_id=document_id,
            document_data=document_data,
            parsed=parsed,
            ai_extraction_id=str(extraction.get("_id")),
            current_user=current_user,
        )
        if not extraction.get("_existing"):
            await self._suggest_metadata_links(
                event_id=str(event.get("_id")),
                extraction_id=str(extraction.get("_id")),
                org_id=org_id,
                project_id=project_id,
                parsed=parsed,
                current_user=current_user,
            )
        return extraction

    def _parsed_metadata(
        self, document_data: Dict[str, Any], metadata: Any, upload_type: Optional[str]
    ) -> Dict[str, Any]:
        subject = str(getattr(metadata, "subject", None) or document_data.get("subject") or "")
        summary = str(getattr(metadata, "summary", None) or document_data.get("summary") or "")
        full_text = _document_body(document_data, metadata)
        refs = [dict(item) for item in (document_data.get("reference") or []) if isinstance(item, dict)]
        clause_values = list(getattr(metadata, "contractual_clauses", None) or document_data.get("contractual_clauses") or [])
        keywords = list(getattr(metadata, "keywords", None) or document_data.get("keywords") or [])
        additional_keywords = list(
            getattr(metadata, "additional_keywords", None) or document_data.get("additional_keywords") or []
        )
        text = " ".join([subject, summary, full_text[:12000]])
        # Reference and span extraction cite the letter; the summary in `text`
        # is classification context only, used for refs when there is no body.
        ref_text = " ".join([subject, full_text[:12000]]) if full_text else text
        return {
            "document_class": self._classify_document(text, upload_type),
            "letter_no": getattr(metadata, "letter_no", None) or document_data.get("letterNo"),
            "subject": subject,
            "summary": summary,
            "date": str(document_data.get("date") or ""),
            "from": getattr(metadata, "from_company", None) or document_data.get("from"),
            "to": getattr(metadata, "to_company", None) or document_data.get("to"),
            "party": getattr(metadata, "from_company", None) or document_data.get("from"),
            "package": document_data.get("package") or document_data.get("package_name"),
            "location": self._first_value(document_data, metadata, "location"),
            "specific_area": self._first_value(document_data, metadata, "specific_area"),
            "asset_type": self._first_value(document_data, metadata, "asset_type"),
            "chainage_from": self._first_value(document_data, metadata, "chainage_from"),
            "chainage_to": self._first_value(document_data, metadata, "chainage_to"),
            "work_type": self._first_value(document_data, metadata, "work_type"),
            "issue_nature": self._first_value(document_data, metadata, "issue_nature"),
            "claim_type": self._first_value(document_data, metadata, "claim_category") or self._extract_claim_type(ref_text),
            "delay_responsibility": self._first_value(document_data, metadata, "alleged_responsibility") or self._extract_delay_responsibility(ref_text),
            "priority": self._first_value(document_data, metadata, "priority"),
            "linked_event_suggested": self._first_value(document_data, metadata, "linked_event_suggested"),
            "reference_chain": self._first_value(document_data, metadata, "reference_chain"),
            "payment_status": self._extract_payment_status(ref_text),
            "references": refs,
            "clauses": self._extract_clauses(ref_text, clause_values),
            "keywords": keywords,
            "additional_keywords": additional_keywords,
            "drawing_refs": self._extract_pattern(ref_text, r"\b(?:DWG|DRG|GFC|IFC)[-/ ]?[A-Z0-9][A-Z0-9./_-]{2,}\b"),
            "payment_refs": self._extract_pattern(ref_text, r"\b(?:IPC|IP|RA|BILL)[-/ ]?\d+[A-Z0-9./_-]*\b"),
            "milestone_refs": self._extract_pattern(ref_text, r"\b(?:KD|MS|M)[-/ ]?\d+[A-Z0-9./_-]*\b"),
            "delay_refs": self._extract_pattern(ref_text, r"\b(?:DEL|DLY|D)[-/ ]?\d+[A-Z0-9./_-]*\b"),
            "source_spans": self._extract_source_spans(full_text or text),
            "tags": document_data.get("tags") or [],
            "extracted_tags": getattr(metadata, "tags", None) or document_data.get("extracted_tags") or [],
            "extracted_subTags": getattr(metadata, "sub_tags", None) or document_data.get("extracted_subTags") or [],
        }

    def _first_value(self, document_data: Dict[str, Any], metadata: Any, field: str) -> Optional[str]:
        return getattr(metadata, field, None) or document_data.get(field)

    def _classify_document(self, text: str, upload_type: Optional[str]) -> str:
        lowered = text.lower()
        candidates: List[Tuple[str, Tuple[str, ...]]] = [
            ("eot_claim", ("extension of time", " eot", "delay claim")),
            ("variation", ("variation", "change order", "vo ")),
            ("payment", ("ipc", "interim payment", "payment certificate", "bill")),
            ("bg", ("bank guarantee", "performance security")),
            ("dlp", ("defect liability", "dlp")),
            ("toc", ("taking over", "toc")),
            ("instruction", ("instruction", "directed", "engineer instructs")),
            ("warning", ("warning", "notice to correct", "default")),
            ("notice", ("notice", "notification")),
            ("programme", ("programme", "program", "baseline schedule")),
            ("drawing_transmittal", ("drawing", "gfc", "ifc", "transmittal")),
            ("meeting_record", ("minutes of meeting", "mom", "meeting")),
        ]
        for label, terms in candidates:
            if any(term in lowered for term in terms):
                return label
        return "letter" if upload_type in {"incoming", "outgoing"} else "other"

    def _extract_claim_type(self, text: str) -> Optional[str]:
        lowered = text.lower()
        if "extension of time" in lowered or " eot" in lowered:
            return "eot"
        if "loss and expense" in lowered or "cost claim" in lowered:
            return "loss_expense"
        if "variation" in lowered:
            return "variation"
        return None

    def _extract_delay_responsibility(self, text: str) -> Optional[str]:
        lowered = text.lower()
        if "employer delay" in lowered or "late issue" in lowered or "engineer delay" in lowered:
            return "employer"
        if "contractor delay" in lowered:
            return "contractor"
        if "concurrent delay" in lowered:
            return "concurrent"
        return None

    def _extract_payment_status(self, text: str) -> Optional[str]:
        lowered = text.lower()
        if "disputed" in lowered or "withheld" in lowered:
            return "disputed"
        if "certified" in lowered or "approved" in lowered:
            return "certified"
        if "paid" in lowered:
            return "paid"
        return None

    def _extract_clauses(self, text: str, existing: Iterable[Any]) -> List[str]:
        clauses = {str(item).strip() for item in existing if str(item).strip()}
        for match in re.findall(r"\b(?:GCC|SCC|Clause)\s*[-:]?\s*\d+(?:\.\d+)*[A-Za-z]?\b", text, flags=re.I):
            clauses.add(re.sub(r"\s+", " ", match).strip())
        return sorted(clauses)

    def _extract_pattern(self, text: str, pattern: str) -> List[str]:
        return sorted({match.strip() for match in re.findall(pattern, text, flags=re.I)})

    def _extract_source_spans(self, text: str) -> List[Dict[str, Any]]:
        spans = []
        for pattern in (
            r"\b(?:GCC|SCC|Clause)\s*[-:]?\s*\d+(?:\.\d+)*[A-Za-z]?\b",
            r"\b(?:DWG|DRG|GFC|IFC)[-/ ]?[A-Z0-9][A-Z0-9./_-]{2,}\b",
            r"\b(?:IPC|IP|RA|BILL)[-/ ]?\d+[A-Z0-9./_-]*\b",
        ):
            for match in re.finditer(pattern, text, flags=re.I):
                spans.append(SourceSpan(start=match.start(), end=match.end(), text=match.group(0)).model_dump())
        return spans[:20]

    async def _get_or_create_document_event(
        self,
        *,
        document_id: str,
        document_data: Dict[str, Any],
        parsed: Dict[str, Any],
        ai_extraction_id: str,
        current_user: Any,
    ) -> Dict[str, Any]:
        db = await self._get_db()
        existing = await db.project_events.find_one(
            {"source_entity_type": EvidenceEntityType.DOCUMENT.value, "source_entity_id": document_id}
        )
        if existing:
            return existing
        event_date = document_data.get("date")
        if not isinstance(event_date, datetime):
            event_date = datetime.utcnow()
        title = parsed.get("linked_event_suggested") or parsed.get("subject") or parsed.get("letter_no") or document_data.get("filename") or "Document event"
        event_type = ProjectEventType.LETTER
        if parsed.get("document_class") in {"instruction", "meeting_record", "payment", "variation", "bg", "eot_claim", "programme", "drawing_transmittal"}:
            event_type = {
                "instruction": ProjectEventType.INSTRUCTION,
                "meeting_record": ProjectEventType.MEETING,
                "payment": ProjectEventType.PAYMENT,
                "variation": ProjectEventType.VARIATION,
                "bg": ProjectEventType.BANK_GUARANTEE,
                "eot_claim": ProjectEventType.CLAIM,
                "programme": ProjectEventType.MILESTONE,
                "drawing_transmittal": ProjectEventType.DRAWING,
            }[parsed["document_class"]]
        return await self.create_project_event(
            ProjectEventCreate(
                organization_id=document_data.get("organization_id"),
                project_id=document_data.get("project_id"),
                event_type=event_type,
                event_date=event_date,
                title=str(title)[:300],
                description=parsed.get("summary"),
                party=parsed.get("party"),
                package=parsed.get("package"),
                impact_area=parsed.get("location"),
                source_entity_type=EvidenceEntityType.DOCUMENT,
                source_entity_id=document_id,
                status=ProjectEventStatus.OPEN,
                confidence=0.75,
                ai_extraction_id=ai_extraction_id,
                metadata={
                    "document_class": parsed.get("document_class"),
                    "letter_no": parsed.get("letter_no"),
                    "claim_type": parsed.get("claim_type"),
                    "delay_responsibility": parsed.get("delay_responsibility"),
                    "payment_status": parsed.get("payment_status"),
                    "location": parsed.get("location"),
                    "specific_area": parsed.get("specific_area"),
                    "asset_type": parsed.get("asset_type"),
                    "chainage_from": parsed.get("chainage_from"),
                    "chainage_to": parsed.get("chainage_to"),
                    "work_type": parsed.get("work_type"),
                    "issue_nature": parsed.get("issue_nature"),
                    "priority": parsed.get("priority"),
                    "reference_chain": parsed.get("reference_chain"),
                    "keywords": parsed.get("keywords") or [],
                    "additional_keywords": parsed.get("additional_keywords") or [],
                    "extracted_tags": parsed.get("extracted_tags") or [],
                    "extracted_subTags": parsed.get("extracted_subTags") or [],
                    "clauses": parsed.get("clauses") or [],
                    "drawing_refs": parsed.get("drawing_refs") or [],
                    "payment_refs": parsed.get("payment_refs") or [],
                    "milestone_refs": parsed.get("milestone_refs") or [],
                    "key_date_refs": parsed.get("milestone_refs") or [],
                    "delay_refs": parsed.get("delay_refs") or [],
                },
            ),
            current_user,
        )

    async def _suggest_metadata_links(
        self,
        *,
        event_id: str,
        extraction_id: str,
        org_id: str,
        project_id: str,
        parsed: Dict[str, Any],
        current_user: Any,
    ) -> None:
        for clause in parsed.get("clauses") or []:
            await self._suggest_once(
                EventLinkCreate(
                    organization_id=org_id or None,
                    project_id=project_id or None,
                    source_type=EvidenceEntityType.PROJECT_EVENT,
                    source_id=event_id,
                    target_type=EvidenceEntityType.CLAUSE,
                    target_id=str(clause),
                    relation_type=EventRelationType.GOVERNED_BY,
                    confidence=0.8,
                    evidence_text=str(clause),
                    source_spans=parsed.get("source_spans") or [],
                    ai_extraction_id=extraction_id,
                ),
                current_user=current_user,
            )
        for ref in parsed.get("references") or []:
            target = ref.get("letterNo") or ref.get("text") or ref.get("raw")
            if target:
                await self._suggest_once(
                    EventLinkCreate(
                        organization_id=org_id or None,
                        project_id=project_id or None,
                        source_type=EvidenceEntityType.PROJECT_EVENT,
                        source_id=event_id,
                        target_type=EvidenceEntityType.UNRESOLVED_REFERENCE,
                        target_id=str(target),
                        relation_type=EventRelationType.REFERS_TO,
                        confidence=0.7,
                        evidence_text=str(target),
                        ai_extraction_id=extraction_id,
                    ),
                    current_user=current_user,
                )
        unresolved_sets = [
            (parsed.get("drawing_refs") or [], EventRelationType.REFERS_TO, EvidenceEntityType.DRAWING),
            (parsed.get("payment_refs") or [], EventRelationType.SUPPORTS_MEASUREMENT, EvidenceEntityType.PAYMENT_EVENT),
            (parsed.get("milestone_refs") or [], EventRelationType.AFFECTS, EvidenceEntityType.KEY_DATE),
            (parsed.get("delay_refs") or [], EventRelationType.CAUSES_DELAY, EvidenceEntityType.UNRESOLVED_REFERENCE),
        ]
        for values, relation, target_type in unresolved_sets:
            for value in values:
                await self._suggest_once(
                    EventLinkCreate(
                        organization_id=org_id or None,
                        project_id=project_id or None,
                        source_type=EvidenceEntityType.PROJECT_EVENT,
                        source_id=event_id,
                        target_type=target_type,
                        target_id=str(value),
                        relation_type=relation,
                        confidence=0.65,
                        evidence_text=str(value),
                        ai_extraction_id=extraction_id,
                    ),
                    current_user=current_user,
                )

    async def _suggest_once(self, payload: EventLinkCreate, current_user: Any = None) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        existing = await db.event_links.find_one(
            {
                "source_type": _enum_value(payload.source_type),
                "source_id": payload.source_id,
                "target_type": _enum_value(payload.target_type),
                "target_id": payload.target_id,
                "relation_type": _enum_value(payload.relation_type),
                "ai_extraction_id": payload.ai_extraction_id,
            }
        )
        if existing:
            return existing
        return await self.suggest_link(payload, current_user=current_user)
