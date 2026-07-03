"""Scoped, idempotent storage for clause-wise contract records.

Phase 1 foundation for the Contract Clause Chunking Agent:
- enforces org/project/contract/document scope (req 2) and PolicyService
  authorization with the new ``dms.*`` permissions (req 3, 4);
- builds ``contract_clauses`` records with parent-child hierarchy (req 11, 13);
- adds a text checksum for change detection (req 22);
- upserts idempotently so reprocessing updates records instead of duplicating
  them (req 21).

Text extraction, cleaning and clause-boundary detection are reused from the
existing ``ClauseExtractor``/OCR pipeline and orchestrated in a later phase.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from ...core.permissions import Permissions
from ...models.contract_clause import (
    ChunkType,
    Confidence,
    ContractClause,
    ExtractionMethod,
)

_CLAUSE_PREFIX_RE = re.compile(r"^(?:sub[-\s]?clause|clause)\s+", re.IGNORECASE)
_DOTTED_NUMERIC_RE = re.compile(r"^\d+(?:\.\d+)*$")


class ClauseScopeError(ValueError):
    """Raised when mandatory scope (org/project/contract/document) is missing."""


class ClauseStorageService:
    """Persists clause records to the ``contract_clauses`` collection."""

    COLLECTION = "contract_clauses"

    def __init__(self, db: Any, policy_service: Any = None) -> None:
        self.db = db
        self.policy_service = policy_service

    # ------------------------------------------------------------------ #
    # Scope & authorization
    # ------------------------------------------------------------------ #
    @staticmethod
    def validate_scope(
        org_id: Optional[str],
        project_id: Optional[str],
        contract_id: Optional[str],
        document_id: Optional[str],
    ) -> None:
        """Block processing when any scope key is missing (req 2, mandatory guardrail)."""
        missing = [
            name
            for name, value in (
                ("org_id", org_id),
                ("project_id", project_id),
                ("contract_id", contract_id),
                ("document_id", document_id),
            )
            if not (value and str(value).strip())
        ]
        if missing:
            raise ClauseScopeError(
                "Contract clause processing requires " + ", ".join(missing)
            )

    async def authorize(
        self,
        current_user: Any,
        permission: str,
        *,
        org_id: Optional[str],
        project_id: Optional[str],
    ) -> None:
        """Authorize via PolicyService only (no legacy permission checks, req 3)."""
        if self.policy_service is None:
            return
        await self.policy_service.authorize(
            current_user,
            permission,
            resource_type="contract_clause",
            organization_id=org_id,
            project_id=project_id,
        )

    async def authorize_processing_run(
        self,
        current_user: Any,
        *,
        org_id: Optional[str],
        project_id: Optional[str],
        contract_id: Optional[str],
        document_id: Optional[str],
    ) -> None:
        """Full guardrail for a clause-processing run: scope + all required perms (req 4)."""
        self.validate_scope(org_id, project_id, contract_id, document_id)
        for permission in (
            Permissions.CONTRACT_READ,
            Permissions.CONTRACT_UPDATE,
            Permissions.CONTRACT_CLAUSE_CREATE,
            Permissions.AI_CONTRACT_PROCESSING_RUN,
        ):
            await self.authorize(
                current_user, permission, org_id=org_id, project_id=project_id
            )

    # ------------------------------------------------------------------ #
    # Hierarchy & identity helpers (pure)
    # ------------------------------------------------------------------ #
    @staticmethod
    def normalize_clause_no(clause_no: Optional[str]) -> Optional[str]:
        if not clause_no:
            return None
        value = _CLAUSE_PREFIX_RE.sub("", str(clause_no).strip()).strip()
        value = value.rstrip(".").strip()
        return value or None

    @classmethod
    def clause_path(cls, clause_no: Optional[str]) -> List[str]:
        """["8","8.4"] for "8.4"; ["Appendix 1"] for non-dotted; [] for none."""
        normalized = cls.normalize_clause_no(clause_no)
        if not normalized:
            return []
        if _DOTTED_NUMERIC_RE.match(normalized):
            parts = normalized.split(".")
            return [".".join(parts[: i + 1]) for i in range(len(parts))]
        return [normalized]

    @classmethod
    def parent_clause_no(cls, clause_no: Optional[str]) -> Optional[str]:
        path = cls.clause_path(clause_no)
        return path[-2] if len(path) >= 2 else None

    @classmethod
    def level(cls, clause_no: Optional[str]) -> int:
        path = cls.clause_path(clause_no)
        return len(path) if path else 1

    @staticmethod
    def checksum(text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

    @classmethod
    def clause_uid(
        cls,
        org_id: str,
        project_id: str,
        contract_id: str,
        document_id: str,
        clause_no: Optional[str],
        chunk_part: int,
        chunk_index: int = 0,
        duplicate_ordinal: int = 1,
    ) -> str:
        """Deterministic per-clause-part identity for idempotent upserts.

        Section chunks (no clause number) fall back to their chunk index so they
        remain stable across reprocessing. Repeated clause numbers (the same
        ``clause_no`` appearing more than once in a document) are disambiguated
        by ``duplicate_ordinal`` so they do not collide onto one record (req #2).
        The first occurrence keeps the legacy key for backward compatibility.
        """
        key = cls.normalize_clause_no(clause_no) or f"__section_{chunk_index}"
        if duplicate_ordinal and duplicate_ordinal > 1:
            key = f"{key}#dup{duplicate_ordinal}"
        raw = "|".join(
            [str(org_id), str(project_id), str(contract_id), str(document_id), key, str(chunk_part)]
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _derive_quality(
        *,
        clause_no: Optional[str],
        document_type: Optional[str],
        confidence: Confidence,
        chunk_type: ChunkType,
    ) -> Dict[str, Any]:
        """Validation rules (req 11 table): map inputs to quality/authorisation flags."""
        human_review = confidence == "low"
        if not document_type:
            quality_status = "incomplete_metadata"
        elif confidence == "low" or (chunk_type == "section_chunk" and not clause_no):
            quality_status = "needs_review"
        elif confidence == "medium":
            quality_status = "needs_review"
        else:
            quality_status = "validated"
        # Only fully validated, high/medium-confidence clauses are auto-authorised for AI.
        is_authorised = quality_status == "validated"
        return {
            "quality_status": quality_status,
            "human_review_required": human_review,
            "is_authorised_for_ai": is_authorised,
        }

    # ------------------------------------------------------------------ #
    # Record building
    # ------------------------------------------------------------------ #
    def build_record(
        self,
        *,
        org_id: str,
        project_id: str,
        contract_id: str,
        document_id: str,
        clause_no: Optional[str] = None,
        clause_title: Optional[str] = None,
        text: str = "",
        cleaned_text: Optional[str] = None,
        chunk_type: ChunkType = "clause",
        chunk_index: int = 0,
        chunk_part: int = 1,
        chunk_total: int = 1,
        page_start: Optional[int] = None,
        page_end: Optional[int] = None,
        document_title: Optional[str] = None,
        document_type: Optional[str] = None,
        volume: Optional[str] = None,
        revision: Optional[str] = None,
        confidence: Confidence = "high",
        extraction_method: ExtractionMethod = "text",
        keywords: Optional[List[str]] = None,
        source_file_path: Optional[str] = None,
        source_pdf_url: Optional[str] = None,
        linked_clause_no: Optional[str] = None,
        table_title: Optional[str] = None,
        is_current: bool = True,
        char_start: Optional[int] = None,
        char_end: Optional[int] = None,
        duplicate_ordinal: int = 1,
        duplicate_status: str = "unique",
    ) -> ContractClause:
        """Build one fully-scoped, hierarchy-aware clause record."""
        self.validate_scope(org_id, project_id, contract_id, document_id)
        normalized_no = self.normalize_clause_no(clause_no)
        cleaned = cleaned_text if cleaned_text is not None else text
        quality = self._derive_quality(
            clause_no=normalized_no,
            document_type=document_type,
            confidence=confidence,
            chunk_type=chunk_type,
        )
        return ContractClause(
            clause_uid=self.clause_uid(
                org_id, project_id, contract_id, document_id,
                normalized_no, chunk_part, chunk_index, duplicate_ordinal,
            ),
            duplicate_group_key=normalized_no,
            duplicate_ordinal=duplicate_ordinal,
            duplicate_status=duplicate_status,  # type: ignore[arg-type]
            char_start=char_start,
            char_end=char_end,
            org_id=str(org_id),
            project_id=str(project_id),
            contract_id=str(contract_id),
            document_id=str(document_id),
            document_title=document_title,
            document_type=document_type,
            volume=volume,
            revision=revision,
            clause_no=normalized_no,
            clause_title=clause_title,
            parent_clause_no=self.parent_clause_no(normalized_no),
            clause_path=self.clause_path(normalized_no),
            level=self.level(normalized_no),
            text=text or "",
            cleaned_text=cleaned or "",
            keywords=keywords or [],
            page_start=page_start,
            page_end=page_end,
            source_file_path=source_file_path,
            source_pdf_url=source_pdf_url,
            chunk_type=chunk_type,
            chunk_index=chunk_index,
            chunk_part=chunk_part,
            chunk_total=chunk_total,
            linked_clause_no=linked_clause_no,
            table_title=table_title,
            confidence=confidence,
            extraction_method=extraction_method,
            is_current=is_current,
            checksum=self.checksum(cleaned or ""),
            embedding_status="pending",
            **quality,
        )

    # ------------------------------------------------------------------ #
    # Persistence (idempotent)
    # ------------------------------------------------------------------ #
    async def ensure_indexes(self) -> None:
        collection = self.db[self.COLLECTION]
        await collection.create_index("clause_uid", unique=True)
        await collection.create_index(
            [("org_id", 1), ("project_id", 1), ("contract_id", 1), ("document_id", 1)]
        )
        await collection.create_index([("document_id", 1), ("clause_no", 1)])
        await collection.create_index([("document_id", 1), ("is_current", 1)])

    # Fields carrying human editorial decisions — preserved across reprocessing
    # unless the caller explicitly resets (req #6).
    _HUMAN_EDIT_FIELDS = (
        "clause_title", "quality_status", "is_authorised_for_ai", "human_review_required",
        "is_current", "is_superseded", "superseded_by_clause_id",
        "manually_edited", "verified_by", "verified_at",
    )

    async def save_clause(self, record: ContractClause, *, force_reset: bool = False) -> str:
        """Idempotently upsert one clause by ``clause_uid``.

        Reprocessing updates the existing record (bumping ``updated_at`` and,
        when the checksum changed, resetting ``embedding_status`` so the clause
        is re-embedded) instead of inserting a duplicate. Human edits on an
        existing record (``manually_edited``) are preserved unless
        ``force_reset`` is set (req #6). Returns "inserted", "updated",
        "unchanged" or "preserved".
        """
        collection = self.db[self.COLLECTION]
        now = datetime.utcnow()
        existing = await collection.find_one({"clause_uid": record.clause_uid})

        doc = record.to_mongo()
        doc.pop("created_at", None)  # created_at is set once, on insert
        doc["updated_at"] = now

        human_edited = bool(existing and existing.get("manually_edited") and not force_reset)
        if existing is not None:
            content_changed = existing.get("checksum") != record.checksum
            if content_changed:
                doc["embedding_status"] = "pending"
                doc["qdrant_point_id"] = None
            else:
                # Preserve prior embedding progress when text is unchanged.
                doc["embedding_status"] = existing.get("embedding_status", record.embedding_status)
                doc["qdrant_point_id"] = existing.get("qdrant_point_id")
            if human_edited:
                # Keep the reviewer's editorial decisions; still refresh content.
                for field in self._HUMAN_EDIT_FIELDS:
                    if field in existing:
                        doc[field] = existing[field]

        await collection.update_one(
            {"clause_uid": record.clause_uid},
            {"$set": doc, "$setOnInsert": {"created_at": (existing or {}).get("created_at", now)}},
            upsert=True,
        )
        if existing is None:
            return "inserted"
        if human_edited:
            return "preserved"
        return "updated" if existing.get("checksum") != record.checksum else "unchanged"

    async def save_clauses(
        self, records: List[ContractClause], *, force_reset: bool = False
    ) -> Dict[str, int]:
        """Persist many clause records idempotently; returns per-outcome counts."""
        counts = {"inserted": 0, "updated": 0, "unchanged": 0, "preserved": 0}
        for record in records:
            outcome = await self.save_clause(record, force_reset=force_reset)
            counts[outcome] = counts.get(outcome, 0) + 1
        return counts


__all__ = ["ClauseStorageService", "ClauseScopeError"]
