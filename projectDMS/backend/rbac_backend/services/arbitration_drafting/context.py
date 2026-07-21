from __future__ import annotations

import hashlib
from textwrap import shorten
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException, status

from ...models.arbitration_drafting import ArbitrationSelectedReferenceCreate
from ...models.contract_models import ContractSearchRequest
from ..contract_service import ContractService
from ..evidence_graph_service import EvidenceGraphService
from .matrix_registry import MATRIX_COLLECTIONS


VERIFIED_SOURCE_STATUSES = {
    "approved",
    "edited_verified",
    "ready",
    "selected",
    "supported",
    "user_verified",
    "valid",
    "verified",
}

REVIEW_ONLY_SOURCE_STATUSES = {
    "ai_suggested",
    "draft",
    "needs_review",
    "pending",
    "under_review",
}


async def _collect(cursor: Any) -> List[Dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return [dict(item) for item in await cursor.to_list(length=None)]
    return [dict(item) async for item in cursor]


def condense(value: Any, width: int = 700) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) <= width:
        return text
    return shorten(text, width=width, placeholder="...")


def source_hash(source: Dict[str, Any]) -> str:
    metadata = source.get("metadata") or {}
    raw = "|".join(
        [
            str(source.get("source_type") or ""),
            str(source.get("source_id") or ""),
            str(source.get("citation") or ""),
            str(source.get("snippet") or ""),
            ",".join(str(page) for page in source.get("page_numbers") or []),
            str(source.get("verification_status") or ""),
            str(metadata.get("matrix_row_id") or ""),
            str(metadata.get("authoritative_revision_id") or ""),
            str(metadata.get("authoritative_sha256") or ""),
            str(metadata.get("authoritative_updated_at") or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _first_present(row: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", []):
            return value
    return None


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _string_list(value: Any) -> List[str]:
    return [str(item) for item in _as_list(value) if item not in (None, "")]


def _status_values(row: Dict[str, Any]) -> set[str]:
    return {
        str(row.get(key) or "").strip().lower()
        for key in ["verification_status", "approval_status", "human_approval_status", "readiness_status", "status"]
        if row.get(key) is not None
    }


def _is_rejected(row: Dict[str, Any]) -> bool:
    return bool(_status_values(row) & {"duplicate", "rejected", "superseded"})


def _is_verified_source(row: Dict[str, Any], *, include_review_sources: bool = False) -> bool:
    statuses = _status_values(row)
    if _is_rejected(row):
        return False
    if statuses & VERIFIED_SOURCE_STATUSES:
        return True
    if include_review_sources and (not statuses or statuses & REVIEW_ONLY_SOURCE_STATUSES):
        return True
    return False


def _allowed_use(value: Any, fallback: str = "fact") -> str:
    raw = str(value or fallback).strip().lower()
    if raw in {
        "annexure",
        "background",
        "chronology",
        "clause",
        "expert",
        "fact",
        "notice",
        "quantum",
    }:
        return raw
    if raw.startswith("soc_") or raw.startswith("sod_") or raw.startswith("rejoinder_") or raw == "counterclaim":
        return "chronology"
    return fallback


def _numeric_amount(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    import re as _re

    cleaned = _re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in {"", "-", "."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _money(value: Any, currency: Optional[str] = None) -> Optional[str]:
    if value in (None, ""):
        return None
    return f"{currency or ''} {value}".strip()


class ArbitrationContextBuilder:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def build(
        self,
        draft: Dict[str, Any],
        references: List[Dict[str, Any]],
        claim_heads: List[Dict[str, Any]],
        paragraph_responses: List[Dict[str, Any]],
        current_user: Any,
        *,
        include_unverified_graph_links: bool = False,
    ) -> Dict[str, Any]:
        context_warnings: List[str] = []
        references = await self.rehydrate_selected_references(draft, references)
        source_ledger = [self._ledger_row(ref, idx) for idx, ref in enumerate(references, start=1)]
        source_ledger.extend(
            await self._case_workspace_sources(
                draft,
                len(source_ledger),
                include_review_sources=include_unverified_graph_links,
                context_warnings=context_warnings,
            )
        )
        source_ledger.extend(await self._contract_search_sources(draft, current_user, len(source_ledger)))
        source_ledger.extend(
            await self._verified_graph_sources(
                draft,
                include_unverified_graph_links,
                len(source_ledger),
                context_warnings,
            )
        )
        source_ledger = self._dedupe(source_ledger)
        self._annotate_source_quality(source_ledger, context_warnings)
        matrix_context = self._matrix_context(source_ledger)
        self._expert_consistency_warnings(matrix_context, context_warnings)
        missing = self._missing_evidence(draft, source_ledger, claim_heads, paragraph_responses)
        return {
            "draft": draft,
            "source_ledger": source_ledger,
            "matrix_context": matrix_context,
            "claim_heads": claim_heads,
            "paragraph_responses": paragraph_responses,
            "missing_evidence": missing,
            "context_warnings": context_warnings,
        }

    async def rehydrate_selected_references(
        self,
        draft: Dict[str, Any],
        references: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Resolve selected IDs against scoped server records and discard client prose.

        Manual facts remain available for working drafts, but they are explicitly
        marked unverified and are blocked by filing validation. Every other
        selected reference must resolve to an authoritative scoped record or an
        approved matrix revision belonging to the linked case.
        """

        hydrated: List[Dict[str, Any]] = []
        for reference in references or []:
            source_type = str(reference.get("source_type") or "")
            if source_type == "manual_fact":
                hydrated.append(
                    {
                        **reference,
                        "metadata": {
                            **(reference.get("metadata") or {}),
                            "source_origin": "manual_user_input",
                            "verification_status": "needs_review",
                        },
                    }
                )
                continue
            matrix_row_id = (reference.get("metadata") or {}).get("matrix_row_id")
            if matrix_row_id:
                matrix_reference = await self._rehydrate_matrix_reference(draft, str(matrix_row_id), source_type)
                if matrix_reference:
                    hydrated.append({**matrix_reference, "_id": reference.get("_id"), "draft_id": reference.get("draft_id")})
                    continue
            authoritative = await self._rehydrate_direct_reference(draft, reference)
            if not authoritative:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "message": "Selected arbitration source could not be verified in the current scope",
                        "source_type": source_type,
                        "source_id": reference.get("source_id"),
                    },
                )
            hydrated.append({**authoritative, "_id": reference.get("_id"), "draft_id": reference.get("draft_id")})
        return hydrated

    async def _rehydrate_matrix_reference(
        self,
        draft: Dict[str, Any],
        matrix_row_id: str,
        source_type: str,
    ) -> Optional[Dict[str, Any]]:
        case_id = draft.get("case_id")
        if not case_id:
            return None
        scope = {
            "_id": matrix_row_id,
            "case_id": case_id,
            "deleted_at": {"$exists": False},
        }
        for slug, collection_name in MATRIX_COLLECTIONS.items():
            row = await self.db[collection_name].find_one(scope)
            if not row:
                continue
            if row.get("organization_id") and str(row.get("organization_id")) != str(draft.get("organization_id")):
                return None
            if row.get("project_id") and str(row.get("project_id")) != str(draft.get("project_id")):
                return None
            if not _is_verified_source(row):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": "Unapproved matrix rows cannot be selected as drafting evidence",
                        "matrix": slug,
                        "matrix_row_id": matrix_row_id,
                    },
                )
            if slug == "document-index":
                return {
                    "source_type": row.get("source_type") or "document",
                    "source_id": str(row.get("source_id")),
                    "label": row.get("title") or row.get("document_type") or row.get("exhibit_id") or "Case document",
                    "citation": row.get("exhibit_id") or row.get("letter_no") or row.get("title"),
                    "snippet": row.get("relevance_note") or row.get("summary") or row.get("document_type"),
                    "page_numbers": row.get("page_numbers") or [],
                    "letter_no": row.get("letter_no"),
                    "allowed_use": row.get("allowed_use") or "fact",
                    "metadata": {
                        "matrix": slug,
                        "matrix_row_id": row.get("_id"),
                        "source_origin": "approved_matrix_revision",
                        "verification_status": "approved",
                        "exhibit_id": row.get("exhibit_id"),
                    },
                }
            if slug == "clause-matrix":
                return {
                    "source_type": "clause",
                    "source_id": str(row.get("clause_source_id") or row.get("source_id") or row.get("_id")),
                    "label": row.get("topic") or row.get("clause_number") or "Clause matrix row",
                    "citation": row.get("clause_number") or row.get("topic"),
                    "snippet": row.get("clause_text_excerpt") or row.get("obligation_or_right"),
                    "page_numbers": row.get("page_numbers") or [],
                    "clause_number": row.get("clause_number"),
                    "allowed_use": "clause",
                    "metadata": {
                        "matrix": slug,
                        "matrix_row_id": row.get("_id"),
                        "source_origin": "approved_matrix_revision",
                        "verification_status": "approved",
                        "risk": row.get("risk"),
                    },
                }
            return {
                "source_type": source_type,
                "source_id": str(row.get("source_id") or row.get("_id")),
                "label": row.get("title") or row.get("claim_head") or row.get("issue") or row.get("topic") or slug,
                "citation": row.get("citation") or row.get("claim_no") or row.get("issue_no"),
                "snippet": row.get("facts") or row.get("summary") or row.get("relevance_note"),
                "page_numbers": row.get("page_numbers") or [],
                "allowed_use": row.get("allowed_use") or "fact",
                "metadata": {
                    "matrix": slug,
                    "matrix_row_id": row.get("_id"),
                    "source_origin": "approved_matrix_revision",
                    "verification_status": "approved",
                },
            }
        return None

    async def _rehydrate_direct_reference(
        self,
        draft: Dict[str, Any],
        reference: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        source_type = str(reference.get("source_type") or "")
        source_id = str(reference.get("source_id") or "")
        if not source_id:
            return None
        collection_names = {
            "document": ("documents",),
            "letter": ("letters", "documents"),
            "clause": ("document_vectors", "contract_clauses"),
            "claim": ("claims",),
            "variation": ("variations",),
            "payment_event": ("ipc_bills",),
            "bank_guarantee": ("bank_guarantees",),
            "chronology_event": ("matter_chronology_events",),
            "project_event": ("matter_chronology_events",),
            "expert_report": ("documents",),
        }.get(source_type, ())
        for collection_name in collection_names:
            query: Dict[str, Any] = {"_id": source_id}
            if draft.get("organization_id"):
                query["organization_id"] = draft.get("organization_id")
            if draft.get("project_id"):
                query["project_id"] = draft.get("project_id")
            record = await self.db[collection_name].find_one(query)
            if not record and source_type == "clause" and collection_name == "document_vectors":
                query.pop("_id", None)
                query["document_id"] = source_id
                record = await self.db[collection_name].find_one(query)
            if not record:
                continue
            label = (
                record.get("subject")
                or record.get("filename")
                or record.get("clause_title")
                or record.get("claim_number")
                or record.get("variation_number")
                or record.get("_id")
            )
            citation = (
                record.get("letterNo")
                or record.get("clause_number")
                or record.get("claim_number")
                or record.get("variation_number")
                or record.get("filename")
            )
            snippet = (
                record.get("summary")
                or record.get("ocrText")
                or record.get("text")
                or record.get("text_enriched")
                or record.get("description")
                or label
            )
            return {
                "source_type": source_type,
                "source_id": source_id,
                "label": str(label or source_id),
                "citation": citation,
                "snippet": condense(snippet, 650),
                "page_numbers": record.get("page_numbers") or [],
                "clause_number": record.get("clause_number"),
                "letter_no": record.get("letterNo"),
                "allowed_use": reference.get("allowed_use") or ("clause" if source_type == "clause" else "fact"),
                "metadata": {
                    "source_origin": f"authoritative:{collection_name}",
                    "verification_status": "verified",
                    "authoritative_revision_id": record.get("current_version_id") or record.get("version_id"),
                    "authoritative_updated_at": record.get("updated_at"),
                    "authoritative_sha256": record.get("sha256"),
                },
            }
        return None

    def _ledger_row(self, ref: Dict[str, Any], idx: int) -> Dict[str, Any]:
        citation = ref.get("citation") or ref.get("clause_number") or ref.get("letter_no") or ref.get("label")
        row = {
            "source_key": f"S{idx}",
            "source_id": str(ref.get("source_id") or ref.get("_id") or idx),
            "source_type": ref.get("source_type"),
            "allowed_use": ref.get("allowed_use") or "fact",
            "permitted_uses": _string_list(ref.get("permitted_uses") or ref.get("allowed_use") or "fact"),
            "label": ref.get("label") or ref.get("citation") or f"Source {idx}",
            "citation": citation,
            "snippet": condense(ref.get("snippet") or ref.get("metadata", {}).get("text"), 650),
            "page_numbers": ref.get("page_numbers") or [],
            "clause_number": ref.get("clause_number"),
            "letter_no": ref.get("letter_no"),
            "verification_status": ref.get("metadata", {}).get("verification_status") or ref.get("verification_status") or "selected",
            "is_user_supplied": ref.get("source_type") == "manual_fact",
            "source_origin": ref.get("metadata", {}).get("source_origin") or "selected_reference",
            "quality_flags": [],
            "metadata": ref.get("metadata") or {},
            "source_hash": "",
        }
        row["source_hash"] = source_hash(row)
        return row

    async def _case_workspace_sources(
        self,
        draft: Dict[str, Any],
        offset: int,
        *,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        case_id = draft.get("case_id")
        if not case_id:
            return []
        rows: List[Dict[str, Any]] = []
        rows.extend(await self._document_index_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._chronology_matrix_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._clause_matrix_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._issue_matrix_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._claim_defence_matrix_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._quantum_matrix_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._notice_matrix_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._jurisdiction_matrix_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._expert_alignment_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._register_sources(draft, offset + len(rows), include_review_sources, context_warnings))
        return rows

    async def _jurisdiction_matrix_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_jurisdiction_matrix.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            check_type = str(row.get("check_type") or "jurisdiction")
            if check_type == "limitation":
                label = f"Limitation: {row.get('subject') or row.get('limitation_subject_id') or 'case'}"
                snippet = "\n".join(
                    part
                    for part in [
                        f"Status: {row.get('limitation_status')}" if row.get("limitation_status") else "",
                        f"Base date: {row.get('limitation_base_date')}" if row.get("limitation_base_date") else "",
                        f"Expiry: {row.get('limitation_expiry_date')}" if row.get("limitation_expiry_date") else "",
                        str(row.get("basis") or ""),
                    ]
                    if part
                )
                citation = row.get("limitation_status") or check_type
            elif check_type == "pre_arbitration_step":
                label = f"Pre-arbitration step: {row.get('step')}"
                snippet = "\n".join(
                    part
                    for part in [
                        f"Required: {row.get('required')}" if row.get("required") is not None else "",
                        f"Compliance: {row.get('compliance_status')}" if row.get("compliance_status") else "",
                        str(row.get("contractual_requirement") or ""),
                    ]
                    if part
                )
                citation = row.get("compliance_status") or check_type
            else:
                label = "Arbitration clause scope check"
                snippet = "\n".join(
                    part
                    for part in [
                        f"Scope status: {row.get('scope_status')}" if row.get("scope_status") else "",
                        str(row.get("claim_description") or ""),
                        str(row.get("notes") or ""),
                    ]
                    if part
                )
                citation = row.get("scope_status") or check_type
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("_id")),
                "source_type": "jurisdiction_check",
                "allowed_use": "fact",
                "permitted_uses": ["background", "fact"],
                "label": label,
                "citation": citation,
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": None,
                "verification_status": row.get("approval_status") or "approved",
                "is_user_supplied": True,
                "source_origin": "case_jurisdiction_matrix",
                "matrix_row_id": row.get("_id"),
                "quality_flags": [],
                "metadata": {
                    "case_id": case_id,
                    "matrix": "jurisdiction-matrix",
                    "matrix_row_id": row.get("_id"),
                    "check_type": check_type,
                    "limitation_status": row.get("limitation_status"),
                    "compliance_status": row.get("compliance_status"),
                    "scope_status": row.get("scope_status"),
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _expert_alignment_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_expert_alignment.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            snippet = "\n".join(
                str(part)
                for part in [
                    row.get("methodology"),
                    row.get("notes"),
                    *(row.get("contradictions") or []),
                ]
                if part
            )
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("expert_report_source_id") or row.get("_id")),
                "source_type": "expert_report",
                "allowed_use": "expert",
                "permitted_uses": ["expert", "fact"],
                "label": f"{row.get('expert_type') or 'expert'} alignment: claim {row.get('claim_no')}",
                "citation": row.get("claim_no") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": None,
                "verification_status": row.get("alignment_status") or row.get("approval_status") or "approved",
                "is_user_supplied": False,
                "source_origin": "case_expert_alignment",
                "matrix_row_id": row.get("_id"),
                "quality_flags": [],
                "metadata": {
                    "case_id": case_id,
                    "matrix": "expert-alignment",
                    "matrix_row_id": row.get("_id"),
                    "expert_type": row.get("expert_type"),
                    "claim_no": row.get("claim_no"),
                    "pleaded_amount": row.get("pleaded_amount"),
                    "verified_amount": row.get("verified_amount"),
                    "calculation_match": row.get("calculation_match"),
                    "concurrency_addressed": row.get("concurrency_addressed"),
                    "contradictions": row.get("contradictions") or [],
                    "risk_flags": row.get("risk_flags") or [],
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _document_index_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_document_index.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            if not row.get("source_id"):
                context_warnings.append(f"Document index row has no source id and was skipped: {row.get('title') or row.get('_id')}")
                continue
            if include_review_sources and not (_status_values(row) & VERIFIED_SOURCE_STATUSES):
                context_warnings.append(f"Review-only document index source included: {row.get('title') or row.get('exhibit_id')}")
            allowed = _allowed_use(row.get("allowed_use"), "fact")
            exhibit_id = row.get("exhibit_id")
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("source_id")),
                "source_type": row.get("source_type") or "document",
                "allowed_use": allowed,
                "permitted_uses": sorted(set([allowed, *[_allowed_use(item, allowed) for item in _as_list(row.get("permitted_uses"))]])),
                "label": row.get("title") or row.get("document_type") or exhibit_id or "Case document",
                "citation": exhibit_id or row.get("letter_no") or row.get("title"),
                "snippet": condense(row.get("relevance_note") or row.get("summary") or row.get("document_type"), 650),
                "page_numbers": row.get("page_numbers") or [],
                "clause_number": None,
                "letter_no": row.get("letter_no"),
                "verification_status": row.get("verification_status") or row.get("human_approval_status") or "approved",
                "is_user_supplied": False,
                "source_origin": "case_document_index",
                "matrix_row_id": row.get("_id"),
                "exhibit_id": exhibit_id,
                "quality_flags": [],
                "metadata": {
                    "case_id": case_id,
                    "matrix": "document-index",
                    "matrix_row_id": row.get("_id"),
                    "document_date": row.get("document_date"),
                    "document_type": row.get("document_type"),
                    "source_file_link": row.get("source_file_link"),
                    "issue_tags": row.get("issue_tags") or [],
                    "claim_tags": row.get("claim_tags") or [],
                    "risk_flags": row.get("risk_flags") or [],
                    "exhibit_id": exhibit_id,
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _chronology_matrix_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_chronology_matrix.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("date", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            event = await self._load_chronology_event(row)
            event_data = {**row, **{f"event_{key}": value for key, value in (event or {}).items()}}
            title = row.get("event") or (event or {}).get("title") or "Chronology event"
            citation = row.get("document_ref") or row.get("date") or (event or {}).get("date_text") or (event or {}).get("letter_no") or title
            snippet = row.get("impact") or row.get("evidence") or (event or {}).get("description") or (event or {}).get("manual_notes")
            allowed = _allowed_use(row.get("pleading_use") or (event or {}).get("pleading_use"), "chronology")
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("chronology_event_id") or row.get("_id")),
                "source_type": "chronology_event",
                "allowed_use": allowed,
                "permitted_uses": sorted(set([allowed, "chronology", "fact"])),
                "label": condense(title, 180),
                "citation": citation,
                "snippet": condense(snippet, 650),
                "page_numbers": [event.get("source_page")] if event and event.get("source_page") else [],
                "clause_number": row.get("clause") or ", ".join((event or {}).get("contract_clauses") or []) or None,
                "letter_no": (event or {}).get("letter_no"),
                "verification_status": row.get("verification_status") or (event or {}).get("verification_status") or "verified",
                "is_user_supplied": False,
                "source_origin": "case_chronology_matrix",
                "matrix_row_id": row.get("_id"),
                "quality_flags": [],
                "metadata": {
                    "case_id": case_id,
                    "matrix": "chronology-matrix",
                    "matrix_row_id": row.get("_id"),
                    "chronology_id": row.get("chronology_id") or (event or {}).get("chronology_id"),
                    "chronology_event_id": row.get("chronology_event_id"),
                    "party_responsible": row.get("party_responsible") or (event or {}).get("responsible_party"),
                    "issue_link": row.get("issue_link"),
                    "claim_link": row.get("claim_link"),
                    "event": event_data,
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _load_chronology_event(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        event_id = row.get("chronology_event_id")
        chronology_id = row.get("chronology_id")
        if not event_id:
            return None
        query: Dict[str, Any] = {"_id": event_id}
        if chronology_id:
            query["chronology_id"] = chronology_id
        try:
            event = await self.db.matter_chronology_events.find_one(query)
        except Exception:
            return None
        return dict(event) if event else None

    async def _clause_matrix_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_clause_matrix.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            citation = row.get("clause_number") or row.get("topic") or row.get("_id")
            snippet = row.get("clause_text_excerpt") or row.get("obligation_or_right")
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("clause_source_id") or row.get("_id")),
                "source_type": "clause",
                "allowed_use": "clause",
                "permitted_uses": ["clause"],
                "label": row.get("topic") or f"Clause {citation}",
                "citation": citation,
                "snippet": condense(snippet, 650),
                "page_numbers": row.get("page_numbers") or [],
                "clause_number": row.get("clause_number"),
                "letter_no": None,
                "verification_status": row.get("approval_status") or "approved",
                "is_user_supplied": False,
                "source_origin": "case_clause_matrix",
                "matrix_row_id": row.get("_id"),
                "quality_flags": [],
                "metadata": {
                    "case_id": case_id,
                    "matrix": "clause-matrix",
                    "matrix_row_id": row.get("_id"),
                    "claimant_use": row.get("claimant_use"),
                    "respondent_use": row.get("respondent_use"),
                    "related_evidence_ids": row.get("related_evidence_ids") or [],
                    "risk": row.get("risk"),
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _issue_matrix_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_issue_matrix.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            snippet = "\n".join(
                part
                for part in [
                    f"Issue: {row.get('issue')}" if row.get("issue") else "",
                    f"Claimant: {row.get('claimant_position')}" if row.get("claimant_position") else "",
                    f"Respondent: {row.get('respondent_position')}" if row.get("respondent_position") else "",
                    f"Required finding: {row.get('required_finding')}" if row.get("required_finding") else "",
                ]
                if part
            )
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("_id")),
                "source_type": "issue_matrix",
                "allowed_use": "fact",
                "permitted_uses": ["background", "fact"],
                "label": row.get("issue") or row.get("issue_no") or "Issue matrix row",
                "citation": row.get("issue_no") or row.get("issue_type") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": ", ".join(_string_list(row.get("clause_ids"))) or None,
                "letter_no": None,
                "verification_status": row.get("status") or row.get("approval_status") or "approved",
                "is_user_supplied": True,
                "source_origin": "case_issue_matrix",
                "matrix_row_id": row.get("_id"),
                "quality_flags": [],
                "metadata": {"case_id": case_id, "matrix": "issue-matrix", "matrix_row_id": row.get("_id"), "issue_type": row.get("issue_type")},
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _claim_defence_matrix_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        matrix_specs = [
            ("claim-matrix", self.db.arbitration_claim_matrix, "claim_matrix", "claim_no", "claim_head", "facts", "claim"),
            ("defence-matrix", self.db.arbitration_defence_matrix, "defence_matrix", "source_claim_no", "defence", "positive_case", "fact"),
            ("counterclaim-matrix", self.db.arbitration_counterclaim_matrix, "counterclaim_matrix", "counterclaim_no", "breach", "facts", "claim"),
            ("rejoinder-matrix", self.db.arbitration_rejoinder_matrix, "rejoinder_matrix", "source_sod_para", "claimant_reply", "nature_of_defence", "fact"),
        ]
        out: List[Dict[str, Any]] = []
        for slug, collection, source_type, citation_key, label_key, snippet_key, use in matrix_specs:
            try:
                rows = await _collect(collection.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1))
            except Exception:
                continue
            for row in rows:
                if not _is_verified_source(row, include_review_sources=include_review_sources):
                    continue
                if slug == "claim-matrix":
                    snippet = "\n".join(str(part) for part in [row.get("facts"), row.get("causation"), row.get("relief"), row.get("weakness")] if part)
                elif slug == "counterclaim-matrix":
                    snippet = "\n".join(str(part) for part in [row.get("facts"), row.get("breach"), row.get("causation"), row.get("relief")] if part)
                elif slug == "rejoinder-matrix":
                    snippet = "\n".join(str(part) for part in [row.get("nature_of_defence"), row.get("claimant_reply"), row.get("reply_to_counterclaim")] if part)
                else:
                    snippet = "\n".join(str(part) for part in [row.get("admission_denial"), row.get("defence"), row.get("positive_case")] if part)
                allowed = "quantum" if row.get("amount_or_days") or row.get("amount") else use
                ledger_row = {
                    "source_key": f"S{offset + len(out) + 1}",
                    "source_id": str(row.get("_id")),
                    "source_type": source_type,
                    "allowed_use": _allowed_use(allowed, "fact"),
                    "permitted_uses": sorted(set(["fact", _allowed_use(allowed, "fact")])),
                    "label": row.get(label_key) or row.get(snippet_key) or row.get(citation_key) or slug,
                    "citation": row.get(citation_key) or row.get("_id"),
                    "snippet": condense(snippet, 650),
                    "page_numbers": [],
                    "clause_number": ", ".join(_string_list(row.get("clause_ids"))) or None,
                    "letter_no": None,
                    "verification_status": row.get("readiness_status") or row.get("approval_status") or "approved",
                    "is_user_supplied": False,
                    "source_origin": f"case_{slug}",
                    "matrix_row_id": row.get("_id"),
                    "quality_flags": [],
                    "metadata": {
                        "case_id": case_id,
                        "matrix": slug,
                        "matrix_row_id": row.get("_id"),
                        "evidence_ids": row.get("evidence_ids") or [],
                        "notice_ids": row.get("notice_ids") or [],
                        "calculation_id": row.get("calculation_id"),
                        "amount_or_days": row.get("amount_or_days"),
                        "new_matter": row.get("new_matter"),
                        "permission_required": row.get("permission_required"),
                        "permission_obtained": row.get("permission_obtained"),
                        "permission_source_id": row.get("permission_source_id"),
                        "permission_approved_by": row.get("permission_approved_by"),
                        "permission_approved_at": row.get("permission_approved_at"),
                    },
                    "source_hash": "",
                }
                ledger_row["source_hash"] = source_hash(ledger_row)
                out.append(ledger_row)
        return out

    async def _quantum_matrix_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_quantum_annexures.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            amount = _money(row.get("amount"), row.get("currency"))
            snippet = "\n".join(str(part) for part in [row.get("formula"), row.get("assumptions"), amount] if part)
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("calculation_id") or row.get("_id")),
                "source_type": "quantum_annexure",
                "allowed_use": "quantum",
                "permitted_uses": ["annexure", "quantum"],
                "label": row.get("calculation_type") or row.get("calculation_id") or "Quantum annexure",
                "citation": row.get("calculation_id") or row.get("calculation_type") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": None,
                "verification_status": row.get("approval_status") or "approved",
                "is_user_supplied": False,
                "source_origin": "case_quantum_annexure",
                "matrix_row_id": row.get("_id"),
                "quality_flags": [],
                "metadata": {
                    "case_id": case_id,
                    "matrix": "quantum-annexures",
                    "matrix_row_id": row.get("_id"),
                    "source_records": row.get("source_records") or [],
                    "calculation_type": row.get("calculation_type"),
                    "amount": row.get("amount"),
                    "currency": row.get("currency"),
                    "tax_treatment": row.get("tax_treatment"),
                    "cost_head": row.get("cost_head"),
                    "critical_path_days": row.get("critical_path_days"),
                    "delay_event_ids": row.get("delay_event_ids") or [],
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _notice_matrix_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_notice_compliance.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            # UI rows use requirement/risk_note; agent rows use contractual_requirement/risk.
            snippet = "\n".join(
                str(part)
                for part in [
                    row.get("requirement") or row.get("contractual_requirement"),
                    row.get("compliance_status"),
                    row.get("risk_note") or row.get("risk"),
                ]
                if part
            )
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("_id")),
                "source_type": "notice_compliance",
                "allowed_use": "notice",
                "permitted_uses": ["fact", "notice"],
                "label": row.get("notice_ref") or "Notice compliance",
                "citation": row.get("notice_ref") or row.get("notice_date") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": row.get("clause") or row.get("clause_number"),
                "letter_no": row.get("notice_ref"),
                "verification_status": row.get("approval_status") or "approved",
                "is_user_supplied": False,
                "source_origin": "case_notice_compliance",
                "matrix_row_id": row.get("_id"),
                "quality_flags": [],
                "metadata": {"case_id": case_id, "matrix": "notice-compliance", "matrix_row_id": row.get("_id")},
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _register_sources(
        self,
        draft: Dict[str, Any],
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        if not draft.get("project_id"):
            return []
        # Audit P2: registers are included by default, but the user controls it.
        if draft.get("include_register_sources") is False:
            context_warnings.append(
                "Project register sources (claims, variations, IPCs, bank guarantees) are disabled for this draft."
            )
            return []
        out: List[Dict[str, Any]] = []
        out.extend(await self._claim_register_sources(draft, offset + len(out), include_review_sources))
        out.extend(await self._variation_register_sources(draft, offset + len(out), include_review_sources))
        out.extend(await self._ipc_register_sources(draft, offset + len(out), include_review_sources))
        out.extend(await self._bank_guarantee_sources(draft, offset + len(out), include_review_sources))
        excluded = {str(item) for item in draft.get("excluded_register_ids") or [] if item}
        if excluded:
            kept = [row for row in out if str(row.get("source_id")) not in excluded]
            removed_count = len(out) - len(kept)
            if removed_count:
                context_warnings.append(
                    f"{removed_count} register source(s) excluded from this draft by user selection."
                )
            out = kept
        return out

    def _scope_query(self, draft: Dict[str, Any], *, require_contract: bool = False) -> Optional[Dict[str, Any]]:
        query: Dict[str, Any] = {"project_id": draft.get("project_id")}
        if draft.get("organization_id"):
            query["organization_id"] = draft.get("organization_id")
        if draft.get("contract_id"):
            query["contract_id"] = draft.get("contract_id")
        elif require_contract:
            return None
        return query

    async def _claim_register_sources(self, draft: Dict[str, Any], offset: int, include_review_sources: bool) -> List[Dict[str, Any]]:
        query = self._scope_query(draft)
        if not query:
            return []
        if not include_review_sources:
            query["status"] = {"$in": ["notified", "submitted", "under_review", "agreed", "rejected", "disputed", "closed"]}
        try:
            rows = await _collect(self.db.claims.find(query).sort("updated_at", -1).limit(10))
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            amount = _money(row.get("amount_claimed"), row.get("currency"))
            eot = f"{row.get('eot_days_claimed')} days" if row.get("eot_days_claimed") is not None else None
            snippet = "\n".join(str(part) for part in [row.get("description"), amount, eot] if part)
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("_id")),
                "source_type": "claim",
                "allowed_use": "quantum" if amount or eot else "fact",
                "permitted_uses": ["fact", "quantum"],
                "label": row.get("title") or row.get("claim_ref") or "Claim register",
                "citation": row.get("claim_ref") or row.get("title") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": ", ".join(row.get("contract_clauses") or []) or None,
                "letter_no": None,
                "verification_status": row.get("status") or "register",
                "is_user_supplied": False,
                "source_origin": "claim_register",
                "quality_flags": [],
                "metadata": {
                    "claim_type": row.get("type"),
                    "status": row.get("status"),
                    "linked_document_ids": row.get("linked_document_ids") or [],
                    "linked_letter_ids": row.get("linked_letter_ids") or [],
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _variation_register_sources(self, draft: Dict[str, Any], offset: int, include_review_sources: bool) -> List[Dict[str, Any]]:
        query = self._scope_query(draft)
        if not query:
            return []
        if not include_review_sources:
            query["status"] = {"$in": ["submitted", "under_review", "recommended", "approved", "rejected"]}
        try:
            rows = await _collect(self.db.variations.find(query).sort("updated_at", -1).limit(10))
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            snippet = "\n".join(
                str(part)
                for part in [
                    row.get("description"),
                    _money(row.get("submitted_amount")),
                    _money(row.get("approved_amount")),
                    row.get("remarks"),
                ]
                if part
            )
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("_id")),
                "source_type": "variation",
                "allowed_use": "quantum" if row.get("submitted_amount") or row.get("approved_amount") else "fact",
                "permitted_uses": ["fact", "quantum"],
                "label": row.get("variation_number") or "Variation register",
                "citation": row.get("variation_number") or row.get("letter_reference") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": row.get("letter_reference"),
                "verification_status": row.get("status") or "register",
                "is_user_supplied": False,
                "source_origin": "variation_register",
                "quality_flags": [],
                "metadata": {"variation_type": row.get("variation_type"), "linked_document_ids": row.get("linked_document_ids") or []},
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _ipc_register_sources(self, draft: Dict[str, Any], offset: int, include_review_sources: bool) -> List[Dict[str, Any]]:
        query = self._scope_query(draft)
        if not query:
            return []
        if not include_review_sources:
            query["status"] = {"$in": ["submitted", "under_verification", "verified", "approved", "partially_paid", "paid", "rejected"]}
        try:
            rows = await _collect(self.db.ipc_bills.find(query).sort("ipc_date", -1).limit(10))
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            currency = row.get("base_currency") or "INR"
            snippet = "\n".join(
                str(part)
                for part in [
                    row.get("ipc_period"),
                    _money(row.get("claimed_total_base"), currency),
                    _money(row.get("approved_total_base"), currency),
                    _money(row.get("net_payable_base"), currency),
                    row.get("remarks"),
                ]
                if part
            )
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("_id")),
                "source_type": "payment_event",
                "allowed_use": "quantum",
                "permitted_uses": ["fact", "quantum"],
                "label": row.get("ipc_number") or "IPC bill",
                "citation": row.get("ipc_number") or row.get("ipc_period") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": None,
                "verification_status": row.get("status") or "register",
                "is_user_supplied": False,
                "source_origin": "ipc_register",
                "quality_flags": [],
                "metadata": {
                    "ipc_date": row.get("ipc_date"),
                    "status": row.get("status"),
                    "letter_references": row.get("letter_references") or [],
                    "linked_document_ids": row.get("linked_document_ids") or [],
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _bank_guarantee_sources(self, draft: Dict[str, Any], offset: int, include_review_sources: bool) -> List[Dict[str, Any]]:
        query = self._scope_query(draft)
        if not query:
            return []
        if not include_review_sources:
            query["bg_status"] = {"$in": ["submitted", "valid", "extension_required", "extended", "expired", "released", "encashment_under_process", "encashed"]}
        try:
            rows = await _collect(self.db.bank_guarantees.find(query).sort("updated_at", -1).limit(10))
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            snippet = "\n".join(
                str(part)
                for part in [
                    row.get("issuing_bank"),
                    _money(row.get("bg_amount"), row.get("currency")),
                    row.get("bg_expiry_date"),
                    row.get("remarks"),
                ]
                if part
            )
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("_id")),
                "source_type": "bank_guarantee",
                "allowed_use": "fact",
                "permitted_uses": ["fact", "quantum"],
                "label": row.get("bg_number") or row.get("bg_type") or "Bank guarantee",
                "citation": row.get("bg_number") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": None,
                "verification_status": row.get("bg_status") or "register",
                "is_user_supplied": False,
                "source_origin": "bank_guarantee_register",
                "quality_flags": [],
                "metadata": {"bg_type": row.get("bg_type"), "linked_document_ids": row.get("linked_document_ids") or []},
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _contract_search_sources(self, draft: Dict[str, Any], current_user: Any, offset: int) -> List[Dict[str, Any]]:
        query = " ".join(
            [
                str(draft.get("title") or ""),
                str(draft.get("manual_facts") or ""),
                str(draft.get("relief_sought") or ""),
                str(draft.get("arbitration_clause") or ""),
            ]
        ).strip()
        if not query or not draft.get("project_id"):
            return []
        try:
            response = await ContractService().search_contracts(
                ContractSearchRequest(
                    query=query[:800],
                    organization_id=draft.get("organization_id"),
                    project_id=draft.get("project_id"),
                    limit=5,
                    top_docs=3,
                    chunks_per_doc=2,
                    summarize=False,
                ),
                current_user,
            )
        except Exception:
            return []
        rows: List[Dict[str, Any]] = []
        for idx, result in enumerate(response.results or [], start=offset + 1):
            row = {
                "source_key": f"S{idx}",
                "source_id": str(result.document_id or result.upload_id or idx),
                "source_type": "clause",
                "allowed_use": "clause",
                "permitted_uses": ["clause"],
                "label": f"{result.clause_number or 'Clause'} {result.clause_title or ''}".strip(),
                "citation": result.clause_number or result.clause_title or result.file_name,
                "snippet": condense(result.text, 650),
                "page_numbers": result.page_numbers or ([result.page] if result.page else []),
                "clause_number": result.clause_number,
                "letter_no": None,
                "verification_status": "retrieved_clause",
                "is_user_supplied": False,
                "source_origin": "contract_search",
                "quality_flags": [],
                "metadata": {"file_name": result.file_name, "document_id": result.document_id, "upload_id": result.upload_id},
                "source_hash": "",
            }
            row["source_hash"] = source_hash(row)
            rows.append(row)
        return rows

    async def _verified_graph_sources(
        self,
        draft: Dict[str, Any],
        include_unverified_graph_links: bool,
        offset: int,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        if not draft.get("project_id"):
            return []
        if include_unverified_graph_links:
            context_warnings.append("Unverified AI-suggested graph links were included for review mode only.")
        try:
            rows = await EvidenceGraphService(self.db).downstream_links(
                {
                    "organization_id": draft.get("organization_id"),
                    "project_id": draft.get("project_id"),
                },
                include_ai_suggested=include_unverified_graph_links,
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for idx, link in enumerate(rows[:10], start=offset + 1):
            row = {
                "source_key": f"S{idx}",
                "source_id": str(link.get("link_group_id") or link.get("_id")),
                "source_type": "event_link",
                "allowed_use": "chronology",
                "permitted_uses": ["chronology", "fact"],
                "label": f"{link.get('source_type')} {link.get('relation_type')} {link.get('target_type')}",
                "citation": link.get("relation_type"),
                "snippet": condense(link.get("evidence_text"), 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": None,
                "verification_status": link.get("status") or "verified",
                "is_user_supplied": False,
                "source_origin": "evidence_graph",
                "quality_flags": [],
                "metadata": {"link_group_id": link.get("link_group_id"), "relation_type": link.get("relation_type")},
                "source_hash": "",
            }
            row["source_hash"] = source_hash(row)
            out.append(row)
        return out

    def _missing_evidence(
        self,
        draft: Dict[str, Any],
        source_ledger: List[Dict[str, Any]],
        claim_heads: List[Dict[str, Any]],
        paragraph_responses: List[Dict[str, Any]],
    ) -> List[str]:
        missing: List[str] = []
        if not source_ledger:
            missing.append("No selected or retrieved evidence is available for this pleading.")
        if draft.get("manual_facts") and not any(row.get("source_type") != "manual_fact" for row in source_ledger):
            missing.append("Manual facts are user-provided and require independent source support before filing.")
        if draft.get("claim_amount") and not any(row.get("allowed_use") == "quantum" for row in source_ledger):
            missing.append("Claim amount is entered but no quantum/payment source is selected.")
        if draft.get("draft_type") == "rejoinder" and not paragraph_responses:
            missing.append("Statement of Defence paragraphs must be imported for paragraph-wise rejoinder replies.")
        for head in claim_heads:
            if not head.get("supporting_source_ids"):
                missing.append(f"Claim head needs support: {head.get('description')}")
            else:
                known_ids = {str(row.get("source_id")) for row in source_ledger}
                unknown = [str(item) for item in head.get("supporting_source_ids") or [] if str(item) not in known_ids]
                if unknown:
                    missing.append(f"Claim head references unavailable source ids: {', '.join(unknown)}")
        known_ids = {str(row.get("source_id")) for row in source_ledger}
        for response in paragraph_responses:
            unknown = [str(item) for item in response.get("supporting_source_ids") or [] if str(item) not in known_ids]
            if unknown:
                missing.append(f"Paragraph {response.get('source_paragraph_number')} references unavailable source ids: {', '.join(unknown)}")
        for row in source_ledger:
            if row.get("source_origin") == "case_document_index":
                if not row.get("exhibit_id"):
                    missing.append(f"Document index source has no exhibit id: {row.get('label')}")
                if "missing_source_link" in row.get("quality_flags", []):
                    missing.append(f"Document index source has no file/source link: {row.get('label')}")
        return missing

    def _dedupe(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        out: List[Dict[str, Any]] = []
        for row in rows:
            key = (row.get("source_type"), row.get("source_id"), row.get("citation"))
            if key in seen:
                continue
            seen.add(key)
            row["source_key"] = f"S{len(out) + 1}"
            out.append(row)
        return out

    def _matrix_context(self, source_ledger: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        groups = {
            "documents": [],
            "chronology": [],
            "clauses": [],
            "issues": [],
            "claims": [],
            "defences": [],
            "counterclaims": [],
            "rejoinder_replies": [],
            "quantum": [],
            "notices": [],
            "experts": [],
            "jurisdiction": [],
        }
        origin_map = {
            "case_document_index": "documents",
            "case_chronology_matrix": "chronology",
            "case_clause_matrix": "clauses",
            "case_issue_matrix": "issues",
            "case_claim-matrix": "claims",
            "case_defence-matrix": "defences",
            "case_counterclaim-matrix": "counterclaims",
            "case_rejoinder-matrix": "rejoinder_replies",
            "case_quantum_annexure": "quantum",
            "case_notice_compliance": "notices",
            "case_expert_alignment": "experts",
            "case_jurisdiction_matrix": "jurisdiction",
        }
        for row in source_ledger:
            group = origin_map.get(str(row.get("source_origin") or ""))
            if group:
                groups[group].append(row)
        return groups

    def _expert_consistency_warnings(
        self,
        matrix_context: Dict[str, List[Dict[str, Any]]],
        context_warnings: List[str],
    ) -> None:
        """Guide §12.1: pleaded amounts and delay claims must align with expert records."""
        expert_rows = matrix_context.get("experts") or []
        claim_rows = matrix_context.get("claims") or []
        experts_by_claim: Dict[tuple[str, str], Dict[str, Any]] = {}
        for row in expert_rows:
            metadata = row.get("metadata") or {}
            claim_no = str(metadata.get("claim_no") or "")
            expert_type = str(metadata.get("expert_type") or "")
            if claim_no and expert_type:
                experts_by_claim[(expert_type, claim_no)] = row
            for contradiction in metadata.get("contradictions") or []:
                message = f"Expert alignment contradiction: {contradiction}"
                if message not in context_warnings:
                    context_warnings.append(message)
        for claim in claim_rows:
            metadata = claim.get("metadata") or {}
            claim_no = str(claim.get("citation") or "")
            pleaded = _numeric_amount(metadata.get("amount_or_days"))
            if pleaded is None or not claim_no:
                continue
            quantum_expert = experts_by_claim.get(("quantum", claim_no))
            if not quantum_expert:
                continue
            verified = _numeric_amount((quantum_expert.get("metadata") or {}).get("verified_amount"))
            if verified is not None and abs(verified - pleaded) >= 0.01:
                context_warnings.append(
                    f"Pleaded amount {pleaded} for claim {claim_no} does not match the expert-verified amount {verified}."
                )
        for (expert_type, claim_no), row in experts_by_claim.items():
            metadata = row.get("metadata") or {}
            if expert_type == "delay" and not metadata.get("concurrency_addressed"):
                context_warnings.append(
                    f"Concurrency has not been addressed for delay claim {claim_no}; align the pleading with the delay expert."
                )

    def _annotate_source_quality(self, rows: List[Dict[str, Any]], context_warnings: List[str]) -> None:
        for row in rows:
            flags: List[str] = list(row.get("quality_flags") or [])
            if not row.get("citation"):
                flags.append("missing_citation")
            if not row.get("snippet"):
                flags.append("missing_snippet")
            if row.get("source_origin") == "case_document_index":
                metadata = row.get("metadata") or {}
                if not row.get("exhibit_id"):
                    flags.append("missing_exhibit_id")
                if not row.get("source_id") and not metadata.get("source_file_link"):
                    flags.append("missing_source_link")
                if row.get("verification_status") not in VERIFIED_SOURCE_STATUSES:
                    flags.append("not_verified_for_filing")
            if row.get("is_user_supplied"):
                flags.append("user_supplied")
            if str(row.get("verification_status") or "").startswith("ai_"):
                flags.append("unverified_ai_suggestion")
            row["quality_flags"] = sorted(set(flags))
            row["evidence_strength"] = "strong" if not flags else ("medium" if flags == ["missing_snippet"] else "needs_review")
        if any("user_supplied" in row.get("quality_flags", []) for row in rows):
            context_warnings.append("Manual fact sources are not independent evidence and require legal review.")
        if any("unverified_ai_suggestion" in row.get("quality_flags", []) for row in rows):
            context_warnings.append("Source ledger includes unverified AI graph suggestions.")
