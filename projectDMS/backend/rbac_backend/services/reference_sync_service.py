# reference_sync_service.py
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from bson.errors import InvalidId
from bson.objectid import ObjectId
from pymongo.database import Database

from ..core.database import get_database
from ..models.document import DocumentReference
from .falkor_graph_service import normalize_letter_code
from .reference_parser import parse_legacy_reference_text

logger = logging.getLogger(__name__)


class ReferenceSyncError(Exception):
    """Raised when reference synchronisation fails."""


class ReferenceSyncService:
    """Keeps document reference relationships consistent across MongoDB collections."""

    def __init__(self, db: Optional[Database] = None) -> None:
        self._db = db

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #
    async def sync_bidirectional(
        self,
        document_id: str,
        references: Sequence[Any],
        *,
        source: str = "parser",
        default_link_type: str = "indirect",
        enqueue_missing: bool = True,
        clear_existing: bool = False,
    ) -> Dict[str, Any]:
        """
        Ensure forward (`references`) and backward (`referencedBy`) links are in sync.

        Args:
            document_id: The source document identifier.
            references: Sequence of reference payloads (dicts, models, etc.).
            source: Origin hint for generated links (e.g. "parser", "manual").
            default_link_type: Fallback link type when not provided.
            enqueue_missing: Whether to queue unresolved references for later processing.
            clear_existing: When true, an empty reference set clears existing
                links for this source bucket instead of behaving as a no-op.

        Returns:
            Summary statistics about the synchronisation operation.
        """
        db = await self._get_db()
        source_oid = self._to_object_id(document_id)
        if source_oid is None:
            raise ReferenceSyncError(f"Invalid document_id supplied: {document_id}")

        source_doc = await db.documents.find_one({"_id": source_oid})
        if not source_doc:
            raise ReferenceSyncError(f"Document {document_id} not found")

        source_letter = source_doc.get("letterNo")
        source_scope = self._scope_filter(source_doc)

        normalized_references: List[Dict[str, Any]] = []
        seen_reference_keys: set[str] = set()
        for item in references or []:
            payload = self._normalize_reference_payload(
                item,
                default_source=source,
                default_link_type=default_link_type,
            )
            if not payload:
                continue
            reference_key = self._reference_key(payload)
            if reference_key and reference_key in seen_reference_keys:
                continue
            if reference_key:
                seen_reference_keys.add(reference_key)
            normalized_references.append(payload)

        if not normalized_references:
            removed_count = 0
            if clear_existing:
                removed_targets = await self._upsert_source_references(
                    db=db,
                    source_doc=source_doc,
                    new_references={},
                    source_key=source,
                )
                removed_count = await self._remove_target_backlinks(
                    db=db,
                    source_id=str(source_oid),
                    target_ids=removed_targets,
                    source_key=source,
                )
            logger.debug(
                "No references to synchronise for document_id=%s (source=%s, clear_existing=%s)",
                document_id,
                source,
                clear_existing,
            )
            return {
                "resolved": 0,
                "missing": [],
                "updated_targets": 0,
                "removed_targets": removed_count,
            }

        resolved: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]] = {}
        missing: List[Dict[str, Any]] = []

        for payload in normalized_references:
            target_doc = await self._resolve_target_document(
                db=db,
                reference=payload,
                skip_ids={str(source_oid)},
                scope_filter=source_scope,
            )
            if target_doc:
                target_id = str(target_doc["_id"])
                resolved[target_id] = (payload, target_doc)
            else:
                missing.append(payload)

        now = datetime.utcnow()
        existing_source_refs = {
            ref.documentId: ref
            for ref in self._coerce_reference_list(source_doc.get("references"))
            if (ref.source or "").lower() == source.lower()
        }
        new_source_refs: Dict[str, DocumentReference] = {}
        target_backlinks: Dict[str, DocumentReference] = {}

        for target_id, (payload, target_doc) in resolved.items():
            existing = existing_source_refs.get(target_id)
            link_type = payload.get("linkType") or default_link_type
            linked_by = (
                payload.get("linkedBy")
                or payload.get("linked_by")
                or (existing.linkedBy if existing else None)
                or source
            )
            linked_at = self._normalize_datetime(
                payload.get("linkedAt")
                or payload.get("linked_at")
                or (existing.linkedAt if existing else None)
                or now
            )
            description = (
                payload.get("description")
                or payload.get("text")
                or (existing.description if existing else None)
            )

            target_letter = target_doc.get("letterNo")
            new_source_refs[target_id] = DocumentReference(
                documentId=target_id,
                linkType=link_type,
                description=description,
                linkedAt=linked_at or now,
                linkedBy=linked_by,
                letterNo=target_letter,
                source=source,
            )

            target_backlinks[target_id] = DocumentReference(
                documentId=str(source_oid),
                linkType=link_type,
                description=description,
                linkedAt=linked_at or now,
                linkedBy=linked_by,
                letterNo=source_letter,
                source=source,
            )

        removed_targets = await self._upsert_source_references(
            db=db,
            source_doc=source_doc,
            new_references=new_source_refs,
            source_key=source,
        )

        updated_targets = await self._upsert_target_backlinks(
            db=db,
            source_id=str(source_oid),
            target_entries=target_backlinks,
            source_key=source,
        )

        removed_count = 0
        if removed_targets:
            removed_count = await self._remove_target_backlinks(
                db=db,
                source_id=str(source_oid),
                target_ids=removed_targets,
                source_key=source,
            )

        if enqueue_missing and missing:
            await self.enqueue_missing(document_id, missing, scope=source_scope)

        resolved_keys = [
            key
            for payload, _target in resolved.values()
            if (key := self._reference_key(payload))
        ]
        if resolved_keys:
            await db.reference_sync_queue.update_many(
                {
                    "document_id": document_id,
                    "reference_key": {"$in": resolved_keys},
                    "status": "pending",
                },
                {"$set": {"status": "resolved", "updatedAt": now}},
            )

        return {
            "resolved": len(resolved),
            "missing": missing,
            "updated_targets": updated_targets,
            "removed_targets": removed_count,
        }

    async def enqueue_missing(
        self,
        document_id: str,
        references: Iterable[Dict[str, Any]],
        *,
        scope: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Store unresolved references for later inspection or offline processing.

        Returns the number of queued entries.
        """
        items: Dict[str, Dict[str, Any]] = {}
        now = datetime.utcnow()
        for ref in references:
            if not ref:
                continue
            reference_key = self._reference_key(ref)
            if not reference_key:
                continue
            items[reference_key] = ref

        if not items:
            return 0

        db = await self._get_db()
        collection = db.reference_sync_queue
        for reference_key, ref in items.items():
            await collection.update_one(
                {
                    "document_id": document_id,
                    "reference_key": reference_key,
                    "status": "pending",
                },
                {
                    "$set": {
                        "reference": ref,
                        "organization_id": (scope or {}).get("organization_id"),
                        "project_id": (scope or {}).get("project_id"),
                        "updatedAt": now,
                    },
                    "$setOnInsert": {
                        "status": "pending",
                        "createdAt": now,
                    },
                },
                upsert=True,
            )
        logger.info(
            "Queued %s missing references for document_id=%s",
            len(items),
            document_id,
        )
        return len(items)

    async def drain_reference_queue(
        self,
        *,
        batch: int = 200,
        ttl_days: int = 30,
    ) -> Dict[str, Any]:
        """Resolve deferred references whose targets have since been ingested.

        Without this reaper the ``reference_sync_queue`` only grows: a letter that
        references a not-yet-uploaded letter is queued at ingest and never retried.

        For each source document with pending entries we replay its *full* parser
        reference set (already-resolved + still-pending) so newly-available targets
        get linked without disturbing existing links (enqueue_missing=False avoids
        re-queueing), then re-resolve each queued entry to set its status:
          - target now found  -> ``resolved``
          - source deleted     -> ``orphaned``
          - older than ttl_days and still unresolved -> ``expired``
          - otherwise          -> left ``pending`` (original age preserved)
        """
        db = await self._get_db()
        now = datetime.utcnow()
        cutoff = now - timedelta(days=ttl_days)

        expired = await db.reference_sync_queue.update_many(
            {"status": "pending", "createdAt": {"$lt": cutoff}},
            {"$set": {"status": "expired", "updatedAt": now}},
        )

        pending = (
            await db.reference_sync_queue.find({"status": "pending"})
            .sort("createdAt", 1)
            .limit(batch)
            .to_list(length=batch)
        )

        by_doc: Dict[str, List[Dict[str, Any]]] = {}
        for entry in pending:
            by_doc.setdefault(str(entry.get("document_id")), []).append(entry)

        resolved_total = 0
        orphaned_total = 0
        processed_docs = 0

        for document_id, entries in by_doc.items():
            source_oid = self._to_object_id(document_id)
            source_doc = (
                await db.documents.find_one({"_id": source_oid}) if source_oid else None
            )
            entry_ids = [e["_id"] for e in entries]

            if not source_doc:
                await db.reference_sync_queue.update_many(
                    {"_id": {"$in": entry_ids}},
                    {"$set": {"status": "orphaned", "updatedAt": now}},
                )
                orphaned_total += len(entry_ids)
                continue

            processed_docs += 1
            current = [
                ref.model_dump()
                for ref in self._coerce_reference_list(source_doc.get("references"))
                if (ref.source or "").lower() == "parser"
            ]
            queued = [e.get("reference") for e in entries if e.get("reference")]

            try:
                await self.sync_bidirectional(
                    document_id,
                    current + queued,
                    source="parser",
                    enqueue_missing=False,
                )
            except ReferenceSyncError:
                logger.warning("reaper: sync failed for document_id=%s", document_id)
                continue

            for e in entries:
                target = await self._resolve_target_document(
                    db=db,
                    reference=e.get("reference") or {},
                    skip_ids={document_id},
                    scope_filter=self._scope_filter(source_doc),
                )
                if target:
                    await db.reference_sync_queue.update_one(
                        {"_id": e["_id"]},
                        {"$set": {"status": "resolved", "updatedAt": now}},
                    )
                    resolved_total += 1

        summary = {
            "pending_scanned": len(pending),
            "processed_docs": processed_docs,
            "resolved": resolved_total,
            "expired": expired.modified_count,
            "orphaned": orphaned_total,
        }
        logger.info("reference_sync reaper: %s", summary)
        return summary

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #
    async def _get_db(self) -> Database:
        if self._db is not None:
            return self._db
        return await get_database()

    def _to_object_id(self, value: Any) -> Optional[ObjectId]:
        if value is None:
            return None
        try:
            return ObjectId(str(value))
        except (InvalidId, TypeError):
            return None

    def _scope_filter(self, document: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not document:
            return {}
        scope: Dict[str, Any] = {}
        organization_id = document.get("organization_id") or document.get("organizationId")
        project_id = document.get("project_id") or document.get("projectId")
        if organization_id:
            scope["organization_id"] = str(organization_id)
        if project_id:
            scope["project_id"] = str(project_id)
        return scope

    def _normalize_reference_payload(
        self,
        entry: Any,
        *,
        default_source: str,
        default_link_type: str,
    ) -> Optional[Dict[str, Any]]:
        if entry is None:
            return None

        if isinstance(entry, DocumentReference):
            data = entry.model_dump(mode="python")
        elif hasattr(entry, "model_dump"):
            try:
                data = entry.model_dump(by_alias=True, exclude_none=True)  # type: ignore[attr-defined]
            except TypeError:
                data = dict(entry)  # type: ignore[arg-type]
        elif isinstance(entry, dict):
            data = {k: v for k, v in entry.items() if v not in (None, "", [], {})}
        elif isinstance(entry, str):
            cleaned = entry.strip()
            if not cleaned:
                return None
            data = {"letterNo": cleaned}
        else:
            data = {"letterNo": str(entry).strip()}

        if not data:
            return None

        candidate_id = data.get("documentId") or data.get("document_id") or data.get("_id")
        if candidate_id:
            data["documentId"] = str(candidate_id)

        candidate_letter = (
            data.get("letterNo")
            or data.get("letter_no")
            or data.get("code")
            or data.get("normCode")
        )
        if not candidate_letter:
            raw_text = data.get("raw") or data.get("text") or data.get("reference")
            if raw_text:
                parsed = parse_legacy_reference_text(str(raw_text))
                candidate_letter = (
                    parsed.get("letterNo") if parsed else str(raw_text).strip()
                )
        if candidate_letter:
            data["letterNo"] = str(candidate_letter).strip()

        candidate_source = data.get("source") or data.get("origin") or default_source
        if candidate_source:
            data["source"] = str(candidate_source).strip().lower()

        data["linkType"] = data.get("linkType") or data.get("link_type") or default_link_type
        if data.get("description") is None and data.get("text"):
            data["description"] = data.get("text")

        return data

    def _reference_key(self, reference: Dict[str, Any]) -> str:
        """Return a stable identity for queue and per-run de-duplication."""
        document_id = reference.get("documentId") or reference.get("document_id")
        if document_id:
            return f"document:{str(document_id).strip()}"

        letter_no = (
            reference.get("letterNo")
            or reference.get("letter_no")
            or reference.get("code")
            or reference.get("normCode")
        )
        normalized = normalize_letter_code(str(letter_no or ""))
        return f"letter:{normalized}" if normalized else ""

    async def _resolve_target_document(
        self,
        *,
        db: Database,
        reference: Dict[str, Any],
        skip_ids: Optional[Iterable[str]] = None,
        scope_filter: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Try to resolve a reference payload to an existing MongoDB document."""
        skip_set = {str(doc_id) for doc_id in (skip_ids or []) if doc_id}
        scoped = {k: v for k, v in (scope_filter or {}).items() if v}

        target_id = reference.get("documentId")
        if target_id:
            target_oid = self._to_object_id(target_id)
            if target_oid:
                query: Dict[str, Any] = {"_id": target_oid}
                query.update(scoped)
                doc = await db.documents.find_one(
                    query,
                    {
                        "_id": 1,
                        "letterNo": 1,
                        "referencedBy": 1,
                        "references": 1,
                        "organization_id": 1,
                        "project_id": 1,
                    },
                )
                if doc and str(doc["_id"]) not in skip_set:
                    return doc

        letter_no = reference.get("letterNo")
        if not letter_no:
            return None

        normalized_letter = normalize_letter_code(str(letter_no))
        if not normalized_letter:
            return None

        normalized_query: Dict[str, Any] = {"letterNoNormalized": normalized_letter}
        normalized_query.update(scoped)
        normalized_match = await db.documents.find_one(
            normalized_query,
            {
                "_id": 1,
                "letterNo": 1,
                "referencedBy": 1,
                "references": 1,
                "organization_id": 1,
                "project_id": 1,
            },
        )
        if normalized_match and str(normalized_match.get("_id")) not in skip_set:
            return normalized_match

        regex_query: Dict[str, Any] = {
            "letterNo": {
                "$regex": f"^{re.escape(str(letter_no))}$",
                "$options": "i",
            }
        }
        regex_query.update(scoped)
        cursor = (
            db.documents.find(
                regex_query,
                {
                    "_id": 1,
                    "letterNo": 1,
                    "referencedBy": 1,
                    "references": 1,
                    "organization_id": 1,
                    "project_id": 1,
                },
            )
            .sort("updatedAt", -1)
            .limit(10)
        )
        candidates = await cursor.to_list(length=10)
        for candidate in candidates:
            if not candidate:
                continue
            candidate_id = str(candidate.get("_id"))
            if candidate_id in skip_set:
                continue
            stored_letter = candidate.get("letterNo")
            if normalize_letter_code(str(stored_letter or "")) == normalized_letter:
                return candidate
        return None

    async def _upsert_source_references(
        self,
        *,
        db: Database,
        source_doc: Dict[str, Any],
        new_references: Dict[str, DocumentReference],
        source_key: str,
    ) -> List[str]:
        """Update the source document's `references` field; return removed target IDs."""
        existing_refs = self._coerce_reference_list(source_doc.get("references"))
        manual_refs: List[DocumentReference] = []
        auto_refs: Dict[str, DocumentReference] = {}

        for entry in existing_refs:
            key = (entry.source or "").lower()
            if key == source_key.lower():
                auto_refs[entry.documentId] = entry
            else:
                manual_refs.append(entry)

        removed_targets = [
            target_id for target_id in auto_refs.keys() if target_id not in new_references
        ]

        has_changes = False
        updated_refs = list(manual_refs)

        for target_id, ref in new_references.items():
            existing = auto_refs.get(target_id)
            if existing:
                if existing.model_dump() != ref.model_dump():
                    has_changes = True
                updated_refs.append(ref)
            else:
                updated_refs.append(ref)
                has_changes = True

        if len(auto_refs) != len(new_references):
            has_changes = True

        if has_changes:
            await db.documents.update_one(
                {"_id": source_doc["_id"]},
                {
                    "$set": {
                        "references": [
                            ref.model_dump(by_alias=True, exclude_none=True)
                            for ref in updated_refs
                        ],
                        "updatedAt": datetime.utcnow(),
                    }
                },
            )

        return removed_targets

    async def _upsert_target_backlinks(
        self,
        *,
        db: Database,
        source_id: str,
        target_entries: Dict[str, DocumentReference],
        source_key: str,
    ) -> int:
        """Ensure each resolved target has a backlink to the source document."""
        updated = 0
        for target_id, backlink in target_entries.items():
            target_oid = self._to_object_id(target_id)
            if not target_oid:
                continue

            target_doc = await db.documents.find_one(
                {"_id": target_oid},
                {"_id": 1, "referencedBy": 1},
            )
            if not target_doc:
                continue

            existing_refs = self._coerce_reference_list(target_doc.get("referencedBy"))
            new_entries: List[DocumentReference] = []
            seen = False
            changed = False
            for ref in existing_refs:
                if ref.documentId == source_id and (ref.source or "").lower() == source_key.lower():
                    if seen:
                        changed = True
                        continue
                    if ref.model_dump() != backlink.model_dump():
                        new_entries.append(backlink)
                        changed = True
                    else:
                        new_entries.append(ref)
                    seen = True
                else:
                    new_entries.append(ref)

            if not seen:
                new_entries.append(backlink)
                changed = True

            if changed:
                await db.documents.update_one(
                    {"_id": target_oid},
                    {
                        "$set": {
                            "referencedBy": [
                                ref.model_dump(by_alias=True, exclude_none=True)
                                for ref in new_entries
                            ],
                            "updatedAt": datetime.utcnow(),
                        },
                    },
                )
                updated += 1
        return updated

    async def _remove_target_backlinks(
        self,
        *,
        db: Database,
        source_id: str,
        target_ids: Sequence[str],
        source_key: str,
    ) -> int:
        if not target_ids:
            return 0

        removed = 0
        for target_id in target_ids:
            target_oid = self._to_object_id(target_id)
            if not target_oid:
                continue
            result = await db.documents.update_one(
                {"_id": target_oid},
                {
                    "$pull": {
                        "referencedBy": {
                            "documentId": source_id,
                            "source": source_key,
                        }
                    },
                    "$set": {"updatedAt": datetime.utcnow()},
                },
            )
            if result.modified_count:
                removed += 1
        return removed

    def _coerce_reference_list(self, values: Any) -> List[DocumentReference]:
        if not values:
            return []
        parsed: List[DocumentReference] = []
        for item in values:
            if isinstance(item, DocumentReference):
                parsed.append(item)
                continue
            if isinstance(item, dict):
                try:
                    parsed.append(DocumentReference(**item))
                except Exception:
                    continue
            elif hasattr(item, "model_dump"):
                try:
                    data = item.model_dump(by_alias=True, exclude_none=True)  # type: ignore[attr-defined]
                    parsed.append(DocumentReference(**data))
                except Exception:
                    continue
        return parsed

    def _normalize_datetime(self, value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None


async def run_reference_sync_reaper() -> Dict[str, Any]:
    """Scheduler entry point: drain the deferred reference-sync queue."""
    try:
        return await ReferenceSyncService().drain_reference_queue()
    except Exception:  # pragma: no cover - defensive: scheduler must not crash
        logger.exception("reference_sync reaper failed")
        return {"error": "reaper_failed"}


__all__ = [
    "ReferenceSyncService",
    "ReferenceSyncError",
    "run_reference_sync_reaper",
]
