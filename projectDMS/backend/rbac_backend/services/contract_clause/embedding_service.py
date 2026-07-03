"""Embed authorised clauses (cleaned text only) and upsert to Qdrant.

Embeddings are generated only from ``cleaned_text`` (never raw OCR, req 16) and
carry a clause-first payload for grounded retrieval (req 17).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ...models.contract_clause import ContractClause

logger = logging.getLogger(__name__)


class ClauseEmbeddingService:
    def __init__(self, db: Any = None, embedding_client: Any = None, vector_client: Any = None) -> None:
        self.db = db
        self.embedding_client = embedding_client
        self.vector_client = vector_client

    @staticmethod
    def build_embedding_text(clause: ContractClause) -> str:
        """Context-prefixed cleaned text used for the embedding (spec section 8)."""
        parts: List[str] = []
        if clause.document_type:
            parts.append(f"Document: {clause.document_type}")
        if clause.clause_no:
            heading = f"Clause {clause.clause_no}"
            if clause.clause_title:
                heading += f" - {clause.clause_title}"
            parts.append(heading)
        parts.append(clause.cleaned_text or "")
        return "\n".join(part for part in parts if part).strip()

    @staticmethod
    def build_payload(clause: ContractClause) -> Dict[str, Any]:
        """Qdrant payload for a clause vector (req 17)."""
        return {
            "clause_id": clause.clause_uid,
            "org_id": clause.org_id,
            "project_id": clause.project_id,
            "contract_id": clause.contract_id,
            "document_id": clause.document_id,
            "document_type": clause.document_type,
            "volume": clause.volume,
            "clause_no": clause.clause_no,
            "clause_title": clause.clause_title,
            "parent_clause_no": clause.parent_clause_no,
            "clause_path": clause.clause_path,
            "page_start": clause.page_start,
            "page_end": clause.page_end,
            "chunk_type": clause.chunk_type,
            "is_current": clause.is_current,
            "is_authorised_for_ai": clause.is_authorised_for_ai,
        }

    @staticmethod
    def is_embeddable(clause: ContractClause) -> bool:
        """Only authorised, validated clauses with cleaned text are embedded."""
        return bool(
            clause.is_authorised_for_ai
            and (clause.cleaned_text or "").strip()
        )

    async def index_clauses(self, clauses: List[ContractClause]) -> Dict[str, int]:
        """Embed cleaned text for authorised clauses and upsert to Qdrant."""
        targets = [c for c in clauses if self.is_embeddable(c)]
        skipped = len(clauses) - len(targets)
        if not targets:
            return {"embedded": 0, "skipped": skipped}
        if self.embedding_client is None or self.vector_client is None:
            return {"embedded": 0, "skipped": len(clauses)}

        texts = [self.build_embedding_text(c) for c in targets]
        vectors = await self.embedding_client.embed(texts)

        chunks = [
            {
                "chunk_id": c.clause_uid,
                "org_id": c.org_id,
                "project_id": c.project_id,
                "document_id": c.document_id,
                "text": c.cleaned_text,
                "metadata": self.build_payload(c),
            }
            for c in targets
        ]
        await self.vector_client.upsert(vectors, chunks, namespace="contract_clauses")

        if self.db is not None:
            for clause in targets:
                await self.db["contract_clauses"].update_one(
                    {"clause_uid": clause.clause_uid},
                    {"$set": {"embedding_status": "done", "qdrant_point_id": clause.clause_uid}},
                )
        return {"embedded": len(targets), "skipped": skipped}


__all__ = ["ClauseEmbeddingService"]
