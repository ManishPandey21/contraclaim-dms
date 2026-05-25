from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional


def first_present(*values: Any) -> Optional[Any]:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def source_hash(payload: Dict[str, Any]) -> str:
    parts = [
        str(payload.get("organization_id") or payload.get("org_id") or ""),
        str(payload.get("project_id") or ""),
        str(payload.get("document_id") or ""),
        str(payload.get("chunk_id") or ""),
        str(payload.get("source_type") or payload.get("document_type") or payload.get("uploadType") or ""),
        str(payload.get("letter_no") or payload.get("letterNo") or ""),
        str(payload.get("clause_number") or payload.get("clause_no") or payload.get("clause_id") or ""),
        str(payload.get("text") or payload.get("snippet") or ""),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def normalize_source_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a common source metadata shape while preserving original fields."""

    normalized = dict(payload or {})
    organization_id = first_present(
        normalized.get("organization_id"),
        normalized.get("org_id"),
        normalized.get("organisation_id"),
    )
    project_id = first_present(normalized.get("project_id"), normalized.get("proj_id"))
    document_id = first_present(normalized.get("document_id"), normalized.get("doc_id"), normalized.get("_id"))
    letter_no = first_present(normalized.get("letter_no"), normalized.get("letterNo"))
    clause_number = first_present(
        normalized.get("clause_number"),
        normalized.get("clause_no"),
        normalized.get("clause_id"),
    )
    source_type = first_present(
        normalized.get("source_type"),
        normalized.get("document_type"),
        normalized.get("uploadType"),
        normalized.get("doc_type"),
    )
    if source_type:
        source_type = str(source_type).lower()

    if organization_id is not None:
        normalized["organization_id"] = str(organization_id)
        normalized.setdefault("org_id", str(organization_id))
    if project_id is not None:
        normalized["project_id"] = str(project_id)
    if document_id is not None:
        normalized["document_id"] = str(document_id)
    if letter_no is not None:
        normalized["letter_no"] = str(letter_no)
        normalized.setdefault("letterNo", str(letter_no))
    if clause_number is not None:
        normalized["clause_number"] = str(clause_number)
    if source_type is not None:
        normalized["source_type"] = str(source_type)
    if "page_numbers" not in normalized:
        page = first_present(normalized.get("page"), normalized.get("page_number"), normalized.get("page_start"))
        normalized["page_numbers"] = [int(page)] if str(page or "").isdigit() else []
    normalized.setdefault("source_hash", source_hash(normalized))
    return normalized
