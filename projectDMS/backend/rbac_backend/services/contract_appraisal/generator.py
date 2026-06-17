"""Appraisal generator (Contract Appraisal Report — v2 Phase 1).

Loops the report sections, asks each as a citation-required question to the
existing ``RetrievalService.contract_iterative_qa`` engine, and assembles the
markdown report + per-section citations + an aggregate confidence. No new AI
infrastructure — this mirrors ``ClaimAssessmentService`` generalised to sections.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .prompts import APPRAISAL_PROMPT_VERSION, STANDARD_DISCLAIMER, section_questions

logger = logging.getLogger(__name__)

_NOT_FOUND_MARKERS = ("not found in uploaded documents", "information not found")


def _is_unsupported(answer: str, citations: List[Any]) -> bool:
    if citations:
        return False
    text = (answer or "").strip().lower()
    if not text:
        return True
    return any(marker in text for marker in _NOT_FOUND_MARKERS)


def _citation_dicts(citations: Optional[List[Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for c in citations or []:
        out.append(c.model_dump() if hasattr(c, "model_dump") else dict(c))
    return out


def _section_confidence(citations: List[Dict[str, Any]]) -> float:
    scores = [c.get("score") for c in citations if isinstance(c.get("score"), (int, float))]
    if scores:
        return round(sum(scores) / len(scores), 3)
    return 0.5 if citations else 0.0


class AppraisalGenerator:
    def __init__(self, retrieval_service: Any) -> None:
        self.retrieval_service = retrieval_service

    def _build_request(self, query: str, organization_id: str, project_id: str, document_ids: List[str]):
        from ...retrieval.models import ContractQARequest, SearchFilters

        filters = SearchFilters(
            org_id=str(organization_id or ""),
            project_id=str(project_id or ""),
            metadata={"uploadType": "contract", "document_type": "contract"},
        )
        # Where exactly one contract document is selected, scope retrieval to it
        # (SearchFilters only carries a single document_id).
        if len(document_ids) == 1:
            filters.document_id = str(document_ids[0])
        return ContractQARequest(
            query=query,
            filters=filters,
            require_citations=True,
            max_iterations=3,
        )

    async def generate(
        self,
        *,
        organization_id: str,
        project_id: str,
        document_ids: List[str],
        current_user: Any,
        progress_cb: Optional[Callable[[int, str], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        sections_spec = section_questions()
        total = len(sections_spec)
        sections: List[Dict[str, Any]] = []
        all_citations: List[Dict[str, Any]] = []
        md_parts: List[str] = ["# Contract Document Appraisal Report", ""]
        executive_summary = ""

        for idx, (key, title, question) in enumerate(sections_spec, start=1):
            try:
                request = self._build_request(question, organization_id, project_id, document_ids)
                response = await self.retrieval_service.contract_iterative_qa(request, current_user)
                answer = (getattr(response, "answer", "") or "").strip()
                citations = _citation_dicts(getattr(response, "citations", None))
            except Exception:  # pragma: no cover - one bad section must not abort the report
                logger.exception("Appraisal section '%s' generation failed", key)
                answer, citations = "Requires Human Review (generation error).", []

            unsupported = _is_unsupported(answer, citations)
            if unsupported and not answer:
                answer = "Not found in uploaded documents."
            supported = bool(citations) and not unsupported

            sections.append(
                {
                    "key": key,
                    "title": title,
                    "markdown": answer,
                    "citations": citations,
                    "supported": supported,
                    "confidence": _section_confidence(citations),
                }
            )
            all_citations.extend(citations)
            if key == "executive_summary":
                executive_summary = answer

            md_parts.append(f"## {idx}. {title}")
            md_parts.append(answer or "Not found in uploaded documents.")
            if citations:
                refs = "; ".join(_human_citation(c) for c in citations)
                md_parts.append(f"\n*Sources: {refs}*")
            md_parts.append("")

            if progress_cb:
                await progress_cb(int(idx / total * 100), f"Generated: {title}")

        md_parts.append("---")
        md_parts.append(f"## {total + 1}. Disclaimer")
        md_parts.append(STANDARD_DISCLAIMER)

        supported_count = sum(1 for s in sections if s["supported"])
        confidence_score = round(supported_count / total, 3) if total else 0.0

        return {
            "sections": sections,
            "full_report_markdown": "\n".join(md_parts),
            "executive_summary": executive_summary,
            "citations": all_citations,
            "confidence_score": confidence_score,
            "overall_risk_rating": None,  # Phase 2 derives this from structured_output
            "ai_prompt_version": APPRAISAL_PROMPT_VERSION,
        }


def _human_citation(c: Dict[str, Any]) -> str:
    doc = c.get("document_title") or c.get("file_name") or c.get("document_id") or "Source"
    clause = c.get("clause_number")
    page = c.get("page")
    parts = [str(doc)]
    if clause:
        parts.append(f"Clause {clause}")
    if page is not None:
        parts.append(f"Page {page}")
    return ", ".join(parts)
