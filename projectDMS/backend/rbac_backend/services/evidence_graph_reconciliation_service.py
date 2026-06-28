"""Mongo evidence graph reconciliation reports for FalkorDB sync."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core.database import get_database
from ..models.evidence_graph import EventLinkStatus, EvidenceEntityType
from .evidence_graph_service import EvidenceGraphService


class EvidenceGraphReconciliationService:
    def __init__(self, db: Any = None) -> None:
        self.db = db

    async def _get_db(self) -> Any:
        return self.db if self.db is not None else await get_database()

    async def report(self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None) -> Dict[str, Any]:
        db = await self._get_db()
        query = dict(scope_filter or {})
        if project_id:
            query["project_id"] = project_id
        events_count = await self._count(db.project_events, query)
        all_latest = await EvidenceGraphService(db).list_links(query, latest_only=True, limit=10000)
        status_counts: Dict[str, int] = {}
        for link in all_latest:
            status = str(link.get("status") or "")
            status_counts[status] = status_counts.get(status, 0) + 1
        deleted_document_links = await self._deleted_document_links(db, all_latest)
        return {
            "mongo_project_events": events_count,
            "mongo_latest_links": len(all_latest),
            "verified_or_approved_links": status_counts.get(EventLinkStatus.USER_VERIFIED.value, 0)
            + status_counts.get(EventLinkStatus.APPROVED.value, 0),
            "ai_suggested_links": status_counts.get(EventLinkStatus.AI_SUGGESTED.value, 0),
            "rejected_links": status_counts.get(EventLinkStatus.REJECTED.value, 0),
            "falkor_sync_candidates": status_counts.get(EventLinkStatus.USER_VERIFIED.value, 0)
            + status_counts.get(EventLinkStatus.APPROVED.value, 0),
            "missing_nodes": [],
            "stale_edges": [],
            "deleted_document_links": deleted_document_links,
            "manual_reference_mutations": [],
            "notes": [
                "MongoDB is authoritative; FalkorDB should mirror user_verified and approved latest links.",
                "Run backfill dry-run before mutating missing project_events.",
            ],
        }

    async def _count(self, collection: Any, query: Dict[str, Any]) -> int:
        if hasattr(collection, "count_documents"):
            return int(await collection.count_documents(query))
        cursor = collection.find(query)
        if hasattr(cursor, "to_list"):
            return len(await cursor.to_list(length=None))
        return sum(1 async for _ in cursor)

    async def _deleted_document_links(self, db: Any, links: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for link in links:
            for side in ("source", "target"):
                entity_type = link.get(f"{side}_type")
                entity_id = link.get(f"{side}_id")
                if entity_type != EvidenceEntityType.DOCUMENT.value or not entity_id:
                    continue
                doc = await db.documents.find_one({"_id": entity_id})
                if not doc or doc.get("deleted_at") or doc.get("is_deleted"):
                    out.append(
                        {
                            "link_group_id": link.get("link_group_id"),
                            "revision": link.get("revision"),
                            "side": side,
                            "document_id": entity_id,
                        }
                    )
        return out
