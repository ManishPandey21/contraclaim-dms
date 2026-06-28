from __future__ import annotations

import hashlib
from textwrap import shorten
from typing import Any, Dict, List

from ...models.arbitration_drafting import ArbitrationSelectedReferenceCreate
from ...models.contract_models import ContractSearchRequest
from ..contract_service import ContractService
from ..evidence_graph_service import EvidenceGraphService


def condense(value: Any, width: int = 700) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) <= width:
        return text
    return shorten(text, width=width, placeholder="...")


def source_hash(source: Dict[str, Any]) -> str:
    raw = "|".join(
        [
            str(source.get("source_type") or ""),
            str(source.get("source_id") or ""),
            str(source.get("citation") or ""),
            str(source.get("snippet") or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        source_ledger = [self._ledger_row(ref, idx) for idx, ref in enumerate(references, start=1)]
        source_ledger.extend(await self._contract_search_sources(draft, current_user, len(source_ledger)))
        source_ledger.extend(await self._verified_graph_sources(draft, include_unverified_graph_links, len(source_ledger)))
        missing = self._missing_evidence(draft, source_ledger, claim_heads, paragraph_responses)
        return {
            "draft": draft,
            "source_ledger": self._dedupe(source_ledger),
            "claim_heads": claim_heads,
            "paragraph_responses": paragraph_responses,
            "missing_evidence": missing,
        }

    def _ledger_row(self, ref: Dict[str, Any], idx: int) -> Dict[str, Any]:
        citation = ref.get("citation") or ref.get("clause_number") or ref.get("letter_no") or ref.get("label")
        row = {
            "source_key": f"S{idx}",
            "source_id": str(ref.get("source_id") or ref.get("_id") or idx),
            "source_type": ref.get("source_type"),
            "allowed_use": ref.get("allowed_use") or "fact",
            "label": ref.get("label") or ref.get("citation") or f"Source {idx}",
            "citation": citation,
            "snippet": condense(ref.get("snippet") or ref.get("metadata", {}).get("text"), 650),
            "page_numbers": ref.get("page_numbers") or [],
            "clause_number": ref.get("clause_number"),
            "letter_no": ref.get("letter_no"),
            "source_hash": "",
        }
        row["source_hash"] = source_hash(row)
        return row

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
                "label": f"{result.clause_number or 'Clause'} {result.clause_title or ''}".strip(),
                "citation": result.clause_number or result.clause_title or result.file_name,
                "snippet": condense(result.text, 650),
                "page_numbers": result.page_numbers or ([result.page] if result.page else []),
                "clause_number": result.clause_number,
                "letter_no": None,
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
    ) -> List[Dict[str, Any]]:
        if not draft.get("project_id"):
            return []
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
                "label": f"{link.get('source_type')} {link.get('relation_type')} {link.get('target_type')}",
                "citation": link.get("relation_type"),
                "snippet": condense(link.get("evidence_text"), 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": None,
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
        if draft.get("claim_amount") and not any(row.get("allowed_use") == "quantum" for row in source_ledger):
            missing.append("Claim amount is entered but no quantum/payment source is selected.")
        if draft.get("draft_type") == "rejoinder" and not paragraph_responses:
            missing.append("Statement of Defence paragraphs must be imported for paragraph-wise rejoinder replies.")
        for head in claim_heads:
            if not head.get("supporting_source_ids"):
                missing.append(f"Claim head needs support: {head.get('description')}")
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

