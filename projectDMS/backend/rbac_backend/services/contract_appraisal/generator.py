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
        from ...core.config import settings
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
        # Retrieval breadth is configurable (M3) so large contracts can trade
        # latency for coverage. Clamp to the engine's own bounds (limit<=50,
        # iterations<=5) so an over-eager setting can't raise a validation error.
        limit = max(1, min(50, int(getattr(settings, "CONTRACT_APPRAISAL_RETRIEVAL_LIMIT", 50))))
        max_iterations = max(1, min(5, int(getattr(settings, "CONTRACT_APPRAISAL_QA_MAX_ITERATIONS", 3))))
        return ContractQARequest(
            query=query,
            filters=filters,
            require_citations=True,
            max_iterations=max_iterations,
            limit=limit,
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
        structured_output = build_structured_output(sections, confidence_score)

        return {
            "sections": sections,
            "full_report_markdown": "\n".join(md_parts),
            "executive_summary": executive_summary,
            "citations": all_citations,
            "structured_output": structured_output,
            "confidence_score": confidence_score,
            "overall_risk_rating": structured_output["overall_appraisal"]["overall_risk_rating"],
            "ai_prompt_version": APPRAISAL_PROMPT_VERSION,
        }


# Which sections feed which register, and any fixed attributes for their rows.
_OBLIGATION_SECTIONS = {"employer_obligations": "employer", "contractor_obligations": "contractor"}
_RISK_SECTIONS = {"risk_register"}
_KEY_DATE_SECTIONS = {"time_and_delay", "contract_particulars"}
_TRIGGER_SECTIONS = {"claim_variation_opportunities"}

_CONFIDENCE_REVIEW_THRESHOLD = 0.75  # spec §12: below this → requires human review


def _snippet(c: Dict[str, Any]) -> str:
    text = str(c.get("snippet") or c.get("clause_title") or "").strip()
    return text[:400]


def _page(c: Dict[str, Any]) -> Optional[int]:
    if c.get("page") is not None:
        return c.get("page")
    pages = c.get("page_numbers") or []
    return pages[0] if pages else None


def _provenance(c: Dict[str, Any], section_conf: float) -> Dict[str, Any]:
    score = c.get("score") if isinstance(c.get("score"), (int, float)) else section_conf
    return {
        "clause_reference": c.get("clause_number"),
        "document_name": c.get("document_title") or c.get("file_name") or c.get("document_id"),
        "page_number": _page(c),
        "source_quote": _snippet(c),
        "confidence_score": round(float(score), 3),
        "verification_status": "ai_generated" if score >= _CONFIDENCE_REVIEW_THRESHOLD else "requires_human_review",
    }


def build_structured_output(sections: List[Dict[str, Any]], confidence_score: float) -> Dict[str, Any]:
    """Derive register-ready evidence items from each section's citations.

    Every row is anchored to a real citation (no invented entries); rows below the
    confidence threshold are flagged ``requires_human_review`` per spec §12.
    """
    obligations: List[Dict[str, Any]] = []
    risks: List[Dict[str, Any]] = []
    key_dates: List[Dict[str, Any]] = []
    triggers: List[Dict[str, Any]] = []

    for section in sections:
        key = section["key"]
        section_conf = float(section.get("confidence") or 0.0)
        for c in section.get("citations", []):
            prov = _provenance(c, section_conf)
            title = (c.get("clause_title") or _snippet(c) or "").strip()[:160] or "See source"
            if key in _OBLIGATION_SECTIONS:
                obligations.append(
                    {**prov, "party": _OBLIGATION_SECTIONS[key], "obligation_title": title, "obligation_description": _snippet(c), "status": "open"}
                )
            elif key in _RISK_SECTIONS:
                risks.append({**prov, "risk_title": title, "risk_description": _snippet(c), "risk_category": "other", "severity": None, "status": "open"})
            elif key in _KEY_DATE_SECTIONS:
                key_dates.append({**prov, "date_title": title, "date_type": "other", "status": "open"})
            elif key in _TRIGGER_SECTIONS:
                triggers.append({**prov, "trigger": title})

    overall_risk_rating = _coverage_to_risk(confidence_score)
    return {
        "obligations": obligations,
        "risks": risks,
        "key_dates": key_dates,
        "claim_variation_opportunities": triggers,
        "overall_appraisal": {
            "confidence_score": confidence_score,
            "overall_risk_rating": overall_risk_rating,
        },
    }


def _coverage_to_risk(coverage: float) -> str:
    """Lower contract-coverage → higher documentation/administration risk."""
    if coverage >= 0.85:
        return "low"
    if coverage >= 0.70:
        return "medium"
    if coverage >= 0.50:
        return "high"
    return "critical"


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
