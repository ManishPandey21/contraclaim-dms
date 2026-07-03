"""Read + editorial operations for the Clause Index UI (Phase 4, req 24-25).

Authorization is PolicyService-only. Mutations are scoped to the clause's own
org/project (loaded from the record) and mark ``manually_edited`` so a future
reprocess can preserve human edits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ...core.permissions import Permissions


class ClauseNotFoundError(LookupError):
    """Raised when a clause_uid does not resolve to a stored clause."""


class ClauseMergeError(ValueError):
    """Raised when clauses cannot be merged (empty / cross-scope)."""


# Fields surfaced to the Clause Index table (req 25 columns).
_ROW_FIELDS = (
    "clause_uid", "clause_no", "clause_title", "parent_clause_no", "clause_path",
    "level", "document_type", "volume", "page_start", "page_end", "chunk_type",
    "chunk_part", "chunk_total", "confidence", "quality_status", "is_current",
    "is_superseded", "superseded_by_clause_id", "is_authorised_for_ai",
    "embedding_status", "human_review_required", "manually_edited",
    "verified_by", "linked_clause_no", "table_title", "table_type", "table_rows",
)


class ClauseIndexService:
    COLLECTION = "contract_clauses"

    def __init__(self, db: Any, policy_service: Any = None) -> None:
        self.db = db
        self.policy_service = policy_service

    # ------------------------------------------------------------------ #
    async def _authorize(self, current_user, permission, org_id, project_id) -> None:
        if self.policy_service is None:
            return
        await self.policy_service.authorize(
            current_user,
            permission,
            resource_type="contract_clause",
            organization_id=org_id,
            project_id=project_id,
        )

    @staticmethod
    def _row(doc: Dict[str, Any]) -> Dict[str, Any]:
        return {field: doc.get(field) for field in _ROW_FIELDS}

    async def _load(self, clause_uid: str) -> Dict[str, Any]:
        doc = await self.db[self.COLLECTION].find_one({"clause_uid": clause_uid})
        if not doc:
            raise ClauseNotFoundError(clause_uid)
        return doc

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #
    async def list_clauses(
        self, current_user, *, document_id: str, org_id: str, project_id: str
    ) -> List[Dict[str, Any]]:
        await self._authorize(current_user, Permissions.CONTRACT_CLAUSE_READ, org_id, project_id)
        cursor = self.db[self.COLLECTION].find({"document_id": document_id})
        rows = [self._row(doc) async for doc in cursor]
        rows.sort(key=lambda r: (r.get("page_start") or 0, str(r.get("clause_no") or ""), r.get("chunk_part") or 0))
        return rows

    # ------------------------------------------------------------------ #
    # Editorial mutations
    # ------------------------------------------------------------------ #
    async def update_clause(
        self,
        current_user,
        clause_uid: str,
        *,
        clause_title: Optional[str] = None,
        mark_verified: bool = False,
        is_superseded: Optional[bool] = None,
        superseded_by_clause_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Edit title / mark verified / mark superseded (req 25)."""
        clause = await self._load(clause_uid)
        await self._authorize(
            current_user, Permissions.CONTRACT_CLAUSE_CREATE, clause["org_id"], clause["project_id"]
        )
        now = datetime.utcnow()
        updates: Dict[str, Any] = {"updated_at": now, "manually_edited": True}
        if clause_title is not None:
            updates["clause_title"] = clause_title
        if mark_verified:
            updates.update(
                quality_status="validated",
                human_review_required=False,
                is_authorised_for_ai=True,
                verified_by=getattr(current_user, "id", None),
                verified_at=now,
            )
        if is_superseded is not None:
            updates["is_superseded"] = is_superseded
            updates["is_current"] = not is_superseded
            if superseded_by_clause_id is not None:
                updates["superseded_by_clause_id"] = superseded_by_clause_id
        await self.db[self.COLLECTION].update_one({"clause_uid": clause_uid}, {"$set": updates})
        return self._row({**clause, **updates})

    async def regenerate_embedding(self, current_user, clause_uid: str) -> Dict[str, Any]:
        """Queue the clause for re-embedding (req 25)."""
        clause = await self._load(clause_uid)
        await self._authorize(
            current_user, Permissions.AI_CONTRACT_PROCESSING_RUN, clause["org_id"], clause["project_id"]
        )
        updates = {"embedding_status": "pending", "qdrant_point_id": None, "updated_at": datetime.utcnow()}
        await self.db[self.COLLECTION].update_one({"clause_uid": clause_uid}, {"$set": updates})
        return self._row({**clause, **updates})

    async def merge_clauses(self, current_user, clause_uids: List[str]) -> Dict[str, Any]:
        """Merge several clause records into the first; delete the rest (req 25)."""
        if len(clause_uids) < 2:
            raise ClauseMergeError("At least two clauses are required to merge")
        clauses = [await self._load(uid) for uid in clause_uids]
        first = clauses[0]
        scope = (first["org_id"], first["project_id"], first["contract_id"], first["document_id"])
        if any((c["org_id"], c["project_id"], c["contract_id"], c["document_id"]) != scope for c in clauses):
            raise ClauseMergeError("Clauses must belong to the same document scope")
        await self._authorize(
            current_user, Permissions.CONTRACT_CLAUSE_CREATE, first["org_id"], first["project_id"]
        )
        merged_text = "\n\n".join((c.get("text") or "").strip() for c in clauses if c.get("text"))
        merged_clean = "\n\n".join((c.get("cleaned_text") or "").strip() for c in clauses if c.get("cleaned_text"))
        now = datetime.utcnow()
        updates = {
            "text": merged_text,
            "cleaned_text": merged_clean,
            "chunk_type": "clause",
            "chunk_part": 1,
            "chunk_total": 1,
            "manually_edited": True,
            "embedding_status": "pending",
            "qdrant_point_id": None,
            "updated_at": now,
        }
        await self.db[self.COLLECTION].update_one({"clause_uid": first["clause_uid"]}, {"$set": updates})
        for c in clauses[1:]:
            await self.db[self.COLLECTION].delete_one({"clause_uid": c["clause_uid"]})
        return self._row({**first, **updates})

    async def split_clause(self, current_user, clause_uid: str, split_at: int) -> List[Dict[str, Any]]:
        """Split a clause's cleaned text at ``split_at`` into two parts (req 25)."""
        clause = await self._load(clause_uid)
        await self._authorize(
            current_user, Permissions.CONTRACT_CLAUSE_CREATE, clause["org_id"], clause["project_id"]
        )
        cleaned = clause.get("cleaned_text") or ""
        if split_at <= 0 or split_at >= len(cleaned):
            raise ClauseMergeError("split_at must fall within the clause text")
        first_text, second_text = cleaned[:split_at].strip(), cleaned[split_at:].strip()
        now = datetime.utcnow()

        part1 = {
            "cleaned_text": first_text, "text": first_text,
            "chunk_type": "clause_part", "chunk_part": 1, "chunk_total": 2,
            "manually_edited": True, "embedding_status": "pending",
            "qdrant_point_id": None, "updated_at": now,
        }
        await self.db[self.COLLECTION].update_one({"clause_uid": clause_uid}, {"$set": part1})

        second = dict(clause)
        second.pop("_id", None)
        second.update(
            clause_uid=f"{clause_uid}:split:2",
            cleaned_text=second_text, text=second_text,
            chunk_type="clause_part", chunk_part=2, chunk_total=2,
            chunk_index=(clause.get("chunk_index") or 0) + 1,
            manually_edited=True, embedding_status="pending", qdrant_point_id=None,
            created_at=now, updated_at=now,
        )
        await self.db[self.COLLECTION].update_one(
            {"clause_uid": second["clause_uid"]}, {"$set": second}, upsert=True
        )
        return [self._row({**clause, **part1}), self._row(second)]


__all__ = ["ClauseIndexService", "ClauseNotFoundError", "ClauseMergeError"]
