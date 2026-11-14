"""Authority-filtered Bank Guarantee evidence projections.

The generic relationship service remains target-agnostic.  This register
projection preserves event ownership for native consumers and exposes a
flattened ID list only for transitional parent-response compatibility.
"""

from __future__ import annotations

from typing import Any, Iterable

from fastapi import HTTPException

from .document_relationship_service import (
    DocumentRelationshipError,
    DocumentRelationshipService,
)


class BankGuaranteeEvidenceService:
    def __init__(self, db: Any, *, policy: Any = None) -> None:
        self.db = db
        self.relationships = DocumentRelationshipService(db, policy=policy)

    async def _scoped_events(
        self, bank_guarantees: Iterable[dict[str, Any]]
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        parents = {
            str(row.get("_id")): row for row in bank_guarantees if row.get("_id")
        }
        if not parents:
            return parents, []
        cursor = self.db.bank_guarantee_events.find(
            {"bank_guarantee_id": {"$in": list(parents)}}
        )
        if hasattr(cursor, "to_list"):
            events = list(await cursor.to_list(length=None))
        else:
            events = [event async for event in cursor]
        events = [
            event
            for event in events
            if (
                (parent := parents.get(str(event.get("bank_guarantee_id") or "")))
                is not None
                and str(event.get("organization_id") or "")
                == str(parent.get("organization_id") or "")
                and str(event.get("project_id") or "")
                == str(parent.get("project_id") or "")
            )
        ]
        events.sort(
            key=lambda event: (
                str(event.get("bank_guarantee_id") or ""),
                int(event.get("sequence") or 0),
                str(event.get("_id") or ""),
            )
        )
        return parents, events

    async def authorized_event_evidence(
        self,
        actor: Any,
        bank_guarantees: Iterable[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        parents, events = await self._scoped_events(bank_guarantees)
        results: dict[str, list[dict[str, Any]]] = {
            parent_id: [] for parent_id in parents
        }
        for event in events:
            event_id = str(event.get("_id") or "")
            parent_id = str(event.get("bank_guarantee_id") or "")
            try:
                links = await self.relationships.list_for_target(
                    actor, "bank_guarantee_event", event_id
                )
            except (DocumentRelationshipError, HTTPException):
                continue
            event_type = event.get("event_type")
            if hasattr(event_type, "value"):
                event_type = event_type.value
            for link in links:
                results[parent_id].append(
                    {
                        "event_id": event_id,
                        "event_type": str(event_type or ""),
                        "event_sequence": int(event.get("sequence") or 0),
                        "revision_number": event.get("revision_number"),
                        "event_date": event.get("event_date"),
                        "reference": event.get("reference"),
                        "document_id": str(link.document_id),
                        "relationship_role": str(link.relationship_role),
                    }
                )
        return results

    async def authorized_document_ids(
        self,
        actor: Any,
        bank_guarantees: Iterable[dict[str, Any]],
    ) -> dict[str, list[str]]:
        parents = [row for row in bank_guarantees if row.get("_id")]
        event_evidence = await self.authorized_event_evidence(actor, parents)
        legacy = await self.relationships.authorized_document_ids_for_targets(
            actor, "bank_guarantee", parents
        )
        results: dict[str, list[str]] = {}
        for parent in parents:
            parent_id = str(parent.get("_id"))
            ordered = [
                row["document_id"] for row in event_evidence.get(parent_id, [])
            ]
            ordered.extend(legacy.get(parent_id, []))
            results[parent_id] = list(dict.fromkeys(ordered))
        return results
