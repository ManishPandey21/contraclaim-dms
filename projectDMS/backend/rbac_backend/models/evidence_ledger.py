"""Unified evidence ledger shared by AI workflows.

Every AI output (Q&A, appraisal, claims, letter drafting, arbitration) is
grounded in retrieved evidence, but each workflow historically carried its own
shape: ``SearchResult``/``Citation`` for retrieval and Q&A, ``SourceEvidence``
for letter drafting. ``EvidenceLedgerEntry`` is the common provenance record
those shapes convert into, so guardrail checks ("is this claim backed by
evidence?") and audit trails have one implementation instead of one per
workflow.

Adapters accept the existing models; ``to_citation()`` regenerates the legacy
Q&A citation shape so response formats stay stable while the ledger becomes
the source of truth.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # avoid import cycles: retrieval.models imports this module
    from ..retrieval.models import Citation, SearchResult
    from .letter_drafting import SourceEvidence


def _hash_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _clean_pages(pages: Any) -> List[int]:
    cleaned: List[int] = []
    for page in pages or []:
        if isinstance(page, (int, float)) or str(page).isdigit():
            cleaned.append(int(page))
    return cleaned


class EvidenceLedgerEntry(BaseModel):
    entry_id: str = Field(default_factory=lambda: uuid4().hex)
    workflow: str = "unknown"  # e.g. contract_qa, appraisal, letter_drafting
    run_id: Optional[str] = None
    source_type: str = "document_chunk"  # document_chunk | contract_clause | letter | ...
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    document_id: Optional[str] = None
    chunk_id: Optional[str] = None
    clause_number: Optional[str] = None
    clause_title: Optional[str] = None
    section_heading: Optional[str] = None
    page_numbers: List[int] = Field(default_factory=list)
    snippet: str = ""
    source_hash: Optional[str] = None
    retrieval_score: Optional[float] = None
    lexical_score: Optional[float] = None
    reranker_score: Optional[float] = None
    final_score: Optional[float] = None
    allowed_use: str = "fact"
    citation_label: Optional[str] = None  # lightweight label used in prompts, e.g. "C1"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Adapters from the existing per-workflow evidence shapes
    # ------------------------------------------------------------------ #
    @classmethod
    def from_search_result(
        cls,
        result: "SearchResult",
        *,
        workflow: str,
        run_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        citation_label: Optional[str] = None,
    ) -> "EvidenceLedgerEntry":
        payload = result.payload or {}
        text = payload.get("text_enriched") or payload.get("text") or result.snippet or ""
        scores = payload.get("scores") or {}
        source_type = "contract_clause" if payload.get("clause_number") or payload.get("clause_no") else "document_chunk"
        return cls(
            workflow=workflow,
            run_id=run_id,
            source_type=source_type,
            organization_id=organization_id or payload.get("org_id") or payload.get("organization_id"),
            project_id=project_id or payload.get("project_id"),
            document_id=result.document_id,
            chunk_id=result.chunk_id,
            clause_number=payload.get("clause_number") or payload.get("clause_no") or payload.get("clause_id"),
            clause_title=payload.get("clause_title"),
            section_heading=payload.get("section_heading") or payload.get("section"),
            page_numbers=_clean_pages(payload.get("page_numbers") or ([result.page] if result.page else [])),
            snippet=result.snippet or "",
            source_hash=_hash_text(text),
            retrieval_score=scores.get("base_score", result.score),
            lexical_score=scores.get("lexical_score"),
            reranker_score=scores.get("reranker_score"),
            final_score=scores.get("final_score", result.score),
            citation_label=citation_label,
            metadata={
                "file_name": payload.get("file_name") or payload.get("source_filename"),
                "document_title": payload.get("document_title"),
            },
        )

    @classmethod
    def from_citation(
        cls,
        citation: "Citation",
        *,
        workflow: str,
        run_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        citation_label: Optional[str] = None,
    ) -> "EvidenceLedgerEntry":
        return cls(
            workflow=workflow,
            run_id=run_id,
            source_type="contract_clause" if citation.clause_number else "document_chunk",
            organization_id=organization_id,
            project_id=project_id,
            document_id=citation.document_id,
            chunk_id=citation.chunk_id,
            clause_number=citation.clause_number,
            clause_title=citation.clause_title,
            section_heading=citation.section_heading,
            page_numbers=_clean_pages(citation.page_numbers or ([citation.page] if citation.page else [])),
            snippet=citation.snippet or "",
            source_hash=_hash_text(citation.snippet),
            retrieval_score=citation.score,
            final_score=citation.score,
            citation_label=citation_label,
            metadata={
                "file_name": citation.file_name,
                "document_title": citation.document_title,
                "letter_no": citation.letter_no,
            },
        )

    @classmethod
    def from_source_evidence(
        cls,
        evidence: "SourceEvidence",
        *,
        workflow: str = "letter_drafting",
        run_id: Optional[str] = None,
        citation_label: Optional[str] = None,
    ) -> "EvidenceLedgerEntry":
        text = evidence.text or evidence.snippet or ""
        return cls(
            workflow=workflow,
            run_id=run_id,
            source_type=str(evidence.source_type),
            organization_id=evidence.organization_id,
            project_id=evidence.project_id,
            document_id=evidence.document_id,
            chunk_id=evidence.source_id,
            clause_number=evidence.clause_number,
            clause_title=evidence.clause_title,
            page_numbers=_clean_pages(evidence.page_numbers),
            snippet=evidence.snippet or (text[:400] if text else ""),
            source_hash=evidence.source_hash or _hash_text(text),
            retrieval_score=evidence.score,
            final_score=evidence.score,
            allowed_use=str(evidence.allowed_use),
            citation_label=citation_label or evidence.label,
            metadata={"letter_id": evidence.letter_id, **(evidence.metadata or {})},
        )

    # ------------------------------------------------------------------ #
    # Legacy shape regeneration (keeps existing API/UI contracts stable)
    # ------------------------------------------------------------------ #
    def to_citation(self) -> "Citation":
        from ..retrieval.models import Citation

        return Citation(
            document_id=self.document_id or "",
            chunk_id=self.chunk_id or "",
            page=self.page_numbers[0] if self.page_numbers else None,
            score=self.final_score if self.final_score is not None else self.retrieval_score,
            snippet=self.snippet,
            document_title=self.metadata.get("document_title"),
            letter_no=self.metadata.get("letter_no"),
            file_name=self.metadata.get("file_name"),
            clause_number=self.clause_number,
            clause_title=self.clause_title,
            section_heading=self.section_heading,
            page_numbers=list(self.page_numbers),
        )
