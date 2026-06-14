"""Structured metadata extracted from OCR/LLM pipelines."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


ReferenceValue = Union[str, Dict[str, Any]]


class ParsedDocumentMetadata(BaseModel):
    date: Optional[str] = None
    subject: Optional[str] = None
    letter_no: Optional[str] = None
    from_company: Optional[str] = None
    to_company: Optional[str] = None
    references: List[ReferenceValue] = Field(default_factory=list)
    summary: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    contractual_clauses: List[str] = Field(default_factory=list)
    full_content: Optional[str] = None


class ProcessingResult(BaseModel):
    """Result of document processing"""
    success: bool
    document_id: Optional[str] = None
    processed_path: Optional[str] = None
    metadata: Optional[ParsedDocumentMetadata] = None
    chunks_created: int = 0
    error: Optional[str] = None
    processing_time: float = 0.0
    metadata_source: str = "legacy_regex"
    metadata_debug: Optional[Dict[str, Any]] = None
    partial_failures: Dict[str, Any] = Field(default_factory=dict)


__all__ = ["ParsedDocumentMetadata", "ProcessingResult", "ReferenceValue"]
